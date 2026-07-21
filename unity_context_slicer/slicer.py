"""
Step 3: Graph Slicer — Extracts N-hop neighborhoods from the project graph.

Given a starting node (e.g. "EnemyController"), walks edges outward N hops
to produce a compact subgraph containing only the relevant context:
  - The node's fields, methods, events
  - What calls it and what it calls
  - What components it's attached to
  - Which prefabs/scenes reference it
  - Inheritance and interface chains

This is the core of the "relevant slice for this task" feature.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .loader import ProjectGraph, GraphNode, GraphEdge


@dataclass
class SubGraph:
    """A subset of a ProjectGraph — the result of a slice operation."""
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    seed_id: str = ""             # The node we started from
    hops: int = 0                 # How many hops out we went
    _seen_edges: set[tuple[str, str, str]] = field(default_factory=set, repr=False)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def add_edge(self, edge: GraphEdge):
        edge_key = (edge.from_id, edge.to_id, edge.edge_type)
        if edge_key not in self._seen_edges:
            self._seen_edges.add(edge_key)
            self.edges.append(edge)

    def stats(self) -> dict:
        type_counts = {}
        for n in self.nodes.values():
            type_counts[n.node_type] = type_counts.get(n.node_type, 0) + 1
        edge_counts = {}
        for e in self.edges:
            edge_counts[e.edge_type] = edge_counts.get(e.edge_type, 0) + 1
        return {
            "seed": self.seed_id,
            "hops": self.hops,
            "total_nodes": self.node_count,
            "total_edges": self.edge_count,
            "node_types": type_counts,
            "edge_types": edge_counts,
        }


def _add_node_to_subgraph(subgraph: SubGraph, graph: ProjectGraph, node_id: str):
    """Add a node to the subgraph, or create a placeholder if it is dangling."""
    if node_id in graph.nodes:
        subgraph.nodes[node_id] = graph.nodes[node_id]
    else:
        subgraph.nodes[node_id] = GraphNode(
            node_id=node_id,
            node_type="unresolved",
            name=node_id.split("::")[-1] if "::" in node_id else node_id,
            metadata={"_dangling": True},
        )


def get_neighborhood(
    graph: ProjectGraph,
    node_id: str,
    hops: int = 2,
    max_nodes: int = 200,
    edge_type_filter: Optional[set[str]] = None,
    exclude_types: Optional[set[str]] = None,
) -> SubGraph:
    """
    BFS outward from `node_id` up to `hops` levels, collecting nodes and edges.

    Args:
        graph:              The full project graph.
        node_id:            Starting node ID (exact match or fuzzy search).
        hops:               Max hops outward (default 2).
        max_nodes:          Safety cap to avoid blowing up context (default 200).
        edge_type_filter:   If set, only traverse edges of these types.
        exclude_types:      Node types to skip (e.g. {"gameobject"} to skip scene hierarchy noise).

    Returns:
        SubGraph containing the neighborhood.

    Raises:
        KeyError if node_id is not found and no fuzzy match exists.
    """
    # Resolve node_id — try exact match first, then fuzzy
    if node_id not in graph.nodes:
        node_id = _resolve_node_id(graph, node_id)

    subgraph = SubGraph(seed_id=node_id, hops=hops)

    # BFS
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque()  # (node_id, current_depth)
    queue.append((node_id, 0))
    visited.add(node_id)

    while queue:
        current_id, depth = queue.popleft()

        # Add node if it exists in the graph
        if current_id in graph.nodes:
            node = graph.nodes[current_id]
            if exclude_types and node.node_type in exclude_types:
                continue

        _add_node_to_subgraph(subgraph, graph, current_id)

        if len(subgraph.nodes) >= max_nodes:
            break

        if depth >= hops:
            continue

        # Walk outgoing edges
        for edge in graph.edges_from(current_id):
            if edge_type_filter and edge.edge_type not in edge_type_filter:
                continue
            subgraph.add_edge(edge)
            if edge.to_id not in visited:
                visited.add(edge.to_id)
                queue.append((edge.to_id, depth + 1))

        # Walk incoming edges
        for edge in graph.edges_to(current_id):
            if edge_type_filter and edge.edge_type not in edge_type_filter:
                continue
            subgraph.add_edge(edge)
            if edge.from_id not in visited:
                visited.add(edge.from_id)
                queue.append((edge.from_id, depth + 1))

    return subgraph


def get_class_context(
    graph: ProjectGraph,
    class_name: str,
    include_callers: bool = True,
    include_callees: bool = True,
    include_scene_refs: bool = True,
) -> SubGraph:
    """
    High-level helper: get everything relevant to a class for LLM context.

    This is the most common query pattern — "tell me about EnemyController":
      - The class itself + its methods/events
      - What it inherits from / implements
      - What calls its methods (callers)
      - What its methods call (callees)
      - Which scenes/prefabs use it as a component

    Uses targeted edge traversal instead of brute-force BFS for cleaner output.
    """
    # Find the class node
    class_id = _resolve_node_id(graph, class_name, prefer_type="class")
    subgraph = SubGraph(seed_id=class_id, hops=2)

    # Add the class itself
    _add_node_to_subgraph(subgraph, graph, class_id)

    # Hop 1: Direct children (methods, events)
    for edge in graph.edges_from(class_id):
        if edge.edge_type in ("declares_method", "declares_event"):
            subgraph.add_edge(edge)
            _add_node_to_subgraph(subgraph, graph, edge.to_id)

    # Inheritance / implementation (structure relations pointing FROM this class)
    for edge in graph.edges_from(class_id):
        if edge.edge_type.startswith("structure_"):
            subgraph.add_edge(edge)
            _add_node_to_subgraph(subgraph, graph, edge.to_id)

    # Inheritance pointing TO this class (subclasses)
    for edge in graph.edges_to(class_id):
        if edge.edge_type.startswith("structure_"):
            subgraph.add_edge(edge)
            _add_node_to_subgraph(subgraph, graph, edge.from_id)

    # Hop 2: Callers and callees (via method nodes)
    method_ids = {e.to_id for e in subgraph.edges if e.edge_type == "declares_method"}

    if include_callees:
        for method_id in method_ids:
            for edge in graph.edges_from(method_id):
                if edge.edge_type in ("calls", "field_ref", "prefab_ref", "event_sub"):
                    subgraph.add_edge(edge)
                    _add_node_to_subgraph(subgraph, graph, edge.to_id)

    if include_callers:
        for method_id in method_ids:
            for edge in graph.edges_to(method_id):
                if edge.edge_type in ("calls", "field_ref", "event_sub"):
                    subgraph.add_edge(edge)
                    _add_node_to_subgraph(subgraph, graph, edge.from_id)

    if include_scene_refs:
        # Find components that are instances of this class
        for edge in graph.edges_to(class_id):
            if edge.edge_type == "is_instance_of":
                comp_id = edge.from_id
                subgraph.add_edge(edge)
                _add_node_to_subgraph(subgraph, graph, comp_id)

                # Walk up to the containing GameObject and Scene/Prefab
                for parent_edge in graph.edges_to(comp_id):
                    if parent_edge.edge_type == "has_component":
                        subgraph.add_edge(parent_edge)
                        go_id = parent_edge.from_id
                        _add_node_to_subgraph(subgraph, graph, go_id)

                        # Walk up to Scene/Prefab
                        for scene_edge in graph.edges_to(go_id):
                            if scene_edge.edge_type == "contains":
                                subgraph.add_edge(scene_edge)
                                _add_node_to_subgraph(subgraph, graph, scene_edge.from_id)

    return subgraph


def _resolve_node_id(graph: ProjectGraph, query: str, prefer_type: Optional[str] = None) -> str:
    """
    Resolve a user-friendly query (e.g. "EnemyController") to an exact node_id.

    Tries in order:
      1. Exact match on node_id
      2. Exact match on node_id with common prefixes (class::, method::, etc.)
      3. Case-insensitive substring match on name, preferring the specified type
    """
    # 1. Exact
    if query in graph.nodes:
        return query

    # 2. Try common prefixes
    prefixes = ["class::", "method::", "scene::", "prefab::", "go::", "comp::", "event::"]
    for prefix in prefixes:
        candidate = f"{prefix}{query}"
        if candidate in graph.nodes:
            if prefer_type is None or graph.nodes[candidate].node_type == prefer_type:
                return candidate

    # 3. Fuzzy match
    matches = []
    for nid, node in graph.nodes.items():
        if query.lower() in node.name.lower():
            matches.append(node)
        elif query.lower() in nid.lower():
            matches.append(node)

    if not matches:
        raise KeyError(f"No node found matching '{query}'. Available types: {list(set(n.node_type for n in graph.nodes.values()))}")

    if prefer_type:
        typed = [m for m in matches if m.node_type == prefer_type]
        if typed:
            matches = typed

    # Prefer exact name match over substring
    exact = [m for m in matches if m.name.lower() == query.lower()]
    if exact:
        return exact[0].node_id

    return matches[0].node_id
