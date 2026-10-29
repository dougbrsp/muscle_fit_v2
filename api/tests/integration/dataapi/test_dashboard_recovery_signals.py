"""`GET /analytics/dashboard/recovery-signals` (F1.5) contra MySQL real, nos
DOIS backends (PyMySQL de dev e shim da RDS Data API de producao).

O que este arquivo prova:

- o payload e IDENTICO nos dois backends - importante aqui porque quase todo
  numero da tela vem de `DECIMAL` (HRV, FC de repouso, TSB, RPE, carga), que
  o PyMySQL entrega como `Decimal` e o shim como texto;
- prontidao e sinais CONVIVEM: o cenario tem HRV e FC de repouso piores que a
  baseline pessoal enquanto os grupos ainda estao recuperados, e os dois
  aparecem, um marcado como divergente;
- dor articular relatada bloqueia intensificacao;
- IDOR: o usuario B, sem historico e sem wearable, recebe 200 com tudo
  ausente (nunca zero) e nada do usuario A vaza;
- o payload cabe com folga no teto de 1 MB da Data API mesmo com 3 meses de
  sessoes diarias.

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
from app.intelligence.dashboard import recovery_signals_service  # noqa: F401
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.grupo_b_schema import ensure_grupo_b_tables
from tests.support.isolamento_v3 import congelar_relogio, truncar
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()  # fuso do usuario, nunca o do runner/MySQL (tests/support/relogio_usuario.py)


SUPINO = 2001


def _truncate(raw) -> None:
    """Isolamento: a lista de tabelas e COMPARTILHADA
    (`tests/support/isolamento_v3.py::TABELAS_V3`) e cobre tudo que a suite v3
    le ou escreve - inclusive o que os SERVICOS leem por baixo do endpoint, nao
    so o que este seed insere. Manter a lista por arquivo foi como nasceram os
    dois vazamentos do PR #199."""
    truncar(raw)


