"""Rascunho do Modelo de Treino (migracao 067) contra MySQL real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API).

O que so o banco prova:

- `PUT /{id}/draft` grava (e sobrescreve) o rascunho SEM criar versao, sem
  tocar slots/itens da vigente nem as colunas do Modelo - e aceita estado que
  a publicacao recusaria;
- o GET do Modelo e a lista mostram `has_draft`/`draft_updated_at`;
- publicar (`PUT /{id}`) cria a versao nova e apaga o rascunho na MESMA
  transacao; publicar invalido (422) mantem o rascunho intacto;
- reverter uma versao apaga o rascunho;
- excluir o Modelo apaga o rascunho (e o CASCADE da FK cobre o DELETE fisico);
- usuario `regular` recebe 403;
- 067 idempotente no schema convergido.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import workout_plan_templates as wpt
from app.security import Identity, get_current_identity
from tests.support.isolamento_v3 import truncar

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

MIGRATION_067 = m.DEFAULT_MIGRATIONS_DIR / "067_workout_plan_template_drafts.py"
PEITORAL = 52


@pytest.fixture
def seed(raw):
    truncar(raw)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_d067', 'x', 'admin')")
        admin_id = cur.lastrowid
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('aluno_d067', 'x', 'regular')")
        aluno_id = cur.lastrowid
        cur.execute("INSERT INTO muscle_groups (id, name) VALUES (%s, 'Peitoral')", (PEITORAL,))
    raw.commit()
    return {"admin": admin_id, "aluno": aluno_id}


def _client(user_id: int, role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(wpt.router)
    identidade = Identity(user_id=user_id, role=role, source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    return TestClient(app)


def _modelo(nome="Rascunhado", *, series=3, slot1_tipo="normal") -> dict:
    return {
        "name": nome,
        "volume_mode": "percent_budget",
        "slots": [
            {
                "sequence": 1,
                "name": "Push",
                "slot_type": slot1_tipo,
                "items": [{"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": PEITORAL, "sets_count": series}],
            },
        ],
    }


def _versoes(raw, template_id: int) -> list[tuple]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT id, version_number, superseded_at FROM workout_plan_template_versions "
            "WHERE template_id = %s ORDER BY version_number",
            (template_id,),
        )
        return [tuple(r.values()) for r in cur.fetchall()]


def _foto(raw, template_id: int) -> tuple:
    """Tudo o que o rascunho NAO pode tocar: colunas do Modelo, versoes, slots e itens."""
    raw.commit()  # nova leitura (REPEATABLE READ)
    with raw.cursor() as cur:
        cur.execute(
            "SELECT name, description, template_type, training_type_id, volume_mode, is_active, grid_rows, "
            "grid_cols, updated_at FROM workout_plan_templates WHERE id = %s",
            (template_id,),
        )
        modelo = tuple(cur.fetchone().values())
        cur.execute(
            "SELECT s.id, s.version_id, s.sequence, s.name, i.id, i.sets_count FROM workout_plan_template_slots s "
            "LEFT JOIN workout_plan_template_slot_items i ON i.slot_id = s.id "
            "WHERE s.template_id = %s AND s.version_id IS NOT NULL ORDER BY s.id, i.id",
            (template_id,),
        )
        arvore = [tuple(r.values()) for r in cur.fetchall()]
    return modelo, _versoes(raw, template_id), arvore


def _tem_rascunho(raw, template_id: int) -> bool:
    raw.commit()
    with raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM workout_plan_template_drafts WHERE template_id = %s", (template_id,))
        return int(cur.fetchone()["n"]) == 1


def _criar(api, db_backend) -> int:
    resposta = api.post("/workout-plan-templates", json=_modelo())
    assert resposta.status_code == 200, (db_backend, resposta.text)
    return resposta.json()["id"]


def test_rascunho_nao_versiona_nem_toca_a_vigente(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    antes = _foto(raw, template_id)

    # Estado que a publicacao recusaria (descanso com grupo muscular, nome vazio).
    incompleto = _modelo(nome="", series=9, slot1_tipo="descanso")
    salvo = api.put(f"/workout-plan-templates/{template_id}/draft", json=incompleto)
    assert salvo.status_code == 200, (db_backend, salvo.text)
    assert salvo.json()["updated_at"] and salvo.json()["updated_by"]["id"] == seed["admin"]
    # Sobrescrever: continua UM rascunho por Modelo.
    incompleto["slots"][0]["items"][0]["sets_count"] = 11
    assert api.put(f"/workout-plan-templates/{template_id}/draft", json=incompleto).status_code == 200

    assert _foto(raw, template_id) == antes, db_backend
    lido = api.get(f"/workout-plan-templates/{template_id}/draft").json()
    assert lido["payload"]["slots"][0]["items"][0]["sets_count"] == 11
    assert lido["payload"]["slots"][0]["slot_type"] == "descanso"
    detalhe = api.get(f"/workout-plan-templates/{template_id}").json()
    assert detalhe["has_draft"] is True and detalhe["draft_updated_at"]
    assert detalhe["slots"][0]["items"][0]["sets_count"] == 3  # a vigente segue oficial
    lista = api.get("/workout-plan-templates").json()["items"]
    assert [t["has_draft"] for t in lista if t["id"] == template_id] == [True]


def test_publicar_cria_versao_e_apaga_o_rascunho(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    assert api.put(f"/workout-plan-templates/{template_id}/draft", json=_modelo(series=5)).status_code == 200

    publicado = api.put(f"/workout-plan-templates/{template_id}", json=_modelo(series=5))
    assert publicado.status_code == 200, (db_backend, publicado.text)
    assert publicado.json()["version_created"] is True
    assert publicado.json()["has_draft"] is False
    assert [v[1] for v in _versoes(raw, template_id)] == [1, 2]
    assert not _tem_rascunho(raw, template_id)
    assert api.get(f"/workout-plan-templates/{template_id}/draft").status_code == 404


def test_publicar_invalido_mantem_o_rascunho(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    invalido = _modelo(slot1_tipo="descanso")
    assert api.put(f"/workout-plan-templates/{template_id}/draft", json=invalido).status_code == 200

    assert api.put(f"/workout-plan-templates/{template_id}", json=invalido).status_code == 422
    assert _tem_rascunho(raw, template_id), db_backend
    assert [v[1] for v in _versoes(raw, template_id)] == [1]


def test_reverter_apaga_o_rascunho(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    api.put(f"/workout-plan-templates/{template_id}", json=_modelo(series=4))
    v1_id = _versoes(raw, template_id)[0][0]
    assert api.put(f"/workout-plan-templates/{template_id}/draft", json=_modelo(series=6)).status_code == 200

    revertido = api.post(f"/workout-plan-templates/{template_id}/versions/{v1_id}/revert")
    assert revertido.status_code == 200, (db_backend, revertido.text)
    assert revertido.json()["has_draft"] is False
    assert not _tem_rascunho(raw, template_id)


def test_descartar_e_excluir_apagam_o_rascunho(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    api.put(f"/workout-plan-templates/{template_id}/draft", json=_modelo())
    assert api.delete(f"/workout-plan-templates/{template_id}/draft").status_code == 200
    assert not _tem_rascunho(raw, template_id)

    api.put(f"/workout-plan-templates/{template_id}/draft", json=_modelo())
    assert api.delete(f"/workout-plan-templates/{template_id}").status_code == 200
    assert not _tem_rascunho(raw, template_id), db_backend
    assert api.put(f"/workout-plan-templates/{template_id}/draft", json=_modelo()).status_code == 404


def test_cascade_da_fk_no_delete_fisico(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    api.put(f"/workout-plan-templates/{template_id}/draft", json=_modelo())
    with raw.cursor() as cur:
        cur.execute("DELETE FROM workout_plan_templates WHERE id = %s", (template_id,))
    raw.commit()
    assert not _tem_rascunho(raw, template_id)


def test_usuario_regular_recebe_403(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    aluno = _client(seed["aluno"], role="regular")
    assert aluno.put(f"/workout-plan-templates/{template_id}/draft", json=_modelo()).status_code == 403
    assert aluno.get(f"/workout-plan-templates/{template_id}/draft").status_code == 403
    assert aluno.delete(f"/workout-plan-templates/{template_id}/draft").status_code == 403
    assert not _tem_rascunho(raw, template_id)


def test_payload_guardado_e_json_valido(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    api.put(f"/workout-plan-templates/{template_id}/draft", json=_modelo(nome="Açúcar"))
    with raw.cursor() as cur:
        cur.execute("SELECT payload FROM workout_plan_template_drafts WHERE template_id = %s", (template_id,))
        payload = cur.fetchone()["payload"]
    assert json.loads(payload)["name"] == "Açúcar", db_backend


def test_067_no_schema_convergido_nao_tem_nada_a_fazer(seed, db_backend):
    import app.db as db  # atributo trocado por use_backend

    conn = db.get_conn()
    try:
        plano = m.load_python_migration(MIGRATION_067).__globals__["plan"](m.MigrationDB(conn))
        assert plano == [], db_backend
    finally:
        conn.close()


def test_aplicar_cardio_no_rascunho(seed, db_backend, raw):
    """M98 C3b: `POST /cardio-recommendations/{id}/apply` grava SÓ no rascunho
    (`replace` por `slot_sequence` e `add` no fim do slot), nada na vigente, e
    aplicar de novo não muda nada (idempotente)."""
    from app import cardio_recommendations as cr

    api = _client(seed["admin"])
    modelo = _modelo()
    modelo["slots"][0]["items"].append(
        {"item_order": 2, "item_kind": "cardio", "cardio_duration_minutes": 20, "cardio_target_zone": 2}
    )
    modelo["slots"].append(
        {"sequence": 2, "name": "Pull", "slot_type": "normal",
         "items": [{"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": PEITORAL, "sets_count": 3}]}
    )
    criado = api.post("/workout-plan-templates", json=modelo)
    assert criado.status_code == 200, (db_backend, criado.text)
    template_id = criado.json()["id"]
    atribuido = api.post(f"/workout-plan-templates/{template_id}/assignments", json={"user_id": seed["admin"]})
    assert atribuido.status_code == 200, (db_backend, atribuido.text)
    detalhe = api.get(f"/workout-plan-templates/{template_id}").json()
    version_id = detalhe["current_version"]["id"]
    cardio_id = next(i["id"] for i in detalhe["slots"][0]["items"] if i["item_kind"] == "cardio")
    antes = _foto(raw, template_id)

    normal = {"cardio_mode": "continuous", "cardio_warmup_minutes": 5, "cardio_duration_minutes": 30,
              "cardio_target_zone": 2, "cardio_cooldown_minutes": 5}
    hiit = {"cardio_mode": "hiit", "cardio_warmup_minutes": 5, "hiit_low_seconds": 90, "hiit_high_seconds": 30,
            "hiit_low_zone": 2, "hiit_high_zone": 4, "hiit_rounds": 8, "cardio_cooldown_minutes": 5}

    def link(sequence, action, item_id=None, item_order=None):
        return {"template_id": template_id, "template_version_id": version_id, "slot_id": None,
                "slot_sequence": sequence, "slot_name": None, "action": action, "item_id": item_id,
                "item_order": item_order, "source": "sequence"}

    recomendacao = {
        "schema_version": 1, "prompt_version": "cardio-agent-v3",
        "sessions": [
            {"date": "2026-10-28", "prescription": normal, "plan_link": link(1, "replace", cardio_id, 2)},
            {"date": "2026-10-29", "prescription": hiit, "plan_link": link(2, "add")},
        ],
        "plan": {"template_id": template_id, "template_name": "Rascunhado", "template_version_id": version_id},
    }
    with raw.cursor() as cur:
        cur.execute("DELETE FROM cardio_recommendations WHERE user_id = %s", (seed["admin"],))
        cur.execute(
            "INSERT INTO cardio_recommendations (user_id, reference_date, model, recommendation_markdown, "
            "recommendation_json, prompt_version) VALUES (%s, '2026-10-27', 'gpt-test', '# v3', %s, 'cardio-agent-v3')",
            (seed["admin"], json.dumps(recomendacao)),
        )
        rec_id = cur.lastrowid
    raw.commit()
    identidade = Identity(user_id=seed["admin"], role="admin", source="session")
    try:
        corpo = cr.CardioApplyIn(dates=["2026-10-28", "2026-10-29"])
        aplicado = cr.apply_cardio_recommendation(rec_id, corpo, identity=identidade)
        assert [(r["date"], r["code"]) for r in aplicado["results"]] == [
            ("2026-10-28", "replaced"), ("2026-10-29", "added")
        ], db_backend
        assert aplicado["draft_saved"] is True
        assert _foto(raw, template_id) == antes, db_backend  # a vigente não muda

        payload = api.get(f"/workout-plan-templates/{template_id}/draft").json()["payload"]
        trocado = payload["slots"][0]["items"][1]
        assert (trocado["item_kind"], trocado["cardio_duration_minutes"], trocado["cardio_warmup_minutes"]) == (
            "cardio", 30, 5
        )
        novo = payload["slots"][1]["items"][1]
        assert (novo["item_order"], novo["cardio_mode"], novo["hiit_rounds"]) == (2, "hiit", 8)

        de_novo = cr.apply_cardio_recommendation(rec_id, corpo, identity=identidade)
        assert [r["status"] for r in de_novo["results"]] == ["already_applied", "already_applied"]
        assert de_novo["draft_saved"] is False
        assert api.get(f"/workout-plan-templates/{template_id}/draft").json()["payload"] == payload
        assert _foto(raw, template_id) == antes, db_backend
    finally:
        with raw.cursor() as cur:
            cur.execute("DELETE FROM cardio_recommendations WHERE id = %s", (rec_id,))
        raw.commit()
