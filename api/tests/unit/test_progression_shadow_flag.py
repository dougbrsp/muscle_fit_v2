"""Modo sombra do PR-10: a flag `PROGRESSION_ENGINE` e a garantia de que o
`finish` nunca cai por causa do motor novo.

Regra do PR: o deploy NAO pode mudar comportamento nenhum. Producao nao define
a variavel, entao o default tem de ser `v1` (desligado).
"""

from __future__ import annotations

import json
import logging

import pytest

from app import progression_state as ps


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ps.ENGINE_FLAG_ENV, raising=False)


@pytest.mark.parametrize(
    "valor, modo, sombra",
    [
        (None, "v1", False),  # producao: variavel ausente -> desligado
        ("", "v1", False),
        ("v1", "v1", False),
        ("shadow", "shadow", True),
        ("SHADOW", "shadow", True),
        (" shadow ", "shadow", True),
        ("v2", "v2", True),
        ("lixo", "v1", False),  # valor desconhecido nunca liga nada
        ("true", "v1", False),
    ],
)
def test_flag_progression_engine(monkeypatch, valor, modo, sombra):
    if valor is not None:
        monkeypatch.setenv(ps.ENGINE_FLAG_ENV, valor)
    assert ps.engine_mode() == modo
    assert ps.shadow_enabled() is sombra


def test_default_de_producao_e_v1():
    """Sem a variavel, `on_session_finish` nem chega a tocar no banco."""
    chamou = []
    assert ps.on_session_finish(object(), 1, 1) is None
    assert chamou == []


def test_modo_sombra_nunca_derruba_o_finish(monkeypatch, caplog):
    """Falha do motor sombra e capturada, logada e ignorada."""
    monkeypatch.setenv(ps.ENGINE_FLAG_ENV, "shadow")

    def explode(*args, **kwargs):
        raise RuntimeError("banco caiu no meio da avaliacao")

    monkeypatch.setattr(ps, "run_session_evaluation", explode)
    with caplog.at_level(logging.WARNING, logger="fitness.progression"):
        assert ps.on_session_finish(object(), 7, 42) is None
    evento = json.loads(caplog.records[-1].message)
    assert evento["event"] == "progression.shadow_failed"
    assert evento["user_id"] == 7 and evento["activity_id"] == 42
    assert evento["error_type"] == "RuntimeError"
    assert evento["mode"] == "shadow"
    # sem PII: nada de mensagem de erro livre, nome, token ou dor
    assert set(evento) == {"event", "user_id", "activity_id", "engine_version", "mode", "error_type"}


def test_sucesso_loga_evento_estruturado(monkeypatch, caplog):
    monkeypatch.setenv(ps.ENGINE_FLAG_ENV, "shadow")
    monkeypatch.setattr(ps, "run_session_evaluation", lambda *a, **k: {"groups": 1, "tracks": 2, "decisions": 1})
    with caplog.at_level(logging.INFO, logger="fitness.progression"):
        summary = ps.on_session_finish(object(), 7, 42)
    assert summary == {"groups": 1, "tracks": 2, "decisions": 1}
    evento = json.loads(caplog.records[-1].message)
    assert evento["event"] == "progression.shadow_evaluated"
    assert evento["engine_version"] == "progression-v2.0.0"


def test_o_finish_chama_o_hook_do_modo_sombra():
    """O hook existe em `workout_sessions.finish_session` (plano, linha do PR 10)."""
    import inspect

    from app import workout_sessions

    source = inspect.getsource(workout_sessions.finish_session)
    assert "on_session_finish(conn, identity.user_id, session_id)" in source
    # e o retorno do finish NAO menciona progressao: modo sombra nao muda a
    # resposta de nenhuma API existente
    retorno = source.split("return {")[-1]
    assert "progression" not in retorno.lower()


def test_dev_e_producao_ligam_o_motor_v2():
    """Em DEV a flag e `v2` desde o PR-12; em producao passou a ser `v2` no
    PR-14, depois do replay. O sentido desta verificacao inverteu (antes ela
    garantia que a flag NAO estava em `infra/`), mas o alvo e o mesmo:
    paridade DEV x producao, sem configuracao pela metade. O mapa Lambda x
    flag esta em `tests/unit/test_pr12_modo_b_flags_e_autoridade.py`."""
    from pathlib import Path

    from tests.support import infra_flags

    root = Path(__file__).resolve().parents[3]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "PROGRESSION_ENGINE: v2" in compose
    if infra_flags.infra_dir().is_dir():
        assert infra_flags.flag_values().get("PROGRESSION_ENGINE") == ps.ENGINE_V2
