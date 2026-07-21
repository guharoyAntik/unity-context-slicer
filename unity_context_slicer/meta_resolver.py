import os
from pathlib import Path
import yaml
import re

class MetaResolver:
    """
    Parses Unity .meta files to build a mapping of GUIDs to file paths.
    """
    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir)
        self.guid_to_path: dict[str, str] = {}
        self.path_to_guid: dict[str, str] = {}

    def resolve(self):
        """Scans the project directory for .meta files and builds the mapping."""
        # Fast regex to find guid: xxxxx
        guid_pattern = re.compile(r"^guid:\s*([a-f0-9]+)", re.MULTILINE)
        
        for root, _, files in os.walk(self.project_dir):
            # Ignore some common large/irrelevant directories
            rel_path = os.path.relpath(root, self.project_dir).replace('\\', '/')
            if rel_path.startswith(('Library', 'Temp', 'Logs', 'Obj', 'Builds', '.git')):
                continue

            for file in files:
                if file.endswith('.meta'):
                    meta_path = os.path.join(root, file)
                    # The actual asset path is the meta path minus the '.meta' extension
                    asset_path = meta_path[:-5]
                    
                    # Store as relative path from project root using forward slashes
                    rel_asset_path = os.path.relpath(asset_path, self.project_dir).replace('\\', '/')
                    
                    try:
                        with open(meta_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            match = guid_pattern.search(content)
                            if match:
                                guid = match.group(1)
                                self.guid_to_path[guid] = rel_asset_path
                                self.path_to_guid[rel_asset_path] = guid
                    except Exception as e:
                        # Skip files that can't be read
                        continue

    def get_path_from_guid(self, guid: str) -> str | None:
        """Returns the relative asset path for a given GUID."""
        return self.guid_to_path.get(guid)

    def get_guid_from_path(self, path: str) -> str | None:
        """Returns the GUID for a given relative asset path."""
        return self.path_to_guid.get(path)

    def get_class_name_from_guid(self, guid: str) -> str | None:
        """
        Special case for C# scripts: Returns the class name (usually the filename without extension)
        from a GUID.
        """
        path = self.get_path_from_guid(guid)
        if path and path.endswith('.cs'):
            # The class name is typically the file name
            return os.path.basename(path)[:-3]
        return None
