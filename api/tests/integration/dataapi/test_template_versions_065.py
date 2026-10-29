"""Versionamento do Modelo de Treino (migracao 065, PR1) contra MySQL real,
nos DOIS backends (PyMySQL de dev e shim da RDS Data API).

O que so o banco prova:

- `POST` cria o Modelo ja com a v1 (`effective_from` = agora); `PUT`
  estrutural grava a v2 e a v1 fica INTACTA (mesmos slots e itens, fechada no
  mesmo instante em que a v2 abre); `GET`/lista mostram `current_version`;
- `PUT` so de nome/descricao/`is_active` NAO cria versao;
- trocar so a intencao grava versao nova com a arvore copiada por
  `INSERT ... SELECT`;
- o UNIQUE da vigente e do banco: uma segunda versao vigente e recusada;
- exclusao logica: `deleted_at`, atribuicoes encerradas (com autor), Modelo
  fora da lista, de `assigned-to-me`, de GET/PUT/atribuicao (404) e da
  geracao; a ficha ja salva continua apontando para ele;
- 065 idempotente (plano vazio no schema convergido); `plan_down` recusa com
  historico; down -> up refaz o backfill da v1 (desde sempre, com a intencao
  da 056, slots pendurados) - inclusive pelo shim da Data API.

Isolamento: `truncar(raw)` com `isolamento_v3.TABELAS_V3` (as tabelas de
Modelo e a de versoes estao la). Sem relogio de Python: o instante vem do
`NOW()` do banco e o teste compara as pontas entre si.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import workout_plan_templates as wpt
from app.security import Identity, get_current_identity, require_admin_or_pt
from app.workout_plan_templates_engine import get_template_if_assigned
from tests.support.isolamento_v3 import truncar

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

MIGRATION_065 = m.DEFAULT_MIGRATIONS_DIR / "065_workout_plan_template_versions.py"
# Posteriores que dependem da tabela de versoes que a 065 recria: o down/up da
# 065 abaixo tira a 073 antes e a reaplica depois (senao a tabela volta sem
# `volume_counting_mode` e quebra o resto da suite no mesmo schema). A 093
# (objetivo de treino) tambem cria coluna na tabela de versoes: sem ela aqui, a
# 065 recriada volta sem `training_goal` e as leituras de versao dao 1054
# (`test_template_versions_historico_pr4.py` roda depois, no mesmo schema).
MIGRATIONS_SOBRE_A_065 = (
    m.DEFAULT_MIGRATIONS_DIR / "073_modo_contagem_volume.py",
    m.DEFAULT_MIGRATIONS_DIR / "093_training_goal.py",
)
PEITORAL = 52


@pytest.fixture
def seed(raw):
    truncar(raw)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_v065', 'x', 'admin')")
        admin_id = cur.lastrowid
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('aluno_v065', 'x', 'regular')")
        aluno_id = cur.lastrowid
        cur.execute("INSERT INTO muscle_groups (id, name) VALUES (%s, 'Peitoral')", (PEITORAL,))
    raw.commit()
    return {"admin": admin_id, "aluno": aluno_id}


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(wpt.me_router)
    app.include_router(wpt.router)
    identidade = Identity(user_id=user_id, role="admin", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    app.dependency_overrides[require_admin_or_pt] = lambda: identidade
    return TestClient(app)


def _modelo(nome="Versionado", *, series=3, slot2="Pull", **extra) -> dict:
    return {
        "name": nome,
        "volume_mode": "percent_budget",
        "slots": [
            {
                "sequence": 1,
                "name": "Push",
                "slot_type": "normal",
                "items": [{"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": PEITORAL, "sets_count": series}],
            },
            {"sequence": 2, "name": slot2, "slot_type": "descanso", "items": []},
        ],
        **extra,
    }


def _versoes(raw, template_id: int) -> list[dict]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT id, version_number, effective_from, superseded_at, methodology_intent, "
            "methodology_target_group_ids, created_by_user_id FROM workout_plan_template_versions "
            "WHERE template_id = %s ORDER BY version_number",
            (template_id,),
        )
        return list(cur.fetchall())


def _arvore(raw, version_id: int) -> list[tuple]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT s.id AS slot_id, s.sequence, s.name, s.slot_type, i.id AS item_id, i.muscle_group_id, i.sets_count "
            "FROM workout_plan_template_slots s LEFT JOIN workout_plan_template_slot_items i ON i.slot_id = s.id "
            "WHERE s.version_id = %s ORDER BY s.sequence, i.item_order",
            (version_id,),
        )
        return [tuple(row.values()) for row in cur.fetchall()]


def _criar(api, db_backend, **kw) -> int:
    resposta = api.post("/workout-plan-templates", json=_modelo(**kw))
    assert resposta.status_code == 200, (db_backend, resposta.text)
    return resposta.json()["id"]


# ---------------------------------------------------------------------------
# Escritor
# ---------------------------------------------------------------------------


def test_post_cria_a_v1_e_put_estrutural_cria_a_v2_com_a_v1_intacta(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    [v1] = _versoes(raw, template_id)
    assert v1["version_number"] == 1 and v1["effective_from"] is not None and v1["superseded_at"] is None
    assert v1["created_by_user_id"] == seed["admin"]
    arvore_v1 = _arvore(raw, v1["id"])
    assert api.get(f"/workout-plan-templates/{template_id}").json()["current_version"] == {"id": v1["id"], "number": 1}

    editado = api.put(f"/workout-plan-templates/{template_id}", json=_modelo(series=5))
    assert editado.status_code == 200, (db_backend, editado.text)
    v1_depois, v2 = _versoes(raw, template_id)
    assert editado.json()["current_version"] == {"id": v2["id"], "number": 2}
    assert editado.json()["slots"][0]["items"][0]["sets_count"] == 5
    # A v1 nao mudou uma linha, e fecha no instante exato em que a v2 abre.
    assert _arvore(raw, v1["id"]) == arvore_v1
    assert v1_depois["superseded_at"] == v2["effective_from"]
    assert v2["superseded_at"] is None
    # A lista mostra a vigente.
    lista = api.get("/workout-plan-templates").json()["items"]
    assert [t["current_version"]["number"] for t in lista if t["id"] == template_id] == [2]


def test_put_so_de_nome_descricao_e_status_nao_versiona(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    corpo = _modelo(nome="Outro nome", description="nova", is_active=False)
    assert api.put(f"/workout-plan-templates/{template_id}", json=corpo).status_code == 200
    assert [v["version_number"] for v in _versoes(raw, template_id)] == [1], db_backend


def test_renomear_um_slot_versiona(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    assert api.put(f"/workout-plan-templates/{template_id}", json=_modelo(slot2="Costas")).status_code == 200
    assert [v["version_number"] for v in _versoes(raw, template_id)] == [1, 2], db_backend


def test_trocar_so_a_intencao_versiona_copiando_a_arvore(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    corpo = {"intent": "specialization", "target_group_ids": [PEITORAL]}
    assert api.put(f"/workout-plan-templates/{template_id}/methodology-intent", json=corpo).status_code == 200
    v1, v2 = _versoes(raw, template_id)
    assert v2["methodology_intent"] == "specialization"
    assert str(PEITORAL) in str(v2["methodology_target_group_ids"])
    copia = [linha[1:4] + linha[5:] for linha in _arvore(raw, v2["id"])]
    original = [linha[1:4] + linha[5:] for linha in _arvore(raw, v1["id"])]
    assert copia == original and copia, db_backend
    # Repetir a mesma intencao nao cria a v3.
    api.put(f"/workout-plan-templates/{template_id}/methodology-intent", json=corpo)
    assert len(_versoes(raw, template_id)) == 2


def test_o_banco_recusa_duas_versoes_vigentes(seed, db_backend, raw):
    import pymysql

    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    with pytest.raises(pymysql.err.IntegrityError) as exc:
        with raw.cursor() as cur:
            cur.execute(
                "INSERT INTO workout_plan_template_versions (template_id, version_number) VALUES (%s, 2)",
                (template_id,),
            )
    assert "uq_wptv_current" in str(exc.value)


# ---------------------------------------------------------------------------
# Exclusao logica
# ---------------------------------------------------------------------------


def test_excluir_esconde_o_modelo_encerra_atribuicoes_e_mantem_a_ficha(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    assert api.post(f"/workout-plan-templates/{template_id}/assignments", json={"user_id": seed["aluno"]}).status_code == 200
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plans (user_id, plan_date, name, plan_type, status, template_id, template_slot_sequence) "
            "VALUES (%s, '2026-10-01', 'Ficha', 'full_body', 'planned', %s, 1)",
            (seed["aluno"], template_id),
        )
        plan_id = cur.lastrowid

    apagado = api.delete(f"/workout-plan-templates/{template_id}")
    assert apagado.status_code == 200, (db_backend, apagado.text)
    assert apagado.json() == {"status": "ok", "template_id": template_id}

    with raw.cursor() as cur:
        cur.execute("SELECT deleted_at FROM workout_plan_templates WHERE id = %s", (template_id,))
        assert cur.fetchone()["deleted_at"] is not None
        cur.execute(
            "SELECT unassigned_at, unassigned_by_user_id FROM workout_plan_template_assignments WHERE template_id = %s",
            (template_id,),
        )
        [atribuicao] = cur.fetchall()
        cur.execute("SELECT template_id FROM workout_plans WHERE id = %s", (plan_id,))
        assert cur.fetchone()["template_id"] == template_id
    assert atribuicao["unassigned_at"] is not None and atribuicao["unassigned_by_user_id"] == seed["admin"]
    assert len(_versoes(raw, template_id)) == 1  # historico fica

    assert template_id not in [t["id"] for t in api.get("/workout-plan-templates").json()["items"]]
    assert api.get(f"/workout-plan-templates/{template_id}").status_code == 404
    assert api.put(f"/workout-plan-templates/{template_id}", json=_modelo()).status_code == 404
    assert api.post(f"/workout-plan-templates/{template_id}/duplicate").status_code == 404
    assert (
        api.post(f"/workout-plan-templates/{template_id}/assignments", json={"user_id": seed["aluno"]}).status_code
        == 404
    )
    assert api.patch(f"/workout-plan-templates/{template_id}/status", json={"is_active": True}).status_code == 404
    assert api.delete(f"/workout-plan-templates/{template_id}").status_code == 404
    assert get_template_if_assigned(template_id, seed["aluno"]) is None
    assert _client(seed["aluno"]).get("/workout-plan-templates/assigned-to-me").json()["items"] == []


# ---------------------------------------------------------------------------
# Migracao 065
# ---------------------------------------------------------------------------


def _plan(conn, nome="plan"):
    return [" ".join(sql.split()) for sql, _ in m.load_python_migration(MIGRATION_065).__globals__[nome](m.MigrationDB(conn))]


def _backend_conn():
    import app.db as db  # atributo trocado por use_backend

    return db.get_conn()


def test_065_no_schema_convergido_nao_tem_nada_a_fazer(seed, db_backend):
    conn = _backend_conn()
    try:
        assert _plan(conn) == [], db_backend
    finally:
        conn.close()


def test_065_plan_down_recusa_quando_ha_historico(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    api.put(f"/workout-plan-templates/{template_id}", json=_modelo(series=4))
    conn = _backend_conn()
    try:
        with pytest.raises(Exception, match="2 versoes"):
            _plan(conn, "plan_down")
    finally:
        conn.close()


def test_065_down_e_up_refazem_o_backfill_da_v1(seed, db_backend, raw, mysql_kwargs, mysql_schema):
    """Down pela conexao neutra; up pelo BACKEND do teste (Data API inclusive).
    Ao final o schema volta convergido de qualquer jeito (finally), inclusive
    as migracoes posteriores que alteram a tabela de versoes (073, 093)."""
    from app.db_pymysql import Connection as PyMySQLConnection

    api = _client(seed["admin"])
    com_intencao = _criar(api, db_backend, nome="Com intencao")
    sem_intencao = _criar(api, db_backend, nome="Sem intencao")
    # Intencao gravada direto nas tabelas da 056 (o PUT da rota ja versionaria).
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_template_methodology_intent (template_id, intent) VALUES (%s, 'specialization')",
            (com_intencao,),
        )
        cur.execute(
            "INSERT INTO workout_plan_template_methodology_intent_targets (template_id, muscle_group_id) VALUES (%s, %s)",
            (com_intencao, PEITORAL),
        )
        cur.execute("SELECT COUNT(*) AS n FROM workout_plan_template_slot_items")
        itens_antes = cur.fetchone()["n"]

    modulo = m.load_python_migration(MIGRATION_065).__globals__
    posteriores = [m.load_python_migration(path).__globals__ for path in MIGRATIONS_SOBRE_A_065]
    kw = mysql_kwargs

    def neutra():
        return PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], mysql_schema)

    conn = neutra()
    try:
        # Posteriores primeiro, da mais nova para a mais antiga (ordem inversa).
        for posterior in reversed(posteriores):
            posterior["down"](m.MigrationDB(conn))
        modulo["down"](m.MigrationDB(conn))
        conn.commit()
    finally:
        conn.close()
    try:
        conn = _backend_conn()
        try:
            executados = _plan(conn)
            assert len(executados) == 8, (db_backend, executados)
            modulo["up"](m.MigrationDB(conn))
            conn.commit()
            assert _plan(conn) == [], db_backend
        finally:
            conn.close()
    finally:
        conn = neutra()
        try:
            modulo["up"](m.MigrationDB(conn))
            for posterior in posteriores:
                posterior["up"](m.MigrationDB(conn))
            conn.commit()
        finally:
            conn.close()

    for template_id, intent in ((com_intencao, "specialization"), (sem_intencao, "standard")):
        [v1] = _versoes(raw, template_id)
        assert (v1["version_number"], v1["effective_from"], v1["superseded_at"]) == (1, None, None), db_backend
        assert v1["methodology_intent"] == intent
        assert len(_arvore(raw, v1["id"])) == 2  # Push (1 item) + descanso
    [v1] = _versoes(raw, com_intencao)
    assert str(PEITORAL) in str(v1["methodology_target_group_ids"])
    with raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM workout_plan_template_slots WHERE version_id IS NULL")
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT COUNT(*) AS n FROM workout_plan_template_slot_items")
        assert cur.fetchone()["n"] == itens_antes
    # E a leitura de agora enxerga os slots pela v1 do backfill.
    assert len(api.get(f"/workout-plan-templates/{com_intencao}").json()["slots"]) == 2
