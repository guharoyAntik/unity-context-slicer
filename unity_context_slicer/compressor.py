"""
Step 4: Compressor — Converts a SubGraph into a dense, LLM-friendly text format.

Takes the sliced subgraph and renders it into a compact template format
(CLASS / ATTACHED_TO / SERIALIZED_FIELDS / CALLED_BY / etc.) instead of raw YAML.
This is where the 5–10× token savings come from.

Output format is designed for a 7B local model's limited context window —
every line carries maximum information density.
"""

from __future__ import annotations

from typing import Optional
from datetime import datetime

from .loader import ProjectGraph, GraphNode, GraphEdge
from .slicer import SubGraph


def compress_to_text(
    subgraph: SubGraph,
    annotations: Optional[dict[str, str]] = None,
    include_stats: bool = True,
    max_lines: int = 500,
) -> str:
    """
    Render a SubGraph into dense, structured text for LLM consumption.

    Args:
        subgraph:     The sliced neighborhood.
        annotations:  Optional dict of node_id → one-line PURPOSE annotation (from Step 5).
        include_stats: Whether to include a header with graph stats.
        max_lines:    Safety cap for output length.

    Returns:
        Compact text string ready to be injected into an LLM prompt.
    """
    if annotations is None:
        annotations = {}

    lines: list[str] = []

    # ── Header ──
    if include_stats:
        stats = subgraph.stats()
        lines.append(f"# CONTEXT SLICE: {subgraph.seed_id}")
        lines.append(f"# Hops: {subgraph.hops} | Nodes: {stats['total_nodes']} | Edges: {stats['total_edges']}")
        lines.append(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("")

    # ── Build lookup structures ──
    edges_from: dict[str, list[GraphEdge]] = {}
    edges_to: dict[str, list[GraphEdge]] = {}
    for edge in subgraph.edges:
        edges_from.setdefault(edge.from_id, []).append(edge)
        edges_to.setdefault(edge.to_id, []).append(edge)

    # ── Group nodes by type ──
    nodes_by_type: dict[str, list[GraphNode]] = {}
    for node in subgraph.nodes.values():
        nodes_by_type.setdefault(node.node_type, []).append(node)

    # ── Render classes first (most important for coding context) ──
    for cls_node in sorted(nodes_by_type.get("class", []), key=lambda n: n.node_id == subgraph.seed_id, reverse=True):
        if len(lines) >= max_lines:
            break
        lines.extend(_render_class(cls_node, edges_from, edges_to, subgraph, annotations))
        lines.append("")

    # ── Render scenes ──
    for scene_node in nodes_by_type.get("scene", []):
        if len(lines) >= max_lines:
            break
        lines.extend(_render_scene(scene_node, edges_from, subgraph, annotations))
        lines.append("")

    # ── Render prefabs ──
    for prefab_node in nodes_by_type.get("prefab", []):
        if len(lines) >= max_lines:
            break
        lines.extend(_render_prefab(prefab_node, edges_from, subgraph, annotations))
        lines.append("")

    # ── Render unresolved references (dangling edges) ──
    unresolved = [n for n in nodes_by_type.get("unresolved", []) if n.metadata.get("_dangling")]
    if unresolved:
        lines.append("## EXTERNAL REFERENCES (not in project)")
        for node in unresolved:
            lines.append(f"  - {node.name} [{node.node_id}]")
        lines.append("")

    # ── Footer ──
    if len(lines) >= max_lines:
        lines.append(f"# ... OUTPUT TRUNCATED at {max_lines} lines")

    return "\n".join(lines[:max_lines])


def compress_for_task(
    subgraph: SubGraph,
    task_description: str = "",
    task_log: str = "",
    annotations: Optional[dict[str, str]] = None,
) -> str:
    """
    Build the full LLM prompt context bundle:
      [task template] + [compressed graph slice] + [task log]

    This is Step 7's convergence point — the final output format.
    """
    sections = []

    # Task description
    if task_description:
        sections.append(f"## TASK\n{task_description}\n")

    # Graph context
    context = compress_to_text(subgraph, annotations=annotations)
    sections.append(f"## PROJECT CONTEXT\n{context}")

    # Task log (external memory)
    if task_log:
        sections.append(f"## TASK LOG (previous decisions)\n{task_log}\n")

    # Constraint reminder
    sections.append("## CONSTRAINTS")
    sections.append("- Only modify files listed in PROJECT CONTEXT unless explicitly asked.")
    sections.append("- Preserve all Inspector-wired references (ATTACHED_TO / SERIALIZED_FIELDS).")
    sections.append("- Do not rename public methods that appear in CALLED_BY - they are called externally.")
    sections.append("- Check INHERITS and IMPLEMENTS before changing method signatures.")

    return "\n\n".join(sections)


# ─── Renderers ──────────────────────────────────────────────────────────────────

def _render_class(
    node: GraphNode,
    edges_from: dict,
    edges_to: dict,
    subgraph: SubGraph,
    annotations: dict,
) -> list[str]:
    """Render a class node into the dense text format."""
    lines = []
    meta = node.metadata

    # Header line
    type_label = meta.get("type", "class").upper()
    mono_tag = " [MonoBehaviour]" if meta.get("isMonoBehaviour") else ""
    ns = meta.get("namespaceName", "")
    ns_tag = f" (ns: {ns})" if ns else ""
    lines.append(f"### {type_label}: {node.name}{mono_tag}{ns_tag}")

    # PURPOSE annotation (from Step 5)
    if node.node_id in annotations:
        lines.append(f"  PURPOSE: {annotations[node.node_id]}")

    # INHERITS / IMPLEMENTS
    for edge in edges_from.get(node.node_id, []):
        if edge.edge_type == "structure_child_of":
            target = subgraph.nodes.get(edge.to_id)
            target_name = target.name if target else edge.to_id
            lines.append(f"  INHERITS: {target_name}")
        elif edge.edge_type == "structure_has_component":
            pass  # handled elsewhere

    for edge in edges_to.get(node.node_id, []):
        if edge.edge_type == "structure_child_of":
            child = subgraph.nodes.get(edge.from_id)
            child_name = child.name if child else edge.from_id
            lines.append(f"  SUBCLASS: {child_name}")

    # METHODS
    methods = []
    for edge in edges_from.get(node.node_id, []):
        if edge.edge_type == "declares_method" and edge.to_id in subgraph.nodes:
            methods.append(subgraph.nodes[edge.to_id])

    if methods:
        lines.append("  METHODS:")
        for m in methods:
            static_tag = " [static]" if m.metadata.get("isStatic") else ""
            type_tag = f" ({m.metadata.get('methodType', '')})" if m.metadata.get("methodType") else ""
            purpose = ""
            if m.node_id in annotations:
                purpose = f" — {annotations[m.node_id]}"
            lines.append(f"    - {m.name}{static_tag}{type_tag}{purpose}")

            # What does this method call?
            for call_edge in edges_from.get(m.node_id, []):
                if call_edge.edge_type in ("calls", "field_ref", "prefab_ref", "event_sub"):
                    target = subgraph.nodes.get(call_edge.to_id)
                    target_name = target.name if target else call_edge.to_id
                    method_name = call_edge.metadata.get("methodName", "")
                    detail = f".{method_name}" if method_name else ""
                    lines.append(f"      -> CALLS: {target_name}{detail}")

            # What calls this method?
            for call_edge in edges_to.get(m.node_id, []):
                if call_edge.edge_type in ("calls", "event_sub"):
                    caller = subgraph.nodes.get(call_edge.from_id)
                    caller_name = caller.name if caller else call_edge.from_id
                    lines.append(f"      <- CALLED_BY: {caller_name}")

    # EVENTS
    events = []
    for edge in edges_from.get(node.node_id, []):
        if edge.edge_type == "declares_event" and edge.to_id in subgraph.nodes:
            events.append(subgraph.nodes[edge.to_id])

    if events:
        lines.append("  EVENTS:")
        for e in events:
            static_tag = " [static]" if e.metadata.get("isStatic") else ""
            lines.append(f"    - {e.name}{static_tag}")

    # ATTACHED_TO (which GameObjects/Scenes use this class as a component?)
    attached = []
    for edge in edges_to.get(node.node_id, []):
        if edge.edge_type == "is_instance_of":
            comp = subgraph.nodes.get(edge.from_id)
            if comp:
                # Walk up to find the parent GameObject
                for go_edge in edges_to.get(comp.node_id, []):
                    if go_edge.edge_type == "has_component":
                        go = subgraph.nodes.get(go_edge.from_id)
                        if go:
                            # Find the scene/prefab containing this GO
                            container = _find_container(go.node_id, edges_to, subgraph)
                            attached.append(f"{go.name} in {container}")

    if attached:
        lines.append("  ATTACHED_TO:")
        for a in attached:
            lines.append(f"    - {a}")

    return lines


def _render_scene(
    node: GraphNode,
    edges_from: dict,
    subgraph: SubGraph,
    annotations: dict,
) -> list[str]:
    """Render a scene node with its key GameObjects."""
    lines = []
    lines.append(f"### SCENE: {node.name}")
    if node.metadata.get("scenePath"):
        lines.append(f"  PATH: {node.metadata['scenePath']}")

    if node.node_id in annotations:
        lines.append(f"  PURPOSE: {annotations[node.node_id]}")

    # List top-level GameObjects
    for edge in edges_from.get(node.node_id, []):
        if edge.edge_type == "contains" and edge.to_id in subgraph.nodes:
            go = subgraph.nodes[edge.to_id]
            comp_names = _get_component_names(go.node_id, edges_from, subgraph)
            comp_tag = f" [{', '.join(comp_names)}]" if comp_names else ""
            lines.append(f"  - GO: {go.name}{comp_tag}")

    return lines


def _render_prefab(
    node: GraphNode,
    edges_from: dict,
    subgraph: SubGraph,
    annotations: dict,
) -> list[str]:
    """Render a prefab node with its structure."""
    lines = []
    lines.append(f"### PREFAB: {node.name}")
    if node.metadata.get("prefabPath"):
        lines.append(f"  PATH: {node.metadata['prefabPath']}")

    if node.node_id in annotations:
        lines.append(f"  PURPOSE: {annotations[node.node_id]}")

    # List contained GameObjects
    for edge in edges_from.get(node.node_id, []):
        if edge.edge_type == "contains" and edge.to_id in subgraph.nodes:
            go = subgraph.nodes[edge.to_id]
            comp_names = _get_component_names(go.node_id, edges_from, subgraph)
            comp_tag = f" [{', '.join(comp_names)}]" if comp_names else ""
            lines.append(f"  - GO: {go.name}{comp_tag}")

    return lines


# ─── Helpers ────────────────────────────────────────────────────────────────────

def _find_container(node_id: str, edges_to: dict, subgraph: SubGraph) -> str:
    """Walk up from a GO to find its containing scene or prefab name."""
    for edge in edges_to.get(node_id, []):
        if edge.edge_type == "contains":
            parent = subgraph.nodes.get(edge.from_id)
            if parent:
                if parent.node_type in ("scene", "prefab"):
                    return f"{parent.node_type}:{parent.name}"
                return _find_container(parent.node_id, edges_to, subgraph)
    return "unknown"


def _get_component_names(go_id: str, edges_from: dict, subgraph: SubGraph) -> list[str]:
    """Get the component type names attached to a GameObject."""
    names = []
    for edge in edges_from.get(go_id, []):
        if edge.edge_type == "has_component" and edge.to_id in subgraph.nodes:
            comp = subgraph.nodes[edge.to_id]
            names.append(comp.name)
    return names
