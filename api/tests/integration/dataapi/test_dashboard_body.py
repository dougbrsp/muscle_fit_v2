"""`GET /analytics/dashboard/body` (F1.4) contra MySQL real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API de producao).

O que este arquivo prova:

- o payload e IDENTICO nos dois backends e fecha no `response_model`;
- peso: prioridade por dia entre as fontes e media movel so sobre dias com
  pesagem (as MESMAS funcoes da visao geral);
- registrar uma cintura muda o sinal: sem cintura `insufficient_data`; com
  duas medidas no protocolo caindo 1,5 cm em 27 dias, peso estavel e os tres
  exercicios progredindo -> `compatible`; a medida fora do protocolo entra
  na serie com LOW e NAO conta;
- a visao geral passa a mostrar a cintura no bloco Corpo e a limitacao
  "cintura nao disponivel" SAI; o insight RECOMPOSITION_COMPATIBLE sobe para
  MEDIUM;
- IDOR: o usuario B (sem historico) recebe 200 com tudo `INSUFFICIENT` e
  NADA do usuario A;
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
from app.body_measurements_schema import reset_body_measurements_table_cache
from app.intelligence import muscle_recovery_service, recovery_read_model, volume_service  # noqa: F401
from app.intelligence.dashboard import (  # noqa: F401
    adherence_service,
    body_service,
    data_quality_service,
    exposure_service,
    muscle_summary_service,
    overview_service,
    recovery_signals_service,
)
from app.intelligence.dashboard.insight_rules import WAIST_UNAVAILABLE_LIMITATION
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.grupo_b_schema import ensure_grupo_b_tables
from tests.support.isolamento_v3 import truncar
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()  # fuso do usuario, nunca o do runner/MySQL


SUPINO = 4001
REMADA = 4002
AGACHAMENTO = 4003


def _truncate(raw) -> None:
    """Isolamento: a lista de tabelas e COMPARTILHADA
    (`tests/support/isolamento_v3.py::TABELAS_V3`) e cobre tudo que a suite v3
    le ou escreve - inclusive o que os SERVICOS leem por baixo do endpoint, nao
    so o que este seed insere. Manter a lista por arquivo foi como nasceram os
    dois vazamentos do PR #199."""
    truncar(raw)


