"""
Unity Context Slicer — MCP Server

Exposes the graph query pipeline as MCP tools over stdio transport.
The graph loads once at startup and auto-reloads when the YAML file changes.

Tools exposed:
  - unity_search:   Find nodes by name (fuzzy) and/or type
  - unity_class:    Get focused class context (methods, callers, scenes, inheritance)
  - unity_slice:    Get N-hop neighborhood around any node
  - unity_bundle:   Build a full LLM prompt context bundle (task + context + constraints)
  - unity_stats:    Show graph statistics
  - unity_reload:   Force-reload the graph from disk

Usage:
    # Start the server (stdio transport, for local MCP clients)
    python -m unity_context_slicer.mcp_server --project-dir D:/MyProject

    # Test with MCP Inspector
    npx -y @modelcontextprotocol/inspector python -m unity_context_slicer.mcp_server --project-dir D:/MyProject
"""

from __future__ import annotations

import argparse
import logging
import sys
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP, ToolError

from .session import GraphSession
from .slicer import get_neighborhood, get_class_context
from .compressor import compress_to_text, compress_for_task
from .annotations import AnnotationCache
from .task_log import TaskLog

# ── Logging to stderr (CRITICAL: stdout is reserved for MCP JSON-RPC) ──
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("unity_context_slicer")

# ── Parse args before FastMCP init (FastMCP doesn't handle custom args) ──
def _parse_args():
    parser = argparse.ArgumentParser(description="Unity Context Slicer MCP Server")
    parser.add_argument(
        "--project-dir",
        default=os.environ.get("UNITY_PROJECT_DIR", "."),
        help="Path to Unity project directory (or set UNITY_PROJECT_DIR env var)",
    )
    parser.add_argument(
        "--annotations",
        default=os.environ.get("UNITY_ANNOTATIONS_PATH", ""),
        help="Path to annotations cache JSON (or set UNITY_ANNOTATIONS_PATH env var)",
    )
    parser.add_argument(
        "--task-log",
        default=os.environ.get("UNITY_TASK_LOG_PATH", ""),
        help="Path to task_log.md (or set UNITY_TASK_LOG_PATH env var)",
    )
    # Parse only known args so FastMCP/mcp can handle its own args
    args, _ = parser.parse_known_args()
    return args

_args = _parse_args()

# ── Initialize session and server ──
_session = GraphSession(_args.project_dir)
_annotation_cache: Optional[AnnotationCache] = None
if _args.annotations:
    _annotation_cache = AnnotationCache(_args.annotations)

mcp = FastMCP(
    "Unity Context Slicer",
    instructions=(
        "Query a Unity project's code/scene/prefab graph. "
        "Use 'unity_search' to find nodes, 'unity_class' for class context, "
        "'unity_slice' for neighborhood exploration, 'unity_bundle' to build "
        "full coding context bundles."
    ),
)


# ─── Tool Definitions ──────────────────────────────────────────────────────────

@mcp.tool()
def unity_search(query: str, node_type: str = "") -> str:
    """Search for nodes in the Unity project graph by name.

    Args:
        query: Search string (case-insensitive substring match).
               Examples: "EnemyController", "Update", "BattleScene"
        node_type: Optional filter by type. Valid types:
                   class, method, event, scene, prefab, gameobject, component.
                   Leave empty to search all types.

    Returns:
        List of matching nodes with their type, name, and ID.
    """
    graph = _session.get_graph()
    type_filter = node_type.strip().lower() if node_type else None
    results = graph.find_nodes(name=query, node_type=type_filter)

    if not results:
        return f"No nodes found matching '{query}'" + (f" (type: {node_type})" if node_type else "")

    lines = [f"Found {len(results)} node(s) matching '{query}':\n"]
    for node in sorted(results, key=lambda n: (n.node_type, n.name)):
        meta_parts = []
        if node.metadata.get("namespaceName"):
            meta_parts.append(f"ns={node.metadata['namespaceName']}")
        if node.metadata.get("isMonoBehaviour"):
            meta_parts.append("MonoBehaviour")
        if node.metadata.get("scenePath"):
            meta_parts.append(node.metadata["scenePath"])
        if node.metadata.get("prefabPath"):
            meta_parts.append(node.metadata["prefabPath"])
        if node.metadata.get("context"):
            meta_parts.append(f"in {node.metadata['context']}")

        meta_str = f"  ({', '.join(meta_parts)})" if meta_parts else ""
        lines.append(f"  [{node.node_type:12s}] {node.name}{meta_str}")
        lines.append(f"                 id: {node.node_id}")

    return "\n".join(lines)


