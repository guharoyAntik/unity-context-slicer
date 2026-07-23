"""
Step 2: YAML Loader — Parses ProjectStructure.yaml into a flat, queryable in-memory graph.

Maps the hierarchical YAML (scenes→gameObjects→components, prefabs→rootObject, etc.)
into a unified set of GraphNode and GraphEdge objects with synthetic IDs, ready for
the slicer (Step 3).

Exact field names match petitioner0/project-visualizer's ProjectScannerModels.cs.
"""

from __future__ import annotations

import yaml
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

CACHE_DIR_NAME = ".unity_context_slicer"
CACHE_FILE_NAME = "graph_cache.json"

logger = logging.getLogger("unity_context_slicer")



# ─── Graph primitives ──────────────────────────────────────────────────────────

@dataclass
class GraphNode:
    """A node in the flattened project graph."""
    node_id: str                     # Synthetic unique ID (e.g. "scene::MainMenu")
    node_type: str                   # scene | gameobject | component | class | method | event | prefab
    name: str                        # Human-readable name
    metadata: dict = field(default_factory=dict)  # All extra fields (path, namespace, isMonoBehaviour, etc.)

    def __repr__(self):
        return f"Node({self.node_type}:{self.name})"


@dataclass
class GraphEdge:
    """A directed edge in the project graph."""
    from_id: str
    to_id: str
    edge_type: str                   # contains | has_component | child_of | declares_method |
                                     # declares_event | calls | inherits | implements | field_ref |
                                     # prefab_ref | structure_rel
    metadata: dict = field(default_factory=dict)  # callType, methodName, fieldName, libraryName, etc.

    def __repr__(self):
        return f"Edge({self.edge_type}: {self.from_id} → {self.to_id})"


@dataclass
class ProjectGraph:
    """The complete flattened graph — nodes indexed by ID, edges stored as a list."""
    nodes: dict[str, GraphNode] = field(default_factory=dict)     # node_id → GraphNode
    edges: list[GraphEdge] = field(default_factory=list)

    # ── Fast lookup indices (built after loading) ──
    _edges_from: dict[str, list[GraphEdge]] = field(default_factory=dict, repr=False)
    _edges_to: dict[str, list[GraphEdge]] = field(default_factory=dict, repr=False)

    def add_node(self, node: GraphNode):
        self.nodes[node.node_id] = node

    def add_edge(self, edge: GraphEdge):
        self.edges.append(edge)
        self._edges_from.setdefault(edge.from_id, []).append(edge)
        self._edges_to.setdefault(edge.to_id, []).append(edge)

    def edges_from(self, node_id: str) -> list[GraphEdge]:
        """All edges originating from this node."""
        return self._edges_from.get(node_id, [])

    def edges_to(self, node_id: str) -> list[GraphEdge]:
        """All edges pointing to this node."""
        return self._edges_to.get(node_id, [])

    def neighbors(self, node_id: str) -> set[str]:
        """All node IDs directly connected (in or out) to this node."""
        result = set()
        for e in self.edges_from(node_id):
            result.add(e.to_id)
        for e in self.edges_to(node_id):
            result.add(e.from_id)
        return result

    def find_nodes(self, name: Optional[str] = None, node_type: Optional[str] = None) -> list[GraphNode]:
        """Search nodes by name substring and/or type."""
        results = []
        for node in self.nodes.values():
            if name and name.lower() not in node.name.lower():
                continue
            if node_type and node.node_type != node_type:
                continue
            results.append(node)
        return results

    def stats(self) -> dict:
        """Quick summary stats."""
        type_counts = {}
        for n in self.nodes.values():
            type_counts[n.node_type] = type_counts.get(n.node_type, 0) + 1
        edge_counts = {}
        for e in self.edges:
            edge_counts[e.edge_type] = edge_counts.get(e.edge_type, 0) + 1
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "node_types": type_counts,
            "edge_types": edge_counts,
        }


