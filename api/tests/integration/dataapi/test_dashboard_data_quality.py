"""`GET /analytics/dashboard/data-quality` contra MySQL real, nos DOIS backends
(PyMySQL de dev e shim da RDS Data API de producao).

O que este arquivo prova:

- as 10 regras do §8.3 disparam de fato contra um banco de verdade (o cenario
  foi montado para violar todas), com o MESMO payload nos dois backends -
  nenhuma regra depende de tipo devolvido pelo driver (funcoes de janela,
  `SUM(...) OVER ()`, `UNION ALL` das tres fontes de peso);
- IDOR: o usuario B, sem historico nenhum, recebe `items: []` e
  `overall=HIGH` - nem contagem nem exemplo do usuario A vazam, embora as
  regras de CADASTRO leiam catalogo compartilhado (contribuicao muscular,
  convencao do par, meta do grupo);
- o estado vazio e positivo e o payload respeita o contrato Pydantic
  (`response_model`), inclusive `scope` por item.

Os dados sao semeados por conexao NEUTRA (PyMySQL direto), entao os dois
backends leem exatamente as mesmas linhas.
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importados no topo de proposito: `use_backend` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import analytics_dashboard, training_types  # noqa: F401
from app.intelligence.dashboard import data_quality_service  # noqa: F401
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.grupo_b_schema import ensure_grupo_b_tables
from tests.support.isolamento_v3 import truncar
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()  # fuso do usuario, nunca o do runner/MySQL (tests/support/relogio_usuario.py)


SUPINO = 1001
AGACHAMENTO = 1002


def _truncate(raw) -> None:
    """Isolamento: a lista de tabelas e COMPARTILHADA
    (`tests/support/isolamento_v3.py::TABELAS_V3`) e cobre tudo que a suite v3
    le ou escreve - inclusive o que os SERVICOS leem por baixo do endpoint, nao
    so o que este seed insere. Manter a lista por arquivo foi como nasceram os
    dois vazamentos do PR #199."""
    truncar(raw)


