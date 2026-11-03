"""Check-in semanal (N7, migração 103) contra MySQL real, nos DOIS backends (PyMySQL de dev e shim da RDS Data
API de produção). O SQL novo (`INSERT ... ON DUPLICATE KEY UPDATE id = id`, `SELECT ... FOR UPDATE`, colunas JSON,
o upsert do dia da semana e a publicação do plano pelo aceite) só tinha rodado contra o banco em memória.

- a 103 aplicada pelo schema da suíte converge (`plan()` = 0) e as duas FKs são só para `users` (CASCADE);
- UNIQUE `(user_id, week_start)`: o INSERT comum falha e o `ON DUPLICATE KEY UPDATE id = id` é no-op (uma
  proposta por semana e por usuário, mesmo com aberturas concorrentes);
- `GET /nutrition/checkin` gera UMA linha por semana (JSON volta como dict nos dois backends) e a segunda
  abertura lê a mesma; o dia da semana é gravado e lido; o histórico lista;
- aceitar publica uma versão nova do plano (a vigente vira substituída, o ritmo proposto é gravado), é
  idempotente, e proposta expirada ou plano trocado respondem 409 sem publicar nada;
- terceiro não alcança o check-in de outro (403 por `user_id`, 404 por id);
- o payload é idêntico nos dois backends.
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import nutrition_checkins, nutrition_plans  # noqa: F401 - use_backend troca o backend de módulos já importados
from app.body_measurements_schema import reset_body_measurements_table_cache
from app.nutrition_schema import reset_nutrition_tables_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
TABELAS = ("nutrition_checkins", "nutrition_checkin_settings", "nutrition_daily_targets", "nutrition_plans", "body_composition")
MIGRACAO = "103_nutrition_checkins.py"
RITMO_PROPOSTO = 0.0085


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
                (f"{nome}_n7",),
            )
            ids[nome] = cur.lastrowid
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_n7', 'x', 'admin')")
        ids["admin"] = cur.lastrowid
        for dias in range(0, 30):
            cur.execute(
                "INSERT INTO body_composition (user_id, local_date, weight_kg, body_fat_percent, source) "
                "VALUES (%s, %s, 80.0, 20.0, 'manual')",
                (ids["aluno"], HOJE - _dt.timedelta(days=dias)),
            )
    raw.commit()
    return ids


def _client(user_id: int, role: str = "regular") -> TestClient:
    app = FastAPI()
    app.include_router(nutrition_plans.router)
    app.include_router(nutrition_checkins.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def _plano_vigente(aluno: TestClient, raw, usuario: int) -> int:
    """Cutting publicado pelo próprio usuário e com início recuado: o check-in só existe para semana depois do plano."""
    criado = aluno.post("/nutrition/plans", json={"strategy": "cutting"})
    assert criado.status_code == 201, criado.text
    publicado = aluno.post(f"/nutrition/plans/{criado.json()['id']}/publish", json={"expected_current_id": None})
    assert publicado.status_code == 200, publicado.text
    with raw.cursor() as cur:
        cur.execute("UPDATE nutrition_plans SET effective_from = %s WHERE id = %s", (HOJE - _dt.timedelta(days=60), criado.json()["id"]))
    raw.commit()
    return int(criado.json()["id"])


def _semear_proposta(raw, usuario: int, plano_id: int, semana: _dt.date, *, gerado_em: _dt.date) -> int:
    """Uma linha `proposed` como o motor a gravaria (mesmas colunas e JSON do `INSERT ... ON DUPLICATE KEY`)."""
    entradas = {"generated_on": gerado_em.isoformat(), "week_start": semana.isoformat(), "strategy": "cutting", "summary": {}, "days": []}
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO nutrition_checkins (user_id, week_start, due_date, status, verdict, inputs_json, result_json, "
            "adjustment_kcal, proposed_changes_json, proposed_targets_json, expires_at, engine_version, source_plan_id) "
            "VALUES (%s, %s, %s, 'proposed', 'slow', %s, %s, -90, %s, %s, %s, 'nutrition-v1', %s)",
            (
                usuario, semana, gerado_em, json.dumps(entradas), json.dumps({"verdict": "slow", "missing": [], "reasons": []}),
                json.dumps({"weekly_rate_share": RITMO_PROPOSTO}),
                json.dumps({"before": {}, "after": {}, "delta_kcal": -90, "changes": {}, "change_items": []}),
                _dt.datetime.combine(gerado_em + _dt.timedelta(days=3), _dt.time(23, 59, 59)), plano_id,
            ),
        )
        novo = int(cur.lastrowid)
    raw.commit()
    return novo


def _sem_volateis(valor):
    if isinstance(valor, dict):
        return {k: _sem_volateis(v) for k, v in valor.items() if k not in (
            "id", "user_id", "created_at", "updated_at", "responded_at", "source_plan_id", "generated_plan_id", "effective_from")}
    if isinstance(valor, list):
        return [_sem_volateis(v) for v in valor]
    return valor


def test_103_converge_no_schema_real_com_as_fks_so_para_users(raw):
    modulo = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / MIGRACAO).__globals__
    assert modulo["plan"](m.MigrationDB(raw)) == []
    with raw.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME, REFERENCED_TABLE_NAME, DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
            "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME IN ('nutrition_checkins', 'nutrition_checkin_settings') "
            "ORDER BY TABLE_NAME"
        )
        assert cur.fetchall() == [
            {"TABLE_NAME": "nutrition_checkin_settings", "REFERENCED_TABLE_NAME": "users", "DELETE_RULE": "CASCADE"},
            {"TABLE_NAME": "nutrition_checkins", "REFERENCED_TABLE_NAME": "users", "DELETE_RULE": "CASCADE"},
        ]


def test_unique_por_usuario_e_semana_e_insercao_idempotente(usuarios, raw):
    aluno = _client(usuarios["aluno"])
    plano = _plano_vigente(aluno, raw, usuarios["aluno"])
    semana = HOJE - _dt.timedelta(days=21)
    _semear_proposta(raw, usuarios["aluno"], plano, semana, gerado_em=HOJE)
    with raw.cursor() as cur:
        with pytest.raises(Exception, match="(?i)duplicate"):
            cur.execute(
                "INSERT INTO nutrition_checkins (user_id, week_start, due_date, status, verdict, inputs_json, result_json, "
                "engine_version, source_plan_id) VALUES (%s, %s, %s, 'informative', 'on_track', '{}', '{}', 'v', %s)",
                (usuarios["aluno"], semana, HOJE, plano),
            )
        raw.rollback()
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO nutrition_checkins (user_id, week_start, due_date, status, verdict, inputs_json, result_json, "
            "engine_version, source_plan_id) VALUES (%s, %s, %s, 'informative', 'on_track', '{}', '{}', 'v', %s) "
            "ON DUPLICATE KEY UPDATE id = id",
            (usuarios["aluno"], semana, HOJE, plano),
        )
        cur.execute("SELECT COUNT(*) AS n, MIN(status) AS status FROM nutrition_checkins WHERE user_id = %s", (usuarios["aluno"],))
        assert cur.fetchone() == {"n": 1, "status": "proposed"}  # a primeira linha fica
    raw.commit()


def test_gera_uma_linha_por_semana_dia_e_historico_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    resultados = []
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        with raw.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for tabela in ("nutrition_checkins", "nutrition_checkin_settings", "nutrition_daily_targets", "nutrition_plans"):
                cur.execute(f"TRUNCATE TABLE {tabela}")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        raw.commit()
        aluno, outro = _client(usuarios["aluno"]), _client(usuarios["outro"])
        sem_plano = aluno.get("/nutrition/checkin")
        assert sem_plano.status_code == 200 and sem_plano.json()["checkin"] is None, (backend, sem_plano.text)
        _plano_vigente(aluno, raw, usuarios["aluno"])
        primeira = aluno.get("/nutrition/checkin")
        assert primeira.status_code == 200, (backend, primeira.text)
        checkin = primeira.json()["checkin"]
        # Sem refeições na semana: inconclusivo, só informa o que faltou e não propõe nada.
        assert checkin["verdict"] == "inconclusive" and checkin["status"] == "informative" and checkin["proposal"] is None
        assert checkin["missing_items"] and all(i["label"] != i["code"] for i in checkin["missing_items"])
        segunda = aluno.get("/nutrition/checkin").json()["checkin"]
        assert segunda["id"] == checkin["id"]
        with raw.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM nutrition_checkins WHERE user_id = %s", (usuarios["aluno"],))
            assert cur.fetchone() == {"n": 1}
            cur.execute("SELECT inputs_json, result_json FROM nutrition_checkins WHERE id = %s", (checkin["id"],))
            gravado = cur.fetchone()
        for coluna in ("inputs_json", "result_json"):
            valor = gravado[coluna]
            assert isinstance(json.loads(valor) if isinstance(valor, (str, bytes)) else valor, dict)

        salvo = aluno.put("/nutrition/checkin/settings", json={"weekday": 3})
        lido = aluno.get("/nutrition/checkin/settings")
        assert salvo.status_code == 200 and lido.json()["weekday"] == 3 and lido.json()["is_default"] is False
        assert aluno.put("/nutrition/checkin/settings", json={"weekday": 9}).status_code == 400
        historico = aluno.get("/nutrition/checkins")
        assert historico.status_code == 200 and [i["id"] for i in historico.json()["items"]] == [checkin["id"]]

        assert outro.get(f"/nutrition/checkin?user_id={usuarios['aluno']}").status_code == 403
        assert outro.post(f"/nutrition/checkins/{checkin['id']}/accept").status_code == 404
        resultados.append({"checkin": _sem_volateis(checkin), "dia": lido.json(), "historico": _sem_volateis(historico.json()["items"])})
    assert resultados[0] == resultados[1]


def test_aceitar_publica_versao_nova_e_e_idempotente_nos_dois_backends(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        with raw.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for tabela in ("nutrition_checkins", "nutrition_daily_targets", "nutrition_plans"):
                cur.execute(f"TRUNCATE TABLE {tabela}")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        raw.commit()
        aluno = _client(usuarios["aluno"])
        antigo = _plano_vigente(aluno, raw, usuarios["aluno"])
        proposta = _semear_proposta(raw, usuarios["aluno"], antigo, HOJE - _dt.timedelta(days=14), gerado_em=HOJE)

        resposta = aluno.post(f"/nutrition/checkins/{proposta}/accept")
        assert resposta.status_code == 200, (backend, resposta.text)
        corpo = resposta.json()
        novo = corpo["plan"]["id"]
        assert corpo["checkin"]["status"] == "accepted" and corpo["checkin"]["generated_plan_id"] == novo and not corpo["already_accepted"]
        with raw.cursor() as cur:
            cur.execute("SELECT id, status, superseded_at, weekly_rate_share, strategy FROM nutrition_plans WHERE user_id = %s ORDER BY id",
                        (usuarios["aluno"],))
            planos = cur.fetchall()
            cur.execute("SELECT status, generated_plan_id FROM nutrition_checkins WHERE id = %s", (proposta,))
            linha = cur.fetchone()
        assert [p["id"] for p in planos] == [antigo, novo]
        assert planos[0]["superseded_at"] is not None and planos[1]["superseded_at"] is None
        assert planos[1]["status"] == "published" and planos[1]["strategy"] == "cutting"
        assert float(planos[1]["weekly_rate_share"]) == RITMO_PROPOSTO
        assert linha == {"status": "accepted", "generated_plan_id": novo}

        repetido = aluno.post(f"/nutrition/checkins/{proposta}/accept")
        assert repetido.status_code == 200 and repetido.json()["already_accepted"] is True
        with raw.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM nutrition_plans WHERE user_id = %s", (usuarios["aluno"],))
            assert cur.fetchone() == {"n": 2}  # o aceite repetido não publicou outra versão
        assert aluno.post(f"/nutrition/checkins/{proposta}/decline").status_code == 409


def test_expirada_ou_plano_trocado_responde_409_sem_publicar(usuarios, raw, monkeypatch, fake_rds, mysql_schema):
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        with raw.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for tabela in ("nutrition_checkins", "nutrition_daily_targets", "nutrition_plans"):
                cur.execute(f"TRUNCATE TABLE {tabela}")
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        raw.commit()
        aluno = _client(usuarios["aluno"])
        vigente = _plano_vigente(aluno, raw, usuarios["aluno"])
        velha = _semear_proposta(raw, usuarios["aluno"], vigente, HOJE - _dt.timedelta(days=28), gerado_em=HOJE - _dt.timedelta(days=10))
        trocada = _semear_proposta(raw, usuarios["aluno"], vigente + 1000, HOJE - _dt.timedelta(days=21), gerado_em=HOJE)

        expirada = aluno.post(f"/nutrition/checkins/{velha}/accept")
        assert expirada.status_code == 409 and expirada.json()["detail"]["error"] == "checkin_expired", (backend, expirada.text)
        mudou = aluno.post(f"/nutrition/checkins/{trocada}/accept")
        assert mudou.status_code == 409 and mudou.json()["detail"]["error"] == "checkin_plan_changed", (backend, mudou.text)
        with raw.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM nutrition_plans WHERE user_id = %s", (usuarios["aluno"],))
            assert cur.fetchone() == {"n": 1}
            cur.execute("SELECT id, status FROM nutrition_checkins WHERE user_id = %s ORDER BY id", (usuarios["aluno"],))
            assert cur.fetchall() == [{"id": velha, "status": "expired"}, {"id": trocada, "status": "proposed"}]
