"""Motor da sugestão de metas por IA (`domain/nutrition/plan_suggestion`, N6, D3): dentro da faixa, fora da faixa sem
correção silenciosa, campo proibido, grama ou kcal rejeitadas, "o que mudou" e o contexto mínimo."""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.domain.nutrition import labels as lb
from app.domain.nutrition import plan_suggestion as ps
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.macros import MEALS_PER_DAY_RANGE
from app.domain.nutrition.subject import Reading

HOJE = date(2026, 11, 3)


def _sugestao(**extra):
    base = {
        "strategy": "cutting",
        "advanced": False,
        "protein_g_per_kg": st.CUT_PROTEIN_G_PER_KG_FFM_DEFAULT,
        "fat_kcal_share": st.CUT_FAT_KCAL_SHARE_DEFAULT,
        "weekly_rate_share": st.CUT_WEEKLY_RATE_SHARE_DEFAULT,
        "surplus_share": None,
        "carb_min_g_per_kg": None,
        "macro_priority": "balanced",
        "meals_per_day": MEALS_PER_DAY_RANGE[0],
        "reason_codes": ["goal_weight_below_current"],
    }
    return {**base, **extra}


def _bulking(**extra):
    return _sugestao(
        **{
            "strategy": "bulking",
            "protein_g_per_kg": st.BULK_PROTEIN_G_PER_KG_DEFAULT,
            "fat_kcal_share": st.BULK_FAT_KCAL_SHARE_DEFAULT,
            "weekly_rate_share": None,
            "surplus_share": st.BULK_SURPLUS_SHARE_DEFAULT,
            "carb_min_g_per_kg": st.BULK_CARB_MIN_G_PER_KG,
            **extra,
        }
    )


def test_dentro_da_faixa_vira_as_configuracoes_do_motor():
    revisao = ps.review_suggestion(_sugestao(macro_priority="high_carb"))
    assert revisao.ok and revisao.errors == ()
    assert revisao.settings.strategy == st.Strategy.CUTTING
    assert revisao.settings.macro_priority == st.MacroPriority.HIGH_CARB
    assert revisao.reasons == (ps.SuggestionReason.GOAL_WEIGHT_BELOW_CURRENT,)
    assert set(ps.suggestion_fields(revisao.settings)) == set(ps.SUGGESTION_FIELDS)


@pytest.mark.parametrize(
    ("alteracao", "codigo"),
    [
        ({"protein_g_per_kg": st.CUT_PROTEIN_G_PER_KG_FFM_RANGE[1] + 1}, "param_out_of_range:protein_g_per_kg"),
        ({"fat_kcal_share": st.CUT_FAT_KCAL_SHARE_RANGE[0] / 2}, "param_out_of_range:fat_kcal_share"),
        ({"weekly_rate_share": st.CUT_WEEKLY_RATE_SHARE_RANGE[1] * 2}, "param_out_of_range:weekly_rate_share"),
        ({"meals_per_day": MEALS_PER_DAY_RANGE[1] + 1}, "param_out_of_range:meals_per_day"),
        ({"surplus_share": st.BULK_SURPLUS_SHARE_DEFAULT}, "param_not_applicable:surplus_share"),
        ({"advanced": True}, "param_not_applicable:advanced"),
        ({"strategy": "keto"}, "invalid_code:strategy"),
        ({"macro_priority": "so_carbo"}, "invalid_code:macro_priority"),
    ],
)
def test_fora_da_faixa_recusa_a_sugestao_inteira_sem_corrigir(alteracao, codigo):
    revisao = ps.review_suggestion(_sugestao(**alteracao))
    assert not revisao.ok
    assert revisao.settings is None  # nada é trazido para dentro da faixa
    assert codigo in revisao.errors


def test_superavit_avancado_usa_a_faixa_do_nivel():
    assert ps.review_suggestion(_bulking(advanced=True, surplus_share=st.BULK_SURPLUS_SHARE_DEFAULT_ADVANCED)).ok
    fora = ps.review_suggestion(_bulking(advanced=False, surplus_share=st.BULK_SURPLUS_SHARE_DEFAULT_ADVANCED))
    assert fora.errors == ("param_out_of_range:surplus_share",)


