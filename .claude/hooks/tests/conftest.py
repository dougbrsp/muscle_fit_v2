"""Testes dos hooks do Claude Code (`.claude/hooks`).

Rodar da raiz do repo: python -m pytest .claude/hooks/tests -q -p no:cacheprovider
(fora do CI: o job backend unit so coleta api/tests/unit).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1]
RAIZ_REPO = HOOKS.parents[1]
sys.path.insert(0, str(HOOKS))


def rodar_hook(script: str, payload: dict | str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Roda o hook como o Claude Code roda: JSON no stdin, decide pelo exit code."""
    entrada = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=entrada,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env=env,
    )
