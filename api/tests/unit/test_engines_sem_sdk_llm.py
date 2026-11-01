"""Guarda: a imagem `musclefit-engines` (motores deterministicos) nao carrega o
SDK de LLM. Ver api/requirements-engines.in e api/Dockerfile.engines."""

import re
import subprocess
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[2]
SDK = ("anthropic", "openai")


def _pacotes(lock: str) -> set[str]:
    return {m.group(1).lower() for m in re.finditer(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==", lock, re.M)}


def test_lock_engines_sem_sdk_llm_e_lock_core_com():
    engines = _pacotes((API / "requirements-engines.txt").read_text(encoding="utf-8"))
    core = _pacotes((API / "requirements.txt").read_text(encoding="utf-8"))
    assert "anthropic" in core
    assert not engines.intersection(SDK)
    assert engines < core


def test_in_engines_nao_traz_sdk():
    for nome in ("requirements-engines.in", "requirements-base.in"):
        texto = (API / nome).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(anthropic|openai)\b", texto, re.M | re.I), nome


def test_dockerfile_engines_instala_o_lock_engines():
    docker = (API / "Dockerfile.engines").read_text(encoding="utf-8")
    ignore = (API / "Dockerfile.engines.dockerignore").read_text(encoding="utf-8")
    assert "-r /app/requirements-engines.txt" in docker
    assert "COPY requirements-engines.txt" in docker
    assert "!requirements-engines.txt" in ignore


def test_main_engines_nao_importa_llm():
    codigo = (
        "import sys, app.main_engines;"
        "ruins=[m for m in sys.modules if m in ('anthropic','app.llm_anthropic','app.openai_client')];"
        "print(ruins); sys.exit(1 if ruins else 0)"
    )
    r = subprocess.run([sys.executable, "-c", codigo], cwd=API, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
