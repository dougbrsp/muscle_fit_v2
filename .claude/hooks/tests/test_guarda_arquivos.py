from __future__ import annotations

import pytest
from conftest import rodar_hook

import guarda_arquivos as g


@pytest.mark.parametrize(
    "caminho",
    [
        r"C:\muscle_fit\api\requirements.txt",
        r"C:\muscle_fit\api\requirements-dev.txt",
        r"C:\muscle_fit\.claude\worktrees\agent-x\api\requirements-ai.txt",
        "/c/muscle_fit/api/requirements-lint.txt",
        r"C:\muscle_fit\frontend\package-lock.json",
        "C:/muscle_fit/.claude/worktrees/a/frontend/package-lock.json",
        r"C:\openclaw-dev-agent\README.md",
        r"c:\OpenClaw-Dev-Agent\src\x.py",
        "/c/openclaw-dev-agent/x.py",
        r"C:\muscle_fit\..\openclaw-dev-agent\x.py",
    ],
)
def test_bloqueados(caminho: str) -> None:
    assert g.avaliar(caminho) is not None, caminho


@pytest.mark.parametrize(
    "caminho",
    [
        r"C:\muscle_fit\api\requirements.in",
        r"C:\muscle_fit\api\requirements-dev.in",
        r"C:\muscle_fit\api\app\requirements_helper.py",
        r"C:\muscle_fit\frontend\package.json",
        r"C:\muscle_fit\docs\requirements.txt",
        r"C:\muscle_fit\api\app\main.py",
        r"C:\openclaw-dev-agent-copia\x.py",
    ],
)
def test_permitidos(caminho: str) -> None:
    assert g.avaliar(caminho) is None, caminho


def test_relativo_resolvido_pelo_cwd() -> None:
    assert g.avaliar("api/requirements.txt", cwd=r"C:\muscle_fit") is not None
    assert g.avaliar("requirements.txt", cwd=r"C:\muscle_fit\api") is not None


def test_mensagens_dizem_a_forma_correta() -> None:
    assert ".in" in g.avaliar(r"C:\muscle_fit\api\requirements.txt")
    assert "npm install" in g.avaliar(r"C:\muscle_fit\frontend\package-lock.json")
    assert "C:\\muscle_fit" in g.avaliar(r"C:\openclaw-dev-agent\x")


@pytest.mark.parametrize("ferramenta", ["Edit", "Write", "MultiEdit"])
def test_main_bloqueia(ferramenta: str) -> None:
    r = rodar_hook(
        "guarda_arquivos.py",
        {"tool_name": ferramenta, "tool_input": {"file_path": r"C:\muscle_fit\api\requirements.txt"}},
    )
    assert r.returncode == 2 and "pip-compile" in r.stderr


def test_main_notebook_edit() -> None:
    r = rodar_hook(
        "guarda_arquivos.py",
        {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": r"C:\openclaw-dev-agent\n.ipynb"}},
    )
    assert r.returncode == 2


def test_main_permite() -> None:
    r = rodar_hook(
        "guarda_arquivos.py",
        {"tool_name": "Edit", "tool_input": {"file_path": r"C:\muscle_fit\api\app\main.py"}},
    )
    assert r.returncode == 0 and r.stderr == ""


@pytest.mark.parametrize("entrada", ["", "{", "[]", '{"tool_input": []}', '{"tool_input": {"file_path": null}}'])
def test_main_json_inesperado_nunca_trava(entrada: str) -> None:
    assert rodar_hook("guarda_arquivos.py", entrada).returncode == 0