@pytest.fixture
def seed(raw):
    """Cenario montado para violar as 10 regras do §8.3 de uma vez, para o
    usuario A. O usuario B fica sem NENHUM dado - e a prova de isolamento."""
    # `intervals_wellness` nasce na migracao 027 (grupo B), que o schema base
    # da suite nao aplica: sem isto o arquivo so passa se outro teste tiver
    # criado a tabela antes (ordem do diretorio), e quebra rodando sozinho.
    ensure_grupo_b_tables(raw)
    _truncate(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome, papel in (("dono", "admin"), ("outro", "regular")):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)", (nome, papel))
            ids[nome] = cur.lastrowid

        for grupo in ("Peito", "Triceps"):
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (grupo,))
            ids[grupo] = cur.lastrowid

        cur.execute("INSERT INTO gyms (name) VALUES ('Academia')")
        ids["gym"] = cur.lastrowid
        cur.execute("INSERT INTO equipment (name) VALUES ('Banco reto')")
        ids["banco"] = cur.lastrowid

        for exercise_id, nome in ((SUPINO, "Supino reto"), (AGACHAMENTO, "Agachamento livre")):
            cur.execute(
                "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                (exercise_id, nome, nome),
            )
            cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, nome))

        # SUPINO distribui estimulo para dois grupos; AGACHAMENTO nao tem
        # NENHUMA contribuicao (regra MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION).
        for grupo, papel, peso in (("Peito", "PRIMARY", "1.00"), ("Triceps", "SECONDARY", "0.50")):
            cur.execute(
                "INSERT INTO exercise_muscle_contributions "
                "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight, is_active) "
                "VALUES (%s, %s, %s, %s, %s, 1)",
                (SUPINO, ids[grupo], papel, peso, peso),
            )

        # par usado pelo historico e SEM convencao de carga cadastrada
        cur.execute(
            "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id) VALUES (%s, %s, %s)",
            (ids["gym"], SUPINO, ids["banco"]),
        )
        ids["ge_banco"] = cur.lastrowid
        cur.execute(
            "INSERT INTO equipment_exercises (equipment_id, exercise_id, is_active) VALUES (%s, %s, 1)",
            (ids["banco"], SUPINO),
        )

        # tipo de treino com meta SO para Peito (Triceps cai no fallback)
        cur.execute(
            "INSERT INTO training_types (name, created_by_user_id) VALUES ('Hipertrofia', %s)", (ids["dono"],)
        )
        ids["training_type"] = cur.lastrowid
        cur.execute(
            "INSERT INTO training_type_muscle_group_targets "
            "(training_type_id, muscle_group_id, min_weekly_sets, target_weekly_sets, max_weekly_sets) "
            "VALUES (%s, %s, 10, 14, 20)",
            (ids["training_type"], ids["Peito"]),
        )

        # Modelo atribuido ao usuario A: e o que torna "sessao sem ficha" um
        # problema (sem Modelo nao ha o que aderir)
        cur.execute(
            "INSERT INTO workout_plan_templates (name, template_type, training_type_id, created_by_user_id) "
            "VALUES ('ABC', 'abc', %s, %s)",
            (ids["training_type"], ids["dono"]),
        )
        ids["template"] = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_template_assignments (template_id, user_id, assigned_by_user_id) "
            "VALUES (%s, %s, %s)",
            (ids["template"], ids["dono"], ids["dono"]),
        )

        # --- sessao 1: completa, com serie sem RPE e serie de tipo desconhecido
        dia1 = HOJE - _dt.timedelta(days=14)
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, session_status) "
            "VALUES (%s, %s, %s, 'Treino A', 'done')",
            (ids["dono"], dia1, _dt.datetime.combine(dia1, _dt.time(19, 0))),
        )
        atividade1 = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, equipment_id, sets_count) "
            "VALUES (%s, 1, 'Supino reto', %s, %s, 3)",
            (atividade1, SUPINO, ids["ge_banco"]),
        )
        supino_row = cur.lastrowid
        for numero, tipo, rpe in ((1, "working", "8.00"), (2, "working", None), (3, "myo_reps", "9.00")):
            cur.execute(
                "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
                "VALUES (%s, %s, %s, 10, '60.00', %s)",
                (supino_row, numero, tipo, rpe),
            )
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) "
            "VALUES (%s, 2, 'Agachamento livre', %s, 1)",
            (atividade1, AGACHAMENTO),
        )
        agacho_row = cur.lastrowid
        cur.execute(
            "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
            "VALUES (%s, 1, 'working', 10, '80.00', '8.00')",
            (agacho_row,),
        )

        # --- sessao 2: legado (sem status, sem started_at) e com exercicio
        # que nao resolve para o catalogo canonico
        dia2 = HOJE - _dt.timedelta(days=7)
        cur.execute(
            "INSERT INTO activities (user_id, local_date, name) VALUES (%s, %s, 'Treino importado')",
            (ids["dono"], dia2),
        )
        atividade2 = cur.lastrowid
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) "
            "VALUES (%s, 1, 'Exercicio desconhecido', NULL, 1)",
            (atividade2,),
        )
        sem_mapa = cur.lastrowid
        cur.execute(
            "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
            "VALUES (%s, 1, 'working', 10, '20.00', '7.00')",
            (sem_mapa,),
        )

        # --- pesagens divergentes no mesmo dia, de fontes diferentes
        dia_peso = HOJE - _dt.timedelta(days=5)
        cur.execute(
            "INSERT INTO body_composition (user_id, local_date, weight_kg, source) VALUES (%s, %s, '80.00', 'balanca')",
            (ids["dono"], dia_peso),
        )
        cur.execute(
            "INSERT INTO daily_biometrics (user_id, local_date, weight_kg) VALUES (%s, %s, '82.40')",
            (ids["dono"], dia_peso),
        )
    return ids


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(analytics_dashboard.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=user_id, role="regular", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def _data_quality(user_id: int, **params):
    return _client(user_id).get("/analytics/dashboard/data-quality", params=params)


def _por_codigo(corpo) -> dict[str, dict]:
    return {item["code"]: item for item in corpo["items"]}


# ---------------------------------------------------------------------------


def test_payload_identico_nos_dois_backends_com_as_dez_regras(seed, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _data_quality(seed["dono"])
        assert resposta.status_code == 200, (backend, resposta.text)
        resultados[backend] = resposta.json()

    assert resultados["pymysql"] == resultados["data-api"]

    corpo = resultados["pymysql"]
    itens = _por_codigo(corpo)
    assert set(itens) == {
        "EXERCISES_WITHOUT_CANONICAL_MAPPING",
        "MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION",
        "SETS_WITH_UNKNOWN_SET_TYPE",
        "EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION",
        "SETS_WITHOUT_RPE",
        "SESSIONS_WITHOUT_FINAL_STATUS",
        "SESSIONS_NOT_LINKED_TO_PLAN",
        "DUPLICATE_WEIGHT_SAME_DAY",
        "ACTIVITIES_WITHOUT_STARTED_AT",
        "MUSCLE_GROUPS_WITHOUT_TARGET",
    }

    assert itens["EXERCISES_WITHOUT_CANONICAL_MAPPING"]["count"] == 1
    assert itens["MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION"]["count"] == 1
    assert itens["SETS_WITH_UNKNOWN_SET_TYPE"]["count"] == 1
    assert itens["EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION"]["count"] == 1
    assert itens["SETS_WITHOUT_RPE"]["count"] == 1  # so a serie WORKING sem RPE
    assert itens["SESSIONS_WITHOUT_FINAL_STATUS"]["count"] == 1
    assert itens["SESSIONS_NOT_LINKED_TO_PLAN"]["count"] == 2
    assert itens["DUPLICATE_WEIGHT_SAME_DAY"]["count"] == 1
    assert itens["ACTIVITIES_WITHOUT_STARTED_AT"]["count"] == 1
    assert itens["MUSCLE_GROUPS_WITHOUT_TARGET"]["count"] == 1  # Triceps sem meta

    # ha regra ACTION -> veredito LOW; a confianca na resposta segue HIGH
    assert corpo["overall"] == "LOW"
    assert corpo["data_quality"] == "HIGH"
    assert corpo["checked_rules"] == 10
    assert corpo["metric_versions"]["data_quality"] == "dq-v1"
    assert corpo["window"]["timezone"] == "America/Sao_Paulo"


def test_escopo_catalog_ignora_a_janela_curta(seed, db_backend):
    """Janela de 7 dias: as regras de JANELA perdem as ocorrencias antigas, as
    de CADASTRO continuam (a lacuna nao expira com o periodo)."""
    corpo = _data_quality(seed["dono"], window="7d").json()
    itens = _por_codigo(corpo)

    assert "SETS_WITH_UNKNOWN_SET_TYPE" not in itens, db_backend  # serie de 14 dias atras
    assert itens["MAPPINGS_WITHOUT_MUSCLE_CONTRIBUTION"]["scope"] == "catalog", db_backend
    assert itens["EQUIPMENT_PAIRS_WITHOUT_LOAD_CONVENTION"]["scope"] == "catalog", db_backend
    assert itens["MUSCLE_GROUPS_WITHOUT_TARGET"]["scope"] == "catalog", db_backend
    assert any("cadastro" in limite for limite in corpo["limitations"]), db_backend


def test_idor_usuario_sem_historico_nao_ve_nada_do_outro(seed, db_backend):
    resposta = _data_quality(seed["outro"])
    assert resposta.status_code == 200, db_backend
    corpo = resposta.json()

    assert corpo["items"] == [], db_backend
    assert corpo["overall"] == "HIGH", db_backend
    texto = json.dumps(corpo, ensure_ascii=False)
    for vazamento in ("Supino", "Agachamento", "Exercicio desconhecido", "Treino importado", "Banco reto"):
        assert vazamento not in texto, (vazamento, db_backend)


def test_itens_trazem_fix_link_severidade_e_exemplos_curtos(seed, db_backend):
    itens = _por_codigo(_data_quality(seed["dono"]).json())

    assert itens["EXERCISES_WITHOUT_CANONICAL_MAPPING"]["fix_link"] == "/dominio-exercicios", db_backend
    assert itens["EXERCISES_WITHOUT_CANONICAL_MAPPING"]["severity"] == "ACTION", db_backend
    # informativo continua informativo, com qualquer contagem
    assert itens["SETS_WITHOUT_RPE"]["severity"] == "INFO", db_backend

    for item in itens.values():
        assert item["description"], db_backend
        assert len(item["examples"]) <= 5, db_backend
        for exemplo in item["examples"]:
            assert set(exemplo) == {"id", "label", "detail"}, db_backend


def test_janela_invalida_vira_400(seed, db_backend):
    assert _data_quality(seed["dono"], window="cycle").status_code == 400, db_backend
    assert _data_quality(seed["dono"], window="42d").status_code == 400, db_backend