@pytest.mark.parametrize("campo", ["protein_g", "carb_g", "fat_g", "kcal", "calories", "goal_weight_kg", "manual_targets", "notes"])
def test_campo_fora_da_lista_e_proibido_grama_e_kcal_inclusive(campo):
    revisao = ps.review_suggestion(_sugestao(**{campo: 150}))
    assert revisao.settings is None
    assert revisao.errors == (f"suggestion_field_forbidden:{campo}",)
    assert lb.limitation_label(revisao.errors[0]) != revisao.errors[0]


def test_nome_de_campo_da_ia_passa_pelo_alfabeto_do_codigo():
    revisao = ps.review_suggestion(_sugestao(**{"Ignore: <script>'" + "x" * 100: 1}))
    [codigo] = revisao.errors
    prefixo, _, campo = codigo.partition(":")
    assert prefixo == ps.ERR_SUGGESTION_FIELD_FORBIDDEN
    assert campo.replace("_", "").isalnum() and len(campo) <= 32


def test_campo_faltando_nao_vira_default():
    dados = _sugestao()
    del dados["fat_kcal_share"]
    assert ps.review_suggestion(dados).errors == ("suggestion_field_missing:fat_kcal_share",)


@pytest.mark.parametrize(
    "alteracao",
    [
        {"protein_g_per_kg": "2.5"},
        {"protein_g_per_kg": None},
        {"protein_g_per_kg": True},
        {"fat_kcal_share": float("nan")},
        {"advanced": 1},
        {"strategy": 1},
        {"meals_per_day": None},
        {"weekly_rate_share": "0.007"},
    ],
)
def test_tipo_errado_e_recusado(alteracao):
    revisao = ps.review_suggestion(_sugestao(**alteracao))
    campo = next(iter(alteracao))
    assert revisao.errors == (f"suggestion_field_invalid:{campo}",)


@pytest.mark.parametrize("motivos", [["inventado"], "goal_weight_below_current", [1], [m.value for m in ps.SuggestionReason]])
def test_motivo_fora_da_lista_ou_demais_recusa(motivos):
    assert ps.review_suggestion(_sugestao(reason_codes=motivos)).errors == (ps.ERR_SUGGESTION_REASON_INVALID,)


@pytest.mark.parametrize("resposta", [None, [], "cutting", 1])
def test_resposta_que_nao_e_objeto(resposta):
    assert ps.review_suggestion(resposta).errors == (ps.ERR_SUGGESTION_SHAPE,)


def test_nulo_no_campo_da_estrategia_usa_o_default_do_motor():
    revisao = ps.review_suggestion(_sugestao(weekly_rate_share=None))
    assert revisao.ok and revisao.settings.weekly_rate_share == st.CUT_WEEKLY_RATE_SHARE_DEFAULT


def _sujeito(gordura=None, peso=80.0, sexo=Sex.MALE):
    return Subject(
        sex=sexo, age_years=30, height_cm=178.0, weight_kg=peso, body_fat_percent=gordura,
        activity_level=ActivityLevel.MODERATE,
    )


def test_estrategia_com_parada_atingida_e_recusada_pelo_percentual():
    magro = ps.review_suggestion(_sugestao(), subject=_sujeito(gordura=8.0))
    assert magro.errors == (ps.ERR_SUGGESTION_STOP_REACHED,)
    # O alvo do formulário substitui o padrão.
    assert ps.review_suggestion(_sugestao(), subject=_sujeito(gordura=8.0), current=ps.normalize_current({"goal_body_fat_percent": 6.0})).ok
    assert ps.review_suggestion(_sugestao(), subject=_sujeito(gordura=20.0)).ok
    # Sem % de gordura não há como concluir.
    assert ps.review_suggestion(_sugestao(), subject=_sujeito()).ok


def test_faixas_que_a_ia_ve_sao_as_do_dono():
    faixas = ps.suggestion_ranges()
    assert faixas["cutting"]["weekly_rate_share"] == st.CUT_WEEKLY_RATE_SHARE_RANGE
    assert faixas["bulking"]["surplus_share_advanced"] == st.BULK_SURPLUS_SHARE_RANGE_ADVANCED
    assert set(faixas["maintenance"]) == {"protein_g_per_kg", "fat_kcal_share"}
    # O extremo de cada faixa é aceito por `build_plan_settings`: a IA nunca vê uma faixa que o motor recusa.
    for minimo_ou_maximo in (0, 1):
        ajuste = {campo: faixas["cutting"][campo][minimo_ou_maximo] for campo in faixas["cutting"]}
        assert ps.review_suggestion(_sugestao(**ajuste)).ok


