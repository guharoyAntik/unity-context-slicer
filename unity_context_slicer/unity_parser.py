import os
from pathlib import Path
import re
from typing import Dict, Any, List

class UnityRegexParser:
    def __init__(self, meta_resolver):
        self.meta_resolver = meta_resolver

    def parse_project(self, project_dir: str | Path):
        project_dir = Path(project_dir)
        scenes = []
        prefabs = []

        for root, _, files in os.walk(project_dir):
            rel_path = os.path.relpath(root, project_dir).replace('\\', '/')
            if rel_path.startswith(('Library', 'Temp', 'Logs', 'Obj', 'Builds', '.git')):
                continue

            for file in files:
                file_path = os.path.join(root, file)
                rel_file_path = os.path.relpath(file_path, project_dir).replace('\\', '/')

                if file.endswith('.unity'):
                    scenes.append(self.parse_scene(file_path, rel_file_path))
                elif file.endswith('.prefab'):
                    prefabs.append(self.parse_prefab(file_path, rel_file_path))

        return scenes, prefabs

    def parse_scene(self, file_path: str, rel_path: str) -> Dict[str, Any]:
        scene_name = os.path.basename(file_path).replace('.unity', '')
        objects = self._parse_yaml_objects(file_path)
        roots = self._build_hierarchy(objects)

        return {
            "sceneName": scene_name,
            "scenePath": rel_path,
            "gameObjects": roots
        }

    def parse_prefab(self, file_path: str, rel_path: str) -> Dict[str, Any]:
        prefab_name = os.path.basename(file_path).replace('.prefab', '')
        objects = self._parse_yaml_objects(file_path)
        roots = self._build_hierarchy(objects)

        root_obj = roots[0] if roots else None

        return {
            "prefabName": prefab_name,
            "prefabPath": rel_path,
            "rootObject": root_obj
        }

    def _parse_yaml_objects(self, file_path: str) -> Dict[str, Any]:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Split by document separator
        docs = content.split('\n--- ')
        
        objects = {}
        # Parse document headers: !u!<class_id> &<file_id>
        header_pattern = re.compile(r'^!u!(\d+)\s+&(\d+)')
        
        for doc in docs:
            lines = doc.split('\n')
            if not lines: continue
            
            header_match = header_pattern.match(lines[0])
            if not header_match: continue
            
            class_id = header_match.group(1)
            file_id = header_match.group(2)
            
            # The second line usually has the type name, e.g. "GameObject:"
            type_name = ""
            if len(lines) > 1:
                type_match = re.match(r'^([A-Za-z0-9_]+):', lines[1])
                if type_match:
                    type_name = type_match.group(1)
                
            # Basic field extraction using regex
            # e.g., m_Name: Directional Light
            # m_GameObject: {fileID: 12345}
            # m_Script: {fileID: 11500000, guid: xxxx, type: 3}
            
            obj_data = {
                "class_id": class_id,
                "type_name": type_name,
                "file_id": file_id,
                "properties": {},
                "components": [],
                "children": [],
                "game_object_id": None
            }
            
            # Very simplistic regex extraction for properties
            prop_pattern = re.compile(r'^\s+([A-Za-z0-9_]+):\s*(.*)')
            ref_pattern = re.compile(r'\{fileID:\s*(-?\d+)(?:,\s*guid:\s*([a-f0-9]+))?')
            
            in_components_list = False
            in_children_list = False
            
            for line in lines[1:]:
                # Check for list starts
                if re.match(r'^\s+m_Component:', line):
                    in_components_list = True
                    in_children_list = False
                    continue
                elif re.match(r'^\s+m_Children:', line):
                    in_children_list = True
                    in_components_list = False
                    continue
                elif re.match(r'^\s+[A-Za-z0-9_]+:', line):
                    in_components_list = False
                    in_children_list = False
                
                # Parse list items
                if in_components_list and line.strip().startswith('- component:'):
                    match = ref_pattern.search(line)
                    if match:
                        obj_data["components"].append(match.group(1))
                    continue
                    
                if in_children_list and line.strip().startswith('- '):
                    match = ref_pattern.search(line)
                    if match:
                        obj_data["children"].append(match.group(1))
                    continue
                
                # Parse basic properties
                prop_match = prop_pattern.match(line)
                if prop_match:
                    key = prop_match.group(1)
                    val = prop_match.group(2)
                    
                    if key in ('m_GameObject', 'm_Father'):
                        match = ref_pattern.search(val)
                        if match:
                            obj_data["game_object_id"] = match.group(1)
                    elif key == 'm_Script':
                        match = ref_pattern.search(val)
                        if match:
                            obj_data["script_guid"] = match.group(2)
                    elif key == 'm_Name':
                        obj_data["name"] = val.strip()
                    else:
                        obj_data["properties"][key] = val.strip()
                        
            objects[file_id] = obj_data
            
        return objects

    def _build_hierarchy(self, objects: Dict[str, Any]) -> List[Dict[str, Any]]:
        # 1. First, build the game objects
        game_objects = {}
        for file_id, obj in objects.items():
            if obj["type_name"] == "GameObject":
                go = {
                    "name": obj.get("name", "GameObject"),
                    "instanceId": file_id,
                    "components": [],
                    "children": []
                }
                game_objects[file_id] = go
                
        # 2. Attach components to GameObjects
        for file_id, obj in objects.items():
            if obj["type_name"] not in ("GameObject", "Transform", "RectTransform"):
                go_id = obj.get("game_object_id")
                if go_id and go_id in game_objects:
                    comp = {
                        "componentType": obj["type_name"],
                        "className": obj["type_name"],
                        "instanceId": file_id,
                        "properties": obj["properties"]
                    }
                    
                    # Resolve script class name
                    if obj["type_name"] == "MonoBehaviour" and "script_guid" in obj:
                        guid = obj["script_guid"]
                        if guid:
                            class_name = self.meta_resolver.get_class_name_from_guid(guid)
                            if class_name:
                                comp["componentType"] = class_name
                                comp["className"] = class_name
                                
                    game_objects[go_id]["components"].append(comp)

        # 3. Build Transform hierarchy
        # Track which GameObjects are root (have no parent Transform)
        root_go_ids = set(game_objects.keys())
        
        for file_id, obj in objects.items():
            if obj["type_name"] in ("Transform", "RectTransform"):
                go_id = obj.get("game_object_id")
                if go_id and go_id in game_objects:
                    # Look at children of this transform
                    for child_transform_id in obj.get("children", []):
                        if child_transform_id in objects:
                            child_transform = objects[child_transform_id]
                            child_go_id = child_transform.get("game_object_id")
                            if child_go_id and child_go_id in game_objects:
                                game_objects[go_id]["children"].append(game_objects[child_go_id])
                                if child_go_id in root_go_ids:
                                    root_go_ids.remove(child_go_id)
                                    
        roots = [game_objects[go_id] for go_id in root_go_ids]
        return roots