@pytest.fixture
def seed(raw):
    """Usuario A: 9 sessoes em 27 dias com tres exercicios PROGREDINDO
    (+3 kg por sessao); peso diario estavel (80,0 ± 0,2 kg, daily_biometrics)
    com body_composition a cada 7 dias (80,0 kg + bioimpedancia). SEM
    cintura - o teste registra. Usuario B: nada."""
    ensure_grupo_b_tables(raw)
    reset_body_measurements_table_cache()
    _truncate(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome, papel in (("dono", "admin"), ("outro", "regular")):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)", (nome, papel))
            ids[nome] = cur.lastrowid

        for grupo in ("Peito", "Costas", "Quadriceps"):
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (grupo,))
            ids[grupo] = cur.lastrowid

        catalogo = ((SUPINO, "Supino reto", "Peito"), (REMADA, "Remada curvada", "Costas"), (AGACHAMENTO, "Agachamento", "Quadriceps"))
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

        # 9 sessoes, uma a cada 3 dias, terminando hoje (dia 0): dias 24..0.
        for indice in range(9):
            dia = HOJE - _dt.timedelta(days=(8 - indice) * 3)
            cur.execute(
                "INSERT INTO activities (user_id, local_date, started_at, name, activity_type, session_status) "
                "VALUES (%s, %s, %s, 'Treino', 'musculacao', 'done')",
                (ids["dono"], dia, _dt.datetime.combine(dia, _dt.time(19, 0))),
            )
            atividade = cur.lastrowid
            exercicios = [(SUPINO, 60 + 3 * indice), (REMADA, 50 + 3 * indice), (AGACHAMENTO, 80 + 3 * indice)]
            for sequencia, (mapping_id, carga) in enumerate(exercicios, start=1):
                cur.execute(
                    "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) "
                    "VALUES (%s, %s, 'x', %s, 3)",
                    (atividade, sequencia, mapping_id),
                )
                exercicio = cur.lastrowid
                for numero in (1, 2, 3):
                    cur.execute(
                        "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe) "
                        "VALUES (%s, %s, 'working', 10, %s, '8.00')",
                        (exercicio, numero, f"{carga:.2f}"),
                    )

        # Pesagens: daily_biometrics todo dia (80,0 ± 0,2), body_composition a
        # cada 7 dias com 80,0 e bioimpedancia - no dia em que as duas
        # existem, vence body_composition.
        for offset in range(0, 35):
            dia = HOJE - _dt.timedelta(days=offset)
            cur.execute(
                "INSERT INTO daily_biometrics (user_id, local_date, weight_kg) VALUES (%s, %s, %s)",
                (ids["dono"], dia, "80.20" if offset % 2 else "79.80"),
            )
            if offset % 7 == 0:
                cur.execute(
                    "INSERT INTO body_composition (user_id, local_date, weight_kg, body_fat_percent, muscle_mass_kg, body_water_percent) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (ids["dono"], dia, "80.00", f"{22.5 - 0.1 * (34 - offset) / 7:.2f}", "38.00", "55.00"),
                )
    raw.commit()
    return ids


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(analytics_dashboard.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _body(user_id: int, **params):
    return _client(user_id).get("/analytics/dashboard/body", params=params)


def cliente_insights(user_id: int, window: str = "28d") -> list[dict]:
    resposta = _client(user_id).get("/analytics/dashboard/insights", params={"window": window})
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["items"]


def _registrar_cintura(raw, user_id: int, dias_atras: int, cm: float, protocol_ok: bool = True) -> None:
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO body_measurements (user_id, local_date, site, value_cm, protocol_ok) VALUES (%s, %s, 'waist', %s, %s) "
            "ON DUPLICATE KEY UPDATE value_cm = VALUES(value_cm), protocol_ok = VALUES(protocol_ok)",
            (user_id, HOJE - _dt.timedelta(days=dias_atras), f"{cm:.1f}", 1 if protocol_ok else 0),
        )
    raw.commit()


# ---------------------------------------------------------------------------


def test_payload_identico_nos_dois_backends(seed, raw, monkeypatch, fake_rds, mysql_schema):
    _registrar_cintura(raw, seed["dono"], 27, 90.0)
    _registrar_cintura(raw, seed["dono"], 0, 88.5)
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        resposta = _body(seed["dono"], window="28d")
        assert resposta.status_code == 200, (backend, resposta.text)
        resultados[backend] = resposta.json()

    assert resultados["pymysql"] == resultados["data-api"]
    corpo = resultados["pymysql"]
    assert corpo["window"]["kind"] == "28d"
    assert corpo["timezone"] == "America/Sao_Paulo"
    assert corpo["metric_versions"]["body_weight_ma7"] == "weight-ma-v1"
    assert corpo["metric_versions"]["waist_cm"] == "waist-v1"
    assert corpo["metric_versions"]["recomposition_signal"] == "recomp-v1"
    assert set(corpo) >= {"weight", "waist", "measurements", "bioimpedance", "recomposition", "performance", "milestones"}


def test_peso_prioriza_fonte_por_dia_e_nao_zera_dias_sem_pesagem(seed, db_backend):
    corpo = _body(seed["dono"], window="28d").json()
    peso = corpo["weight"]
    assert peso["weighings_count"] == 28, db_backend
    hoje = next(p for p in peso["points"] if p["local_date"] == HOJE.isoformat())
    assert hoje["weight_kg"] == 80.0 and hoje["source"] == "body_composition"
    ontem = next(p for p in peso["points"] if p["local_date"] == (HOJE - _dt.timedelta(days=1)).isoformat())
    assert ontem["source"] == "daily_biometrics"
    assert hoje["ma7_points"] == 7
    assert all(p["weight_kg"] > 0 for p in peso["points"])
    assert peso["data_quality"] == "HIGH"
    assert abs(peso["change_pct"]["value"]) <= 1.0
    # Bioimpedancia: 4 leituras na janela (dias 0, 7, 14, 21) -> tendencia, ESTIMATED.
    bio = corpo["bioimpedance"]
    assert bio["readings_count"] == 4
    assert bio["body_fat_percent"]["nature"] == "ESTIMATED"
    assert bio["body_fat_change"]["value"] is not None
    assert bio["data_quality"] == "MEDIUM"
    # Desempenho: 3 exercicios comparaveis progredindo, um ponto por sessao.
    assert corpo["performance"]["comparable_exercises"] == 3
    assert corpo["performance"]["progressing_or_stable"] == 3
    # um ponto por sessao, ate o teto de exposicoes por exercicio do lote
    assert len(corpo["performance"]["points"]) == min(9, exposure_service.MAX_BATCH_EXPOSURES_PER_EXERCISE)
    assert corpo["performance"]["points"][-1]["exercises"] == 3