# ─── Graph Cache Persistence ───────────────────────────────────────────────────

def get_cache_path(project_dir: str | Path) -> Path:
    """Return absolute path to the project graph cache file."""
    return Path(project_dir) / CACHE_DIR_NAME / CACHE_FILE_NAME


def save_graph_cache(project_dir: str | Path, graph: ProjectGraph, mtime: float) -> Path:
    """Save graph and metadata to project-local cache JSON safely."""
    cache_path = get_cache_path(project_dir)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_suffix(".tmp")

    nodes_data = [
        {
            "node_id": n.node_id,
            "node_type": n.node_type,
            "name": n.name,
            "metadata": n.metadata,
        }
        for n in graph.nodes.values()
    ]
    edges_data = [
        {
            "from_id": e.from_id,
            "to_id": e.to_id,
            "edge_type": e.edge_type,
            "metadata": e.metadata,
        }
        for e in graph.edges
    ]
    data = {
        "mtime": mtime,
        "nodes": nodes_data,
        "edges": edges_data,
    }
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    temp_path.replace(cache_path)
    return cache_path


def load_graph_cache(project_dir: str | Path) -> tuple[Optional[ProjectGraph], float]:
    """Load graph and metadata from project-local cache JSON if available."""
    cache_path = get_cache_path(project_dir)
    if not cache_path.exists():
        return None, 0.0
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mtime = float(data.get("mtime", 0.0))
        graph = ProjectGraph()
        for n_dict in data.get("nodes", []):
            graph.add_node(GraphNode(
                node_id=n_dict["node_id"],
                node_type=n_dict["node_type"],
                name=n_dict["name"],
                metadata=n_dict.get("metadata", {}),
            ))
        for e_dict in data.get("edges", []):
            graph.add_edge(GraphEdge(
                from_id=e_dict["from_id"],
                to_id=e_dict["to_id"],
                edge_type=e_dict["edge_type"],
                metadata=e_dict.get("metadata", {}),
            ))
        return graph, mtime
    except Exception as e:
        logger.warning(f"Failed to load graph cache from {cache_path}: {e}")
        return None, 0.0


# ─── YAML → Graph loader ───────────────────────────────────────────────────────


import subprocess
import json
from .meta_resolver import MetaResolver
from .unity_parser import UnityRegexParser

