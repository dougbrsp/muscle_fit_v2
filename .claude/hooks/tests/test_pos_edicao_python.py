from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import RAIZ_REPO, rodar_hook

import pos_edicao_python as h

PRECISA_RUFF = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff fora do PATH")


# --- funcoes puras -------------------------------------------------------------


def test_mapa_real_cobre_os_donos_conhecidos() -> None:
    sufixos = h.sufixos_do_mapa((RAIZ_REPO / h.MAPA).read_text(encoding="utf-8"))
    for rel in (
        "api/app/domain/e1rm.py",
        "api/app/domain/next_set_load.py",
        "api/app/intelligence/volume_service.py",
        "api/app/periodization/limits.py",
    ):
        assert h.e_dominio_de_motor(rel, sufixos), rel


def test_fora_do_dominio_e_testes_citados_nao_contam() -> None:
    sufixos = h.sufixos_do_mapa("dono `domain/e1rm.py`; teste `test_rpe10_lote6_uma_voz.py`")
    assert not h.e_dominio_de_motor("api/app/routers/auth.py", sufixos)
    assert not h.e_dominio_de_motor("api/tests/unit/test_rpe10_lote6_uma_voz.py", sufixos)
    assert not h.e_dominio_de_motor("api/app/domain/xe1rm.py", sufixos)  # sufixo exige `/`
    assert h.e_dominio_de_motor("api/tests/unit/test_guarda_fontes_da_verdade.py", sufixos)


def test_token_com_argumentos_no_mapa() -> None:
    sufixos = h.sufixos_do_mapa("`api/tests/x.py --gravar` e `generation/mode_a.py::f`")
    assert "generation/mode_a.py" in sufixos


# --- checkout falso (vale para worktrees: a raiz vem do caminho do arquivo) ---------

GUARDA_OK = "def test_ok():\n    assert True\n"
GUARDA_FALHA = "def test_guarda():\n    assert False, 'carga_proxima_serie: reps > 15 fora do dono'\n"


def _checkout(tmp_path: Path, guarda: str) -> Path:
    repo = tmp_path / "wt"
    api = repo / "api"
    (api / "app" / "domain").mkdir(parents=True)
    (api / "app" / "routers").mkdir(parents=True)
    (api / "vendor").mkdir()
    (api / "tests" / "unit").mkdir(parents=True)
    shutil.copy(RAIZ_REPO / "api" / "ruff.toml", api / "ruff.toml")
    (api / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (api / "tests" / "unit" / "test_guarda_fontes_da_verdade.py").write_text(guarda, encoding="utf-8")
    (repo / "docs" / "motores").mkdir(parents=True)
    (repo / "docs" / "motores" / "FONTES_DA_VERDADE.md").write_text(
        "| regra | `domain/e1rm.py` |\n", encoding="utf-8"
    )
    return repo


def _payload(arquivo: Path) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": str(arquivo)}}


@PRECISA_RUFF
def test_ruff_falha_bloqueia(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, GUARDA_OK)
    arq = repo / "api" / "app" / "routers" / "x.py"
    arq.write_text("def f():\n    return nome_indefinido\n", encoding="utf-8")
    r = rodar_hook("pos_edicao_python.py", _payload(arq))
    assert r.returncode == 2
    assert "F821" in r.stderr


@PRECISA_RUFF
def test_arquivo_limpo_fora_do_motor_passa(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, GUARDA_FALHA)  # guarda vermelha, mas nao e motor: nao roda
    arq = repo / "api" / "app" / "routers" / "x.py"
    arq.write_text("def f():\n    return 1\n", encoding="utf-8")
    r = rodar_hook("pos_edicao_python.py", _payload(arq))
    assert r.returncode == 0, r.stderr


def test_motor_com_guarda_vermelha_bloqueia(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, GUARDA_FALHA)
    arq = repo / "api" / "app" / "domain" / "e1rm.py"
    arq.write_text("X = 1\n", encoding="utf-8")
    r = rodar_hook("pos_edicao_python.py", _payload(arq))
    assert r.returncode == 2
    assert "guarda das fontes da verdade falhou" in r.stderr
    assert "reps > 15 fora do dono" in r.stderr


def test_motor_com_guarda_verde_passa(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, GUARDA_OK)
    arq = repo / "api" / "app" / "domain" / "e1rm.py"
    arq.write_text("X = 1\n", encoding="utf-8")
    r = rodar_hook("pos_edicao_python.py", _payload(arq))
    assert r.returncode == 0, r.stderr


def test_editar_o_teste_da_guarda_roda_a_guarda(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, GUARDA_FALHA)
    arq = repo / "api" / "tests" / "unit" / "test_guarda_fontes_da_verdade.py"
    r = rodar_hook("pos_edicao_python.py", _payload(arq))
    assert r.returncode == 2


def test_vendor_e_fora_de_api_sao_ignorados(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, GUARDA_FALHA)
    vend = repo / "api" / "vendor" / "x.py"
    vend.write_text("return_fora = nome_indefinido\n", encoding="utf-8")
    fora = repo / "scripts_x.py"
    fora.write_text("y = nome_indefinido\n", encoding="utf-8")
    assert rodar_hook("pos_edicao_python.py", _payload(vend)).returncode == 0
    assert rodar_hook("pos_edicao_python.py", _payload(fora)).returncode == 0


def test_nao_python_ignorado(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, GUARDA_FALHA)
    md = repo / "docs" / "motores" / "FONTES_DA_VERDADE.md"
    assert rodar_hook("pos_edicao_python.py", _payload(md)).returncode == 0


def test_sem_ruff_no_path_pula_em_silencio(tmp_path: Path, monkeypatch) -> None:
    import os

    repo = _checkout(tmp_path, GUARDA_OK)
    arq = repo / "api" / "app" / "routers" / "x.py"
    arq.write_text("def f():\n    return nome_indefinido\n", encoding="utf-8")
    env = dict(os.environ, PATH="")
    r = rodar_hook("pos_edicao_python.py", _payload(arq), env=env)
    assert r.returncode == 0


@pytest.mark.parametrize("entrada", ["", "x", "[]", '{"tool_input": {"file_path": 3}}'])
def test_json_inesperado_nunca_trava(entrada: str) -> None:
    assert rodar_hook("pos_edicao_python.py", entrada).returncode == 0


def test_caminho_git_bash(tmp_path: Path) -> None:
    repo = _checkout(tmp_path, GUARDA_FALHA)
    arq = repo / "api" / "app" / "domain" / "e1rm.py"
    arq.write_text("X = 1\n", encoding="utf-8")
    p = str(arq).replace("\\", "/")
    if len(p) > 2 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    r = rodar_hook("pos_edicao_python.py", {"tool_input": {"file_path": p}})
    assert r.returncode == 2

