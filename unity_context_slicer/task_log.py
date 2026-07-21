"""
Step 6: Task Log — Simple external memory file persisting across LLM turns.

Append-only log of decisions made, files touched, and next steps.
Independent of the graph pipeline — pure discipline in the prompting harness.
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime


class TaskLog:
    """
    Manages a flat task_log.md file — append-only external memory.

    Format:
    ```
    ## Session: 2024-01-15 14:30
    - DECISION: Use ObjectPool for projectiles instead of Instantiate
    - TOUCHED: Assets/Scripts/Combat/ProjectileManager.cs
    - NEXT: Wire pool to EnemyController.OnDeath spawn
    ```
    """

    def __init__(self, log_path: str | Path = "task_log.md"):
        self.log_path = Path(log_path)
        if not self.log_path.exists():
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text("# Task Log\n\n", encoding="utf-8")

    def new_session(self, session_name: str = ""):
        """Start a new session block."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        header = f"\n## Session: {timestamp}"
        if session_name:
            header += f" — {session_name}"
        header += "\n"
        self._append(header)

    def decision(self, text: str):
        """Log a design decision."""
        self._append(f"- DECISION: {text}\n")

    def touched(self, filepath: str):
        """Log a file that was modified."""
        self._append(f"- TOUCHED: {filepath}\n")

    def next_step(self, text: str):
        """Log the planned next step."""
        self._append(f"- NEXT: {text}\n")

    def note(self, text: str):
        """Log a general note."""
        self._append(f"- NOTE: {text}\n")

    def read(self) -> str:
        """Read the full task log content."""
        if self.log_path.exists():
            return self.log_path.read_text(encoding="utf-8")
        return ""

    def read_last_session(self) -> str:
        """Read only the most recent session block."""
        content = self.read()
        sessions = content.split("\n## Session:")
        if len(sessions) > 1:
            return "## Session:" + sessions[-1]
        return content

    def _append(self, text: str):
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(text)
