"""
Agent Rules Manager — Automatically ensures project-level rule files exist
for popular AI coding agents (Cursor, Windsurf, Cline/Roo Code, Antigravity/Gemini).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("unity_context_slicer")

RULE_MARKER = "UNITY_CONTEXT_SLICER_RULES"

RULE_CONTENT = f"""
<!-- {RULE_MARKER}_START -->
# Unity Context Slicer Guidelines
When answering questions or writing C#/Unity code in this project:
1. ALWAYS use `unity_search` to discover relevant classes, methods, or scene GameObjects.
2. ALWAYS use `unity_class` to inspect MonoBehaviour callers, attached scene objects, and class inheritance.
3. Use `unity_slice` for N-hop neighborhood graph exploration.
4. Use `unity_bundle` before performing refactors to load complete prompt context.
<!-- {RULE_MARKER}_END -->
"""

RULE_FILES = [
    ".cursorrules",
    ".windsurfrules",
    ".clinerules",
    Path(".gemini") / "rules.md",
]


def ensure_agent_rules(project_dir: str | Path) -> list[Path]:
    """
    Ensure all standard AI agent rule files exist in project_dir with Unity Context Slicer instructions.
    Returns list of modified/created rule file paths.
    """
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        return []

    updated_files: list[Path] = []

    for rel_path in RULE_FILES:
        target_file = project_dir / rel_path

        try:
            if target_file.exists():
                content = target_file.read_text(encoding="utf-8", errors="ignore")
                if "unity_search" in content or RULE_MARKER in content:
                    continue  # Already present
                # Append rule content
                new_content = content.rstrip() + "\n" + RULE_CONTENT
                target_file.write_text(new_content, encoding="utf-8")
                updated_files.append(target_file)
                logger.info(f"Appended Unity Context Slicer rules to {target_file}")
            else:
                # Create file
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(RULE_CONTENT.lstrip(), encoding="utf-8")
                updated_files.append(target_file)
                logger.info(f"Created agent rules file at {target_file}")
        except Exception as e:
            logger.warning(f"Could not update agent rule file {target_file}: {e}")

    return updated_files
