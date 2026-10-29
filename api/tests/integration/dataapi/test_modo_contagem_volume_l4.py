"""Modo de contagem do volume - Lote 4, contra MySQL real nos DOIS backends
(PyMySQL e shim da RDS Data API).

O que so o banco prova:
- o modo de cada sessao sai da versao do Modelo que governa a ficha dela
  (`template_versions.plan_version_expr`: a versao gravada na ficha - 066 - ou
  a vigente no fim do `plan_date`); sessao sem ficha = `primary_only`; tudo em
  UM statement;
- a "Ativacao 7 dias" (`GET /analytics/methodology-realized`) usa UM modo para a
  janela inteira, o da sessao mais recente ate hoje, e o devolve no payload.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import analytics
from app.db import transaction
from app.intelligence.methodology_volume import VolumeCountingMode
from app.intelligence.methodology_volume_service import load_counting_mode_at, load_session_counting_modes
from app.security import Identity, get_current_identity
from tests.support.isolamento_v3 import congelar_relogio, truncar
from tests.support.relogio_usuario import hoje_do_usuario

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

HOJE = hoje_do_usuario()
PUXADA = 2201
PO, PS = VolumeCountingMode.PRIMARY_ONLY, VolumeCountingMode.PRIMARY_SECONDARY


def _dia(offset: int) -> _dt.date:
    return HOJE - _dt.timedelta(days=offset)


@pytest.fixture
def seed(raw):
    truncar(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('dono_l4', 'x', 'admin')")
        ids["dono"] = cur.lastrowid
        for grupo in ("Costas", "Biceps"):
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (grupo,))
            ids[grupo] = cur.lastrowid
        cur.execute(
            "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, 'Puxada', 'Puxada')",
            (PUXADA,),
        )
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, 'Puxada')", (PUXADA,))
        cur.executemany(
            "INSERT INTO exercise_muscle_contributions "
            "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) VALUES (%s,%s,%s,%s,%s)",
            [(PUXADA, ids["Costas"], "PRIMARY", 1.0, 1.0), (PUXADA, ids["Biceps"], "SECONDARY", 0.5, 0.4)],
        )
        # Modelo: v1 ponderada ate anteontem 12h (UTC do banco), v2 primary_only desde entao.
        cur.execute("INSERT INTO workout_plan_templates (name, created_by_user_id) VALUES ('L4', %s)", (ids["dono"],))
        ids["template"] = cur.lastrowid
        troca = _dt.datetime.combine(_dia(2), _dt.time(12, 0))
        cur.execute(
            "INSERT INTO workout_plan_template_versions (template_id, version_number, volume_counting_mode, superseded_at) "
            "VALUES (%s, 1, 'primary_secondary', %s)",
            (ids["template"], troca),
        )
        ids["v1"] = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_versions (template_id, version_number, volume_counting_mode, effective_from) "
            "VALUES (%s, 2, 'primary_only', %s)",
            (ids["template"], troca),
        )
    raw.commit()
    return ids


def _sessao(raw, ids, nome: str, offset: int, *, plano: bool = True, versao: int | None = None) -> int:
    with raw.cursor() as cur:
        plan_id = None
        if plano:
            cur.execute(
                "INSERT INTO workout_plans (user_id, plan_date, name, plan_type, status, template_id, "
                "template_slot_sequence, template_version_id) VALUES (%s, %s, %s, 'full_body', 'planned', %s, 1, %s)",
                (ids["dono"], _dia(offset), nome, ids["template"], versao),
            )
            plan_id = cur.lastrowid
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, activity_type, session_status, workout_plan_id) "
            "VALUES (%s, %s, %s, %s, 'Musculação', 'done', %s)",
            (ids["dono"], _dia(offset), _dt.datetime.combine(_dia(offset), _dt.time(19, 0)), nome, plan_id),
        )
        activity_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) VALUES (%s, 1, 'Puxada', %s, 4)",
            (activity_id, PUXADA),
        )
    raw.commit()
    return activity_id


def _realizado(user_id: int) -> dict:
    app = FastAPI()
    app.include_router(analytics.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    resposta = TestClient(app, raise_server_exceptions=False).get("/analytics/methodology-realized")
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


def _biceps_por_dia(corpo: dict) -> dict[str, float]:
    (linha,) = [item for item in corpo["items"] if item["muscle_group"] == "Biceps"]
    return {dia["date"]: dia["direct_sets"] for dia in linha["days"] if dia["effective_sets"]}


def test_modo_por_sessao_pela_versao_que_governa_a_ficha(seed, db_backend, raw):
    ponderada = _sessao(raw, seed, "antes da troca", 3)
    depois = _sessao(raw, seed, "depois da troca", 1)
    gravada = _sessao(raw, seed, "ficha gravada na v1", 1, versao=seed["v1"])
    livre = _sessao(raw, seed, "treino livre", 1, plano=False)
    with transaction() as conn:
        with conn.cursor() as cur:
            modos = load_session_counting_modes(cur, [ponderada, depois, gravada, livre])
            assert load_counting_mode_at(cur, seed["dono"], _dia(3)) is PS
            assert load_counting_mode_at(cur, seed["dono"], _dia(5)) is PO  # nenhuma sessao ate ali
    assert modos == {ponderada: PS, depois: PO, gravada: PS, livre: PO}, db_backend


def test_ativacao_7_dias_usa_um_modo_para_a_janela_inteira(seed, db_backend, raw, monkeypatch):
    congelar_relogio(monkeypatch)
    _sessao(raw, seed, "antes da troca", 3)
    _sessao(raw, seed, "depois da troca", 1)

    # Mais recente em primary_only: o biceps da sessao ponderada nao conta.
    corpo = _realizado(seed["dono"])
    assert corpo["counting_mode"] == "primary_only" and corpo["metric_version"] == "realized-v2"
    assert set(_biceps_por_dia(corpo).values()) == {0.0}, db_backend

    # Sessao de hoje numa ficha da v1 (ponderada): a janela inteira vira ponderada.
    _sessao(raw, seed, "hoje na v1", 0, versao=seed["v1"])
    corpo = _realizado(seed["dono"])
    assert corpo["counting_mode"] == "primary_secondary"
    assert _biceps_por_dia(corpo) == {
        _dia(3).isoformat(): 2.0,
        _dia(1).isoformat(): 2.0,
        _dia(0).isoformat(): 2.0,
    }, db_backend
