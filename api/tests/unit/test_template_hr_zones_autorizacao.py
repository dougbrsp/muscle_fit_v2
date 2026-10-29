"""Zonas de FC do editor do Modelo por papel (M98, correção da C1).

`GET /workout-plan-templates/hr-zones?template_id=N` decide no backend DE QUEM
são as zonas (`athlete_hr_zones.resolve_template_zones_subject`):

- admin: as do usuário logado (mesmo num Modelo atribuído a um aluno);
- aluno: as próprias, nunca as de terceiros;
- personal trainer: as do aluno vinculado (atribuição vigente) ao Modelo; sem
  aluno, com mais de um ou sem Modelo, nenhuma (nunca as do próprio PT).

Sem banco: cursor roteirizado por SQL.
Rodar: cd api && python -m pytest tests/unit/test_template_hr_zones_autorizacao.py
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import athlete_hr_zones, workout_plan_templates
from app.security import Identity, get_current_identity
from tests.support.fake_db import fake_transaction

# FCmáx manual por usuário: 1 = admin, 2 = PT, 3 = aluno A, 4 = aluno B.
_USERS = {
    1: {"id": 1, "full_name": "Admin", "max_hr_bpm_manual": 180},
    2: {"id": 2, "full_name": "PT", "max_hr_bpm_manual": 200},
    3: {"id": 3, "full_name": "Ana", "username": "ana", "max_hr_bpm_manual": 190},
    4: {"id": 4, "full_name": "Bia", "username": "bia", "max_hr_bpm_manual": 170},
}
# Modelo -> alunos com atribuição vigente.
_ASSIGNEES = {10: [3], 11: [], 12: [3, 4]}
# Zonas do Intervals gravadas pelo sync (C2): só a aluna Bia (4) tem.
_INTERVALS = {4: [{"sport_settings_id": 1, "sport_types": '["Run"]', "hr_zones_bpm": "[129,137,145,153,157,162,170]", "max_hr_bpm": 170, "lthr_bpm": 154}]}


class _Cursor:
    def __init__(self):
        self.executed: list[str] = []
        self._one = None
        self._all: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.executed.append(flat)
        if flat.startswith("SELECT * FROM users"):
            self._one = dict(_USERS[params[0]]) if params[0] in _USERS else None
        elif "FROM workout_plan_template_assignments" in flat:
            self._all = [
                {"user_id": uid, "full_name": _USERS[uid]["full_name"], "username": _USERS[uid].get("username")}
                for uid in _ASSIGNEES.get(params[0], [])
            ]
        elif "FROM intervals_athlete_hr_zones" in flat:
            self._all = [dict(row) for row in _INTERVALS.get(params[0], [])]
        else:  # pragma: no cover - SQL inesperado quebra o teste
            raise AssertionError(flat)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


def _client(monkeypatch, user_id: int, role: str, source: str = "session"):
    cur = _Cursor()
    monkeypatch.setattr(workout_plan_templates, "transaction", fake_transaction(cur))
    app = FastAPI()
    app.include_router(workout_plan_templates.me_router)
    identity = Identity(user_id=user_id, role=role, source=source)
    app.dependency_overrides[get_current_identity] = lambda: identity
    return TestClient(app), cur


def test_pt_ve_as_zonas_do_aluno_vinculado(monkeypatch):
    client, _ = _client(monkeypatch, 2, "personal_trainer")
    resp = client.get("/workout-plan-templates/hr-zones", params={"template_id": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"] == {"kind": "assignee", "user_id": 3, "name": "Ana", "reason": None}
    assert body["max_hr_bpm"] == 190  # do aluno, não os 200 do PT
    assert len(body["zones"]) == 5


@pytest.mark.parametrize(
    "params, reason",
    [({"template_id": 11}, "no_assignee"), ({"template_id": 12}, "multiple_assignees"), ({}, "no_template")],
)
def test_pt_sem_aluno_vinculado_unico_nao_ve_zonas(monkeypatch, params, reason):
    client, cur = _client(monkeypatch, 2, "personal_trainer")
    resp = client.get("/workout-plan-templates/hr-zones", params=params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"]["kind"] == "none" and body["subject"]["reason"] == reason
    assert body["zones"] == [] and body["source"] == "none"
    # Nem as do próprio PT nem as de qualquer aluno foram lidas.
    assert not any(sql.startswith("SELECT * FROM users") for sql in cur.executed)


def test_admin_ve_as_proprias_mesmo_no_modelo_do_aluno(monkeypatch):
    client, cur = _client(monkeypatch, 1, "admin")
    resp = client.get("/workout-plan-templates/hr-zones", params={"template_id": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"]["kind"] == "self" and body["subject"]["user_id"] == 1
    assert body["max_hr_bpm"] == 180
    assert not any("workout_plan_template_assignments" in sql for sql in cur.executed)


def test_aluno_nao_ve_zonas_de_terceiros(monkeypatch):
    # Aluno B pede o Modelo atribuído ao aluno A: recebe as PRÓPRIAS.
    client, _ = _client(monkeypatch, 4, "regular")
    resp = client.get("/workout-plan-templates/hr-zones", params={"template_id": 10})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject"]["user_id"] == 4
    assert body["max_hr_bpm"] == 170


def test_exige_sessao(monkeypatch):
    client, _ = _client(monkeypatch, 3, "regular", source="device")
    assert client.get("/workout-plan-templates/hr-zones").status_code == 403


def test_resolucao_do_sujeito_e_a_unica_regra(monkeypatch):
    """O papel decide; o template_id de um aluno é ignorado para admin e aluno."""
    cur = _Cursor()
    admin = Identity(user_id=1, role="admin", source="session")
    assert athlete_hr_zones.resolve_template_zones_subject(cur, admin, 10).user_id == 1
    pt = Identity(user_id=2, role="personal_trainer", source="session")
    assert athlete_hr_zones.resolve_template_zones_subject(cur, pt, 10).user_id == 3