def test_registrar_uma_cintura_muda_o_sinal(seed, raw, db_backend):
    # 1) Sem cintura: insuficiente, e a limitacao diz que falta a medida.
    antes = _body(seed["dono"], window="28d").json()
    assert antes["waist"]["available"] is False
    assert antes["waist"]["table_available"] is True
    assert antes["waist"]["latest"]["value"] is None
    assert antes["recomposition"]["status"] == "insufficient_data", db_backend
    checks = {c["code"]: c["result"] for c in antes["recomposition"]["checks"]}
    assert checks == {"weight_stable": True, "waist_dropping": None, "performance_kept": True}
    assert body_service.WAIST_NOT_RECORDED_LIMITATION in antes["recomposition"]["limitations"]
    assert WAIST_UNAVAILABLE_LIMITATION not in antes["waist"]["limitations"]

    # 2) Uma cintura so: continua insuficiente (a variacao precisa de duas).
    _registrar_cintura(raw, seed["dono"], 27, 90.0)
    uma = _body(seed["dono"], window="28d").json()
    assert uma["waist"]["available"] is True
    assert uma["waist"]["latest"]["value"] == 90.0
    assert uma["recomposition"]["status"] == "insufficient_data"

    # 3) Fora do protocolo nao conta, mesmo caindo 5 cm.
    _registrar_cintura(raw, seed["dono"], 10, 85.0, protocol_ok=False)
    fora = _body(seed["dono"], window="28d").json()
    assert fora["recomposition"]["status"] == "insufficient_data"
    ponto_fora = next(p for p in fora["waist"]["points"] if not p["protocol_ok"])
    assert ponto_fora["data_quality"] == "LOW"
    assert fora["waist"]["latest"]["value"] == 90.0  # ultima NO PROTOCOLO
    assert fora["waist"]["out_of_protocol_points"] == 1

    # 4) Segunda medida no protocolo, 1,5 cm abaixo: compativel.
    _registrar_cintura(raw, seed["dono"], 0, 88.5)
    depois = _body(seed["dono"], window="28d").json()
    assert depois["waist"]["latest"]["value"] == 88.5
    assert depois["waist"]["change"]["value"] == -1.5
    assert depois["waist"]["change_span_days"] == 27
    sinal = depois["recomposition"]
    assert sinal["status"] == "compatible", sinal
    assert sinal["nature"] == "INFERRED"
    assert sinal["data_quality"] == "MEDIUM"
    assert "compatível com recomposição" in sinal["answer"].lower()
    assert "confirmad" not in sinal["answer"].lower()
    assert {e["metric"] for e in sinal["evidence"]} == {"body_weight_ma7", "waist_cm", "progress_share"}
    assert body_service.RECOMPOSITION_NEVER_PROOF_LIMITATION in sinal["limitations"]
    assert sinal["window"]["days"] == 28

    # 5) Na janela de 90 dias o sinal continua na janela PROPRIA de 28 dias.
    noventa = _body(seed["dono"], window="90d").json()
    assert noventa["window"]["days"] == 90
    assert noventa["recomposition"]["window"]["days"] == 28
    assert noventa["recomposition"]["status"] == "compatible"

    # 6) Cintura PARADA (cai so 0,5 cm): as tres verificacoes sao avaliaveis e
    #    uma falha -> `not_compatible` com o veredito explicado, NAO
    #    `insufficient_data`. E o caso "manutencao": a home silencia, mas esta
    #    tela mostra (decisao do usuario, 2026-09-24).
    _registrar_cintura(raw, seed["dono"], 0, 89.5)
    parada = _body(seed["dono"], window="28d").json()["recomposition"]
    assert parada["status"] == "not_compatible", parada
    assert [c["result"] for c in parada["checks"]] == [True, False, True]
    assert parada["answer"] == (
        "Não compatível com recomposição: o peso ficou estável e o desempenho se manteve, "
        "mas a cintura não caiu nos últimos 28 dias."
    )
    assert "descartad" not in parada["answer"].lower()
    assert {e["metric"] for e in parada["evidence"]} == {"body_weight_ma7", "waist_cm", "progress_share"}
    assert parada["data_quality"] != "INSUFFICIENT"

    # O insight, no entanto, continua CALADO nesse cenario.
    insights = cliente_insights(seed["dono"])
    assert not any(i["code"] == "RECOMPOSITION_COMPATIBLE" for i in insights), insights


