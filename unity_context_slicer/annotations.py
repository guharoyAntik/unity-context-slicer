"""
Step 5: Annotation Cache — Stores one-line PURPOSE summaries per node, keyed by content hash.

Annotations are generated once by a larger LLM and cached to disk.
They regenerate only when the source content changes (detected via hash).
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Optional


class AnnotationCache:
    """
    Persistent cache of node_id → one-line PURPOSE annotation.

    Storage format (JSON):
    {
        "node_id": {
            "purpose": "Manages enemy health, death, and damage animations",
            "hash": "abc123...",
            "source": "EnemyController.cs"
        }
    }
    """

    def __init__(self, cache_path: str | Path = "annotations_cache.json"):
        self.cache_path = Path(cache_path)
        self._cache: dict[str, dict] = {}
        self._load()

    def _load(self):
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    def _save(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, indent=2, ensure_ascii=False)

    def get(self, node_id: str) -> Optional[str]:
        """Get cached purpose annotation for a node, or None if not cached."""
        entry = self._cache.get(node_id)
        return entry["purpose"] if entry else None

    def get_all(self) -> dict[str, str]:
        """Get all annotations as node_id → purpose string."""
        return {k: v["purpose"] for k, v in self._cache.items()}

    def set(self, node_id: str, purpose: str, content_hash: str = "", source: str = ""):
        """Store a purpose annotation for a node."""
        self._cache[node_id] = {
            "purpose": purpose,
            "hash": content_hash,
            "source": source,
        }
        self._save()

    def needs_update(self, node_id: str, content_hash: str) -> bool:
        """Check if the annotation needs regeneration (hash mismatch or missing)."""
        entry = self._cache.get(node_id)
        if not entry:
            return True
        return entry.get("hash", "") != content_hash

    def bulk_set(self, annotations: dict[str, dict]):
        """Bulk update: dict of node_id → {"purpose": ..., "hash": ..., "source": ...}"""
        self._cache.update(annotations)
        self._save()

    @staticmethod
    def hash_content(content: str) -> str:
        """Hash a string (e.g. method body) for change detection."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def stats(self) -> dict:
        return {
            "total_annotations": len(self._cache),
            "cache_path": str(self.cache_path),
        }