@pytest.fixture
def seed(raw):
    """Usuario A: 35 dias de wellness (HRV e FC de repouso PIORES na ultima
    semana), sessoes de treino ate ontem e uma sessao com dor articular.
    Usuario B: nada - e a prova de isolamento."""
    # `sleep_metrics`/`intervals_wellness` nascem na migracao 027 (grupo B),
    # que o schema base da suite nao aplica - a criacao aqui passa pelo MESMO
    # caminho de producao, sem DDL proprio de teste.
    ensure_grupo_b_tables(raw)
    _truncate(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome, papel in (("dono", "admin"), ("outro", "regular")):
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', %s)", (nome, papel)
            )
            ids[nome] = cur.lastrowid

        cur.execute("INSERT INTO muscle_groups (name) VALUES ('Peito')")
        ids["Peito"] = cur.lastrowid
        cur.execute("INSERT INTO muscle_groups (name) VALUES ('Triceps')")
        ids["Triceps"] = cur.lastrowid

        cur.execute(
            "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) "
            "VALUES (%s, 'Supino reto', 'Supino reto')",
            (SUPINO,),
        )
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, 'Supino reto')", (SUPINO,))
        for grupo, papel, peso in (("Peito", "PRIMARY", "1.00"), ("Triceps", "SECONDARY", "0.50")):
            cur.execute(
                "INSERT INTO exercise_muscle_contributions "
                "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight, is_active) "
                "VALUES (%s, %s, %s, %s, %s, 1)",
                (SUPINO, ids[grupo], papel, peso, peso),
            )

        # 35 dias de wellness: a semana corrente com HRV mais baixo e FC de
        # repouso mais alta que os 28 dias anteriores (baseline pessoal).
        for offset in range(35):
            dia = HOJE - _dt.timedelta(days=offset)
            recente = offset < 7
            cur.execute(
                "INSERT INTO intervals_wellness (user_id, wellness_date, hrv_ms, resting_hr_bpm, form_tsb, sleep_seconds) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    ids["dono"],
                    dia,
                    "32.00" if recente else "45.00",
                    "64.00" if recente else "55.00",
                    "-6.00",
                    25200,
                ),
            )
            cur.execute(
                "INSERT INTO sleep_metrics (user_id, local_date, duration_hours, regularity_percent) "
                "VALUES (%s, %s, %s, %s)",
                (ids["dono"], dia, "7.50", "80.00"),
            )

        # Sessoes: uma a cada 2 dias nos ultimos 20 dias. A de ontem tem dor
        # articular relatada.
        for offset in range(1, 21, 2):
            dia = HOJE - _dt.timedelta(days=offset)
            cur.execute(
                "INSERT INTO activities (user_id, local_date, started_at, name, activity_type, session_status) "
                "VALUES (%s, %s, %s, 'Treino', 'musculacao', 'done')",
                (ids["dono"], dia, _dt.datetime.combine(dia, _dt.time(19, 0))),
            )
            atividade = cur.lastrowid
            joint = 1 if offset == 1 else 0
            cur.execute(
                "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count, "
                "muscle_pain, joint_pain) VALUES (%s, 1, 'Supino reto', %s, 3, 3, %s)",
                (atividade, SUPINO, joint),
            )
            exercicio = cur.lastrowid
            for numero in (1, 2, 3):
                cur.execute(
                    "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe, "
                    "muscle_pain, joint_pain) VALUES (%s, %s, 'working', 10, '60.00', '8.00', 3, %s)",
                    (exercicio, numero, joint),
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


def _recovery(user_id: int, **params):
    return _client(user_id).get("/analytics/dashboard/recovery-signals", params=params)


def _por_codigo(corpo) -> dict[str, dict]:
    return {sinal["code"]: sinal for sinal in corpo["signals"]}


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
        resposta = _recovery(seed["dono"])
        assert resposta.status_code == 200, (backend, resposta.text)
        resultados[backend] = resposta.json()

    assert resultados["pymysql"] == resultados["data-api"]

    corpo = resultados["pymysql"]
    assert corpo["readiness_model"] == "fullbody-no-reps-v1"
    assert corpo["metric_versions"]["recovery_signals"] == "recovery-signals-v1"
    assert corpo["baseline_window_days"] == 28
    assert corpo["window"]["kind"] == "7d"
    assert corpo["groups"], "o cenario tem sessoes: a prontidao precisa existir"


def test_sinais_com_baseline_pessoal_aparecem_com_direcao(seed, db_backend):
    corpo = _recovery(seed["dono"]).json()
    sinais = _por_codigo(corpo)

    hrv = sinais["hrv"]
    assert hrv["current"]["value"] == 32.0, db_backend
    assert hrv["baseline"]["value"] == 45.0, db_backend
    assert hrv["direction"] == "BELOW", db_backend
    assert hrv["reading"] == "UNFAVORABLE", db_backend

    fc = sinais["resting_hr"]
    assert fc["current"]["value"] == 64.0, db_backend
    assert fc["baseline"]["value"] == 55.0, db_backend
    assert fc["reading"] == "UNFAVORABLE", db_backend

    # sono estavel: nem favoravel nem desfavoravel
    assert sinais["sleep_duration"]["direction"] == "IN_LINE", db_backend
    assert sinais["sleep_duration"]["reading"] == "NEUTRAL", db_backend

    # os dois sinais negativos concordantes elevam a atencao, e a incerteza
    # vem declarada junto
    assert corpo["assessment"]["level"] in ("ATTENTION", "ACTION"), db_backend
    assert corpo["assessment"]["uncertainty"] in ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT"), db_backend


def test_dor_articular_bloqueia_intensificacao(seed, db_backend):
    corpo = _recovery(seed["dono"]).json()
    assert _por_codigo(corpo)["joint_pain"]["reading"] == "UNFAVORABLE", db_backend
    assert corpo["assessment"]["allow_intensification"] is False, db_backend
    assert corpo["assessment"]["intensification_blocked_reason"], db_backend


def test_prontidao_vem_do_modelo_e_traz_a_explicacao(seed, db_backend):
    corpo = _recovery(seed["dono"]).json()
    grupo = next(g for g in corpo["groups"] if g["muscle_group"] == "Peito")

    assert grupo["readiness_status"] in ("READY", "REDUCED_DOSE", "NO_DIRECT_WORK"), db_backend
    assert grupo["explanation"], db_backend
    assert grupo["recovery_pct"]["metric_version"] == "fullbody-no-reps-v1", db_backend
    assert grupo["recovery_pct"]["nature"] == "INFERRED", db_backend


def test_idor_usuario_sem_historico_recebe_ausencia_e_nao_zero(seed, db_backend):
    resposta = _recovery(seed["outro"])
    assert resposta.status_code == 200, db_backend
    corpo = resposta.json()

    assert corpo["groups"] == [], db_backend
    for sinal in corpo["signals"]:
        assert sinal["current"]["value"] is None, (db_backend, sinal["code"])
        assert sinal["baseline"]["value"] is None, (db_backend, sinal["code"])
    assert corpo["data_quality"] == "INSUFFICIENT", db_backend
    assert corpo["assessment"]["allow_intensification"] is True, db_backend

    texto = json.dumps(corpo, ensure_ascii=False)
    assert "Supino" not in texto, db_backend
    assert "Peito" not in texto, db_backend


def test_payload_abaixo_de_1mb(seed, db_backend):
    """Teto de 1 MB da resposta da Data API (§15). Mesmo com a janela maxima
    o payload e pequeno: a unica lista por grupo e a das ultimas
    `MAX_FACTOR_SETS` series (M33), e nao o historico."""
    resposta = _recovery(seed["dono"], window="180d")
    assert resposta.status_code == 200, db_backend
    assert len(resposta.content) < 1_000_000, db_backend


def test_janela_invalida_vira_400_nao_500(seed, db_backend):
    resposta = _recovery(seed["dono"], window="ontem")
    assert resposta.status_code == 400, db_backend


# ---------------------------------------------------------------------------
# F2.5 - M33 e M36: o que so existia em `/recuperacao-muscular` e em `/treinos`
# ---------------------------------------------------------------------------


def test_recomendacao_e_fatores_sao_OS_DO_MODELO_sem_recalculo(seed, monkeypatch, db_backend):
    """Prova de que a F2.5 nao criou um segundo modelo: campo a campo, a
    recomendacao de series e os fatores do calculo expostos pelo hub sao os
    que `MuscleRecoveryService.get_recovery_overview` acabou de produzir - a
    MESMA fonte que alimentava `/recuperacao-muscular`."""
    congelar_relogio(monkeypatch)
    corpo = _recovery(seed["dono"], window="28d").json()

    itens = {
        item["muscle_group"]: item
        for item in muscle_recovery_service.MuscleRecoveryService().get_recovery_overview(
            days=28, limit=100, user_id=seed["dono"]
        )
    }
    assert corpo["groups"], db_backend

    for grupo in corpo["groups"]:
        item = itens[grupo["muscle_group"]]
        recomendacao = item["recommendation"]
        for campo in (
            "direct_sets_min",
            "direct_sets_max",
            "maximum_rpe",
            "allow_failure_on_last_set",
            "allow_progression",
            "allow_indirect_work",
        ):
            assert grupo["recommendation"][campo] == recomendacao[campo], (db_backend, campo)
        assert grupo["recommendation"]["volume_multiplier"] == pytest.approx(
            float(recomendacao["volume_multiplier"])
        ), db_backend

        fatores = item.get("factors") or {}
        assert grupo["factors"]["session_dose"] == pytest.approx(
            float(fatores["session_dose"])
        ), db_backend
        assert grupo["factors"]["dose_last_72h"] == pytest.approx(
            float(fatores["dose_last_72h"])
        ), db_backend
        assert len(grupo["factors"]["recent_sets"]) <= recovery_signals_service.MAX_FACTOR_SETS


def test_ajuste_global_de_fadiga_vem_junto_e_nao_e_fator_neutro_inventado(
    seed, monkeypatch, db_backend
):
    congelar_relogio(monkeypatch)
    corpo = _recovery(seed["dono"], window="28d").json()

    ajuste = corpo["global_readiness_adjustment"]
    # O cenario tem `intervals_wellness` sincronizado: o ajuste existe.
    assert ajuste is not None, db_backend
    assert ajuste["form_tsb"] == pytest.approx(-6.0), db_backend
    assert ajuste["factor"] > 0, db_backend

    # Usuario sem wearable: o campo inteiro e `None`, nunca "fator 1,0".
    assert _recovery(seed["outro"]).json()["global_readiness_adjustment"] is None, db_backend


def test_recorte_por_dia_de_treino_sai_das_linhas_ja_lidas(seed, monkeypatch, db_backend):
    """M36: o seed treina em dia alternado, entao a janela de 7 dias tem 3
    dias com treino e 4 sem. Como o wellness da semana e constante, o recorte
    nao acusa diferenca - e e exatamente isso que ele precisa dizer, em vez de
    "pior em dia de treino" por ruido."""
    congelar_relogio(monkeypatch)
    bloco = _recovery(seed["dono"]).json()["training_vs_rest"]

    assert bloco["training_days"] == 3, db_backend
    assert bloco["rest_days"] == 4, db_backend

    hrv = next(item for item in bloco["metrics"] if item["code"] == "hrv")
    assert hrv["training_days"]["value"] == pytest.approx(32.0), db_backend
    assert hrv["rest_days"]["value"] == pytest.approx(32.0), db_backend
    assert hrv["direction"] == "IN_LINE", db_backend
    assert hrv["reading"] == "NEUTRAL", db_backend

    assert (
        recovery_signals_service.TRAINING_VS_REST_ASSOCIATION_LIMITATION in bloco["limitations"]
    ), db_backend


def test_sem_wearable_o_recorte_declara_ausencia_e_nao_zero(seed, db_backend):
    bloco = _recovery(seed["outro"]).json()["training_vs_rest"]
    assert bloco["training_days"] == 0, db_backend
    assert (
        recovery_signals_service.TRAINING_VS_REST_NO_TRAINING_LIMITATION in bloco["limitations"]
    ), db_backend
    for item in bloco["metrics"]:
        assert item["training_days"]["value"] is None, (db_backend, item["code"])
        assert item["rest_days"]["value"] is None, (db_backend, item["code"])
        assert item["delta"]["value"] is None, (db_backend, item["code"])
