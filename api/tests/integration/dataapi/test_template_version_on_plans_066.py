"""Versao do Modelo na ficha e no congelamento (migracao 066, PR2) contra
MySQL real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API).

O que so o banco prova:

- rascunho gerado na v1 -> Modelo editado (v2) -> ficha salva com a versao do
  rascunho: `workout_plans.template_version_id` = v1 e o congelamento do
  Modo B le os itens da v1 (series da v1), nao os da vigente;
- ficha ANTIGA (`template_version_id` NULL) congela pela vigente, como hoje;
- versao de OUTRO Modelo nao e gravada (NULL) e gera o evento de log;
- editar a ficha sem mandar a versao (tela antiga) mantem a gravada;
- o Modo A le `volume_mode`/`training_type_id` da versao da ficha;
- `generation_runs.template_version_id` recebe a versao do rascunho;
- 066: orfaos da janela de deploy viram uma versao NOVA e vigente (a anterior
  fecha no mesmo instante), reexecucao = 0 statements; `sequence` repetida
  entre orfaos para antes de tudo; a 065 continua reexecutavel depois da 066.

Isolamento: `truncar(raw)` (`isolamento_v3.TABELAS_V3`). O instante vem do
`NOW()` do banco; nada deriva do relogio do Python.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import services
from app import workout_plan_templates as wpt
from app.generation import freeze_mode_a, freeze_mode_b
from app.generation.generation_runs import record_generation_run
from app.security import Identity, get_current_identity, require_admin_or_pt
from app.template_versions import current_version_id_of
from tests.support.isolamento_v3 import truncar

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

MIGRATION_066 = m.DEFAULT_MIGRATIONS_DIR / "066_template_version_on_plans.py"
PEITORAL, SUPINO, BANCO = 52, 7100, 9001


@pytest.fixture
def seed(raw):
    truncar(raw)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_v066', 'x', 'admin')")
        admin_id = cur.lastrowid
        cur.execute("INSERT INTO muscle_groups (id, name) VALUES (%s, 'Peitoral')", (PEITORAL,))
        cur.execute("INSERT INTO equipment (id, name) VALUES (%s, 'Banco reto')", (BANCO,))
        cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (%s, 'Supino reto', 1)", (SUPINO,))
        cur.execute(
            "INSERT INTO training_types (name, is_active, created_by_user_id) VALUES ('Hipertrofia', 1, %s)",
            (admin_id,),
        )
        training_type_id = cur.lastrowid
    raw.commit()
    return {"admin": admin_id, "training_type": training_type_id}


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(wpt.router)
    identidade = Identity(user_id=user_id, role="admin", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    app.dependency_overrides[require_admin_or_pt] = lambda: identidade
    return TestClient(app)


def _por_equipamento(series: int) -> dict:
    return {
        "name": "Modo B",
        "volume_mode": "equipment_series",
        "slots": [
            {
                "sequence": 1,
                "name": "Push",
                "slot_type": "normal",
                "items": [
                    {
                        "item_order": 1,
                        "item_kind": "equipment",
                        "muscle_group_id": PEITORAL,
                        "equipment_id": BANCO,
                        "mapping_id": SUPINO,
                        "sets_count": series,
                    }
                ],
            }
        ],
    }


def _por_grupo(training_type_id) -> dict:
    return {
        "name": "Modo A",
        "volume_mode": "percent_budget",
        "training_type_id": training_type_id,
        "slots": [
            {
                "sequence": 1,
                "name": "Push",
                "slot_type": "normal",
                "items": [{"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": PEITORAL}],
            }
        ],
    }


def _ficha(user_id, template_id, version_id, **extra) -> dict:
    payload = {
        "name": "Ficha",
        "plan_type": "full_body",
        "status": "planned",
        "plan_date": "2026-10-06",
        "template_id": template_id,
        "template_slot_sequence": 1,
        "template_version_id": version_id,
        "exercises": [],
    }
    payload.update(extra)
    return payload


def _versao_da_ficha(raw, plan_id):
    with raw.cursor() as cur:
        cur.execute("SELECT template_version_id FROM workout_plans WHERE id = %s", (plan_id,))
        return cur.fetchone()["template_version_id"]


def _contexto_b(plan_id):
    from app.db import transaction

    with transaction() as conn:
        return freeze_mode_b._plan_context(conn, plan_id)


def _contexto_a(plan_id):
    from app.db import transaction

    with transaction() as conn:
        return freeze_mode_a._plan_context(conn, plan_id)


# ---------------------------------------------------------------------------
# Rascunho -> edicao do Modelo -> save
# ---------------------------------------------------------------------------


def test_ficha_salva_congela_a_versao_do_rascunho_mesmo_com_o_modelo_editado(seed, db_backend, raw):
    api = _client(seed["admin"])
    criado = api.post("/workout-plan-templates", json=_por_equipamento(3)).json()
    v1 = current_version_id_of(criado)
    assert v1 is not None
    # O Modelo e editado ENTRE o rascunho (v1) e o save.
    editado = api.put(f"/workout-plan-templates/{criado['id']}", json=_por_equipamento(5)).json()
    v2 = current_version_id_of(editado)
    assert v2 != v1

    plano = services.create_workout_plan(_ficha(seed["admin"], criado["id"], v1), user_id=seed["admin"])
    assert plano["template_version_id"] == v1, db_backend
    assert _versao_da_ficha(raw, plano["id"]) == v1

    contexto = _contexto_b(plano["id"])
    assert contexto["version_id"] == v1
    assert [item["sets_count"] for item in contexto["items"]] == [3], db_backend  # da v1, nao 5


def test_ficha_antiga_sem_versao_congela_pela_vigente(seed, db_backend, raw):
    api = _client(seed["admin"])
    criado = api.post("/workout-plan-templates", json=_por_equipamento(3)).json()
    api.put(f"/workout-plan-templates/{criado['id']}", json=_por_equipamento(5))
    plano = services.create_workout_plan(_ficha(seed["admin"], criado["id"], None), user_id=seed["admin"])
    assert _versao_da_ficha(raw, plano["id"]) is None
    assert [item["sets_count"] for item in _contexto_b(plano["id"])["items"]] == [5], db_backend


def test_versao_de_outro_modelo_vira_null_e_registra_evento(seed, db_backend, raw, caplog):
    api = _client(seed["admin"])
    a = api.post("/workout-plan-templates", json=_por_equipamento(3)).json()
    b = api.post("/workout-plan-templates", json=_por_equipamento(4)).json()
    with caplog.at_level(logging.WARNING):
        plano = services.create_workout_plan(
            _ficha(seed["admin"], a["id"], current_version_id_of(b)), user_id=seed["admin"]
        )
    assert _versao_da_ficha(raw, plano["id"]) is None, db_backend
    assert "workout_plan.template_version_rejected" in caplog.text
    # E o congelamento cai na vigente do proprio Modelo.
    assert [item["sets_count"] for item in _contexto_b(plano["id"])["items"]] == [3]


def test_editar_a_ficha_sem_mandar_a_versao_mantem_a_gravada(seed, db_backend, raw):
    api = _client(seed["admin"])
    criado = api.post("/workout-plan-templates", json=_por_equipamento(3)).json()
    v1 = current_version_id_of(criado)
    plano = services.create_workout_plan(_ficha(seed["admin"], criado["id"], v1), user_id=seed["admin"])
    services.update_workout_plan(
        plano["id"], _ficha(seed["admin"], criado["id"], None, name="Renomeada"), user_id=seed["admin"]
    )
    assert _versao_da_ficha(raw, plano["id"]) == v1, db_backend


def test_modo_a_le_tipo_de_treino_e_modo_da_versao_da_ficha(seed, db_backend):
    api = _client(seed["admin"])
    criado = api.post("/workout-plan-templates", json=_por_grupo(seed["training_type"])).json()
    v1 = current_version_id_of(criado)
    # v2 sem Tipo de Treinamento: o Modo A deixaria de se aplicar pela vigente.
    api.put(f"/workout-plan-templates/{criado['id']}", json=_por_grupo(None))

    da_v1 = services.create_workout_plan(_ficha(seed["admin"], criado["id"], v1), user_id=seed["admin"])
    antiga = services.create_workout_plan(_ficha(seed["admin"], criado["id"], None), user_id=seed["admin"])
    contexto = _contexto_a(da_v1["id"])
    assert contexto is not None and contexto["training_type_id"] == seed["training_type"], db_backend
    assert _contexto_a(antiga["id"]) is None


def test_generation_runs_recebe_a_versao_do_rascunho(seed, db_backend, raw):
    draft = {
        "template_id": 1,
        "template_slot_sequence": 1,
        "template_version_id": 77,
        "plan_date": "2026-10-06",
        "raw_payload": {"generation_audit": {"template_id": 1, "slot_sequence": 1, "engine_version": "t"}},
    }
    run_id = record_generation_run(draft, user_id=seed["admin"])
    assert run_id is not None, db_backend
    with raw.cursor() as cur:
        cur.execute("SELECT template_version_id FROM generation_runs WHERE id = %s", (run_id,))
        assert cur.fetchone()["template_version_id"] == 77


# ---------------------------------------------------------------------------
# Migracao 066
# ---------------------------------------------------------------------------


def _modulo():
    return m.load_python_migration(MIGRATION_066).__globals__


def _neutra(mysql_kwargs, mysql_schema):
    from app.db_pymysql import Connection as PyMySQLConnection

    kw = mysql_kwargs
    return PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], mysql_schema)


def _rodar(conn, nome):
    resultado = _modulo()[nome](m.MigrationDB(conn))
    conn.commit()
    return resultado


def _backend_conn():
    import app.db as db  # atributo trocado por use_backend

    return db.get_conn()


def test_066_no_schema_convergido_nao_tem_nada_a_fazer(seed, db_backend):
    conn = _backend_conn()
    try:
        assert _modulo()["plan"](m.MigrationDB(conn)) == [], db_backend
    finally:
        conn.close()


def test_066_orfaos_viram_versao_nova_vigente_e_a_065_segue_reexecutavel(
    seed, db_backend, raw, mysql_kwargs, mysql_schema
):
    """Simula o codigo ANTIGO na janela de deploy: DELETE dos slots do Modelo
    + INSERT sem versao. Up pelo BACKEND do teste (Data API inclusive)."""
    api = _client(seed["admin"])
    criado = api.post("/workout-plan-templates", json=_por_equipamento(3)).json()
    template_id, v1 = criado["id"], current_version_id_of(criado)

    conn = _neutra(mysql_kwargs, mysql_schema)
    try:
        _rodar(conn, "down")
    finally:
        conn.close()
    try:
        with raw.cursor() as cur:
            cur.execute("DELETE FROM workout_plan_template_slots WHERE template_id = %s", (template_id,))
            for seq, nome in ((1, "Push novo"), (2, "Pull novo")):
                cur.execute(
                    "INSERT INTO workout_plan_template_slots (template_id, sequence, name, slot_type) "
                    "VALUES (%s, %s, %s, 'normal')",
                    (template_id, seq, nome),
                )
        conn = _backend_conn()
        try:
            _rodar(conn, "up")
            assert _modulo()["plan"](m.MigrationDB(conn)) == [], db_backend
        finally:
            conn.close()
    finally:
        conn = _neutra(mysql_kwargs, mysql_schema)
        try:
            _rodar(conn, "up")
        finally:
            conn.close()

    with raw.cursor() as cur:
        cur.execute(
            "SELECT id, version_number, effective_from, superseded_at, change_summary "
            "FROM workout_plan_template_versions WHERE template_id = %s ORDER BY version_number",
            (template_id,),
        )
        antiga, nova = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS n FROM workout_plan_template_slots WHERE version_id IS NULL")
        assert cur.fetchone()["n"] == 0
    assert antiga["id"] == v1 and antiga["superseded_at"] == nova["effective_from"]
    assert nova["version_number"] == 2 and nova["superseded_at"] is None
    assert "066" in nova["change_summary"]
    nomes = [s["name"] for s in api.get(f"/workout-plan-templates/{template_id}").json()["slots"]]
    assert nomes == ["Push novo", "Pull novo"], db_backend
    # A 065 (imutavel) continua reexecutavel sobre o schema com a 066.
    conn = _backend_conn()
    try:
        modulo_065 = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "065_workout_plan_template_versions.py")
        assert modulo_065.__globals__["plan"](m.MigrationDB(conn)) == []
    finally:
        conn.close()


def test_066_orfaos_com_sequence_repetida_param_sem_executar_nada(seed, db_backend, raw, mysql_kwargs, mysql_schema):
    api = _client(seed["admin"])
    template_id = api.post("/workout-plan-templates", json=_por_equipamento(3)).json()["id"]
    conn = _neutra(mysql_kwargs, mysql_schema)
    try:
        _rodar(conn, "down")
    finally:
        conn.close()
    try:
        with raw.cursor() as cur:
            for nome in ("A", "B"):
                cur.execute(
                    "INSERT INTO workout_plan_template_slots (template_id, sequence, name, slot_type) "
                    "VALUES (%s, 7, %s, 'normal')",
                    (template_id, nome),
                )
        conn = _backend_conn()
        try:
            with pytest.raises(Exception, match=f"modelo {template_id} sequence 7"):
                _modulo()["plan"](m.MigrationDB(conn))
        finally:
            conn.close()
    finally:
        with raw.cursor() as cur:
            cur.execute("DELETE FROM workout_plan_template_slots WHERE version_id IS NULL")
        conn = _neutra(mysql_kwargs, mysql_schema)
        try:
            _rodar(conn, "up")
        finally:
            conn.close()
