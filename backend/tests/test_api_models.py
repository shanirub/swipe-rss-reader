"""The generated API models must match api/openapi.yaml (PROJECT_PLAN.md §3 Repository)."""

import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_generated_models_are_fresh():
    # Uses the [tool.datamodel-codegen] settings in pyproject.toml, same as `uv run datamodel-codegen`.
    result = subprocess.run(
        [sys.executable, "-m", "datamodel_code_generator", "--check"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "src/swipe_rss/api_models.py is stale; run `cd backend && uv run datamodel-codegen`\n"
        + result.stdout
        + result.stderr
    )
