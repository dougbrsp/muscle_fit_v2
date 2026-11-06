"""Lote G do motor de nutrição: fechamento manual do dia, correção manual de atividades e resumo adaptativo do
relatório. Módulo puro: sem banco. Donos: `adaptive_energy`, `activity_energy`, `report_window`."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest

from app.domain.nutrition import activity_energy as ae
from app.domain.nutrition import adaptive_energy as adp
from app.domain.nutrition import export_for_frontend
from app.domain.nutrition.activity_energy import ActivityCorrection, ActivityFlag, RawActivity
from app.domain.nutrition.adaptive_energy import DayInput, DayState, DayStatus
from app.domain.nutrition.report_window import adaptive_report_summary, window_report

TMB = 2020.0
DIA = date(2026, 11, 3)
SYNC = datetime(2026, 11, 4, 5, 45)
DESDE = date(2026, 10, 1)


def _raw(id="a1", *, inicio=datetime(2026, 11, 3, 18, 0), kcal=428.0, modo="gross", externo=None, tipo="strength"):
    return RawActivity(id=id, start=inicio, duration_seconds=3600, kind=tipo, name="x", calories_kcal=kcal,
                       calorie_mode=modo, external_id=externo)


def _dia(atividades, correcoes=()):
    return ae.summarize_days(atividades, TMB, [DIA], SYNC, DESDE, (), correcoes)[0]


# --- fechamento manual ------------------------------------------------------------------------------------


def test_sem_fechamento_vale_a_regra_dos_70_por_cento():
    abaixo = adp.COMPLETE_MIN_INTAKE_BMR_SHARE * TMB - 1
    assert adp.classify_day_with_source(DayState.LOGGED, abaixo, TMB) == (DayStatus.PARTIAL, None)
    assert adp.classify_day_with_source(DayState.LOGGED, 2000, TMB) == (DayStatus.COMPLETE, adp.COMPLETENESS_SOURCE_INTAKE_RULE)


def test_dia_fechado_conta_como_completo_abaixo_dos_70_e_com_macros_incompletas():
    abaixo = adp.COMPLETE_MIN_INTAKE_BMR_SHARE * TMB - 1
    for estado in (DayState.LOGGED, DayState.INCOMPLETE):
        assert adp.classify_day_with_source(estado, abaixo, TMB, True) == (DayStatus.COMPLETE, adp.COMPLETENESS_SOURCE_MANUAL_CLOSE)
    assert adp.classify_day(DayState.INCOMPLETE, abaixo, TMB, manually_closed=True) is DayStatus.COMPLETE
    assert adp.classify_day(DayState.INCOMPLETE, abaixo, TMB) is DayStatus.PARTIAL  # assinatura antiga intacta


def test_fechar_nao_inventa_ingestao_nem_abre_dia_sem_registro():
    assert adp.classify_day_with_source(DayState.LOGGED, None, TMB, True)[0] is DayStatus.PARTIAL
    assert adp.classify_day_with_source(DayState.NO_RECORD, 0, TMB, True) == (DayStatus.MISSING, None)
    assert adp.classify_day_with_source(DayState.OPEN_TODAY, 1500, TMB, True) == (DayStatus.MISSING, None)


def test_shadow_day_state_com_fechamento():
    hoje = date(2026, 11, 6)
    assert adp.shadow_day_state(hoje, hoje, True, False) is DayState.OPEN_TODAY
    # Fechar HOJE não muda o próprio dia (meta de hoje estável); vale a partir de amanhã.
    assert adp.shadow_day_state(hoje, hoje, True, False, manually_closed=True) is DayState.OPEN_TODAY
    assert adp.shadow_day_state(hoje, hoje, False, False, manually_closed=True) is DayState.OPEN_TODAY
    assert adp.shadow_day_state(hoje + timedelta(days=1), hoje, True, True, manually_closed=True) is DayState.OPEN_TODAY
    ontem = hoje - timedelta(days=1)
    assert adp.shadow_day_state(ontem, hoje, True, False) is DayState.INCOMPLETE
    assert adp.shadow_day_state(ontem, hoje, True, False, manually_closed=True) is DayState.LOGGED


def _entradas(n, fechados=(), ingestao=1200.0):
    dia0 = date(2026, 10, 1)
    return [
        DayInput(day=dia0 + timedelta(days=i), day_state=DayState.LOGGED, intake_kcal=ingestao, bmr_kcal=TMB,
                 weights_kg=(80.0,), manually_closed=i in fechados)
        for i in range(n)
    ]


def test_replay_registra_origem_e_conta_dias_fechados():
    estados = adp.replay(_entradas(5, fechados={1, 3}))
    origens = [w.completeness_source for w in estados[-1].window]
    assert origens == [adp.COMPLETENESS_SOURCE_MANUAL_CLOSE if i in (1, 3) else None for i in range(5)]
    assert estados[-1].manually_closed_days == 2 and estados[-1].complete_days == 2 == estados[-1].valid_days


def test_estado_publico_nao_muda_com_o_fechamento_manual():
    """to_dict/hash intactos: as contagens novas são propriedades, não chaves."""
    chaves = set(adp.replay(_entradas(3))[-1].to_dict())
    assert "manually_closed_days" not in chaves and "completeness_source" not in chaves


# --- correção de atividades -------------------------------------------------------------------------------


def test_excluir_lista_marca_e_nao_soma():
    dia = _dia([_raw("a1", kcal=300, modo="active"), _raw("a2", inicio=datetime(2026, 11, 3, 7, 0), kcal=200, modo="active")],
               [ActivityCorrection("a1", "exclude")])
    por_id = {a.id: a for a in dia.activities}
    assert por_id["a1"].excluded and not por_id["a1"].counts_in_total
    assert ActivityFlag.EXCLUDED_BY_USER in por_id["a1"].flags
    assert dia.active_kcal == 200.0 and len(dia.activities) == 2
    d = por_id["a1"].to_dict()
    assert d["excluded"] is True and d["correction"] == {"action": "exclude", "energy_kind": None}
    json.dumps(dia.to_dict())


def test_excluida_nao_participa_da_deduplicacao():
    a = _raw("a1", externo="e1", kcal=300, modo="active")
    b = _raw("a2", externo="e1", kcal=300, modo="active")
    assert _dia([a, b]).active_kcal == 300.0
    # Exclui a primeira (a "original"): a segunda deixa de ser duplicata e conta.
    com = _dia([a, b], [ActivityCorrection("a1", "exclude")])
    por_id = {x.id: x for x in com.activities}
    assert por_id["a2"].duplicate_of is None and por_id["a2"].counts_in_total
    assert com.active_kcal == 300.0


def test_reclassificar_muda_o_modo_antes_de_normalizar():
    bruto = _dia([_raw(kcal=428.0, modo="gross")])
    assert bruto.active_kcal == pytest.approx(428.0 - TMB / 1440 * 60)
    como_ativa = _dia([_raw(kcal=428.0, modo="gross")], [ActivityCorrection("a1", "reclassify", "active")])
    assert como_ativa.active_kcal == 428.0
    atividade = como_ativa.activities[0]
    assert atividade.correction == ActivityCorrection("a1", "reclassify", "active") and not atividade.excluded
    como_total = _dia([_raw(kcal=428.0, modo="unknown")], [ActivityCorrection("a1", "reclassify", "gross")])
    assert ActivityFlag.REDUCED_PRECISION not in como_total.activities[0].flags  # agora o modo é conhecido


def test_correcao_invalida_ou_de_id_inexistente_e_ignorada():
    base = _dia([_raw(kcal=428.0, modo="gross")])
    for c in (ActivityCorrection("zzz", "exclude"), ActivityCorrection("a1", "reclassify"),
              ActivityCorrection("a1", "reclassify", "xyz")):
        dia = _dia([_raw(kcal=428.0, modo="gross")], [c])
        assert dia.active_kcal == base.active_kcal and dia.activities[0].correction is None


def test_apply_activity_corrections_preserva_ordem_e_ultima_vale():
    brutas = [_raw("a1"), _raw("a2", inicio=datetime(2026, 11, 3, 9, 0))]
    out = ae.apply_activity_corrections(
        brutas, [ActivityCorrection("a1", "exclude"), ActivityCorrection("a1", "reclassify", "active")]
    )
    assert [r.id for r in out] == ["a1", "a2"]
    assert out[0].calorie_mode == "active" and out[0].correction.action == "reclassify" and out[1].correction is None


def test_dia_so_com_excluidas_vira_descanso_confirmado():
    dia = _dia([_raw("a1", kcal=300, modo="active")], [ActivityCorrection("a1", "exclude")])
    assert dia.confirmed and dia.active_kcal == 0.0 and len(dia.activities) == 1


def test_active_kcal_by_day_aceita_correcoes():
    r = ae.active_kcal_by_day([_raw("a1", kcal=300, modo="active")], TMB, [DIA], SYNC, DESDE, (), [ActivityCorrection("a1", "exclude")])
    assert r == {DIA: 0.0}


# --- relatório --------------------------------------------------------------------------------------------


def test_resumo_adaptativo_do_relatorio():
    estados = adp.replay(_entradas(6, fechados={0, 2}))
    dias = [e.day for e in estados]
    r = adaptive_report_summary(dias, estados)
    assert r["manually_closed_days"] == 2 and r["complete_days"] == 2 and r["days_with_state"] == 6
    assert r["base_source"] == adp.REPORT_BASIS_BOOTSTRAP and r["as_of"] == dias[-1]
    assert r["base_kcal"] is not None and r["base_source_label"]
    assert r["activity_adjustment_kcal"] is not None and "confidence_level" in r
    assert adaptive_report_summary([date(2020, 1, 1)], estados) is None
    assert adaptive_report_summary(dias, []) is None


def test_window_report_traz_adaptive_ou_none():
    dias = [date(2026, 11, 1), date(2026, 11, 2)]
    sem = window_report(dias, date(2026, 11, 6), None, [None, None], [None, None], [None, None], [None, None])
    assert sem["adaptive"] is None
    estados = adp.replay(_entradas(3))
    com = window_report([e.day for e in estados], date(2026, 11, 6), None, [None] * 3, [None] * 3, [None] * 3, [None] * 3, estados)
    assert com["adaptive"]["days_with_state"] == 3


# --- rótulos ----------------------------------------------------------------------------------------------


def test_rotulos_novos_no_json_do_frontend():
    rot = export_for_frontend()["adaptive_energy_labels"]
    assert set(rot["completeness_source"]) == {adp.COMPLETENESS_SOURCE_INTAKE_RULE, adp.COMPLETENESS_SOURCE_MANUAL_CLOSE}
    assert set(rot["activity_correction_action"]) == set(ae.CORRECTION_ACTIONS)
    assert set(rot["activity_correction_energy_kind"]) == set(ae.CORRECTION_ENERGY_KINDS)
    assert set(rot["report_adaptive_basis"]) == {adp.REPORT_BASIS_ADAPTIVE, adp.REPORT_BASIS_BOOTSTRAP}
    assert "excluded_by_user" in rot["activity_flag"]


def test_fechar_hoje_nao_muda_a_base_de_hoje_e_vale_no_dia_seguinte():
    hoje = date(2026, 10, 6)
    abaixo = 1000.0  # < 70% da TMB: so completa pelo fechamento

    def estados(fechado_hoje, ingestao_hoje):
        entradas = []
        for i in range(6):
            dia = date(2026, 10, 1) + timedelta(days=i)
            registro = DayState.LOGGED if dia < hoje else adp.shadow_day_state(dia, hoje, True, True, manually_closed=fechado_hoje)
            kcal = 2000.0 if dia < hoje else ingestao_hoje
            entradas.append(DayInput(day=dia, day_state=registro, intake_kcal=kcal, bmr_kcal=TMB, weights_kg=(80.0,),
                                     manually_closed=fechado_hoje and dia == hoje))
        return adp.replay(entradas)

    antes = adp.estimate_for_day(estados(False, 1500.0), hoje, hoje)
    depois = adp.estimate_for_day(estados(True, abaixo), hoje, hoje)
    assert antes.estimated_kcal == depois.estimated_kcal and antes.base_kcal == depois.base_kcal
    # No dia seguinte o dia fechado ja e passado e conta como completo.
    amanha = hoje + timedelta(days=1)
    entradas = [DayInput(day=hoje, day_state=adp.shadow_day_state(hoje, amanha, True, False, True), intake_kcal=abaixo,
                         bmr_kcal=TMB, weights_kg=(80.0,), manually_closed=True)]
    assert adp.replay(entradas)[-1].window[-1].completeness_source == adp.COMPLETENESS_SOURCE_MANUAL_CLOSE


def test_codigos_exportados_para_o_frontend_vem_do_dono():
    codigos = export_for_frontend()["adaptive_codes"]
    assert codigos["completeness_source_manual_close"] == adp.COMPLETENESS_SOURCE_MANUAL_CLOSE
    assert codigos["correction_energy_kind_active"] == "active" and codigos["correction_energy_kind_gross"] == "gross"
    assert set(ae.CORRECTION_ENERGY_KINDS) == {ae.CalorieMode.ACTIVE.value, ae.CalorieMode.GROSS.value}
