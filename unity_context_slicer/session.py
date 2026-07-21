"""
Graph Session — Manages a resident, auto-reloading ProjectGraph instance.

The graph loads once and stays in memory across MCP tool calls.
Before each query, the project's source files' mtime are checked; if they are newer
than what's currently loaded, the graph reloads automatically.
"""

from __future__ import annotations

import os
import logging
import time
from pathlib import Path
from typing import Optional

from .loader import ProjectGraph, load_project_graph

logger = logging.getLogger("unity_context_slicer")


class GraphSession:
    """
    Persistent, auto-reloading graph session.

    Usage:
        session = GraphSession("D:/MyProject")
        graph = session.get_graph()  # loads on first call, reloads if files changed
    """

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir)
        self._graph: Optional[ProjectGraph] = None
        self._loaded_mtime: float = 0.0
        self._load_time_ms: float = 0.0

    @property
    def is_loaded(self) -> bool:
        return self._graph is not None

    def _get_latest_mtime(self) -> float:
        """Find the latest mtime among relevant project files."""
        if not self.project_dir.exists():
            return 0.0
            
        latest = 0.0
        extensions = ('.cs', '.unity', '.prefab', '.meta')
        for root, dirs, files in os.walk(self.project_dir):
            rel_path = os.path.relpath(root, self.project_dir).replace('\\', '/')
            if rel_path.startswith(('Library', 'Temp', 'Logs', 'Obj', 'Builds', '.git')):
                dirs.clear() # don't recurse
                continue
                
            for file in files:
                if file.endswith(extensions):
                    try:
                        mtime = os.path.getmtime(os.path.join(root, file))
                        if mtime > latest:
                            latest = mtime
                    except OSError:
                        pass
        return latest

    @property
    def is_stale(self) -> bool:
        """Check if project files have been modified since last load."""
        return self._get_latest_mtime() > self._loaded_mtime

    def get_graph(self) -> ProjectGraph:
        """
        Get the current graph, reloading if the source files changed.
        """
        if self._graph is None or self.is_stale:
            self._reload()
        return self._graph

    def _reload(self):
        """Force-reload the graph from disk."""
        if not self.project_dir.exists():
            raise FileNotFoundError(
                f"Project directory not found at: {self.project_dir}"
            )

        start = time.monotonic()
        self._graph = load_project_graph(self.project_dir)
        self._loaded_mtime = self._get_latest_mtime()
        self._load_time_ms = (time.monotonic() - start) * 1000

        stats = self._graph.stats()
        logger.info(
            f"Graph loaded: {stats['total_nodes']} nodes, {stats['total_edges']} edges "
            f"in {self._load_time_ms:.0f}ms from {self.project_dir}"
        )

    def force_reload(self) -> dict:
        """Explicitly reload (exposed as an MCP tool)."""
        self._reload()
        stats = self._graph.stats()
        return {
            "status": "reloaded",
            "load_time_ms": round(self._load_time_ms, 1),
            **stats,
        }

    def status(self) -> dict:
        """Return session status info."""
        return {
            "project_dir": str(self.project_dir),
            "is_loaded": self.is_loaded,
            "is_stale": self.is_stale,
            "loaded_mtime": self._loaded_mtime,
            "load_time_ms": round(self._load_time_ms, 1),
            "stats": self._graph.stats() if self._graph else None,
        }
