"""Leitores por data (PR3 do versionamento de Modelo) contra MySQL real, nos
DOIS backends (PyMySQL de dev e shim da RDS Data API).

O teste principal: editar o Modelo (v1 -> v2) NAO muda a leitura de um
periodo ANTERIOR a edicao
ligada - os payloads de `/engine/plans/{id}`, `/workouts` e `/adherence`
continuam iguais byte a byte, e a meta do Modelo em `/muscles` e a mesma.
Com a flag desligada, os mesmos leitores passam a mostrar a VIGENTE (v2),
como antes do PR3.

Cenario: Modelo `equipment_series` com a v1 "desde sempre" (o estado em que
a 065 deixou os Modelos que ja existiam), atribuido ha 30 dias; uma ficha
ANTIGA (sem `template_version_id`) de 5 dias atras e a sessao dela. A edicao
grava a v2 AGORA (supino 3 -> 5 series, slot "Push A" -> "Push B").

Sem relogio de Python: `congelar_relogio` fixa o "agora" do usuario entre as
chamadas; a vigencia das versoes vem do `NOW()` do banco.
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import analytics_dashboard
from app.intelligence.dashboard import engine_service  # noqa: F401 - precisa estar importado
from app.security import Identity, get_current_identity
from tests.support.isolamento_v3 import congelar_relogio, truncar
from tests.support.relogio_usuario import hoje_do_usuario
from tests.support.template_versions import versao_v1

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

HOJE = hoje_do_usuario()
DIA_DA_FICHA = HOJE - _dt.timedelta(days=5)
SUPINO, CRUCIFIXO = 4101, 4102


@pytest.fixture
def cenario(raw, monkeypatch):
    truncar(raw)
    congelar_relogio(monkeypatch)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('dono_pr3', 'x', 'admin')")
        dono = cur.lastrowid
        cur.execute("INSERT INTO muscle_groups (name) VALUES ('Peitoral')")
        peitoral = cur.lastrowid
        for exercise_id, nome in ((SUPINO, "Supino reto"), (CRUCIFIXO, "Crucifixo")):
            cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (%s, %s, 1)", (exercise_id, nome))
            cur.execute(
                "INSERT INTO exercise_muscle_contributions "
                "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight, is_active) "
                "VALUES (%s, %s, 'PRIMARY', '1.00', '1.00', 1)",
                (exercise_id, peitoral),
            )
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, is_active, created_by_user_id) "
            "VALUES ('Modelo PR3', 'equipment_series', 1, %s)",
            (dono,),
        )
        template_id = cur.lastrowid
        for sequence, nome, exercicio in ((1, "Push A", SUPINO), (2, "Pull A", CRUCIFIXO)):
            cur.execute(
                "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
                "VALUES (%s, %s, %s, %s, 'normal')",
                (versao_v1(cur, template_id), template_id, sequence, nome),
            )
            slot_id = cur.lastrowid
            cur.execute(
                "INSERT INTO workout_plan_template_slot_items "
                "(slot_id, item_order, item_kind, muscle_group_id, mapping_id, sets_count, reps_target) "
                "VALUES (%s, 1, 'equipment', %s, %s, 3, '8-10')",
                (slot_id, peitoral, exercicio),
            )
        cur.execute(
            "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id, assigned_at) "
            "VALUES (%s, %s, %s, %s)",
            (template_id, dono, dono, _dt.datetime.combine(HOJE - _dt.timedelta(days=30), _dt.time(8, 0))),
        )
        # Ficha ANTIGA: sem `template_version_id` (resolvida pela data).
        cur.execute(
            "INSERT INTO workout_plans (user_id, plan_date, name, plan_type, status, template_id, "
            "template_slot_sequence) VALUES (%s, %s, 'Ficha antiga', 'full_body', 'planned', %s, 1)",
            (dono, DIA_DA_FICHA, template_id),
        )
        plan_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_exercises (plan_id, sequence, exercise_name, mapping_id, sets_count, "
            "reps_target, load_target, rpe_target, rest_seconds) VALUES (%s, 1, 'Supino reto', %s, 3, '8-10', "
            "'60', '8', 90)",
            (plan_id, SUPINO),
        )
        plan_exercise_id = cur.lastrowid
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, session_status, workout_plan_id, "
            "duration_seconds) VALUES (%s, %s, %s, 'Treino', 'done', %s, 3600)",
            (dono, DIA_DA_FICHA, _dt.datetime.combine(DIA_DA_FICHA, _dt.time(19, 0)), plan_id),
        )
        activity_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count, "
            "workout_plan_exercise_id) VALUES (%s, 1, 'Supino reto', %s, 3, %s)",
            (activity_id, SUPINO, plan_exercise_id),
        )
        exercise_row = cur.lastrowid
        for numero in (1, 2, 3):
            cur.execute(
                "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
                "VALUES (%s, %s, 'working', 10, '60.00', '8.00')",
                (exercise_row, numero),
            )
    raw.commit()
    return {"dono": dono, "template_id": template_id, "plan_id": plan_id, "peitoral": peitoral}


def _editar_modelo(cenario) -> None:
    """Grava a v2 AGORA pelo escritor de producao."""
    from app.db import transaction
    from app.template_versions import write_structural_version

    def item(mapping_id, series):
        return {"item_order": 1, "item_kind": "equipment", "muscle_group_id": cenario["peitoral"],
                "mapping_id": mapping_id, "sets_count": series, "reps_target": "8-10", "prioritize": False}

    payload = {
        "training_type_id": None, "volume_mode": "equipment_series", "template_type": "full_body",
        "grid_rows": None, "grid_cols": None,
        "slots": [
            {"sequence": 1, "name": "Push B", "slot_type": "normal", "items": [item(SUPINO, 5)]},
            {"sequence": 2, "name": "Pull A", "slot_type": "normal", "items": [item(CRUCIFIXO, 3)]},
            {"sequence": 3, "name": "Legs", "slot_type": "normal", "items": [item(SUPINO, 2)]},
        ],
    }
    with transaction() as conn:
        with conn.cursor() as cur:
            assert write_structural_version(cur, cenario["template_id"], payload, None, actor=cenario["dono"])


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(analytics_dashboard.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


JANELA = {
    "window": "custom",
    "date_from": (DIA_DA_FICHA - _dt.timedelta(days=6)).isoformat(),
    "date_to": DIA_DA_FICHA.isoformat(),
}


def _leituras(cenario) -> dict:
    api = _client(cenario["dono"])
    respostas = {
        "engine": api.get(f"/analytics/dashboard/engine/plans/{cenario['plan_id']}", params=JANELA),
        "workouts": api.get("/analytics/dashboard/workouts", params=JANELA),
        "adherence": api.get("/analytics/dashboard/adherence", params=JANELA),
        "muscles": api.get("/analytics/dashboard/muscles", params={**JANELA, "include_readiness": "false"}),
    }
    for nome, resposta in respostas.items():
        assert resposta.status_code == 200, (nome, resposta.text)
    corpo = {nome: resposta.json() for nome, resposta in respostas.items()}
    # `/muscles` traz o PROJETADO, que olha para a frente (fica na vigente de
    # proposito): compara-se a META do Modelo, nao o payload inteiro.
    musculos = corpo.pop("muscles")
    corpo["muscles_target"] = {
        "target_source": musculos.get("target_source"),
        "targets": sorted(
            json.dumps({"group": g.get("muscle_group_id"), "targets": g.get("targets")}, sort_keys=True, default=str)
            for g in musculos.get("items") or musculos.get("groups") or []
        ),
    }
    return corpo


def _planejado(corpo) -> list:
    return [item["planned"].get("sets_count") for item in corpo["engine"]["items"]]


def _nome_do_slot(corpo) -> list:
    return [
        (s.get("plan_link") or {}).get("template_slot_name")
        for s in corpo["workouts"]["items"]
    ]


def test_periodo_anterior_a_edicao_fica_igual(cenario, db_backend):
    antes = _leituras(cenario)
    assert _planejado(antes) == [3], db_backend
    assert _nome_do_slot(antes) == ["Push A"]
    assert antes["muscles_target"]["target_source"] == "template_plan"

    _editar_modelo(cenario)
    depois = _leituras(cenario)

    assert depois == antes, db_backend  # byte a byte (JSON)


def test_janela_que_termina_hoje_le_a_vigente_e_a_anterior_le_a_v1(cenario, db_backend):
    _editar_modelo(cenario)
    pela_v1 = _leituras(cenario)
    assert len(pela_v1["adherence"]["slots"]) == 2, db_backend
    api = _client(cenario["dono"])
    hoje = {"window": "custom", "date_from": JANELA["date_from"], "date_to": HOJE.isoformat()}
    corpo_hoje = api.get("/analytics/dashboard/adherence", params=hoje).json()
    assert len(corpo_hoje["slots"]) == 3, db_backend  # v2


def test_edicao_entre_21h_e_24h_de_brasilia_ja_vale_para_o_dia_do_usuario(cenario, db_backend, monkeypatch):
    """M76 Lote 2b - a causa do flake deste arquivo, reproduzida a qualquer hora.

    A vigencia da versao nasce de `NOW()` do banco (UTC). Uma edicao as 22h30
    de Brasilia e 01h30 UTC do dia SEGUINTE; o "hoje" do usuario continua
    sendo `HOJE`. Antes, o fim do dia `HOJE` era comparado como 23:59:59 com o
    instante UTC, e a janela que termina hoje lia a v1 - o teste acima falhava
    entre 00:00Z e 03:00Z. Agora o fim do dia do usuario vai para o relogio do
    banco (`time_windows.user_day_end_db`): a edicao das 22h30 e de `HOJE`."""
    from app import template_versions
    from app.intelligence.dashboard.time_windows import user_day_start_db
    from tests.support.relogio_usuario import TZ_USUARIO

    as_22h30_de_hoje = user_day_start_db(HOJE + _dt.timedelta(days=1), TZ_USUARIO) - _dt.timedelta(minutes=90)
    assert as_22h30_de_hoje.date() == HOJE + _dt.timedelta(days=1)  # no relogio do banco ja e "amanha"
    monkeypatch.setattr(template_versions, "db_now", lambda cur: as_22h30_de_hoje)
    _editar_modelo(cenario)

    api = _client(cenario["dono"])
    hoje = {"window": "custom", "date_from": JANELA["date_from"], "date_to": HOJE.isoformat()}
    corpo_hoje = api.get("/analytics/dashboard/adherence", params=hoje).json()
    assert len(corpo_hoje["slots"]) == 3, db_backend  # v2
    # E o periodo ANTERIOR a edicao continua lendo a v1.
    assert len(_leituras(cenario)["adherence"]["slots"]) == 2, db_backend


def test_planejado_da_ficha_antiga_no_engine_e_a_v1_depois_da_edicao(cenario, db_backend):
    _editar_modelo(cenario)
    corpo = _client(cenario["dono"]).get(
        f"/analytics/dashboard/engine/plans/{cenario['plan_id']}", params={"window": "28d"}
    )
    assert corpo.status_code == 200, corpo.text
    itens = corpo.json()["items"]
    assert [i["planned"]["sets_count"] for i in itens] == [3], db_backend
    assert itens[0]["planned"]["source"] == "template_item"
