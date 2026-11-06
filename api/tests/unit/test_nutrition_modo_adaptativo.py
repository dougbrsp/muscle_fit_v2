"""Modo `adaptive_balance` no motor (Lote C): gasto do dia pela estimativa adaptativa, meta, plano e check-in.

Dono: `adaptive_energy.py` (estimativa), `energy_expenditure.py` (gasto e composição), `daily_target.py` (meta),
`plan.py` (validação) e `weekly_checkin.py` (informativo). Mapa `docs/motores/FONTES_DA_VERDADE.md` §94 e §96."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Optional

import pytest

from app.domain.nutrition import adaptive_energy as ae
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition import strategy as strategy_mod
from app.domain.nutrition import weekly_checkin as wc
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.body_composition import resolved_fat_mass_kg
from app.domain.nutrition.dashboard import DayState
from app.domain.nutrition.daily_target import DayContext, compute_daily_target, day_context_for
from app.domain.nutrition.plan import (
    FIELD_ENERGY_PARAMS,
    build_plan_settings,
    projection_tdee,
)
from tests.unit.test_nutrition_weekly_checkin import _cenario, _settings

D0 = date(2026, 1, 1)
BMR = 1780.0  # homem 80 kg, 180 cm, 30 anos (Mifflin)
SUJEITO = Subject(
    sex=Sex.MALE, age_years=30, height_cm=180.0, weight_kg=80.0, body_fat_percent=20.0, activity_level=ActivityLevel.ACTIVE
)


def _entradas(n: int, *, ativas=lambda i: 600.0, confirmado=lambda i: True, logado=lambda i: True, intake=2400.0, bmr=BMR):
    return [
        ae.DayInput(
            day=D0 + timedelta(days=i),
            day_state=DayState.LOGGED if logado(i) else DayState.NO_RECORD,
            intake_kcal=intake if logado(i) else None,
            bmr_kcal=bmr,
            weights_kg=(80.0,),
            fat_mass_kg=16.0,
            active_kcal=ativas(i),
            activity_confirmed=confirmado(i),
        )
        for i in range(n)
    ]


def _estados(n: int, **kw: Any) -> list[ae.AdaptiveEnergyState]:
    return ae.replay(_entradas(n, **kw))


def _plano(estrategia: str = "maintenance", **extra: Any):
    validacao = build_plan_settings(strategy=estrategia, tdee_mode="adaptive_balance", activity_level="active", **extra)
    assert validacao.ok, validacao.errors
    return validacao.settings


def _meta(settings, dia: date, estimativa: Optional[ae.DayEnergyEstimate]):
    contexto = day_context_for(dia, dia, energy_estimate=estimativa)
    return compute_daily_target(SUJEITO, settings.plan_params(), contexto)


# --- estimativa do dia -------------------------------------------------------


def test_estimativa_de_dia_passado_traz_o_ajuste_que_valeu():
    estados = _estados(40, ativas=lambda i: 1000.0 if i == 30 else 600.0)
    dia = D0 + timedelta(days=30)
    est = ae.estimate_for_day(estados, dia, today=D0 + timedelta(days=39))
    assert est is not None and not est.day_open and not est.activity_pending
    assert est.adjustment_kcal > 0
    assert est.estimated_kcal == pytest.approx(est.base_kcal + est.adjustment_kcal)
    assert est.snapshot["activity_adjustment"] == est.adjustment_kcal
    assert est.algorithm_version == ae.ALGORITHM_VERSION


def test_hoje_e_dia_futuro_usam_a_base_sem_ajuste():
    estados = _estados(40, ativas=lambda i: 2000.0 if i == 39 else 600.0)
    hoje = D0 + timedelta(days=39)
    atual = estados[-1]
    assert atual.activity_adjustment_kcal > 0  # o estado de hoje calculou ajuste, mas hoje está em andamento
    for dia in (hoje, hoje + timedelta(days=3)):
        est = ae.estimate_for_day(estados, dia, hoje)
        assert est.day_open and est.adjustment_kcal == 0.0 and not est.activity_pending
        assert est.estimated_kcal == est.base_kcal
        assert ae.FLAG_DAY_IN_PROGRESS in est.snapshot["data_quality_flags"]
        assert est.snapshot["activity_adjustment"] == 0.0


def test_dia_passado_nao_confirmado_fica_sem_ajuste_e_pendente():
    estados = _estados(40, ativas=lambda i: None if i == 35 else 600.0, confirmado=lambda i: i != 35)
    est = ae.estimate_for_day(estados, D0 + timedelta(days=35), D0 + timedelta(days=39))
    assert est.adjustment_kcal == 0.0 and est.activity_pending


def test_sem_estado_ou_sem_tmb_nao_ha_estimativa():
    estados = _estados(10)
    assert ae.estimate_for_day(estados, D0 - timedelta(days=5), D0 + timedelta(days=9)) is None
    sem_tmb = _estados(10, bmr=None)
    assert ae.estimate_for_day(sem_tmb, D0 + timedelta(days=5), D0 + timedelta(days=9)) is None
    assert ae.estimate_for_day([], D0, D0) is None


def test_estimativa_inicial_so_com_formula():
    est = ae.bootstrap_only_estimate(D0, BMR)
    assert est.base_kcal == pytest.approx(BMR * ae.BOOTSTRAP_ACTIVITY_FACTOR, abs=0.05)
    assert est.bootstrap and est.confidence == 0.0 and est.confidence_level == "low" and est.adjustment_kcal == 0.0
    assert ae.bootstrap_only_estimate(D0, None) is None


def test_transicao_7_14_21_dias_na_estimativa():
    def est(n: int) -> ae.DayEnergyEstimate:
        estados = _estados(40, logado=lambda i: i >= 40 - n)
        return ae.estimate_for_day(estados, D0 + timedelta(days=30), D0 + timedelta(days=39))

    assert est(7).bootstrap and est(7).confidence == 0.0
    meio = ae.estimate_for_day(_estados(40, logado=lambda i: i >= 26), D0 + timedelta(days=39), D0 + timedelta(days=39))
    assert not meio.bootstrap and meio.confidence == pytest.approx(0.5)
    assert meio.confidence_level == "medium"
    cheio = ae.estimate_for_day(_estados(40), D0 + timedelta(days=39), D0 + timedelta(days=39))
    assert cheio.confidence == 1.0 and cheio.confidence_level == "high"
    assert cheio.base_kcal == pytest.approx(2400.0, abs=0.1)


# --- gasto e meta ---------------------------------------------------------------


def test_adaptativo_ignora_o_fator_do_perfil_e_usa_o_intervals_so_como_delta():
    estados = _estados(40, ativas=lambda i: 900.0 if i == 30 else 600.0)
    dia = D0 + timedelta(days=30)
    est = ae.estimate_for_day(estados, dia, D0 + timedelta(days=39))
    meta = _meta(_plano(), dia, est)
    assert meta.tdee.mode == ee.TdeeMode.ADAPTIVE_BALANCE
    assert meta.tdee.kcal == est.estimated_kcal
    assert meta.tdee.kcal != pytest.approx(BMR * 1.725)  # o perfil "ativo" (fator 1,725) não entra
    assert meta.tdee.activity_factor is None
    # Ajuste de delta (nunca "base + toda a atividade").
    assert meta.tdee.kcal - est.base_kcal == pytest.approx(est.adjustment_kcal)
    assert abs(est.adjustment_kcal) < 900.0
    assert meta.tdee_basis == ee.TdeeBasis.ADAPTIVE
    assert meta.energy is not None and meta.energy["estimated_tdee_today"] == est.estimated_kcal
    # Manutenção sem ajuste: a meta é o gasto do dia.
    assert meta.macros.kcal == pytest.approx(est.estimated_kcal, rel=1e-3)


def test_bootstrap_puro_sem_historico_usa_a_formula():
    meta = _meta(_plano(), D0, None)
    assert meta.tdee_basis == ee.TdeeBasis.ADAPTIVE_BOOTSTRAP
    assert meta.tdee.kcal == pytest.approx(meta.bmr.kcal * ae.BOOTSTRAP_ACTIVITY_FACTOR, abs=0.05)
    assert meta.tdee.adaptive.bootstrap


def test_so_o_gasto_troca_o_resto_da_meta_e_o_mesmo_do_motor():
    """Déficit pelo ritmo × Hall/Forbes, teto, piso e macros: `kcal_target` é o do motor com o gasto da estimativa."""
    estados = _estados(40)
    est = ae.estimate_for_day(estados, D0 + timedelta(days=35), D0 + timedelta(days=39))
    for estrategia in ("cutting", "bulking", "maintenance"):
        settings = _plano(estrategia)
        meta = _meta(settings, est.day, est)
        esperado = strategy_mod.kcal_target(
            settings.strategy_params(), est.estimated_kcal, meta.bmr.kcal, SUJEITO.weight_kg, resolved_fat_mass_kg(SUJEITO)
        )
        assert meta.kcal_target.kcal == pytest.approx(esperado.kcal)
        assert meta.macros.kcal == pytest.approx(esperado.kcal, rel=1e-2)


def test_cutting_respeita_teto_de_deficit_e_piso_da_tmb():
    est = ae.bootstrap_only_estimate(D0, BMR)
    settings = _plano("cutting", weekly_rate_share=strategy_mod.CUT_WEEKLY_RATE_SHARE_RANGE[1])
    meta = _meta(settings, D0, est)
    assert meta.kcal_target.kcal >= strategy_mod.CUT_MIN_INTAKE_BMR_MULTIPLE * meta.bmr.kcal - 1e-6
    assert meta.kcal_target.kcal >= meta.tdee.kcal * (1.0 - strategy_mod.CUT_MAX_DEFICIT_SHARE) - 1e-6


def test_ciclo_de_carbo_e_refeicoes_preservados_no_adaptativo():
    tipos = {0: "high", 1: "normal", 2: "low", 3: "normal", 4: "high", 5: "low", 6: "normal"}
    settings = _plano("maintenance", cycle_days=tipos, meals_per_day=4)
    estados = _estados(40)
    segunda = date(2026, 2, 9)  # semana de teste, segunda-feira
    metas = [
        _meta(settings, segunda + timedelta(days=i), ae.estimate_for_day(estados, D0 + timedelta(days=35), D0 + timedelta(days=39)))
        for i in range(7)
    ]
    assert len({round(m.macros.carb_g, 3) for m in metas}) > 1  # o ciclo varia o carboidrato
    plano = _plano("maintenance", meals_per_day=4)
    sem_ciclo = _meta(plano, segunda, metas[0].tdee and ae.estimate_for_day(estados, D0 + timedelta(days=35), D0 + timedelta(days=39)))
    media_kcal = sum(m.macros.kcal for m in metas) / 7
    assert media_kcal == pytest.approx(sem_ciclo.macros.kcal, abs=2.0)  # soma zero no ciclo
    assert all(m.protein_per_meal_g is not None for m in metas)  # refeições por dia preservadas


def test_gasto_adaptativo_sem_estimativa_nem_tmb_e_none_com_limitacao():
    resultado = ee.tdee(None, None, ee.TdeeMode.ADAPTIVE_BALANCE, adaptive_estimate=None)
    assert resultado.kcal is None and ee.LIMIT_ADAPTIVE_ESTIMATE_MISSING in resultado.limitations
    assert ee.tdee_breakdown(resultado, 0.0, ee.TdeeBasis.ADAPTIVE) is None


def test_projecao_usa_a_base_adaptativa_sem_o_ajuste():
    estados = _estados(40, ativas=lambda i: 1000.0 if i == 30 else 600.0)
    est = ae.estimate_for_day(estados, D0 + timedelta(days=30), D0 + timedelta(days=39))
    gasto = ee.adaptive_tdee_result(est, BMR)
    valor, premissas = projection_tdee(gasto, [], False)
    assert valor == est.base_kcal and premissas == ()
    assert projection_tdee(ee.adaptive_tdee_result(None, BMR), [], False) == (None, ())


# --- hash e JSON ------------------------------------------------------------------


def test_contexto_e_meta_legados_nao_ganham_chaves():
    contexto = DayContext(day=D0)
    assert "energy_estimate" not in contexto.to_dict()
    meta = compute_daily_target(
        SUJEITO, build_plan_settings(strategy="maintenance", activity_level="moderate").settings.plan_params(), contexto
    )
    assert "energy" not in meta.to_dict()
    assert "adaptive" not in meta.tdee.to_dict()


def test_contexto_adaptativo_leva_a_estimativa_ao_hash_e_a_meta_leva_o_retrato():
    est = ae.estimate_for_day(_estados(40), D0 + timedelta(days=35), D0 + timedelta(days=39))
    contexto = day_context_for(est.day, est.day, energy_estimate=est)
    d = contexto.to_dict()
    assert set(d["energy_estimate"]) == {
        "day", "estimated_kcal", "base_kcal", "adjustment_kcal", "confidence", "bootstrap", "algorithm_version",
    }
    outro = replace(est, estimated_kcal=est.estimated_kcal + 10)
    assert day_context_for(est.day, est.day, energy_estimate=outro).to_dict() != d
    meta = _meta(_plano(), est.day, est).to_dict()
    json.dumps(meta, default=str)
    assert meta["energy"]["algorithm_version"] == "adaptive-v1"
    assert meta["tdee_basis"] == "adaptive"
    assert meta["tdee"]["adaptive"]["base_kcal"] == est.base_kcal


# --- composição do gasto -----------------------------------------------------------


def _decomp(est: ae.DayEnergyEstimate):
    return ee.tdee_breakdown(ee.adaptive_tdee_result(est, BMR), 0.0, ee.TdeeBasis.ADAPTIVE)


def test_composicao_adaptativa_base_mais_ajuste_fecha_o_total():
    estados = _estados(40, ativas=lambda i: 1000.0 if i == 30 else 600.0)
    est = ae.estimate_for_day(estados, D0 + timedelta(days=30), D0 + timedelta(days=39))
    b = _decomp(est)
    assert [p.code for p in b.parts] == [ee.BREAKDOWN_BASE, ee.BREAKDOWN_ACTIVITY_ADJUSTMENT]
    assert sum(p.kcal for p in b.parts) == b.total_kcal
    assert b.base == ee.BreakdownBase.ADAPTIVE and b.parts[1].kcal > 0
    assert all(float(p.kcal).is_integer() for p in b.parts)


def test_composicao_ajuste_negativo_com_sinal_e_zero_omitido():
    estados = _estados(40, ativas=lambda i: 0.0 if i == 30 else 600.0)
    est = ae.estimate_for_day(estados, D0 + timedelta(days=30), D0 + timedelta(days=39))
    b = _decomp(est)
    assert b.parts[1].code == ee.BREAKDOWN_ACTIVITY_ADJUSTMENT and b.parts[1].kcal < 0
    assert sum(p.kcal for p in b.parts) == b.total_kcal
    hoje = ae.estimate_for_day(estados, D0 + timedelta(days=39), D0 + timedelta(days=39))
    sem_ajuste = _decomp(hoje)
    assert [p.code for p in sem_ajuste.parts] == [ee.BREAKDOWN_BASE]
    assert ee.BREAKDOWN_NOTE_ADAPTIVE_DAY_OPEN in sem_ajuste.notes


def test_composicao_notas_de_estimativa_inicial_confianca_e_pendencia():
    inicial = _decomp(ae.bootstrap_only_estimate(D0, BMR))
    assert inicial.base == ee.BreakdownBase.ADAPTIVE_BOOTSTRAP and inicial.notes[0] == ee.BREAKDOWN_NOTE_ADAPTIVE_BOOTSTRAP
    medio = _decomp(ae.estimate_for_day(_estados(40, logado=lambda i: i >= 26), D0 + timedelta(days=39), D0 + timedelta(days=45)))
    assert ee.BREAKDOWN_NOTE_ADAPTIVE_CONFIDENCE_MEDIUM in medio.notes or ee.BREAKDOWN_NOTE_ADAPTIVE_CONFIDENCE_LOW in medio.notes
    pendente = _decomp(
        ae.estimate_for_day(
            _estados(40, ativas=lambda i: None if i == 35 else 600.0, confirmado=lambda i: i != 35),
            D0 + timedelta(days=35),
            D0 + timedelta(days=39),
        )
    )
    assert ee.BREAKDOWN_NOTE_ADAPTIVE_ACTIVITY_PENDING in pendente.notes


# --- plano -------------------------------------------------------------------------


def test_plano_adaptativo_valida_e_so_aceita_gramas_manuais_fixed():
    assert _plano().is_adaptive
    manual = {"protein_g": 160.0, "carb_g": 300.0, "fat_g": 70.0}
    fixo = build_plan_settings(strategy="maintenance", tdee_mode="adaptive_balance", manual_targets=manual)
    assert fixo.ok, fixo.errors
    soma = build_plan_settings(
        strategy="maintenance", tdee_mode="adaptive_balance", manual_targets=manual, manual_training_mode="add_training"
    )
    assert not soma.ok and any(e.startswith("param_not_applicable") for e in soma.errors)


def test_energy_params_so_entram_no_dict_e_no_hash_quando_saem_do_padrao():
    padrao = _plano()
    assert "energy_params" not in padrao.to_dict() and "energy_params" not in padrao.fingerprint_dict()
    explicito = _plano(energy_params={"watch_calorie_mode": "unknown"})
    assert explicito.fingerprint_dict() == padrao.fingerprint_dict()
    bruta = _plano(energy_params={"watch_calorie_mode": "gross"})
    assert bruta.to_dict()["energy_params"] == {"watch_calorie_mode": "gross"}
    assert bruta.fingerprint_dict() != padrao.fingerprint_dict()


@pytest.mark.parametrize("valor", [{"watch_calorie_mode": "x"}, {"outra": 1}, "gross", 3])
def test_energy_params_invalidos(valor):
    r = build_plan_settings(strategy="maintenance", tdee_mode="adaptive_balance", energy_params=valor)
    assert not r.ok and any(e.endswith(FIELD_ENERGY_PARAMS) for e in r.errors)


def test_energy_params_fora_do_padrao_nao_valem_nos_modos_legados():
    r = build_plan_settings(strategy="maintenance", tdee_mode="explicit_training", energy_params={"watch_calorie_mode": "gross"})
    assert not r.ok and any(e == f"param_not_applicable:{FIELD_ENERGY_PARAMS}" for e in r.errors)
    ok = build_plan_settings(strategy="maintenance", tdee_mode="explicit_training", energy_params={"watch_calorie_mode": "unknown"})
    assert ok.ok


def test_fingerprint_dos_planos_legados_nao_muda():
    chaves = {
        "strategy", "advanced", "protein_g_per_kg", "protein_basis", "fat_kcal_share", "weekly_rate_share",
        "surplus_share", "carb_min_g_per_kg", "bmr_formula", "tdee_mode", "activity_level", "meals_per_day",
        "goal_body_fat_percent", "goal_weight_kg", "carb_cycling_enabled",
    }
    for modo in ("explicit_training", "included_in_factor", "auto_intervals"):
        s = build_plan_settings(strategy="maintenance", tdee_mode=modo, activity_level="moderate").settings
        assert set(s.fingerprint_dict()) == chaves


# --- check-in informativo ---------------------------------------------------------


def test_checkin_do_plano_adaptativo_e_informativo_sem_proposta():
    settings = _settings("cutting", tdee_mode="adaptive_balance")
    r = wc.evaluate_week(_cenario(settings, mudanca_kg_semana=-0.1))
    assert r.verdict == wc.CheckinVerdict.SLOW
    assert r.proposal is None and r.status == wc.CheckinStatus.INFORMATIVE
    assert wc.REASON_ADAPTIVE_INFORMATIVE in r.reasons


def test_apply_changes_preserva_o_modo_da_kcal_do_relogio():
    settings = _settings("cutting", tdee_mode="adaptive_balance", energy_params={"watch_calorie_mode": "gross"})
    novo = wc.apply_changes(settings, {wc.CHANGE_WEEKLY_RATE_SHARE: 0.005})
    assert novo.ok and novo.settings.energy_params.watch_calorie_mode.value == "gross"
    assert novo.settings.weekly_rate_share == 0.005
    padrao = wc.apply_changes(_settings("cutting", tdee_mode="adaptive_balance"), {wc.CHANGE_WEEKLY_RATE_SHARE: 0.005})
    assert padrao.ok and padrao.settings.energy_params.is_default
    legado = wc.apply_changes(_settings("cutting"), {wc.CHANGE_WEEKLY_RATE_SHARE: 0.005})
    assert legado.ok and legado.settings.energy_params.is_default


def test_checkin_do_plano_legado_continua_propondo():
    r = wc.evaluate_week(_cenario(mudanca_kg_semana=-0.1))
    assert r.proposal is not None and wc.REASON_ADAPTIVE_INFORMATIVE not in r.reasons


# --- sugestão por IA e relatório ---------------------------------------------------


def test_sugestao_por_ia_nao_propoe_o_modo_adaptativo_mas_aceita_o_valor_no_formulario():
    from app.domain.nutrition import plan_suggestion as ps

    assert "tdee_mode" not in ps.SUGGESTION_FIELDS  # a IA não escolhe o modo de gasto
    atual = ps.normalize_current({"strategy": "cutting", "tdee_mode": "adaptive_balance"})
    assert atual["tdee_mode"] == "adaptive_balance"


def test_relatorio_conta_as_bases_adaptativas_e_rotula():
    from app.domain.nutrition import labels as lb

    for base in (ee.TdeeBasis.ADAPTIVE_BOOTSTRAP, ee.TdeeBasis.ADAPTIVE):
        assert base.value in lb.TDEE_BASIS_LABELS


# --- rodada de correção 1: versão do modelo, TMB única, histórico indisponível -------


def test_energy_model_version_e_regra_do_dono():
    assert _plano().energy_model_version == ae.ALGORITHM_VERSION
    legado = build_plan_settings(strategy="maintenance", tdee_mode="explicit_training", activity_level="moderate").settings
    assert legado.energy_model_version is None


def test_tmb_do_historico_e_da_estimativa_inicial_seguem_a_mesma_regra():
    from app.domain.nutrition import bmr as bmr_mod
    from app.domain.nutrition.plan import history_bmr_formula

    assert ae.resolve_day_bmr(2000.0, 1900.0, 1800.0) == 2000.0  # gravada na meta
    assert ae.resolve_day_bmr(None, 1900.0, 1800.0) == 1900.0  # fórmula do plano
    assert ae.resolve_day_bmr(0, None, 1800.0) == 1800.0  # sem plano: a padrão
    assert ae.resolve_day_bmr(None, None, None) is None
    assert ae.resolve_day_bmr(2000.0, 1900.0) == 2000.0  # assinatura antiga (2 argumentos) segue valendo
    assert history_bmr_formula(None) == bmr_mod.DEFAULT_BMR_FORMULA
    assert history_bmr_formula(_plano()) == _plano().bmr_formula
    # Mesma TMB -> mesma estimativa inicial nos dois caminhos (histórico e meta sem histórico).
    tmb = ae.resolve_day_bmr(None, 1900.0, 1800.0)
    via_replay = ae.step(None, _entradas(1, bmr=tmb, ativas=lambda i: None, confirmado=lambda i: False)[0])
    inicial = ae.bootstrap_only_estimate(D0, tmb)
    assert via_replay.bootstrap_today_kcal == pytest.approx(inicial.base_kcal, abs=0.05)


def test_rest_tdee_adaptativo_usa_o_mesmo_fator_da_estimativa_inicial():
    rest = ee.rest_tdee(BMR, ActivityLevel.ACTIVE, ee.TdeeMode.ADAPTIVE_BALANCE)
    assert rest.activity_factor == ae.BOOTSTRAP_ACTIVITY_FACTOR
    assert rest.kcal == pytest.approx(BMR * ae.BOOTSTRAP_ACTIVITY_FACTOR)
    assert ee.rest_tdee(None, None, ee.TdeeMode.ADAPTIVE_BALANCE).kcal is None


def test_historico_indisponivel_marca_estimativa_composicao_e_hash():
    normal = ae.bootstrap_only_estimate(D0, BMR)
    falha = ae.bootstrap_only_estimate(D0, BMR, history_unavailable=True)
    assert falha.history_unavailable and not normal.history_unavailable
    assert ae.FLAG_HISTORY_UNAVAILABLE in falha.snapshot["data_quality_flags"]
    assert ae.FLAG_HISTORY_UNAVAILABLE not in normal.snapshot["data_quality_flags"]
    assert "history_unavailable" not in normal.to_dict() and falha.to_dict()["history_unavailable"] is True
    assert falha.estimated_kcal == normal.estimated_kcal
    b = ee.tdee_breakdown(ee.adaptive_tdee_result(falha, BMR), 0.0, ee.TdeeBasis.ADAPTIVE_BOOTSTRAP)
    assert ee.BREAKDOWN_NOTE_ADAPTIVE_HISTORY_UNAVAILABLE in b.notes
    meta = _meta(_plano(), D0, falha)
    assert ae.FLAG_HISTORY_UNAVAILABLE in meta.energy["data_quality_flags"]
    assert meta.tdee.adaptive.history_unavailable
    # O hash do contexto muda quando o histórico falhou (a estimativa gravada é outra).
    assert day_context_for(D0, D0, energy_estimate=falha).to_dict() != day_context_for(D0, D0, energy_estimate=normal).to_dict()


def test_composicao_usa_o_enum_do_nivel_de_confianca():
    assert {n.value for n in ae.ConfidenceLevel} == {"low", "medium", "high"}


def test_contexto_com_historico_indisponivel_usa_a_estimativa_inicial_marcada():
    normal = day_context_for(D0, D0)
    assert "energy_history_unavailable" not in normal.to_dict()
    ctx = day_context_for(D0, D0, energy_history_unavailable=True)
    assert ctx.to_dict()["energy_history_unavailable"] is True
    for dia in (D0, D0 + timedelta(days=3)):  # os três ramos de `day_context_for`: hoje, futuro e com sessão
        assert day_context_for(dia, D0, energy_history_unavailable=True).energy_history_unavailable
    meta = compute_daily_target(SUJEITO, _plano().plan_params(), ctx)
    assert meta.tdee_basis == ee.TdeeBasis.ADAPTIVE_BOOTSTRAP
    assert ae.FLAG_HISTORY_UNAVAILABLE in meta.energy["data_quality_flags"]
    assert meta.tdee.adaptive.history_unavailable
    sem_falha = compute_daily_target(SUJEITO, _plano().plan_params(), normal)
    assert ae.FLAG_HISTORY_UNAVAILABLE not in sem_falha.energy["data_quality_flags"]
    assert meta.tdee.kcal == sem_falha.tdee.kcal
    # Com estimativa pronta o sinal não a substitui.
    est = ae.bootstrap_only_estimate(D0, BMR)
    assert compute_daily_target(SUJEITO, _plano().plan_params(), replace(ctx, energy_estimate=est)).energy is not None