def load_project_graph(project_dir: str | Path) -> ProjectGraph:
    """
    Parse a Unity project directory into a ProjectGraph by running the standalone
    C# scanner for code semantics and Python Unity regex parser for scenes/prefabs.
    """
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        raise ValueError(f"Not a valid directory: {project_dir}")

    # 1. Resolve GUIDs
    meta_resolver = MetaResolver(project_dir)
    meta_resolver.resolve()

    # 2. Parse Unity scenes and prefabs
    unity_parser = UnityRegexParser(meta_resolver)
    scenes, prefabs = unity_parser.parse_project(project_dir)

    # 3. Run C# scanner
    csharp_scanner_dir = Path(__file__).parent / "csharp_scanner"
    result = subprocess.run(
        ["dotnet", "run", "--project", str(csharp_scanner_dir), "--", str(project_dir)],
        capture_output=True, text=True, check=False
    )

    if result.returncode != 0:
        raise RuntimeError(f"C# Scanner failed: {result.stderr}")

    try:
        csharp_data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse C# scanner output: {e}\nOutput: {result.stdout}")

    # 4. Combine data
    raw = {
        "scenes": scenes,
        "prefabs": prefabs,
        "externalClasses": csharp_data.get("externalClasses", []),
        "calls": csharp_data.get("calls", []),
        "structureRelations": csharp_data.get("structureRelations", [])
    }

    graph = ProjectGraph()
    pending_instance_edges: list[tuple[str, str]] = []

    # ── 1. Scenes ──
    for scene in (raw.get("scenes") or []):
        scene_name = scene.get("sceneName", "UnknownScene")
        scene_id = f"scene::{scene_name}"
        graph.add_node(GraphNode(
            node_id=scene_id,
            node_type="scene",
            name=scene_name,
            metadata={"scenePath": scene.get("scenePath", "")},
        ))

        for go in (scene.get("gameObjects") or []):
            _flatten_gameobject(graph, go, parent_id=scene_id, context_prefix=f"scene::{scene_name}", pending_instance_edges=pending_instance_edges)

    # ── 2. External classes ──
    for cls in (raw.get("externalClasses") or []):
        class_name = cls.get("className", "UnknownClass")
        ns = cls.get("namespaceName", "")
        fqn = f"{ns}.{class_name}" if ns else class_name
        class_id = f"class::{fqn}"

        graph.add_node(GraphNode(
            node_id=class_id,
            node_type="class",
            name=class_name,
            metadata={
                "namespaceName": ns,
                "type": cls.get("type", "class"),           # class | interface | struct | enum
                "isMonoBehaviour": cls.get("isMonoBehaviour", False),
                "fqn": fqn,
            },
        ))

        # Methods
        for method in (cls.get("methods") or []):
            _add_method_node(graph, method, owner_id=class_id, owner_name=fqn)

        # Events
        for event in (cls.get("events") or []):
            event_name = event.get("eventName", "UnknownEvent")
            event_node_id = event.get("nodeId", f"event::{fqn}.{event_name}")
            graph.add_node(GraphNode(
                node_id=event_node_id,
                node_type="event",
                name=event_name,
                metadata={
                    "isStatic": event.get("isStatic", False),
                    "ownerClass": fqn,
                },
            ))
            graph.add_edge(GraphEdge(
                from_id=class_id,
                to_id=event_node_id,
                edge_type="declares_event",
            ))

        # Static initializers (treated as methods)
        for init in (cls.get("staticInitializers") or []):
            _add_method_node(graph, init, owner_id=class_id, owner_name=fqn)

    # ── 3. Calls (edges between nodeIds) ──
    for call in (raw.get("calls") or []):
        from_id = call.get("fromNodeId", "")
        to_id = call.get("toNodeId", "")
        if not from_id or not to_id:
            continue

        call_type = call.get("callType", "unknown")

        # Map callType to our edge_type vocabulary
        edge_type_map = {
            "method": "calls",
            "field_reference": "field_ref",
            "prefab_reference": "prefab_ref",
            "event_subscription": "event_sub",
        }
        edge_type = edge_type_map.get(call_type, f"call_{call_type}")

        graph.add_edge(GraphEdge(
            from_id=from_id,
            to_id=to_id,
            edge_type=edge_type,
            metadata={
                k: v for k, v in {
                    "callType": call_type,
                    "methodName": call.get("methodName"),
                    "fieldName": call.get("fieldName"),
                    "libraryName": call.get("libraryName"),
                }.items() if v
            },
        ))

    # ── 4. Structure relations (edges between nodeIds) ──
    for rel in (raw.get("structureRelations") or []):
        from_id = rel.get("fromNodeId", "")
        to_id = rel.get("toNodeId", "")
        rel_type = rel.get("relationType", "unknown")
        if not from_id or not to_id:
            continue

        graph.add_edge(GraphEdge(
            from_id=from_id,
            to_id=to_id,
            edge_type=f"structure_{rel_type}",  # e.g. structure_child_of, structure_has_component
            metadata={"relationType": rel_type},
        ))

    # ── 5. Prefabs ──
    for prefab in (raw.get("prefabs") or []):
        prefab_name = prefab.get("prefabName", "UnknownPrefab")
        prefab_id = f"prefab::{prefab_name}"
        graph.add_node(GraphNode(
            node_id=prefab_id,
            node_type="prefab",
            name=prefab_name,
            metadata={"prefabPath": prefab.get("prefabPath", "")},
        ))

        root_obj = prefab.get("rootObject")
        if root_obj:
            _flatten_gameobject(graph, root_obj, parent_id=prefab_id, context_prefix=f"prefab::{prefab_name}", pending_instance_edges=pending_instance_edges)

    # ── 6. Resolve pending component -> class (is_instance_of) edges ──
    # Build class name / FQN lookup
    class_lookup: dict[str, str] = {}
    for node in graph.nodes.values():
        if node.node_type == "class":
            fqn = node.metadata.get("fqn", node.name)
            class_lookup[fqn] = node.node_id
            if node.name not in class_lookup:
                class_lookup[node.name] = node.node_id

    for comp_id, comp_class in pending_instance_edges:
        class_id = class_lookup.get(comp_class)
        if not class_id:
            # Fallback to fclass if not found in lookup
            class_id = f"class::{comp_class}"
        graph.add_edge(GraphEdge(
            from_id=comp_id,
            to_id=class_id,
            edge_type="is_instance_of",
        ))

    return graph