@mcp.tool()
def unity_class(class_name: str, include_callers: bool = True, include_callees: bool = True, include_scenes: bool = True) -> str:
    """Get focused context for a C# class — the primary tool for coding tasks.

    Returns the class's methods, what calls them, what they call, events,
    inheritance chain, and which scenes/prefabs use it as a component.
    Output is in dense structured format optimized for LLM consumption.

    Args:
        class_name: Class name to look up. Can be simple ("EnemyController")
                    or fully qualified ("RogueliteAutoBattler.EnemyController").
        include_callers: Include methods that call this class's methods.
        include_callees: Include methods that this class's methods call.
        include_scenes: Include scene/prefab attachment information.

    Returns:
        Compressed class context showing methods, call chains, events,
        inheritance, and scene/prefab attachments.
    """
    graph = _session.get_graph()
    annotations = _annotation_cache.get_all() if _annotation_cache else {}

    try:
        subgraph = get_class_context(
            graph,
            class_name,
            include_callers=include_callers,
            include_callees=include_callees,
            include_scene_refs=include_scenes,
        )
    except KeyError as e:
        raise ToolError(str(e))

    result = compress_to_text(subgraph, annotations=annotations)

    # Surface data quality warnings
    warnings = _check_subgraph_quality(subgraph)
    if warnings:
        result += "\n\n## WARNINGS\n" + "\n".join(f"- {w}" for w in warnings)

    return result


@mcp.tool()
def unity_slice(node_name: str, hops: int = 2, max_nodes: int = 200) -> str:
    """Get the N-hop neighborhood around any node in the project graph.

    More general than unity_class — works on scenes, prefabs, methods,
    GameObjects, or any node type. Use this when exploring connections
    beyond a single class.

    Args:
        node_name: Node name or ID to center the slice on.
                   Examples: "BattleScene", "EnemyPrefab", "BattleManager.StartBattle"
        hops: How many edge-hops outward to include (default: 2).
              1 = direct connections only, 3 = very wide context.
        max_nodes: Safety cap on nodes in the result (default: 200).

    Returns:
        Compressed neighborhood context showing all connected nodes and edges.
    """
    graph = _session.get_graph()
    annotations = _annotation_cache.get_all() if _annotation_cache else {}

    try:
        subgraph = get_neighborhood(
            graph,
            node_name,
            hops=hops,
            max_nodes=max_nodes,
        )
    except KeyError as e:
        raise ToolError(str(e))

    result = compress_to_text(subgraph, annotations=annotations)

    warnings = _check_subgraph_quality(subgraph)
    if warnings:
        result += "\n\n## WARNINGS\n" + "\n".join(f"- {w}" for w in warnings)

    return result


@mcp.tool()
def unity_bundle(node_name: str, task: str, hops: int = 2) -> str:
    """Build a complete LLM coding context bundle for a task.

    Combines: task description + compressed graph slice + task log + constraints.
    This is the primary output format — paste this into your coding LLM's prompt.

    Args:
        node_name: The class or node the task is centered on.
                   Examples: "EnemyController", "PlayerController"
        task: Description of the coding task.
              Example: "Add a health bar that updates when TakeDamage is called"
        hops: How many hops of context to include (default: 2).

    Returns:
        Complete context bundle with TASK, PROJECT CONTEXT, TASK LOG, and CONSTRAINTS sections.
    """
    graph = _session.get_graph()
    annotations = _annotation_cache.get_all() if _annotation_cache else {}

    # Try class-focused context first, fall back to generic neighborhood
    try:
        subgraph = get_class_context(graph, node_name)
    except KeyError:
        try:
            subgraph = get_neighborhood(graph, node_name, hops=hops)
        except KeyError as e:
            raise ToolError(str(e))

    task_log_content = ""
    if _args.task_log:
        tl = TaskLog(_args.task_log)
        task_log_content = tl.read_last_session()

    result = compress_for_task(
        subgraph,
        task_description=task,
        task_log=task_log_content,
        annotations=annotations,
    )

    warnings = _check_subgraph_quality(subgraph)
    if warnings:
        result += "\n\n## DATA QUALITY WARNINGS\n" + "\n".join(f"- {w}" for w in warnings)

    return result


