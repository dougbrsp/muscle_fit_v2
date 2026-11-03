"""Check-in semanal (N7; dono `domain/nutrition/weekly_checkin.py`, mapa §83).

Cenários construídos com o MESMO motor de metas (`compute_daily_target`): a semana tem metas e gasto reais do plano,
a ingestão é a meta vezes um fator, e o peso segue uma reta de `mudanca_kg_semana` por semana."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Optional

import pytest

from app.domain.nutrition import weekly_checkin as wc
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.daily_target import DayContext, compute_daily_target
from app.domain.nutrition.macros import Macros
from app.domain.nutrition.plan import build_plan_settings, check_cycle_floors
from app.domain.nutrition.strategy import BULK_SURPLUS_SHARE_RANGE, CUT_WEEKLY_RATE_SHARE_RANGE
from app.domain.nutrition.weekly_budget import MIN_LOGGED_DAYS_FOR_WEEK, target_macros

V = wc.CheckinVerdict
SEGUNDA = date(2026, 10, 19)  # início da semana avaliada (segunda); domingo = 25
HOJE = date(2026, 10, 26)  # dia do check-in (segunda seguinte)
SUJEITO = Subject(
    sex=Sex.MALE, age_years=30, height_cm=180.0, weight_kg=80.0, body_fat_percent=20.0, activity_level=ActivityLevel.MODERATE
)


def _settings(strategy: str = "cutting", **extra: Any):
    validacao = build_plan_settings(strategy=strategy, activity_level="moderate", **extra)
    assert validacao.ok, validacao.errors
    return validacao.settings


def _cenario(
    settings=None,
    *,
    mudanca_kg_semana: Optional[float] = 0.0,
    fator_ingestao: float = 1.0,
    dias_completos: int = 7,
    pesagens_cada: int = 1,
    gordura: Optional[dict[int, float]] = None,
    musculo: Optional[dict[int, float]] = None,
    peso_final: float = 80.0,
) -> wc.CheckinInputs:
    """28 dias terminando no domingo `SEGUNDA + 6` (índice 27). `mudanca_kg_semana` None = sem nenhuma pesagem;
    o peso termina em `peso_final` e anda `mudanca_kg_semana` por semana."""
    settings = settings or _settings()
    inicio = SEGUNDA + timedelta(days=6) - timedelta(days=wc.BODY_WINDOW_DAYS - 1)
    dias = []
    for i in range(wc.BODY_WINDOW_DAYS):
        dia = inicio + timedelta(days=i)
        meta = compute_daily_target(SUJEITO, settings.plan_params(), DayContext(day=dia))
        alvo = target_macros(meta.macros.kcal, meta.macros.protein_g, meta.macros.carb_g, meta.macros.fat_g)
        na_semana = i >= wc.BODY_WINDOW_DAYS - 7
        completo = (i - (wc.BODY_WINDOW_DAYS - 7)) < dias_completos if na_semana else True
        ingestao = (
            Macros(
                kcal=meta.macros.kcal * fator_ingestao,
                protein_g=meta.macros.protein_g,
                carb_g=meta.macros.carb_g,
                fat_g=meta.macros.fat_g,
            )
            if completo
            else None
        )
        pesa = mudanca_kg_semana is not None and (wc.BODY_WINDOW_DAYS - 1 - i) % pesagens_cada == 0
        peso = peso_final + (mudanca_kg_semana or 0.0) / 7.0 * (i - (wc.BODY_WINDOW_DAYS - 1)) if pesa else None
        dias.append(
            wc.CheckinDay(
                local_date=dia,
                target=alvo,
                intake=ingestao,
                tdee_kcal=meta.tdee.kcal,
                weight_kg=peso,
                body_fat_percent=(gordura or {}).get(i),
                muscle_mass_kg=(musculo or {}).get(i),
            )
        )
    return wc.CheckinInputs(settings=settings, subject=SUJEITO, days=tuple(dias), reference_day=HOJE)


def _esperado(settings) -> float:
    """Variação semanal de peso que o plano espera (kg), lida do próprio motor."""
    return wc.evaluate_week(_cenario(settings, mudanca_kg_semana=0.0)).summary.expected_weekly_change_kg


# --- cutting -------------------------------------------------------------------------------------------------------------


def test_cutting_no_ritmo_nao_propoe_nada():
    # Meta de 0,7%/semana de 80 kg = 0,56 kg/semana, com MG 16 kg (densidade ~6.400 kcal/kg): o motor espera ~-0,5 kg.
    esperado = _esperado(_settings())
    assert esperado == pytest.approx(-0.5, abs=0.12)
    r = wc.evaluate_week(_cenario(mudanca_kg_semana=esperado))
    assert r.verdict == V.ON_TRACK and r.proposal is None and r.status == wc.CheckinStatus.INFORMATIVE


def test_cutting_lento_reduz_kcal_com_passo_limitado():
    r = wc.evaluate_week(_cenario(mudanca_kg_semana=-0.1))
    assert r.verdict == V.SLOW and r.proposal is not None and r.status == wc.CheckinStatus.PROPOSED
    delta = r.proposal.delta_kcal
    assert delta < 0
    assert abs(delta) <= wc.MAX_KCAL_STEP_SHARE * r.summary.mean_tdee_kcal + 15  # passo máximo (+ arredondamento do ritmo)
    assert r.proposal.changes[wc.CHANGE_WEEKLY_RATE_SHARE] > 0.007
    assert r.proposal.after.protein_g == pytest.approx(r.proposal.before.protein_g, abs=1.0)


def test_cutting_rapido_reduz_o_deficit():
    r = wc.evaluate_week(_cenario(mudanca_kg_semana=-1.1))
    assert r.verdict == V.FAST and r.proposal is not None and r.proposal.delta_kcal > 0
    assert r.proposal.changes[wc.CHANGE_WEEKLY_RATE_SHARE] < 0.007


def test_cutting_no_maximo_da_faixa_de_ritmo_nao_corta_mais():
    s = _settings(weekly_rate_share=CUT_WEEKLY_RATE_SHARE_RANGE[1])
    r = wc.evaluate_week(_cenario(s, mudanca_kg_semana=0.1))
    assert r.verdict == V.SLOW and r.proposal is None
    assert {wc.REASON_AT_STRATEGY_LIMIT, wc.REASON_ADJUSTMENT_BELOW_MIN} & set(r.reasons)


def test_cutting_nunca_propoe_abaixo_do_teto_de_deficit_nem_do_piso_de_energia():
    s = _settings(weekly_rate_share=0.009)
    for mudanca in (0.2, -0.05, 0.0):
        r = wc.evaluate_week(_cenario(s, mudanca_kg_semana=mudanca))
        if r.proposal is not None:
            assert r.proposal.after.kcal >= 0.75 * r.summary.mean_tdee_kcal - 60  # teto de 25% de déficit (com folga do treino)
            assert r.proposal.after.kcal >= 800.0


# --- bulking e manutenção ------------------------------------------------------------------------------------------------


def test_bulking_lento_aumenta_o_superavit_dentro_da_faixa():
    s = _settings("bulking")
    r = wc.evaluate_week(_cenario(s, mudanca_kg_semana=0.0))
    assert r.verdict == V.SLOW and r.proposal is not None and r.proposal.delta_kcal > 0
    novo = r.proposal.changes[wc.CHANGE_SURPLUS_SHARE]
    assert BULK_SURPLUS_SHARE_RANGE[0] < novo <= BULK_SURPLUS_SHARE_RANGE[1]


def test_bulking_rapido_reduz_o_superavit_e_no_piso_nao_reduz_mais():
    s = _settings("bulking", surplus_share=0.15)
    r = wc.evaluate_week(_cenario(s, mudanca_kg_semana=0.9))
    assert r.verdict == V.FAST and r.proposal is not None and r.proposal.delta_kcal < 0
    assert r.proposal.changes[wc.CHANGE_SURPLUS_SHARE] >= BULK_SURPLUS_SHARE_RANGE[0]
    no_piso = _settings("bulking", surplus_share=BULK_SURPLUS_SHARE_RANGE[0])
    assert wc.evaluate_week(_cenario(no_piso, mudanca_kg_semana=0.9)).proposal is None


def test_manutencao_na_banda_e_fora_dela_so_sugere_revisar():
    s = _settings("maintenance")
    assert wc.evaluate_week(_cenario(s, mudanca_kg_semana=0.05)).verdict == V.ON_TRACK
    acima = wc.evaluate_week(_cenario(s, mudanca_kg_semana=0.6))
    abaixo = wc.evaluate_week(_cenario(s, mudanca_kg_semana=-0.6))
    assert acima.verdict == V.ABOVE_BAND and abaixo.verdict == V.BELOW_BAND
    assert acima.proposal is None and wc.REASON_MAINTENANCE_REVIEW in acima.reasons
    assert 0 < acima.summary.maintenance_band_kg < 0.3  # 5% do gasto em kg/semana


# --- D9: dados insuficientes e baixa aderência ---------------------------------------------------------------------------


def test_poucos_dias_completos_ou_sem_pesagem_e_inconclusivo_e_diz_o_que_faltou():
    poucos = wc.evaluate_week(_cenario(mudanca_kg_semana=-0.1, dias_completos=wc.MIN_COMPLETE_DAYS - 1))
    assert poucos.verdict == V.INCONCLUSIVE and wc.MISSING_FEW_COMPLETE_DAYS in poucos.missing and poucos.proposal is None
    sem_peso = wc.evaluate_week(_cenario(mudanca_kg_semana=None))
    assert sem_peso.verdict == V.INCONCLUSIVE and wc.MISSING_FEW_WEIGHINGS in sem_peso.missing
    esparsa = wc.evaluate_week(_cenario(mudanca_kg_semana=-0.1, pesagens_cada=14))  # 2 pesagens em 28 dias
    assert esparsa.verdict == V.INCONCLUSIVE


def test_o_minimo_de_dias_e_o_da_semana_valida_do_weekly_budget():
    assert wc.MIN_COMPLETE_DAYS == MIN_LOGGED_DAYS_FOR_WEEK
    assert wc.evaluate_week(_cenario(mudanca_kg_semana=-0.1, dias_completos=wc.MIN_COMPLETE_DAYS)).verdict != V.INCONCLUSIVE


def test_baixa_aderencia_nao_vira_corte_de_calorias():
    # Comeu 20% acima da meta e emagreceu devagar: o motor não corta (SLOW, sem proposta, motivo de aderência).
    r = wc.evaluate_week(_cenario(mudanca_kg_semana=-0.1, fator_ingestao=1.2))
    assert r.verdict == V.SLOW and r.proposal is None
    assert wc.REASON_ADHERENCE_OFF in r.reasons
    assert r.summary.kcal_within_tolerance is False


# --- D10: bioimpedância ---------------------------------------------------------------------------------------------------


def test_bioimpedancia_de_uma_semana_e_ignorada():
    musculo = {21: 36.0, 27: 35.0}  # 2 leituras em 6 dias: não é tendência
    r = wc.evaluate_week(_cenario(mudanca_kg_semana=-0.5, musculo=musculo))
    assert wc.REASON_LEAN_LOSS not in r.reasons and wc.REASON_BIA_SHORT_TREND in r.reasons


def test_perda_de_massa_magra_em_2_semanas_reduz_o_deficit_mesmo_no_ritmo():
    esperado = _esperado(_settings())
    musculo = {7: 36.0, 14: 35.6, 21: 35.2, 27: 34.9}  # ~-0,4 kg/semana de 36 kg (acima de 0,25%/semana)
    r = wc.evaluate_week(_cenario(mudanca_kg_semana=esperado, musculo=musculo))
    assert wc.REASON_LEAN_LOSS in r.reasons and r.proposal is not None
    assert r.proposal.delta_kcal > 0
    assert r.proposal.changes[wc.CHANGE_WEEKLY_RATE_SHARE] < 0.007


def test_perda_de_massa_magra_com_perda_lenta_so_segura_nao_corta():
    musculo = {7: 36.0, 14: 35.6, 21: 35.2, 27: 34.9}
    r = wc.evaluate_week(_cenario(mudanca_kg_semana=-0.1, musculo=musculo))
    assert r.verdict == V.SLOW and r.proposal is None and wc.REASON_BIA_HOLD in r.reasons


def test_bulking_ganho_de_gordura_em_2_semanas_reduz_o_superavit():
    s = _settings("bulking", surplus_share=0.15)
    esperado = _esperado(s)
    # O peso sobe no ritmo esperado; a bioimpedância (abaixo do %G de parada do bulking) mostra o ganho vindo da gordura.
    gordura = {7: 9.0, 14: 10.0, 21: 11.0, 27: 11.8}
    r = wc.evaluate_week(_cenario(s, mudanca_kg_semana=esperado, gordura=gordura))
    assert wc.REASON_FAT_GAIN in r.reasons and r.proposal is not None and r.proposal.delta_kcal < 0


# --- alvo atingido ---------------------------------------------------------------------------------------------------------


def test_alvo_de_peso_atingido_tem_veredito_proprio_sem_ajuste():
    s = _settings(goal_weight_kg=80.5)
    r = wc.evaluate_week(_cenario(s, mudanca_kg_semana=-0.4))
    assert r.verdict == V.GOAL_REACHED and r.proposal is None and wc.REASON_GOAL_WEIGHT_REACHED in r.reasons


def test_alvo_de_gordura_exige_duas_leituras_recentes():
    s = _settings(goal_body_fat_percent=12.0)
    uma = wc.evaluate_week(_cenario(s, mudanca_kg_semana=-0.4, gordura={26: 11.5}))
    assert uma.verdict != V.GOAL_REACHED
    duas = wc.evaluate_week(_cenario(s, mudanca_kg_semana=-0.4, gordura={20: 11.8, 26: 11.5}))
    assert duas.verdict == V.GOAL_REACHED and wc.REASON_GOAL_BODY_FAT_REACHED in duas.reasons


# --- gramas manuais, ciclo e prioridade ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("modo", ["fixed", "add_training"])
def test_gramas_manuais_mudam_so_carbo_e_gordura_nos_dois_modos(modo):
    s = _settings(manual_targets={"protein_g": 190.0, "carb_g": 240.0, "fat_g": 70.0}, manual_training_mode=modo)
    r = wc.evaluate_week(_cenario(s, mudanca_kg_semana=0.1))  # subiu, esperado perder: lento
    assert r.proposal is not None, r.reasons
    novas = r.proposal.changes[wc.CHANGE_MANUAL_TARGETS]
    assert novas["protein_g"] == 190.0 and novas["carb_g"] < 240.0 and novas["fat_g"] < 70.0
    assert 4 * novas["protein_g"] + 4 * novas["carb_g"] + 9 * novas["fat_g"] < 4 * 190 + 4 * 240 + 9 * 70
    aplicado = wc.apply_changes(s, r.proposal.changes)
    assert aplicado.ok and aplicado.settings.manual_macros.training_mode.value == modo  # o modo do treino é preservado


def test_gramas_manuais_no_piso_de_energia_nao_cortam():
    s = _settings(manual_targets={"protein_g": 150.0, "carb_g": 100.0, "fat_g": 40.0})  # 1.360 kcal: abaixo do teto de déficit
    r = wc.evaluate_week(_cenario(s, mudanca_kg_semana=0.2))
    assert r.proposal is None


def test_ciclo_de_dias_e_prioridade_sao_preservados_na_proposta():
    ciclo = {"0": "high", "1": "low", "2": "normal", "3": "high", "4": "low", "5": "normal", "6": "normal"}
    s = _settings(macro_priority="high_carb", cycle_days=ciclo)
    r = wc.evaluate_week(_cenario(s, mudanca_kg_semana=-0.05))
    assert r.proposal is not None
    novo = wc.apply_changes(s, r.proposal.changes).settings
    assert novo.cycle_days == s.cycle_days and novo.macro_priority == s.macro_priority
    assert check_cycle_floors(novo, SUJEITO)[0] == ()


def test_plano_publicado_fora_da_faixa_de_hoje_ainda_recebe_proposta():
    # A faixa pode mudar depois da publicação (o orquestrador só avisa): o que já estava fora não bloqueia o ajuste.
    base = _settings()
    fora = build_plan_settings(strategy="cutting", activity_level="moderate", protein_g_per_kg=2.0)
    assert fora.errors and wc.apply_changes(base, {wc.CHANGE_WEEKLY_RATE_SHARE: 0.008}).ok
    antigo = fora.settings
    assert wc.apply_changes(antigo, {wc.CHANGE_WEEKLY_RATE_SHARE: 0.008}).ok
    assert not wc.apply_changes(antigo, {wc.CHANGE_WEEKLY_RATE_SHARE: 0.5}).ok  # o que a mudança introduz bloqueia
    r = wc.evaluate_week(_cenario(antigo, mudanca_kg_semana=-0.1))
    assert r.proposal is not None


def test_passo_maximo_semanal_limita_o_ajuste_mesmo_com_diferenca_enorme():
    r = wc.evaluate_week(_cenario(mudanca_kg_semana=+1.5))  # engordando em vez de emagrecer
    assert r.verdict == V.SLOW and r.proposal is not None
    assert abs(r.proposal.delta_kcal) <= wc.MAX_KCAL_STEP_SHARE * r.summary.mean_tdee_kcal + 15


def test_plano_com_gasto_embutido_no_fator_tambem_ajusta():
    s = _settings(tdee_mode="included_in_factor")
    r = wc.evaluate_week(_cenario(s, mudanca_kg_semana=0.1))
    assert r.proposal is not None


# --- calendário e validade (D7, D8) ---------------------------------------------------------------------------------------------


def test_dia_do_checkin_e_semana_fechada():
    assert wc.due_day(date(2026, 10, 28), 0) == date(2026, 10, 26)  # quarta -> segunda
    assert wc.due_day(date(2026, 10, 26), 0) == date(2026, 10, 26)
    assert wc.due_day(date(2026, 10, 26), 4) == date(2026, 10, 23)  # sexta anterior
    assert wc.closed_week_start(date(2026, 10, 26)) == date(2026, 10, 19)
    assert wc.closed_week_start(date(2026, 10, 30)) == date(2026, 10, 19)  # sexta: a semana anterior à dela
    janela = wc.window_dates(date(2026, 10, 19))
    assert len(janela) == wc.BODY_WINDOW_DAYS and janela[-1] == date(2026, 10, 25) and janela[-7] == date(2026, 10, 19)


def test_proposta_expira_em_3_dias():
    criada = date(2026, 10, 26)
    assert wc.PROPOSAL_VALIDITY_DAYS == 3
    assert not wc.is_proposal_expired(criada, date(2026, 10, 29))
    assert wc.is_proposal_expired(criada, date(2026, 10, 30))


@pytest.mark.parametrize("valor,ok", [(0, True), (6, True), (7, False), (-1, False), (True, False), ("1", False)])
def test_dia_da_semana_do_checkin(valor, ok):
    assert wc.checkin_weekday_is_valid(valor) is ok


def test_resultado_serializa_em_json():
    r = wc.evaluate_week(_cenario(mudanca_kg_semana=-0.1))
    texto = json.dumps(r.to_dict())
    assert '"verdict": "slow"' in texto
