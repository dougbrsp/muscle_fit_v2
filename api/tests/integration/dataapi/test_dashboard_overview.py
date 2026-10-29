"""`GET /analytics/dashboard/overview` e `/insights` (F1.6) contra MySQL
real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API de producao).

O que este arquivo prova:

- o payload e IDENTICO nos dois backends (quase todo numero vem de DECIMAL);
- os 6 blocos respondem, cada um com a propria `data_quality` e um
  `drilldown_href`; o bloco Corpo le as tres fontes de peso com prioridade
  por dia e a media movel nao zera dias sem pesagem;
- cintura: este seed nao registra nenhuma, entao o bloco Corpo declara
  "nenhuma medida registrada" (a tabela da 047 EXISTE no schema de teste). O
  caso COM cintura e do arquivo da F1.4, `test_dashboard_body.py`;
- `next_actions` tem no maximo 2 itens e `/insights` traz todos, deduplicados
  por fingerprint, com paginacao;
- IDOR: o usuario B (sem historico) recebe 200 com blocos `INSUFFICIENT`,
  `next_actions.items: []` e NADA do usuario A;
- o payload cabe com folga no teto de 1 MB da Data API.

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
from app import analytics_dashboard  # noqa: F401
from app.intelligence import muscle_recovery_service, recovery_read_model, volume_service  # noqa: F401
from app.intelligence.dashboard import (  # noqa: F401
    adherence_service,
    data_quality_service,
    exposure_service,
    muscle_summary_service,
    overview_service,
    recovery_signals_service,
)
from app.intelligence.dashboard.body_service import WAIST_NOT_RECORDED_LIMITATION
from app.intelligence.dashboard.insight_rules import (
    WAIST_UNAVAILABLE_LIMITATION,
    disabled_rule_codes,
    enabled_rule_codes,
)
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.grupo_b_schema import ensure_grupo_b_tables
from tests.support.isolamento_v3 import congelar_relogio, truncar
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()  # fuso do usuario, nunca o do runner/MySQL (tests/support/relogio_usuario.py)


SUPINO = 3001
REMADA = 3002
ROSCA = 3003


def _truncate(raw) -> None:
    """Isolamento: a lista de tabelas e COMPARTILHADA
    (`tests/support/isolamento_v3.py::TABELAS_V3`) e cobre tudo que a suite v3
    le ou escreve - inclusive o que os SERVICOS leem por baixo do endpoint, nao
    so o que este seed insere. Manter a lista por arquivo foi como nasceram os
    dois vazamentos do PR #199."""
    truncar(raw)