def test_visao_geral_usa_o_mesmo_servico_e_tira_a_limitacao_de_cintura(seed, raw, db_backend):
    cliente = _client(seed["dono"])
    sem = cliente.get("/analytics/dashboard/overview", params={"window": "28d"}).json()["blocks"]["body"]
    assert sem["waist_cm"]["value"] is None
    assert body_service.WAIST_NOT_RECORDED_LIMITATION in sem["limitations"]
    assert sem["drilldown_href"] == "/dashboard/corpo"

    _registrar_cintura(raw, seed["dono"], 27, 90.0)
    _registrar_cintura(raw, seed["dono"], 0, 88.5)
    overview = cliente.get("/analytics/dashboard/overview", params={"window": "28d"}).json()
    bloco = overview["blocks"]["body"]
    assert bloco["waist_cm"]["value"] == 88.5, db_backend
    assert bloco["waist_cm"]["nature"] == "OBSERVED"
    assert not any("intura" in l for l in bloco["limitations"])

    # Mesmos numeros de peso nas duas rotas (uma implementacao).
    corpo = _body(seed["dono"], window="28d").json()
    assert bloco["weight_points"] == corpo["weight"]["points"]
    assert bloco["weight_ma7_latest"] == corpo["weight"]["ma7_latest"]

    # O insight de recomposicao passa a usar a cintura (MEDIUM, sem a limitacao antiga).
    insights = cliente.get("/analytics/dashboard/insights", params={"window": "28d"}).json()["items"]
    recomp = next(i for i in insights if i["code"] == "RECOMPOSITION_COMPATIBLE")
    assert recomp["data_quality"] == "MEDIUM"
    assert WAIST_UNAVAILABLE_LIMITATION not in recomp["limitations"]
    assert any(e["metric"] == "waist_cm" for e in recomp["evidence"])


def test_idor_usuario_sem_historico_nao_ve_nada_do_outro(seed, raw, db_backend):
    _registrar_cintura(raw, seed["dono"], 0, 88.5)
    resposta = _body(seed["outro"], window="28d")
    assert resposta.status_code == 200, db_backend
    corpo = resposta.json()
    assert corpo["data_quality"] == "INSUFFICIENT"
    assert corpo["weight"]["points"] == [] and corpo["weight"]["latest"]["value"] is None
    assert corpo["waist"]["available"] is False and corpo["waist"]["points"] == []
    assert corpo["measurements"] == []
    assert corpo["bioimpedance"]["readings_count"] == 0
    assert corpo["recomposition"]["status"] == "insufficient_data"
    assert corpo["performance"]["points"] == []
    assert corpo["milestones"] == []


def test_janela_invalida_responde_400(seed, db_backend):
    assert _body(seed["dono"], window="1000d").status_code == 400


def test_payload_cabe_no_limite_da_data_api(seed, raw, db_backend):
    for dias in range(0, 28, 3):
        _registrar_cintura(raw, seed["dono"], dias, 90.0 - dias * 0.05)
    resposta = _body(seed["dono"], window="90d")
    assert resposta.status_code == 200
    assert len(json.dumps(resposta.json(), ensure_ascii=False).encode("utf-8")) < 1_000_000
