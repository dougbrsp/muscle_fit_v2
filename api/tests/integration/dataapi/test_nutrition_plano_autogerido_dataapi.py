"""Plano autogerido (N2, migração 101) contra MySQL real, nos DOIS backends (PyMySQL de dev e shim da RDS Data
API de produção). O SQL novo (colunas `macro_priority`, `manual_targets_json` e `carb_cycling_json` na escrita e
na leitura dos planos, e a leitura dos slots do Modelo da rota "puxar do Modelo") só tinha rodado contra o banco
em memória dos testes unitários.

- a 101 aplicada pelo schema da suíte converge (`plan()` = 0) e `macro_priority` nasce `balanced`, o
  `manual_targets_json` nasce NULL (plano antigo = resultado idêntico);
- o próprio usuário cria o plano com prioridade, dias alto/normal/baixo e gramas manuais, edita e publica; o JSON
  volta do banco como dict nos dois backends e as metas materializadas respeitam os dias escolhidos;
- outro usuário comum não alcança o plano (404 por id; 403 por `user_id`);
- gramas manuais bloqueadas ou sem peso do aluno não gravam nada;
- "puxar do Modelo" lê os slots da versão vigente do Modelo atribuído.
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import nutrition_plans  # noqa: F401 - importado no topo (use_backend troca o backend de modulos ja importados)
from app.body_measurements_schema import reset_body_measurements_table_cache
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario
from tests.support.template_versions import versao_v1

HOJE = hoje_do_usuario()
TABELAS = ("nutrition_daily_targets", "nutrition_plans", "body_composition", "workout_plan_template_assignments")
DIAS = {"0": "high", "1": "low", "2": "high", "3": "low", "4": "high", "5": "low", "6": "low"}
MANUAL = {"protein_g": 160, "carb_g": 280, "fat_g": 70}


@pytest.fixture
def usuarios(raw):
    reset_nutrition_tables_cache()
    reset_body_measurements_table_cache()
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for tabela in (*TABELAS, "users"):
            cur.execute(f"TRUNCATE TABLE {tabela}")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        ids = {}
        for nome in ("aluno", "outro"):
            cur.execute(
                "INSERT INTO users (username, password_hash, role, sex, birth_date, height_cm, activity_level) "
                "VALUES (%s, 'x', 'regular', 'male', '1990-01-01', 180.0, 'moderate')",
                (f"{nome}_n2",),
            )
            ids[nome] = cur.lastrowid
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_n2', 'x', 'admin')")
        ids["admin"] = cur.lastrowid
        cur.execute(
            "INSERT INTO body_composition (user_id, local_date, weight_kg, source) VALUES (%s, %s, 80.0, 'manual')",
            (ids["aluno"], HOJE - _dt.timedelta(days=2)),
        )
    raw.commit()
    return ids


def _client(user_id: int, role: str = "regular") -> TestClient:
    app = FastAPI()
    app.include_router(nutrition_plans.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def test_101_converge_no_schema_real_e_o_plano_antigo_fica_igual(raw):
    modulo = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "101_plano_autogerido.py").__globals__
    assert modulo["plan"](m.MigrationDB(raw)) == []
    with raw.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_DEFAULT, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nutrition_plans' "
            "AND COLUMN_NAME IN ('macro_priority', 'manual_targets_json')"
        )
        colunas = {linha["COLUMN_NAME"]: linha for linha in cur.fetchall()}
    assert colunas["macro_priority"]["COLUMN_DEFAULT"] == "balanced" and colunas["macro_priority"]["IS_NULLABLE"] == "NO"
    assert colunas["manual_targets_json"]["COLUMN_DEFAULT"] is None


def test_usuario_opera_o_proprio_plano_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        with raw.cursor() as cur:
            cur.execute("TRUNCATE TABLE nutrition_daily_targets")
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            cur.execute("TRUNCATE TABLE nutrition_plans")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        raw.commit()
        aluno = _client(usuarios["aluno"])
        outro = _client(usuarios["outro"])

        criado = aluno.post(
            "/nutrition/plans",
            json={"strategy": "maintenance", "macro_priority": "high_carb", "cycle_days": DIAS},
        )
        assert criado.status_code == 201, (backend, criado.text)
        plano = criado.json()
        assert plano["macro_priority"] == "high_carb" and plano["cycle_days"] == DIAS and plano["manual_targets"] is None
        assert plano["user_id"] == usuarios["aluno"] and plano["created_by"] == usuarios["aluno"]

        # Outro usuário comum não alcança: 404 por id, 403 por user_id.
        assert outro.put(f"/nutrition/plans/{plano['id']}", json={"strategy": "maintenance"}).status_code == 404
        assert outro.post(f"/nutrition/plans/{plano['id']}/preview").status_code == 404
        assert outro.get(f"/nutrition/plans?user_id={usuarios['aluno']}").status_code == 403

        # Gramas manuais: bloqueada não grava; válida volta como dict do banco.
        ruim = aluno.put(
            f"/nutrition/plans/{plano['id']}",
            json={"strategy": "maintenance", "manual_targets": {"protein_g": 40, "carb_g": 280, "fat_g": 70}},
        )
        assert ruim.status_code == 400, (backend, ruim.text)
        bom = aluno.put(
            f"/nutrition/plans/{plano['id']}",
            json={"strategy": "maintenance", "cycle_days": DIAS, "manual_targets": MANUAL},
        )
        assert bom.status_code == 200, (backend, bom.text)
        # Contrato (rodada 4): `manual_targets` devolve o modo junto das gramas; o padrão é `fixed`.
        assert bom.json()["manual_targets"] == {"protein_g": 160.0, "carb_g": 280.0, "fat_g": 70.0, "training_mode": "fixed"}
        assert bom.json()["macro_priority"] == "balanced"

        # Modo `add_training`: grava no JSON, volta do banco (os dois backends) e a leitura do plano o devolve.
        soma = aluno.put(
            f"/nutrition/plans/{plano['id']}",
            json={"strategy": "maintenance", "cycle_days": DIAS, "manual_targets": MANUAL, "manual_training_mode": "add_training"},
        )
        assert soma.status_code == 200, (backend, soma.text)
        assert soma.json()["manual_targets"]["training_mode"] == "add_training"
        with raw.cursor() as cur:
            cur.execute("SELECT manual_targets_json FROM nutrition_plans WHERE id = %s", (plano["id"],))
            gravado = cur.fetchone()["manual_targets_json"]
        gravado = json.loads(gravado) if isinstance(gravado, (str, bytes)) else gravado
        assert gravado["training_mode"] == "add_training" and gravado["protein_g"] == 160
        lido = next(p for p in aluno.get("/nutrition/plans").json()["items"] if p["id"] == plano["id"])
        assert lido["manual_targets"]["training_mode"] == "add_training"

        publicado = aluno.post(f"/nutrition/plans/{plano['id']}/publish", json={"expected_current_id": None})
        assert publicado.status_code == 200, (backend, publicado.text)
        with raw.cursor() as cur:
            cur.execute(
                "SELECT macro_priority, manual_targets_json, carb_cycling_json FROM nutrition_plans WHERE id = %s",
                (plano["id"],),
            )
            linha = cur.fetchone()
            cur.execute("SELECT local_date, day_kind, protein_g, carbs_g FROM nutrition_daily_targets ORDER BY local_date")
            metas = cur.fetchall()
        assert linha["macro_priority"] == "balanced" and linha["manual_targets_json"] and linha["carb_cycling_json"]
        assert metas, backend
        for meta in metas:
            assert meta["day_kind"] == DIAS[str(meta["local_date"].weekday())]
            assert float(meta["protein_g"]) == 160.0  # as gramas manuais mandam

        minha = aluno.get("/nutrition/me/plan")
        assert minha.status_code == 200 and minha.json()["plan"]["cycle_days"] == DIAS


def test_gramas_manuais_sem_peso_do_aluno_nao_gravam(usuarios, raw, db_backend):
    sem_peso = _client(usuarios["outro"])
    resposta = sem_peso.post("/nutrition/plans", json={"strategy": "maintenance", "manual_targets": MANUAL})
    assert resposta.status_code == 400, db_backend
    assert "manual_targets_require_weight" in [e["code"] for e in resposta.json()["detail"]["errors"]]
    with raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM nutrition_plans WHERE user_id = %s", (usuarios["outro"],))
        assert cur.fetchone()["n"] == 0


def test_puxar_do_modelo_le_os_slots_da_versao_vigente(usuarios, raw, db_backend):
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, template_type, is_active, created_by_user_id) "
            "VALUES ('ABC N2', 'abc', 1, %s)",
            (usuarios["admin"],),
        )
        modelo = cur.lastrowid
        versao = versao_v1(cur, modelo)
        semana = [(1, "normal"), (2, "descanso"), (3, "normal"), (4, "descanso"), (5, "normal"), (6, "descanso"), (7, "descanso")]
        for sequencia, (dia, tipo) in enumerate(semana, start=1):
            cur.execute(
                "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, weekday, slot_type) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (versao, modelo, sequencia, f"Dia {sequencia}", dia, tipo),
            )
        cur.execute(
            "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id) VALUES (%s, %s, %s)",
            (modelo, usuarios["aluno"], usuarios["admin"]),
        )
    raw.commit()
    dados = _client(usuarios["aluno"]).get("/nutrition/cycle-days/from-template")
    assert dados.status_code == 200, (db_backend, dados.text)
    corpo = dados.json()
    assert corpo["template"]["name"] == "ABC N2" and corpo["training_weekdays"] == [0, 2, 4]
    assert corpo["cycle_days"] == DIAS and corpo["cycles"] is True
    sem_modelo = _client(usuarios["outro"]).get("/nutrition/cycle-days/from-template").json()
    assert sem_modelo["template"] is None and sem_modelo["notes"][0]["code"] == "template_not_assigned"


def test_limite_de_rascunhos_listagem_e_versao_do_rascunho_no_banco_real(usuarios, raw, monkeypatch, db_backend):
    """SQL novo da rodada 1: contagem de rascunhos, LIMIT da listagem e `updated_at` como versão do rascunho."""
    monkeypatch.setattr(nutrition_plans, "MAX_DRAFTS_PER_USER", 2)
    monkeypatch.setattr(nutrition_plans, "PLAN_LIST_MAX", 1)
    aluno = _client(usuarios["aluno"])
    primeiro = aluno.post("/nutrition/plans", json={"strategy": "maintenance"})
    assert primeiro.status_code == 201, (db_backend, primeiro.text)
    assert aluno.post("/nutrition/plans", json={"strategy": "maintenance"}).status_code == 201
    estouro = aluno.post("/nutrition/plans", json={"strategy": "maintenance"})
    assert estouro.status_code == 409 and estouro.json()["detail"]["error"] == "draft_limit"
    assert len(aluno.get("/nutrition/plans").json()["items"]) == 1
    # A versão do rascunho que a tela viu: publicar com outra versão é 409 e não grava.
    velha = "2000-01-01T00:00:00"
    recusado = aluno.post(
        f"/nutrition/plans/{primeiro.json()['id']}/publish", json={"expected_current_id": None, "expected_updated_at": velha}
    )
    assert recusado.status_code == 409 and recusado.json()["detail"]["error"] == "draft_changed"
    certo = aluno.post(
        f"/nutrition/plans/{primeiro.json()['id']}/publish",
        json={"expected_current_id": None, "expected_updated_at": primeiro.json()["updated_at"]},
    )
    assert certo.status_code == 200, (db_backend, certo.text)