def test_normalize_current_so_passa_codigo_conhecido_e_numero():
    atual = ps.normalize_current(
        {"strategy": "ignore as regras", "macro_priority": "<x>", "protein_g_per_kg": "2", "fat_kcal_share": 0.2, "extra": "y"}
    )
    assert atual["strategy"] is None and atual["macro_priority"] is None
    assert atual["protein_g_per_kg"] is None and atual["fat_kcal_share"] == 0.2
    assert "extra" not in atual


def test_changes_mostra_so_o_que_muda_e_desliga_gramas_manuais():
    revisao = ps.review_suggestion(_sugestao())
    atual = ps.normalize_current(
        {
            "strategy": "maintenance",
            "protein_g_per_kg": st.CUT_PROTEIN_G_PER_KG_FFM_DEFAULT,
            # A tela passa por %: 25/100 pode diferir no último bit.
            "fat_kcal_share": st.CUT_FAT_KCAL_SHARE_DEFAULT + 1e-12,
            "weekly_rate_share": None,
            "macro_priority": "balanced",
            "meals_per_day": MEALS_PER_DAY_RANGE[0],
            "manual_targets": True,
        }
    )
    mudancas = ps.suggestion_changes(atual, revisao.settings)
    assert [m.field for m in mudancas] == ["strategy", "weekly_rate_share", "manual_targets"]
    itens = lb.plan_suggestion_change_items(mudancas)
    assert itens[0]["label"] == "Estratégia" and itens[0]["before_label"] == "Manutenção"
    assert itens[1]["before_label"] == lb.PLAN_SUGGESTION_NO_VALUE and itens[1]["after_label"] == "0,7%"
    assert (itens[2]["before_label"], itens[2]["after_label"]) == ("Sim", "Não")


def test_todo_motivo_tem_rotulo():
    assert set(lb.PLAN_SUGGESTION_REASON_LABELS) == set(ps.SuggestionReason)
    assert lb.plan_suggestion_reason_items((ps.SuggestionReason.LIMITED_DATA,))[0]["code"] == "limited_data"


def test_contexto_nao_leva_identificador_nem_data_de_nascimento():
    pesos = [Reading(80.0 - i * 0.1, HOJE - timedelta(days=21 - i * 7)) for i in range(4)]
    contexto = ps.build_context(
        _sujeito(gordura=19.26),
        bmr_kcal=1800.4,
        tdee_kcal=2500.6,
        weights=ps.daily_weights(pesos, HOJE),
        past_training_kcal=[300.0, 0.0, None, 0.0],
        current=ps.normalize_current({"strategy": "cutting", "goal_weight_kg": 75}),
    )
    assert set(contexto) == {"profile", "energy", "history", "current_form"}
    assert set(contexto["profile"]) == {
        "sex", "age_years", "height_cm", "weight_kg", "body_fat_percent", "fat_mass_known", "activity_level",
    }
    texto = str(contexto)
    for proibido in ("name", "email", "username", "birth", "user_id"):
        assert proibido not in texto
    assert contexto["profile"]["body_fat_percent"] == 19.3
    assert contexto["energy"] == {"bmr_kcal": 1800.0, "tdee_kcal": 2501.0}
    assert contexto["history"]["weight_readings"] == 4
    assert contexto["history"]["weekly_weight_change_share"] == pytest.approx(-0.00125, abs=1e-4)
    assert contexto["history"]["training"] == {"window_days": 4, "training_days": 2, "mean_training_kcal_per_day": 100.0}
    assert contexto["current_form"]["goal_weight_kg"] == 75.0


def test_tendencia_sem_pesagens_suficientes_e_nula():
    assert ps.weekly_weight_change_share(ps.daily_weights([Reading(80.0, HOJE)], HOJE), 80.0) is None
    assert ps.training_summary([])["mean_training_kcal_per_day"] is None


# ---------------------------------------------------------------------------
# Limites por campo e estratégia
# ---------------------------------------------------------------------------


def _manutencao(**extra):
    return _sugestao(
        **{
            "strategy": "maintenance",
            "protein_g_per_kg": st.MAINT_PROTEIN_G_PER_KG_DEFAULT,
            "fat_kcal_share": st.MAINT_FAT_KCAL_SHARE_DEFAULT,
            "weekly_rate_share": None,
            **extra,
        }
    )