# ─── Helpers ────────────────────────────────────────────────────────────────────

def _flatten_gameobject(graph: ProjectGraph, go: dict, parent_id: str, context_prefix: str, pending_instance_edges: list[tuple[str, str]]):
    """Recursively flatten a GameObjectStructure tree into nodes + edges."""
    go_name = go.get("name", "UnknownGO")
    instance_id = go.get("instanceId", "")
    go_id = f"go::{context_prefix}/{go_name}"
    if instance_id:
        go_id = f"go::{instance_id}"

    graph.add_node(GraphNode(
        node_id=go_id,
        node_type="gameobject",
        name=go_name,
        metadata={"instanceId": instance_id, "context": context_prefix},
    ))

    # Parent → GameObject edge
    graph.add_edge(GraphEdge(
        from_id=parent_id,
        to_id=go_id,
        edge_type="contains",
    ))

    # Components
    for comp in (go.get("components") or []):
        comp_type = comp.get("componentType", "UnknownComp")
        comp_class = comp.get("className", comp_type)
        comp_instance = comp.get("instanceId", "")
        comp_id = f"comp::{comp_instance}" if comp_instance else f"comp::{go_id}/{comp_type}"

        graph.add_node(GraphNode(
            node_id=comp_id,
            node_type="component",
            name=comp_type,
            metadata={
                "componentType": comp_type,
                "className": comp_class,
                "instanceId": comp_instance,
                "properties": comp.get("properties", {}),
            },
        ))

        graph.add_edge(GraphEdge(
            from_id=go_id,
            to_id=comp_id,
            edge_type="has_component",
        ))

        # Link component → its class definition (if one exists in the graph)
        # This is a soft link — the class node may or may not exist yet.
        # The slicer handles dangling edges gracefully.
        if comp_class:
            pending_instance_edges.append((comp_id, comp_class))

        # Methods declared on this component (from scene scanning)
        for method in (comp.get("methods") or []):
            _add_method_node(graph, method, owner_id=comp_id, owner_name=f"{go_name}/{comp_type}")

    # Recurse into children
    for child in (go.get("children") or []):
        _flatten_gameobject(graph, child, parent_id=go_id, context_prefix=context_prefix, pending_instance_edges=pending_instance_edges)


def _add_method_node(graph: ProjectGraph, method: dict, owner_id: str, owner_name: str):
    """Add a method node and owner→method edge."""
    method_name = method.get("methodName", "UnknownMethod")
    method_node_id = method.get("nodeId", f"method::{owner_name}.{method_name}")

    graph.add_node(GraphNode(
        node_id=method_node_id,
        node_type="method",
        name=method_name,
        metadata={
            "methodType": method.get("methodType", ""),
            "isStatic": method.get("isStatic", False),
            "ownerNode": owner_id,
        },
    ))

    graph.add_edge(GraphEdge(
        from_id=owner_id,
        to_id=method_node_id,
        edge_type="declares_method",
    ))
