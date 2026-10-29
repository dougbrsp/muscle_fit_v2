"""SEG-09 (vinculo workout_plan_exercise_id so com item de ficha do proprio
usuario) e SEG-20 itens 1 e 3 (sem senha temporaria nem evento inteiro em log).

Rodar: cd api && python -m pytest tests/unit/test_seg09_seg20_vinculo_e_logs.py
"""

from __future__ import annotations

import logging

import pytest

from app import auth, lambda_workout_ai, services
from app.domain import effective_intensity
from tests.support.fake_db import LoopingExecuteMany, fake_transaction

# ---------------------------------------------------------------------------
# SEG-09
# ---------------------------------------------------------------------------

MEU_ITEM = 31  # pertence a uma ficha do usuario 7
ITEM_ALHEIO = 99  # ficha de outro usuario
VINCULO_ATUAL = 30


class _Cursor(LoopingExecuteMany):
    def __init__(self):
        self.update_exercises_params = None
        self.ownership_queries: list[tuple] = []
        self._result: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT e.id, e.activity_id"):
            self._result = [{"id": 10, "activity_id": 20, "original_name": "Supino", "mapping_id": 5,
                             "workout_plan_exercise_id": VINCULO_ATUAL, "equipment_id": None,
                             "muscle_pain": None, "joint_pain": None}]
        elif flat.startswith("SELECT wpe.id FROM workout_plan_exercises wpe"):
            assert "wp.user_id=%s" in flat
            self.ownership_queries.append(params)
            self._result = [{"id": params[0]}] if params == (MEU_ITEM, 7) else []
        elif flat.startswith("UPDATE exercises"):
            self.update_exercises_params = params
            self._result = []
        elif flat.startswith("SELECT set_number,"):
            self._result = []
        else:
            self._result = [{"avg_rpe": None}]

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


def _run(monkeypatch, payload_extra):
    cur = _Cursor()

    monkeypatch.setattr(services, "transaction", fake_transaction(cur))
    monkeypatch.setattr(
        effective_intensity, "resolve_for_logged_exercise", lambda conn, eid: effective_intensity.UNKNOWN_INTENSITY
    )
    payload = {"local_date": "2026-09-30", "original_name": "Supino", "sets": [], **payload_extra}
    result = services.update_exercise_log(10, payload, user_id=7)
    return result, cur


def test_vincular_item_da_propria_ficha_200(monkeypatch):
    _, cur = _run(monkeypatch, {"workout_plan_exercise_id_provided": True, "workout_plan_exercise_id": MEU_ITEM})
    assert cur.ownership_queries == [(MEU_ITEM, 7)]
    assert MEU_ITEM in cur.update_exercises_params


def test_null_desvincula_sem_consultar_dono(monkeypatch):
    _, cur = _run(monkeypatch, {"workout_plan_exercise_id_provided": True, "workout_plan_exercise_id": None})
    assert cur.ownership_queries == []
    assert cur.update_exercises_params[2] is None


def test_item_alheio_levanta_nao_encontrado_e_nao_grava(monkeypatch):
    with pytest.raises(services.WorkoutPlanExerciseNotFoundError):
        _run(monkeypatch, {"workout_plan_exercise_id_provided": True, "workout_plan_exercise_id": ITEM_ALHEIO})


def test_reenviar_o_vinculo_ja_gravado_nao_exige_nova_checagem(monkeypatch):
    _, cur = _run(monkeypatch, {"workout_plan_exercise_id_provided": True, "workout_plan_exercise_id": VINCULO_ATUAL})
    assert cur.ownership_queries == []
    assert VINCULO_ATUAL in cur.update_exercises_params


def test_sem_o_campo_preserva_o_vinculo_atual(monkeypatch):
    _, cur = _run(monkeypatch, {})
    assert cur.ownership_queries == []
    assert VINCULO_ATUAL in cur.update_exercises_params


def test_rota_traduz_o_erro_para_404(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    from app.security import Identity, get_current_identity

    def boom(*a, **k):
        raise services.WorkoutPlanExerciseNotFoundError(ITEM_ALHEIO)

    monkeypatch.setattr(main, "update_exercise_log", boom)
    main.app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=7, role="regular", source="session")
    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        resp = client.put(
            "/exercises/logs/10",
            json={"local_date": "2026-09-30", "original_name": "Supino", "sets": [], "workout_plan_exercise_id": ITEM_ALHEIO},
        )
    finally:
        main.app.dependency_overrides.pop(get_current_identity, None)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Item de ficha não encontrado."


# ---------------------------------------------------------------------------
# SEG-20 item 1: seed do admin
# ---------------------------------------------------------------------------


class _SeedCursor:
    def __init__(self):
        self.inserts = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if "INSERT INTO users" in sql:
            self.inserts.append(params)

    def fetchone(self):
        return None  # 'doug' ainda nao existe


def test_seed_do_admin_nao_loga_nem_imprime_a_senha(monkeypatch, caplog, capsys):
    cur = _SeedCursor()

    senha = "SenhaTemporariaSecreta123"
    monkeypatch.setattr(auth, "transaction", fake_transaction(cur))
    monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda n: senha)
    monkeypatch.setattr(auth, "hash_password", lambda p: "hash-" + p)

    with caplog.at_level(logging.DEBUG):
        auth.ensure_admin_seed()

    assert cur.inserts and cur.inserts[0][1] == "hash-" + senha  # o admin continua criado
    saida = capsys.readouterr()
    assert senha not in saida.out + saida.err
    assert senha not in caplog.text
    assert "redefinicao" in caplog.text


# ---------------------------------------------------------------------------
# SEG-20 item 3: evento sem job_id
# ---------------------------------------------------------------------------


def test_evento_sem_job_id_loga_so_as_chaves(caplog):
    evento = {"job_type": "insights", "payload": {"notes": "DADO-SENSIVEL-DO-USUARIO"}, "user_id": 3}
    with caplog.at_level(logging.ERROR):
        lambda_workout_ai.handler(evento, None)

    assert "DADO-SENSIVEL-DO-USUARIO" not in caplog.text
    assert "['job_type', 'payload', 'user_id']" in caplog.text
    assert "insights" in caplog.text