def _casos_de_faixa():
    bases = {"cutting": _sugestao, "bulking": _bulking, "maintenance": _manutencao}
    for estrategia, faixas in ps.suggestion_ranges().items():
        for chave, faixa in faixas.items():
            avancado = chave.endswith("_advanced")
            yield bases[estrategia], chave.removesuffix("_advanced"), faixa, avancado


CASOS_DE_FAIXA = list(_casos_de_faixa())


@pytest.mark.parametrize(("base", "campo", "faixa", "avancado"), CASOS_DE_FAIXA)
def test_limites_da_faixa_sao_inclusivos_e_o_lado_de_fora_recusa(base, campo, faixa, avancado):
    extras = {"advanced": True, "surplus_share": st.BULK_SURPLUS_SHARE_DEFAULT_ADVANCED} if avancado else {}
    for limite in faixa:
        revisao = ps.review_suggestion(base(**{**extras, campo: limite}))
        assert revisao.ok, (campo, limite, revisao.errors)
        assert getattr(revisao.settings, campo) == limite  # o limite cabe na resolução: nada se move
    for fora in (faixa[0] * 0.99, faixa[1] * 1.01):
        revisao = ps.review_suggestion(base(**{**extras, campo: fora}))
        assert revisao.settings is None
        assert f"param_out_of_range:{campo}" in revisao.errors


def test_limites_de_refeicoes_e_valores_invalidos():
    minimo, maximo = MEALS_PER_DAY_RANGE
    assert ps.review_suggestion(_sugestao(meals_per_day=minimo)).settings.meals_per_day == minimo
    assert ps.review_suggestion(_sugestao(meals_per_day=maximo)).settings.meals_per_day == maximo
    for fora in (minimo - 1, maximo + 1, minimo + 0.5):
        assert ps.review_suggestion(_sugestao(meals_per_day=fora)).errors == ("param_out_of_range:meals_per_day",)
    for invalido in (float("inf"), float("nan")):
        assert ps.review_suggestion(_sugestao(meals_per_day=invalido)).errors == ("suggestion_field_invalid:meals_per_day",)


def test_motor_devolve_o_valor_na_resolucao_da_tela():
    revisao = ps.review_suggestion(_sugestao(protein_g_per_kg=2.556, fat_kcal_share=0.2512, weekly_rate_share=0.00712))
    campos = ps.suggestion_fields(revisao.settings)
    assert (campos["protein_g_per_kg"], campos["fat_kcal_share"], campos["weekly_rate_share"]) == (2.56, 0.251, 0.0071)
    # Quem está dentro da faixa não sai dela ao arredondar.
    assert ps.review_suggestion(_sugestao(fat_kcal_share=0.29996)).settings.fat_kcal_share == 0.3
    # "O que mudou" mostra o valor efetivo.
    [mudanca] = [m for m in ps.suggestion_changes(ps.normalize_current({}), revisao.settings) if m.field == "protein_g_per_kg"]
    assert mudanca.after == 2.56


def test_todo_limite_de_faixa_cabe_na_resolucao():
    for _base, campo, faixa, _avancado in CASOS_DE_FAIXA:
        casas = ps.SUGGESTION_DECIMALS[campo]
        assert all(round(limite, casas) == limite for limite in faixa), campo


def test_resolucao_do_motor_e_a_que_o_formulario_mostra():
    """O `decimalScale` do campo em % tem duas casas a menos que a fração."""
    tela = (Path(__file__).resolve().parents[3] / "frontend/src/components/nutrition/PlanForm.tsx").read_text(encoding="utf-8")
    for rotulo, campo, a_mais in (
        ("Proteína (g/kg)", "protein_g_per_kg", 0),
        ("Gordura (% das kcal)", "fat_kcal_share", 2),
        ("Ritmo de perda (% do peso por semana)", "weekly_rate_share", 2),
        ("Superávit (% do gasto)", "surplus_share", 2),
        ("Carboidrato mínimo (g/kg)", "carb_min_g_per_kg", 0),
    ):
        casas = re.search(rf'label="{re.escape(rotulo)}".*?decimalScale=\{{(\d+)\}}', tela, re.S).group(1)
        assert int(casas) + a_mais == ps.SUGGESTION_DECIMALS[campo], rotulo


# ---------------------------------------------------------------------------
# Parada, motivos, formulário
# ---------------------------------------------------------------------------


