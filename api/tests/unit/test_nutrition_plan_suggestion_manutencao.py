"""Sugestão por IA (N6) e `maintenance_kcal_adjust` (N7): a sugestão não perde nem inventa o ajuste vigente.

Em manutenção o ajuste do formulário é PRESERVADO na configuração revisada; se a sugestão sai da manutenção, o ajuste deixa de
valer e isso aparece em "o que mudou". A IA nunca o sugere (fora de `SUGGESTION_FIELDS`) e ele não vai ao contexto."""

from __future__ import annotations

from app.domain.nutrition import labels as lb
from app.domain.nutrition import plan_suggestion as ps
from app.domain.nutrition import strategy as st
from app.domain.nutrition.macros import MEALS_PER_DAY_RANGE


def _manutencao(**extra):
    base = {
        "strategy": "maintenance",
        "advanced": False,
        "protein_g_per_kg": st.MAINT_PROTEIN_G_PER_KG_DEFAULT,
        "fat_kcal_share": st.MAINT_FAT_KCAL_SHARE_DEFAULT,
        "weekly_rate_share": None,
        "surplus_share": None,
        "carb_min_g_per_kg": None,
        "macro_priority": "balanced",
        "meals_per_day": MEALS_PER_DAY_RANGE[0],
        "reason_codes": ["limited_data"],
    }
    return {**base, **extra}


def _cutting():
    return _manutencao(
        strategy="cutting",
        protein_g_per_kg=st.CUT_PROTEIN_G_PER_KG_FFM_DEFAULT,
        fat_kcal_share=st.CUT_FAT_KCAL_SHARE_DEFAULT,
        weekly_rate_share=st.CUT_WEEKLY_RATE_SHARE_DEFAULT,
    )


def test_a_ia_nao_sugere_o_ajuste_e_ele_nao_vai_ao_contexto():
    assert ps.FIELD_MAINTENANCE_ADJUST not in ps.SUGGESTION_FIELDS
    atual = ps.normalize_current({"strategy": "maintenance", "maintenance_kcal_adjust": -150})
    assert atual[ps.FIELD_MAINTENANCE_ADJUST] == -150
    recusa = ps.review_suggestion(_manutencao(maintenance_kcal_adjust=-50), current=atual)
    assert recusa.settings is None  # a IA mandar o campo é forma inválida


def test_em_manutencao_a_sugestao_preserva_o_ajuste_vigente_sem_inventar_outro():
    atual = ps.normalize_current({"strategy": "maintenance", "maintenance_kcal_adjust": -150})
    revisao = ps.review_suggestion(_manutencao(), current=atual)
    assert revisao.settings is not None and revisao.settings.maintenance_kcal_adjust == -150
    assert ps.FIELD_MAINTENANCE_ADJUST not in {m.field for m in ps.suggestion_changes(atual, revisao.settings)}  # o ajuste não muda
    sem_ajuste = ps.review_suggestion(_manutencao(), current=ps.normalize_current({"strategy": "maintenance"}))
    assert sem_ajuste.settings.maintenance_kcal_adjust == 0  # não inventa


def test_ajuste_fora_da_faixa_ou_nao_inteiro_do_formulario_nao_vira_valor_da_sugestao():
    fora = ps.normalize_current({"maintenance_kcal_adjust": st.MAINT_KCAL_ADJUST_RANGE[1] + 100})
    assert ps.review_suggestion(_manutencao(), current=fora).settings.maintenance_kcal_adjust == 0
    assert ps.normalize_current({"maintenance_kcal_adjust": 12.5})[ps.FIELD_MAINTENANCE_ADJUST] == 0
    assert ps.normalize_current({"maintenance_kcal_adjust": "abc"})[ps.FIELD_MAINTENANCE_ADJUST] == 0


def test_sugestao_que_sai_da_manutencao_mostra_o_ajuste_deixando_de_valer():
    atual = ps.normalize_current({"strategy": "maintenance", "maintenance_kcal_adjust": -150})
    revisao = ps.review_suggestion(_cutting(), current=atual)
    assert revisao.settings.maintenance_kcal_adjust == 0
    mudancas = {m.field: m for m in ps.suggestion_changes(atual, revisao.settings)}
    assert mudancas[ps.FIELD_MAINTENANCE_ADJUST].before == -150 and mudancas[ps.FIELD_MAINTENANCE_ADJUST].after == 0
    itens = {i["field"]: i for i in lb.plan_suggestion_change_items(mudancas.values())}
    assert itens[ps.FIELD_MAINTENANCE_ADJUST]["before_label"] == "-150 kcal por dia"
    assert itens[ps.FIELD_MAINTENANCE_ADJUST]["after_label"] == "0 kcal por dia"
    assert itens[ps.FIELD_MAINTENANCE_ADJUST]["label"] != ps.FIELD_MAINTENANCE_ADJUST


def test_formulario_sem_ajuste_nao_gera_linha_nova_em_o_que_mudou():
    atual = ps.normalize_current({"strategy": "maintenance"})
    revisao = ps.review_suggestion(_cutting(), current=atual)
    assert ps.FIELD_MAINTENANCE_ADJUST not in {m.field for m in ps.suggestion_changes(atual, revisao.settings)}