@pytest.fixture
def seed(raw):
    """Usuario A: 8 sessoes em 24 dias com tres exercicios - supino
    progredindo, remada estagnada (mesma carga e reps 4x), rosca so uma vez;
    pesagens em duas fontes no mesmo dia (prioridade) e dias sem pesagem.
    Usuario B: nada."""
    ensure_grupo_b_tables(raw)
    _truncate(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome, papel in (("dono", "admin"), ("outro", "regular")):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)", (nome, papel))
            ids[nome] = cur.lastrowid

        for grupo in ("Peito", "Costas", "Biceps"):
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (grupo,))
            ids[grupo] = cur.lastrowid

        catalogo = ((SUPINO, "Supino reto", "Peito"), (REMADA, "Remada curvada", "Costas"), (ROSCA, "Rosca direta", "Biceps"))
        for exercise_id, nome, grupo in catalogo:
            cur.execute(
                "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                (exercise_id, nome, nome),
            )
            cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, nome))
            cur.execute(
                "INSERT INTO exercise_muscle_contributions "
                "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight, is_active) "
                "VALUES (%s, %s, 'PRIMARY', '1.00', '1.00', 1)",
                (exercise_id, ids[grupo]),
            )

        # 8 sessoes, uma a cada 3 dias, terminando ontem.
        for indice in range(8):
            dia = HOJE - _dt.timedelta(days=1 + (7 - indice) * 3)
            cur.execute(
                "INSERT INTO activities (user_id, local_date, started_at, name, activity_type, session_status) "
                "VALUES (%s, %s, %s, 'Treino', 'musculacao', 'done')",
                (ids["dono"], dia, _dt.datetime.combine(dia, _dt.time(19, 0))),
            )
            atividade = cur.lastrowid
            # +3 kg por sessao no supino: e1RM sobe >= 3% a cada exposicao
            # (regra §8.2 `e1rm_progress_pct`), inclusive na ultima (78 -> 81).
            exercicios = [(SUPINO, 60 + 3 * indice, 10), (REMADA, 50, 10)]
            if indice == 7:
                exercicios.append((ROSCA, 20, 12))
            for sequencia, (mapping_id, carga, reps) in enumerate(exercicios, start=1):
                cur.execute(
                    "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) "
                    "VALUES (%s, %s, 'x', %s, 3)",
                    (atividade, sequencia, mapping_id),
                )
                exercicio = cur.lastrowid
                for numero in (1, 2, 3):
                    cur.execute(
                        "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
                        "VALUES (%s, %s, 'working', %s, %s, '8.00')",
                        (exercicio, numero, reps, f"{carga:.2f}"),
                    )

        # Pesagens: body_composition a cada 4 dias (com bioimpedancia) e
        # daily_biometrics todo dia com valor 1 kg maior - no dia em que as
        # duas existem, vence body_composition.
        for offset in range(0, 21):
            dia = HOJE - _dt.timedelta(days=offset)
            cur.execute(
                "INSERT INTO daily_biometrics (user_id, local_date, weight_kg) VALUES (%s, %s, %s)",
                (ids["dono"], dia, "81.00"),
            )
            if offset % 4 == 0:
                cur.execute(
                    "INSERT INTO body_composition (user_id, local_date, weight_kg, body_fat_percent, muscle_mass_kg) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (ids["dono"], dia, "80.00", "22.50", "38.00"),
                )
    raw.commit()
    return ids


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(analytics_dashboard.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=user_id, role="regular", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def _overview(user_id: int, **params):
    return _client(user_id).get("/analytics/dashboard/overview", params=params)


def _insights(user_id: int, **params):
    return _client(user_id).get("/analytics/dashboard/insights", params=params)


# ---------------------------------------------------------------------------


def test_payload_identico_nos_dois_backends(seed, monkeypatch, fake_rds, mysql_schema):
    # Relogio congelado: `hours_since_last_stimulus`, `remaining_hours`,
    # `recovery_pct` e `reassess_after_hours` saem de `now - last_trained_at` e
    # sao arredondados a 2 casas de HORA (degrau de 36 s). Sem congelar, as
    # duas chamadas caem em lados diferentes de um degrau de vez em quando e a
    # comparacao vira ruido - foi o flake do CI no PR #199 (medido: duas
    # chamadas a 40 s de distancia divergem em 5 campos).
    congelar_relogio(monkeypatch)

    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _overview(seed["dono"])
        assert resposta.status_code == 200, (backend, resposta.text)
        resultados[backend] = resposta.json()

    assert resultados["pymysql"] == resultados["data-api"]

    corpo = resultados["pymysql"]
    assert corpo["window"]["kind"] == "28d"
    assert corpo["timezone"] == "America/Sao_Paulo"
    assert set(corpo["blocks"]) == {"consistency", "progression", "priority_muscles", "body", "recovery", "next_actions"}
    for bloco in corpo["blocks"].values():
        assert bloco["question"] and bloco["answer"]
        assert bloco["drilldown_href"].startswith("/")
        assert bloco["data_quality"] in {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT"}
    assert corpo["metric_versions"]["insight_rules"] == "insight-rules-v2"
    assert corpo["metric_versions"]["body_weight_ma7"] == "weight-ma-v1"


def test_bloco_progressao_conta_por_status_sem_score(seed, db_backend):
    corpo = _overview(seed["dono"]).json()
    bloco = corpo["blocks"]["progression"]
    assert bloco["exercises_total"] == 3, db_backend
    contagens = {c["status"]: c for c in bloco["counts"]}
    assert contagens["PROGRESSING"]["count"] == 1
    assert contagens["PROGRESSING"]["exercises"][0]["exercise_id"] == SUPINO
    assert contagens["PLATEAU_SUSPECTED"]["count"] == 1
    assert contagens["PLATEAU_SUSPECTED"]["exercises"][0]["exercise_id"] == REMADA
    assert contagens["INSUFFICIENT_DATA"]["count"] == 1
    assert bloco["comparable_total"] == 2
    assert bloco["exposures_per_exercise"] == exposure_service.MAX_BATCH_EXPOSURES_PER_EXERCISE
    assert bloco["truncated"] is False
    assert "score" not in bloco


def test_bloco_corpo_prioriza_fonte_por_dia_e_nao_zera_dias_sem_pesagem(seed, db_backend):
    corpo = _overview(seed["dono"]).json()
    bloco = corpo["blocks"]["body"]
    assert bloco["weighings_count"] == 21, db_backend
    hoje = next(p for p in bloco["weight_points"] if p["local_date"] == HOJE.isoformat())
    assert hoje["weight_kg"] == 80.0 and hoje["source"] == "body_composition"
    ontem = next(p for p in bloco["weight_points"] if p["local_date"] == (HOJE - _dt.timedelta(days=1)).isoformat())
    assert ontem["weight_kg"] == 81.0 and ontem["source"] == "daily_biometrics"
    # Media de 7 dias do ultimo ponto: 7 pesagens (2 x 80 + 5 x 81).
    assert hoje["ma7_points"] == 7
    assert abs(hoje["ma7_kg"] - (2 * 80.0 + 5 * 81.0) / 7) < 0.01
    # Nenhum ponto com zero, nenhum dia sem pesagem na lista.
    assert all(p["weight_kg"] > 0 for p in bloco["weight_points"])
    assert bloco["weight_ma7_latest"]["nature"] == "DERIVED"
    assert bloco["body_fat_percent"]["value"] == 22.5
    assert bloco["body_fat_percent"]["nature"] == "ESTIMATED"
    # Cintura: a 047 JA existe no schema de teste (F1.4) e este arquivo NAO
    # registra nenhuma medida - entao a ausencia aqui significa "nenhuma medida
    # registrada", nao "tabela ausente". Asserir a limitacao EXATA (em vez de um
    # "Cintura" solto em qualquer texto) e o que distingue os dois estados: se a
    # 047 sumisse do schema de teste, a limitacao viraria a outra e este assert
    # falharia. O caso COM cintura - valor, natureza e a limitacao que SAI do
    # bloco - vive no arquivo da F1.4 (`test_dashboard_body.py::
    # test_visao_geral_usa_o_mesmo_servico_e_tira_a_limitacao_de_cintura`),
    # junto do resto da cobertura de cintura.
    assert bloco["waist_cm"]["value"] is None
    assert bloco["waist_cm"]["nature"] == "OBSERVED"
    assert bloco["waist_cm"]["data_quality"] == "INSUFFICIENT"
    assert bloco["waist_cm"]["limitations"] == [WAIST_NOT_RECORDED_LIMITATION]
    assert WAIST_UNAVAILABLE_LIMITATION not in bloco["limitations"]


def test_next_actions_maximo_dois_e_insights_traz_todos(seed, db_backend):
    corpo = _overview(seed["dono"]).json()
    proximas = corpo["blocks"]["next_actions"]
    assert len(proximas["items"]) <= proximas["max_items"] == 2

    todos = _insights(seed["dono"], limit=50).json()
    assert todos["total"] == proximas["total_triggered"]
    assert [i["fingerprint"] for i in todos["items"][:2]] == [i["fingerprint"] for i in proximas["items"]]
    assert len({i["fingerprint"] for i in todos["items"]}) == len(todos["items"])
    # Inventario de regras exposto na resposta. Ate a F2.1,
    # `PERFORMANCE_DROP_WITH_CONTEXT_EVENT` era a UNICA desligada (a tabela
    # `context_events` nao existia); a migracao 048 a ligou e trouxe
    # `TRAINING_PAUSE_DETECTED` junto, entao hoje nao ha regra desligada. O
    # numero fica fixado ao lado do `len()` de proposito: o `len()` mantem a
    # afirmacao honesta e o literal faz uma regra nova (ou perdida) aparecer
    # aqui em vez de passar batido.
    assert todos["rules_disabled"] == disabled_rule_codes() == []
    assert todos["rules_evaluated"] == len(enabled_rule_codes()) == 11
    assert "PERFORMANCE_DROP_WITH_CONTEXT_EVENT" in enabled_rule_codes()
    for insight in todos["items"]:
        assert insight["severity"] in {"INFO", "ATTENTION", "ACTION"}
        assert insight["question"] and insight["answer"] and insight["evidence"]
        assert insight["comparison"]["basis"]
        assert insight["source_screen"].startswith("/dashboard")

    # Paginacao simples.
    if todos["total"] > 1:
        pagina = _insights(seed["dono"], limit=1, offset=1).json()
        assert pagina["items"][0]["fingerprint"] == todos["items"][1]["fingerprint"]
        assert pagina["has_more"] == (todos["total"] > 2)


def test_blocos_de_consistencia_e_recuperacao(seed, db_backend):
    corpo = _overview(seed["dono"]).json()
    consistencia = corpo["blocks"]["consistency"]
    assert consistencia["has_template"] is False
    assert consistencia["completed_sessions"]["value"] == 8
    assert consistencia["rate"]["value"] is None  # sem Modelo: sem denominador inventado
    assert consistencia["data_quality"] == "LOW"

    recuperacao = corpo["blocks"]["recovery"]
    assert recuperacao["window"]["kind"] == "7d"
    assert recuperacao["groups_total"] >= 1
    assert recuperacao["level"] in {"INFO", "ATTENTION", "ACTION"}
    assert recuperacao["drilldown_href"] == "/dashboard/recovery"


def test_idor_usuario_sem_historico_nao_ve_nada_do_outro(seed, db_backend):
    corpo = _overview(seed["outro"]).json()
    blocos = corpo["blocks"]
    assert blocos["progression"]["exercises_total"] == 0
    assert blocos["progression"]["data_quality"] == "INSUFFICIENT"
    assert blocos["body"]["weighings_count"] == 0
    assert blocos["body"]["weight_latest"]["value"] is None
    assert blocos["consistency"]["completed_sessions"]["value"] == 0
    assert blocos["next_actions"]["items"] == []
    assert "Nenhuma ação sugerida agora" in blocos["next_actions"]["answer"]
    assert _insights(seed["outro"]).json()["total"] == 0


def test_janela_invalida_responde_400(seed, db_backend):
    assert _overview(seed["dono"], window="1y").status_code == 400
    assert _overview(seed["dono"], window="cycle").status_code == 400  # sem Modelo atribuido


def test_payload_cabe_no_limite_da_data_api(seed, db_backend):
    resposta = _overview(seed["dono"], window="180d")
    assert resposta.status_code == 200
    assert len(json.dumps(resposta.json()).encode("utf-8")) < 1_000_000