def test_parada_no_bulking_e_no_sexo_feminino():
    assert ps.review_suggestion(_bulking(), subject=_sujeito(gordura=15.0)).errors == (ps.ERR_SUGGESTION_STOP_REACHED,)
    assert ps.review_suggestion(_bulking(), subject=_sujeito(gordura=14.9)).ok
    assert ps.review_suggestion(_bulking(), subject=_sujeito(gordura=25.0, sexo=Sex.FEMALE)).errors == (ps.ERR_SUGGESTION_STOP_REACHED,)
    assert ps.review_suggestion(_bulking(), subject=_sujeito(gordura=24.9, sexo=Sex.FEMALE)).ok
    assert ps.review_suggestion(_sugestao(), subject=_sujeito(gordura=18.0, sexo=Sex.FEMALE)).errors == (ps.ERR_SUGGESTION_STOP_REACHED,)
    assert ps.review_suggestion(_sugestao(), subject=_sujeito(gordura=18.1, sexo=Sex.FEMALE)).ok


@pytest.mark.parametrize("alvo", [999, 0, -3, "x", None])
def test_alvo_de_gordura_invalido_cai_no_padrao_do_motor(alvo):
    atual = ps.normalize_current({"goal_body_fat_percent": alvo})
    assert ps.review_suggestion(_sugestao(), subject=_sujeito(gordura=8.0), current=atual).errors == (
        ps.ERR_SUGGESTION_STOP_REACHED,
    )


def test_lista_vazia_de_motivos_vale_e_repetido_sai_mas_conta_no_teto():
    assert ps.review_suggestion(_sugestao(reason_codes=[])).reasons == ()
    assert ps.review_suggestion(_sugestao(reason_codes=["limited_data", "limited_data"])).reasons == (
        ps.SuggestionReason.LIMITED_DATA,
    )
    assert ps.review_suggestion(_sugestao(reason_codes=["limited_data"] * (ps.REASONS_MAX + 1))).errors == (
        ps.ERR_SUGGESTION_REASON_INVALID,
    )


def test_changes_com_ponto_flutuante_estado_vazio_e_gramas_desligadas():
    revisao = ps.review_suggestion(_sugestao())
    novos = ps.suggestion_fields(revisao.settings)
    # A tela manda 0,7/100, diferente de 0,007 em ponto flutuante: não é mudança.
    igual = ps.normalize_current({**novos, "weekly_rate_share": 0.7 / 100, "manual_targets": False})
    assert ps.suggestion_changes(igual, revisao.settings) == ()
    # Formulário vazio: muda tudo que tem valor; o que continua None ou False não entra.
    vazio = ps.suggestion_changes(ps.normalize_current(None), revisao.settings)
    assert {m.field for m in vazio} == {campo for campo, valor in novos.items() if valor not in (None, False)}
    assert all(m.before is None for m in vazio)
    assert lb.plan_suggestion_change_items(vazio)[0]["before_label"] == lb.PLAN_SUGGESTION_NO_VALUE


def test_normalize_current_barra_numero_absurdo_e_codigo_desconhecido():
    atual = ps.normalize_current(
        {"protein_g_per_kg": 1e300, "fat_kcal_share": -1, "activity_level": "very_active", "tdee_mode": "x", "bmr_formula": 3}
    )
    assert atual["protein_g_per_kg"] is None and atual["fat_kcal_share"] is None
    assert atual["activity_level"] == "very_active" and atual["tdee_mode"] is None and atual["bmr_formula"] is None
    assert ps.normalize_current({"goal_weight_kg": ps.CURRENT_NUMBER_MAX})["goal_weight_kg"] == ps.CURRENT_NUMBER_MAX


def test_o_que_o_formulario_fixa_entra_na_referencia_e_na_revisao():
    atual = ps.normalize_current({"activity_level": "very_active", "tdee_mode": "included_in_factor", "bmr_formula": "cunningham"})
    for settings in (ps.reference_settings(atual), ps.review_suggestion(_sugestao(), current=atual).settings):
        assert settings.activity_level.value == "very_active"
        assert settings.tdee_mode.value == "included_in_factor"
        assert settings.bmr_formula.value == "cunningham"
    padrao = ps.reference_settings()
    assert padrao.activity_level is None


def test_contexto_diz_se_a_massa_gorda_e_conhecida():
    def _contexto(sujeito):
        return ps.build_context(sujeito, bmr_kcal=None, tdee_kcal=None, weights=[], past_training_kcal=[], current={})

    assert _contexto(_sujeito(gordura=20.0))["profile"]["fat_mass_known"] is True
    assert _contexto(_sujeito())["profile"]["fat_mass_known"] is False