@mcp.tool()
def unity_stats() -> str:
    """Show project graph statistics - node/edge counts by type.

    Use this to verify the graph loaded correctly and get an overview
    of the project's structure before querying specific nodes.
    """
    graph = _session.get_graph()
    stats = graph.stats()
    session_status = _session.status()

    lines = [
        f"Graph: {session_status['project_dir']}",
        f"Loaded in: {session_status['load_time_ms']:.0f}ms",
        f"Stale: {'YES — will reload on next query' if session_status['is_stale'] else 'no'}",
        "",
        f"Total nodes: {stats['total_nodes']}",
        f"Total edges: {stats['total_edges']}",
        "",
        "Node types:",
    ]
    for ntype, count in sorted(stats["node_types"].items(), key=lambda x: -x[1]):
        lines.append(f"  {ntype}: {count}")
    lines.append("")
    lines.append("Edge types:")
    for etype, count in sorted(stats["edge_types"].items(), key=lambda x: -x[1]):
        lines.append(f"  {etype}: {count}")

    return "\n".join(lines)


@mcp.tool()
def unity_reload() -> str:
    """Force-reload the project graph from disk.

    Use this after running a new scan in Unity ('Project Scanner > Scan Project')
    to pick up the latest ProjectStructure.yaml without restarting the server.
    """
    result = _session.force_reload()
    return (
        f"Reloaded in {result['load_time_ms']:.0f}ms\n"
        f"Nodes: {result['total_nodes']}, Edges: {result['total_edges']}\n"
        f"Node types: {result['node_types']}\n"
        f"Edge types: {result['edge_types']}"
    )


# ─── Data quality checks ───────────────────────────────────────────────────────

def _check_subgraph_quality(subgraph) -> list[str]:
    """
    Check for data quality issues in a subgraph result.
    Returns a list of warning strings (empty if clean).

    This surfaces problems that would otherwise be silent gaps —
    the exact "confidently wrong" failure mode we're guarding against.
    """
    warnings = []

    # Count unresolved/dangling nodes
    unresolved = [n for n in subgraph.nodes.values() if n.node_type == "unresolved"]
    if unresolved:
        names = [n.name for n in unresolved[:5]]
        more = f" (+{len(unresolved) - 5} more)" if len(unresolved) > 5 else ""
        warnings.append(
            f"{len(unresolved)} unresolved reference(s): {', '.join(names)}{more}. "
            f"These nodes are referenced by edges but not defined in the YAML — "
            f"likely external/Unity API types. Don't assume their API shape."
        )

    # Check for classes with no methods (might indicate scan failure)
    for node in subgraph.nodes.values():
        if node.node_type == "class" and node.metadata.get("type") == "class":
            has_methods = any(
                e.from_id == node.node_id and e.edge_type == "declares_method"
                for e in subgraph.edges
            )
            if not has_methods and node.metadata.get("isMonoBehaviour"):
                warnings.append(
                    f"Class '{node.name}' is a MonoBehaviour with no methods in the graph. "
                    f"This may indicate the scanner didn't analyze its source file — "
                    f"verify the class exists in the project."
                )

    # Check for nodes with truncated/empty names
    for node in subgraph.nodes.values():
        if node.name in ("", "UnknownClass", "UnknownMethod", "UnknownGO"):
            warnings.append(
                f"Node '{node.node_id}' has a placeholder name — "
                f"data may be incomplete for this node."
            )

    return warnings


# ─── Entry point ────────────────────────────────────────────────────────────────

def run():
    """Start the MCP server with stdio transport."""
    logger.info(f"Starting Unity Context Slicer MCP server")
    logger.info(f"Project directory: {_args.project_dir}")

    # Eagerly load graph at startup so first tool call is fast
    try:
        _session.get_graph()
        logger.info("Graph pre-loaded successfully")
    except FileNotFoundError:
        logger.warning(
            f"Project directory not found at startup: {_args.project_dir} — "
            f"will attempt to load on first tool call"
        )
    except Exception as e:
        logger.warning(f"Failed to pre-load graph: {e} — will retry on first tool call")

    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
