"""Root conftest: loads the live-desktop safety plugin (clipboard restore, guarded keys)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tests" / "codex"))
pytest_plugins = ["live_safety"]
