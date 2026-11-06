from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.p5.contracts import PromptRecord


def _version(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10,
                                   check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return (completed.stdout or completed.stderr).strip().splitlines()[0]


def prompt_inventory(directory: Path) -> list[PromptRecord]:
    records = []
    for path in sorted(Path(directory).rglob("*.txt")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        match = re.search(r"\.v([0-9]+)\.txt$", path.name)
        version = f"v{match.group(1)}" if match else "unversioned"
        records.append(PromptRecord(path=path.relative_to(directory).as_posix(),
                                    version=version, sha256=digest))
    return records


def public_environment_snapshot() -> dict:
    endpoint = os.environ.get("OPENAI_BASE_URL")
    pandoc_version = _version(["pandoc", "--version"])
    if pandoc_version is None:
        try:
            import pypandoc
            pandoc_version = f"pypandoc bundled {pypandoc.get_pandoc_version()}"
        except (ImportError, OSError, RuntimeError):
            pandoc_version = None
    return {
        "operating_system": platform.platform(),
        "python_version": platform.python_version(),
        "node_version": _version(["node", "--version"]),
        "npm_version": _version(["npm", "--version"]),
        "pandoc_version": pandoc_version,
        "playwright_chromium": "installed" if any(
            Path.home().glob("Library/Caches/ms-playwright/chromium-*")
        ) else "not_detected",
        "model_api_key": "configured" if os.environ.get("OPENAI_API_KEY") else "not_configured",
        "model_endpoint": ("default_application_endpoint" if not endpoint else
                           "configured_custom_endpoint"),
        "requested_model": os.environ.get("OPENAI_MODEL", "gpt-6.1-sol"),
        "git_executable": shutil.which("git") is not None,
        "executable": sys.executable,
    }
