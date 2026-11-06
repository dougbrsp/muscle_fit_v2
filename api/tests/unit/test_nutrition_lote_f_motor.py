"""Lote F do motor: dia confirmado exato por registro de execuções do sync e composição do gasto a partir do gravado.

Só verifica o contrato do motor (`activity_energy`, `energy_expenditure`, `labels`); não há regra aqui."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from app.domain.nutrition import activity_energy as ae
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition import export_for_frontend
from app.domain.nutrition import labels as lb
from app.domain.nutrition.activity_energy import RawActivity, SyncRun
from app.domain.nutrition.anthropometrics import ActivityLevel
from tests.unit.test_nutrition_modos_legados_ouro import _CENARIOS, _dia

D = date(2026, 10, 10)
INICIO = date(2026, 9, 1)


def _run(fim: datetime, ini: date = date(2026, 9, 10), fim_janela: date = date(2026, 10, 20)) -> SyncRun:
    return SyncRun(finished_at=fim, window_start=ini, window_end=fim_janela)


def test_dia_de_descanso_real_confirmado_na_execucao_do_dia_seguinte():
    runs = (
        _run(datetime(2026, 10, 9, 5, 45), date(2026, 9, 9), date(2026, 10, 9)),  # primeira execução registrada
        _run(datetime(2026, 10, 11, 5, 45), date(2026, 10, 1), date(2026, 10, 11)),
    )
    # o Intervals não tinha atividade no dia 10; a execução das 05:45 do dia 11 cobre o dia -> descanso confirmado
    resumo = ae.summarize_days([], 1800, [D], datetime(2026, 10, 11, 5, 45), INICIO, runs)[0]
    assert resumo.confirmed and resumo.active_kcal == 0.0
    assert resumo.confirmation_source == ae.DAY_CONFIRMATION_SOURCE_RUN and resumo.sync_status == "confirmed"


def test_execucao_antes_da_folga_nao_confirma():
    assert not ae.day_confirmed(D, datetime(2026, 10, 11, 4, 59), INICIO, (_run(datetime(2026, 10, 11, 4, 59)),))
    assert ae.day_confirmed(D, datetime(2026, 10, 11, 5, 0), INICIO, (_run(datetime(2026, 10, 11, 5, 0)),))


def test_ausencia_de_execucao_nao_vira_zero_mesmo_com_execucao_posterior():
    # primeira execução em 08/10; a de 20/10 tem janela que NAO inclui o dia 10 -> pendente, nunca zero
    runs = (
        _run(datetime(2026, 10, 8, 5, 45), date(2026, 10, 1), date(2026, 10, 8)),
        _run(datetime(2026, 10, 20, 5, 45), date(2026, 10, 12), date(2026, 10, 20)),
    )
    resumo = ae.summarize_days([], 1800, [D], datetime(2026, 10, 20, 5, 45), INICIO, runs)[0]
    assert not resumo.confirmed and resumo.active_kcal is None and resumo.sync_status == "pending"
    assert resumo.confirmation_source is None


def test_atividade_no_dia_sem_execucao_que_cobre_fica_pendente():
    atividade = RawActivity(id="a", start=datetime(2026, 10, 10, 8), duration_seconds=3600, calories_kcal=400, calorie_mode="active")
    runs = (_run(datetime(2026, 10, 8, 5, 45), date(2026, 10, 1), date(2026, 10, 8)),)
    assert ae.active_kcal_by_day([atividade], 1800, [D], datetime(2026, 10, 20), INICIO, runs) == {D: None}


def test_atividade_no_dia_coberto_por_execucao_soma():
    atividade = RawActivity(id="a", start=datetime(2026, 10, 10, 8), duration_seconds=3600, calories_kcal=400, calorie_mode="active")
    runs = (_run(datetime(2026, 10, 11, 5, 45), date(2026, 10, 1), date(2026, 10, 11)),)
    assert ae.active_kcal_by_day([atividade], 1800, [D], datetime(2026, 10, 11, 5, 45), INICIO, runs) == {D: 400.0}


def test_dia_antes_da_primeira_execucao_segue_a_regra_antiga():
    runs = (_run(datetime(2026, 10, 20, 5, 45), date(2026, 10, 15), date(2026, 10, 20)),)
    ultimo = datetime(2026, 10, 20, 5, 45)
    assert ae.day_confirmation_source(D, ultimo, INICIO, runs) == ae.DAY_CONFIRMATION_SOURCE_LAST_SYNC
    assert ae.day_confirmation_source(D, ultimo, INICIO, ()) == ae.DAY_CONFIRMATION_SOURCE_LAST_SYNC
    assert ae.day_confirmed(D, ultimo, INICIO) is True and ae.day_confirmed(D, None, INICIO, ()) is False


def test_hoje_futuro_e_antes_do_acompanhamento_nunca_confirmam():
    runs = (_run(datetime(2026, 10, 10, 23, 0), date(2026, 10, 1), date(2026, 10, 10)),)
    assert not ae.day_confirmed(D, datetime(2026, 10, 10, 23, 0), INICIO, runs)
    assert not ae.day_confirmed(date(2026, 10, 12), datetime(2026, 10, 11, 5, 45), INICIO, runs)
    ok = (_run(datetime(2026, 10, 11, 5, 45)),)
    assert not ae.day_confirmed(D, datetime(2026, 10, 11, 5, 45), None, ok)
    assert not ae.day_confirmed(D, datetime(2026, 10, 11, 5, 45), date(2026, 10, 11), ok)


def test_trava_de_lacuna_vale_tambem_para_dia_coberto_por_execucao():
    # 30 dias sem atividade, todos cobertos por execuções ok: sequência > 7 dias fica pendente (relógio parado indistinguível)
    dias = [date(2026, 9, 2) + timedelta(days=i) for i in range(30)]
    runs = (
        _run(datetime(2026, 9, 1, 5, 45), date(2026, 8, 1), date(2026, 9, 1)),  # primeira execução registrada
        _run(datetime(2026, 10, 15, 5, 45), date(2026, 8, 1), date(2026, 10, 15)),
    )
    resumo = ae.summarize_days([], 1800, dias, datetime(2026, 10, 15, 5, 45), date(2026, 8, 1), runs)
    assert all(not d.confirmed and d.active_kcal is None for d in resumo)


def test_sequencia_curta_sem_atividade_com_execucao_segue_descanso_confirmado():
    # até MAX_CONFIRMED_REST_STREAK_DAYS dias seguidos (entre duas atividades) são descanso confirmado
    n = ae.MAX_CONFIRMED_REST_STREAK_DAYS
    primeiro = date(2026, 10, 1)
    ativ = lambda i, d: RawActivity(id=i, start=datetime.combine(d, datetime.min.time()).replace(hour=8), duration_seconds=3600, calories_kcal=300, calorie_mode="active")  # noqa: E731
    atividades = [ativ("a", primeiro), ativ("b", primeiro + timedelta(days=n + 1))]
    dias = [primeiro + timedelta(days=i) for i in range(n + 2)]
    runs = (
        _run(datetime(2026, 9, 1, 5, 45), date(2026, 8, 1), date(2026, 9, 1)),
        _run(datetime(2026, 10, 20, 5, 45), date(2026, 9, 1), date(2026, 10, 20)),
    )
    ultimo = datetime(2026, 10, 20, 5, 45)
    curto = ae.summarize_days(atividades, 1800, dias, ultimo, INICIO, runs)
    assert all(d.confirmed for d in curto) and curto[1].active_kcal == 0.0
    mais_longo = [ativ("a", primeiro), ativ("b", primeiro + timedelta(days=n + 2))]
    longo = ae.summarize_days(mais_longo, 1800, dias + [dias[-1] + timedelta(days=1)], ultimo, INICIO, runs)
    assert all(not d.confirmed for d in longo[1:-1])


def test_relogio_parado_longo_com_last_sync_antes_do_horizonte_nao_vira_zero_em_massa():
    # execuções ok cobrem o horizonte inteiro; nenhuma atividade; `last_sync_at` (das atividades) é anterior ao horizonte
    dias = [date(2026, 10, 1) + timedelta(days=i) for i in range(40)]
    runs = (
        _run(datetime(2026, 9, 20, 5, 45), date(2026, 8, 20), date(2026, 9, 20)),  # primeira execução registrada
        _run(datetime(2026, 11, 15, 5, 45), date(2026, 9, 1), date(2026, 11, 15)),
    )
    resumo = ae.summarize_days([], 1800, dias, datetime(2026, 5, 1, 5, 45), date(2026, 8, 1), runs)
    assert not any(d.confirmed and d.active_kcal == 0.0 for d in resumo)
    sem_ultimo = ae.summarize_days([], 1800, dias, None, date(2026, 8, 1), runs)
    assert not any(d.confirmed for d in sem_ultimo)


def _ativ(i, d):
    return RawActivity(id=i, start=datetime.combine(d, datetime.min.time()).replace(hour=8), duration_seconds=3600,
                       calories_kcal=300, calorie_mode="active")


def _runs_ate(hoje):
    return (
        _run(datetime(2026, 9, 1, 5, 45), date(2026, 8, 1), date(2026, 9, 1)),
        _run(datetime.combine(hoje, datetime.min.time()).replace(hour=5, minute=45), date(2026, 9, 1), hoje),
    )


def test_hoje_nao_conta_na_sequencia_de_descanso():
    n = ae.MAX_CONFIRMED_REST_STREAK_DAYS
    hoje = date(2026, 10, 20)
    ultima = hoje - timedelta(days=n + 1)  # atividade; depois n dias de descanso até ontem; hoje sem atividade ainda
    dias = [ultima + timedelta(days=i) for i in range(n + 2)]  # termina em hoje
    ultimo_sync = datetime.combine(hoje, datetime.min.time()).replace(hour=5, minute=45)
    sete = ae.summarize_days([_ativ("a", ultima)], 1800, dias, ultimo_sync, INICIO, _runs_ate(hoje))
    assert [d.active_kcal for d in sete[1:-1]] == [0.0] * n and not sete[-1].confirmed  # 7 dias até ontem: confirmados
    ultima8 = ultima - timedelta(days=1)
    dias8 = [ultima8 + timedelta(days=i) for i in range(n + 3)]
    oito = ae.summarize_days([_ativ("a", ultima8)], 1800, dias8, ultimo_sync, INICIO, _runs_ate(hoje))
    assert all(not d.confirmed for d in oito[1:])  # 8 dias até ontem: pendentes


def test_dayactivity_to_dict_inclui_estado_e_origem():
    resumo = ae.summarize_days([], 1800, [D], datetime(2026, 10, 11, 5, 45), INICIO, ())[0].to_dict()
    assert resumo["sync_status"] == "confirmed" and resumo["confirmation_source"] == "last_sync"
    pendente = ae.summarize_days([], 1800, [D], None, INICIO, ())[0].to_dict()
    assert pendente["sync_status"] == "pending" and pendente["confirmation_source"] is None


# --- composição a partir do gravado -------------------------------------------------------------------------------


def _basis(modo, sincronizado):
    if modo == "auto_intervals":
        return ee.TdeeBasis.AUTO_SYNCED if sincronizado else ee.TdeeBasis.AUTO_FALLBACK
    return ee.TdeeBasis.CLASSIC


@pytest.mark.parametrize("nome", sorted(_CENARIOS))
def test_breakdown_do_gravado_igual_ao_do_resultado_em_todos_os_cenarios_ouro(nome):
    entrada, _ = _CENARIOS[nome]
    comp = _dia(*entrada)
    modo, sincronizado, extras = entrada[1], entrada[4], entrada[3]
    extra = sum(e.calories_kcal for e in extras) if sincronizado else 0.0
    base = _basis(modo, sincronizado)
    gravado = json.loads(json.dumps(comp.target.tdee.to_dict()))  # passa pelo JSON como o inputs_json
    esperado = ee.tdee_breakdown(comp.target.tdee, extra, base)
    obtido = ee.tdee_breakdown_from_stored(gravado, extra, base)
    assert obtido is not None and obtido == esperado
    assert sum(p.kcal for p in obtido.parts) == obtido.total_kcal
    assert lb.tdee_breakdown_public(obtido) == lb.tdee_breakdown_public(esperado)


def _adaptativo(ajuste, bootstrap=False, nivel="high"):
    return {
        "kcal": 2400.4, "mode": "adaptive_balance", "activity_factor": None, "bmr_kcal": 1800.0, "training_kcal": None,
        "limitations": [],
        "adaptive": {"base_kcal": 2400.4 - ajuste, "adjustment_kcal": ajuste, "confidence": 0.7, "confidence_level": nivel,
                     "bootstrap": bootstrap, "activity_pending": False, "day_open": False},
    }


def test_adaptativo_com_ajuste_negativo_fecha_o_total_e_iguala_o_resultado():
    gravado = _adaptativo(-150.4, nivel="medium")
    r = ee.tdee_result_from_stored(gravado)
    assert r is not None and r.to_dict() == gravado
    obtido = ee.tdee_breakdown_from_stored(gravado, 0, ee.TdeeBasis.ADAPTIVE)
    assert obtido == ee.tdee_breakdown(r, 0, ee.TdeeBasis.ADAPTIVE)
    codigos = {p.code: p.kcal for p in obtido.parts}
    assert codigos["activity_adjustment"] == -150.0 and sum(codigos.values()) == obtido.total_kcal == 2400.0
    assert obtido.base == ee.BreakdownBase.ADAPTIVE and ee.BREAKDOWN_NOTE_ADAPTIVE_CONFIDENCE_MEDIUM in obtido.notes


def test_adaptativo_bootstrap_e_ajuste_zero_omite_a_parcela():
    obtido = ee.tdee_breakdown_from_stored(_adaptativo(0.0, bootstrap=True), 0, ee.TdeeBasis.ADAPTIVE_BOOTSTRAP)
    assert [p.code for p in obtido.parts] == ["base"] and obtido.base == ee.BreakdownBase.ADAPTIVE_BOOTSTRAP


@pytest.mark.parametrize(
    "gravado",
    [
        {},
        {"mode": "explicit_training"},
        {"kcal": None, "mode": "explicit_training", "activity_factor": None, "bmr_kcal": None, "training_kcal": None},
        {"kcal": 2500.0, "mode": "explicit_training", "activity_factor": None, "bmr_kcal": 1800.0},
        {"kcal": 2500.0, "mode": "explicit_training", "activity_factor": 1.4, "bmr_kcal": None},
        {"kcal": "2500", "mode": "explicit_training", "activity_factor": 1.4, "bmr_kcal": 1800.0},
        {"kcal": 2500.0, "mode": "modo_inexistente"},
        {"kcal": 2400.0, "mode": "adaptive_balance"},
        {"kcal": 2400.0, "mode": "adaptive_balance", "adaptive": {"base_kcal": 2000.0}},
        {"kcal": float("nan"), "mode": "included_in_factor", "activity_factor": 1.5, "bmr_kcal": 1800.0},
    ],
)
def test_dicts_incompletos_devolvem_none(gravado):
    assert ee.tdee_breakdown_from_stored(gravado, 0, ee.TdeeBasis.CLASSIC) is None


def test_gravado_nao_mapeamento_devolve_none():
    assert ee.tdee_breakdown_from_stored(None, 0, ee.TdeeBasis.CLASSIC) is None  # type: ignore[arg-type]


def test_explicito_gravado_separa_forca_e_extras():
    r = ee.tdee(1780.0, ActivityLevel.MODERATE, ee.TdeeMode.EXPLICIT_TRAINING, 428.0 + 120.0)
    obtido = ee.tdee_breakdown_from_stored(r.to_dict(), 120.0, ee.TdeeBasis.AUTO_SYNCED)
    partes = {p.code: p.kcal for p in obtido.parts}
    assert partes["training"] == 428.0 and partes["extras"] == 120.0


# --- rótulos --------------------------------------------------------------------------------------------------------


def test_rotulos_do_diario_cobrem_os_codigos_e_vao_para_o_json():
    grupos = export_for_frontend()["adaptive_energy_labels"]
    assert set(grupos["day_sync_status"]) == {ae.DAY_SYNC_STATUS_CONFIRMED, ae.DAY_SYNC_STATUS_PENDING}
    assert set(grupos["day_confirmation_source"]) == {ae.DAY_CONFIRMATION_SOURCE_RUN, ae.DAY_CONFIRMATION_SOURCE_LAST_SYNC}
    assert all(grupos["day_sync_status"].values()) and all(grupos["day_confirmation_source"].values())


def test_retencao_do_registro_cobre_o_replay_mais_folga():
    from app.domain.nutrition import adaptive_energy

    assert ae.SYNC_RUN_RETENTION_DAYS == adaptive_energy.REPLAY_HISTORY_DAYS + 30
