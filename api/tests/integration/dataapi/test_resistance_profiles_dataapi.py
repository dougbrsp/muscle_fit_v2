"""Perfis de resistencia (L41, migracao 126) contra MySQL 8 real, nos DOIS backends (PyMySQL e shim da RDS Data API).

Prova: vocabulario -> PUT revisado (com assinatura) -> GET do par -> historico com 1 entrada -> cobertura conta o par;
codigo fora do vocabulario -> 422."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import resistance_profiles
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend

USUARIO = "admin_perfis_resistencia"
EXERCICIO = 9_288_001
EQUIPAMENTO = "Maquina perfis L41"


def _limpar(cur) -> None:
    cur.execute("SELECT id FROM equipment WHERE name = %s", (EQUIPAMENTO,))
    for eq in cur.fetchall():
        par = "SELECT id FROM equipment_exercises WHERE equipment_id = %s"
        cur.execute(
            "DELETE FROM equipment_exercise_resistance_profile_history WHERE profile_id IN "
            f"(SELECT id FROM equipment_exercise_resistance_profiles WHERE equipment_exercise_id IN ({par}))",
            (eq["id"],),
        )
        cur.execute(
            f"DELETE FROM equipment_exercise_resistance_profiles WHERE equipment_exercise_id IN ({par})", (eq["id"],)
        )
        cur.execute("DELETE FROM equipment_exercises WHERE equipment_id = %s", (eq["id"],))
        cur.execute("DELETE FROM equipment WHERE id = %s", (eq["id"],))
    cur.execute("DELETE FROM exercise_catalog WHERE id = %s", (EXERCICIO,))
    cur.execute("DELETE FROM exercise_zepp_mappings WHERE id = %s", (EXERCICIO,))
    # fk_wpt_created_by e RESTRICT: so os Modelos que apontam para este usuario saem antes dele.
    cur.execute(
        "DELETE FROM workout_plan_templates WHERE created_by_user_id IN (SELECT id FROM users WHERE username = %s)",
        (USUARIO,),
    )
    cur.execute("DELETE FROM users WHERE username = %s", (USUARIO,))


@pytest.fixture
def cenario(raw, conceder_grupo):
    resistance_profiles.reset_tables_cache()
    with raw.cursor() as cur:
        _limpar(cur)
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'admin')", (USUARIO,))
        uid = cur.lastrowid
        conceder_grupo(cur, uid, "admin")
        cur.execute(
            "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, 'Ex perfis', 'Ex perfis')",
            (EXERCICIO,),
        )
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, 'Ex perfis')", (EXERCICIO,))
        cur.execute("INSERT INTO equipment (name) VALUES (%s)", (EQUIPAMENTO,))
        eq = cur.lastrowid
        cur.execute(
            "INSERT INTO equipment_exercises (equipment_id, exercise_id, is_active) VALUES (%s, %s, 1)", (eq, EXERCICIO)
        )
        par = cur.lastrowid
    raw.commit()
    yield uid, par
    with raw.cursor() as cur:
        _limpar(cur)
    raw.commit()


def test_fluxo_do_perfil_identico_nos_dois_backends(cenario, raw, monkeypatch, fake_rds, mysql_schema):
    uid, par = cenario
    app = FastAPI()
    app.include_router(resistance_profiles.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=uid, role="admin", source="session")
    c = TestClient(app, raise_server_exceptions=False)
    corpo = {"source": "cable", "mechanism": "simple_pulley", "trajectory": "cable_free_path",
             "profile": "approximately_constant", "line": "cable_angle_variable", "direction_changes": "no",
             "flags": ["friction"], "confidence": "high", "status": "reviewed", "reason": "integracao"}

    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        with raw.cursor() as cur:  # recomeca cada backend sem perfil
            cur.execute("DELETE FROM equipment_exercise_resistance_profiles WHERE equipment_exercise_id = %s", (par,))
        raw.commit()
        resistance_profiles.reset_tables_cache()

        assert c.get("/resistance-profiles/vocabulary").status_code == 200, backend
        antes = c.get("/resistance-profiles/coverage").json()

        salvo = c.put(f"/resistance-profiles/{par}", json=corpo)
        assert salvo.status_code == 200, (backend, salvo.text)
        assert salvo.json()["signature"] == "cable:simple_pulley:cable_free_path:approximately_constant:cable_angle_variable"
        assert salvo.json()["status"] == "reviewed" and salvo.json()["reliable"] is True, backend

        lido = c.get(f"/resistance-profiles/{par}")
        assert lido.status_code == 200 and lido.json() == salvo.json(), backend
        historico = c.get(f"/resistance-profiles/{par}/history").json()["items"]
        assert len(historico) == 1 and historico[0]["before"] is None and historico[0]["reason"] == "integracao", backend

        depois = c.get("/resistance-profiles/coverage").json()
        assert depois["with_profile"] == antes["with_profile"] + 1, backend
        assert depois["active_pairs"] == antes["active_pairs"], backend
        assert depois["by_status"]["reviewed"] == antes["by_status"]["reviewed"] + 1, backend

        invalido = c.put(f"/resistance-profiles/{par}", json={**corpo, "source": "nao_existe"})
        assert invalido.status_code == 422, (backend, invalido.text)
