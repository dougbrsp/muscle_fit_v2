"""Testes-ouro dos 3 modos legados de gasto (`explicit_training`, `included_in_factor`, `auto_intervals`).

Valores capturados em 2026-11-05 com o código INTOCADO (develop @ bec68a25), antes do lote da nutrição adaptativa.
São a trava de que os modos legados não mudam nos próximos lotes: gasto (`tdee`), meta calórica, macros e o
`inputs_hash` das metas (`nutrition_targets_service.inputs_fingerprint`). Se um valor aqui precisar mudar, a mudança
alterou o resultado de planos publicados e o hash das metas gravadas (regravaria hoje/ontem de todos os usuários):
pare e reavalie, não atualize o literal.

Exceção documentada (ADR 0008, `nutrition-v3`, 2026-11-08): o modo explícito e o automático sincronizado passaram a
descontar o repouso da janela do treino (kcal informada 428 em 60 min, TMB 1.780: 428 − 74,17 = 353,83). Mudaram de
propósito `tdee`, `training_kcal`, meta e macros desses cenários e o hash de TODOS (versão do motor no hash). Os extras
destes cenários não têm duração: entram como vieram. Embutido, fallback e descanso mantêm os valores de antes.

Não há regra nesta suíte: só literais. O dono dos cálculos segue sendo `energy_expenditure.py`/`daily_target.py`
(mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7)."""

from __future__ import annotations

import math
from datetime import date

import pytest

from app import nutrition_targets_service as svc
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition.anthropometrics import ActivityLevel
from app.domain.nutrition.daily_target import SessionFacts
from app.domain.nutrition.energy_expenditure import ExtraActivity
from app.domain.nutrition.plan import build_plan_settings
from app.domain.nutrition.subject import Reading, SubjectSeries

HOJE = date(2026, 10, 14)
PLANO_ID = 7
# Homem 80 kg, 180 cm, 30 anos, nível "ativo" no perfil -> Mifflin 1.780 kcal. Plano com nível "moderado".
SERIE = SubjectSeries(
    sex="male", birth_date=date(1996, 1, 1), height_cm=180, activity_level="active",
    weight=(Reading(80.0, date(2026, 10, 1)),),
)
FORCA = [SessionFacts(calories_kcal=428.0, duration_seconds=3600)]
EXTRAS = [
    ExtraActivity(kind="cardio", calories_kcal=200.0, label="Esteira"),
    ExtraActivity(kind="other", calories_kcal=120.0, label="Caminhada"),
]

# (estrategia, modo, sessoes, extras, sincronizado) -> gasto, treino no gasto, fator, meta kcal, proteína, carbo, gordura, hash
_CENARIOS = {
    "maintenance/explicit_treino": (("maintenance", "explicit_training", FORCA, [], False),
        (2845.8333333333335, 353.8333333333333, 1.4, 2845.8333333333335, 160.0, 373.59375000000006, 79.05092592592592, "4e8ec0fc87c18d0975a3c588fb35aa0d24165c65ca11ac6829dc78f22dd65a9b")),
    "maintenance/explicit_descanso": (("maintenance", "explicit_training", [], [], False),
        (2492.0, 0.0, 1.4, 2492.0, 160.0, 307.25, 69.22222222222223, "5155b1ec273a8253dd82dfa51bada6ae585b8d1923efd46ae34fa3c3d04da563")),
    "maintenance/embutido_treino": (("maintenance", "included_in_factor", FORCA, [], False),
        (2759.0, None, 1.55, 2759.0, 160.0, 357.3125, 76.63888888888889, "21e8914e029f87b0afa0e369c298a666692b506d74a8b25eecec831ddc5b7dbd")),
    "maintenance/embutido_descanso": (("maintenance", "included_in_factor", [], [], False),
        (2759.0, None, 1.55, 2759.0, 160.0, 357.3125, 76.63888888888889, "892502dfb287f38af3e09eaf9c5eaac3392fb6f8e059d08b91d524ebbef07f43")),
    "maintenance/auto_sync_extras": (("maintenance", "auto_intervals", FORCA, EXTRAS, True),
        (3165.833333333333, 673.8333333333333, 1.4, 3165.833333333333, 160.0, 433.59374999999994, 87.93981481481481, "23dbd2f769f9689952fc353d014ad1267eac8dca1b1f8e9b3b4bfe8609676bce")),
    "maintenance/auto_sync_descanso": (("maintenance", "auto_intervals", [], [], True),
        (2492.0, 0.0, 1.4, 2492.0, 160.0, 307.25, 69.22222222222223, "9ddee2bba5ee2380fb70be0fe8a7ccbb0bbc036583d4544e9f171b0165fe6efc")),
    "maintenance/auto_fallback": (("maintenance", "auto_intervals", FORCA, EXTRAS, False),
        (3070.5, None, 1.725, 3070.5, 160.0, 415.71875, 85.29166666666667, "95aef43ebfb0f2aa471a76266506e29d9cf743eaab1204cc77b3337c24f0c7d4")),
    "bulking/explicit_treino": (("bulking", "explicit_training", FORCA, [], False),
        (2845.8333333333335, 353.8333333333333, 1.4, 3130.416666666667, 160.0, 426.95312500000006, 86.95601851851853, "ebd9b0da93f7175823fae742d923ae8e42a2e225221a99dbc3f0258748cc2734")),
    "bulking/explicit_descanso": (("bulking", "explicit_training", [], [], False),
        (2492.0, 0.0, 1.4, 2741.2, 160.0, 353.97499999999997, 76.14444444444445, "d9f1f258c07602a195c948d07ae5a96c7e888b3c2b882201de55c9fec15fb045")),
    "bulking/embutido_treino": (("bulking", "included_in_factor", FORCA, [], False),
        (2759.0, None, 1.55, 3034.9, 160.0, 409.04375000000005, 84.30277777777778, "16f4a2e21e99dcb3dc2008302cdf2ab5d2e6c5f741612bc4891ee30a84cb6e60")),
    "bulking/embutido_descanso": (("bulking", "included_in_factor", [], [], False),
        (2759.0, None, 1.55, 3034.9, 160.0, 409.04375000000005, 84.30277777777778, "6558d4fe163230a531d468b870e302ecbad67ba1a643961f486d7b8e68894e81")),
    "bulking/auto_sync_extras": (("bulking", "auto_intervals", FORCA, EXTRAS, True),
        (3165.833333333333, 673.8333333333333, 1.4, 3482.4166666666665, 160.0, 492.953125, 96.73379629629629, "0bf55b98b6c9dc0cf87ee2663ef3c0e02f46590b6bc4fbd6fa85eafdc1740f8e")),
    "bulking/auto_sync_descanso": (("bulking", "auto_intervals", [], [], True),
        (2492.0, 0.0, 1.4, 2741.2, 160.0, 353.97499999999997, 76.14444444444445, "90f72c63cd184ccf5268459de423c55d47917012b933283e31d607eeca34a5b7")),
    "bulking/auto_fallback": (("bulking", "auto_intervals", FORCA, EXTRAS, False),
        (3070.5, None, 1.725, 3377.55, 160.0, 473.29062500000003, 93.82083333333334, "5bfb9adc9430fc27a5bae15095cf6d7af42a41f7c5c79ff6d3326db696449764")),
}


def _dia(estrategia, modo, sessoes, extras, sincronizado):
    settings = build_plan_settings(strategy=estrategia, tdee_mode=modo, activity_level="moderate").settings
    entradas = svc.UserInputs(
        series=SERIE,
        sessions={HOJE: sessoes} if sessoes else {},
        extra_activities={HOJE: extras} if extras else {},
        intervals_in_sync=sincronizado,
        intervals_loaded=modo == "auto_intervals",
    )
    return svc.compute_day(entradas, settings, HOJE, HOJE)


@pytest.mark.parametrize("nome", sorted(_CENARIOS))
def test_modo_legado_mantem_gasto_meta_macros_e_hash(nome):
    entrada, (gasto, treino, fator, meta, proteina, carbo, gordura, hash_esperado) = _CENARIOS[nome]
    comp = _dia(*entrada)
    alvo = comp.target
    assert alvo.tdee.kcal == pytest.approx(gasto, rel=1e-12)
    assert alvo.tdee.training_kcal == treino
    assert alvo.tdee.activity_factor == fator
    assert alvo.kcal_target.kcal == pytest.approx(meta, rel=1e-12)
    assert alvo.macros.kcal == pytest.approx(meta, rel=1e-12)
    assert alvo.macros.protein_g == pytest.approx(proteina, rel=1e-12)
    assert alvo.macros.carb_g == pytest.approx(carbo, rel=1e-12)
    assert alvo.macros.fat_g == pytest.approx(gordura, rel=1e-12)
    # Trava do hash: `inputs_fingerprint` só olha as ENTRADAS (sujeito, plano, contexto do dia) e a versão do motor.
    assert svc.inputs_fingerprint(PLANO_ID, comp) == hash_esperado
    assert alvo.engine_version == "nutrition-v3"


def test_embutido_tmb_2020_fator_1725_da_3485_sem_somar_o_treino_de_novo():
    """Spec §21 / §1.1: 2.020 × 1,725 = 3.484,5 ≈ 3.485; as 428 kcal da musculação já estão no fator."""
    resultado = ee.tdee(2020.0, ActivityLevel.ACTIVE, ee.TdeeMode.INCLUDED_IN_FACTOR, 428.0)
    assert resultado.kcal == pytest.approx(3484.5)
    assert math.floor(resultado.kcal + 0.5) == 3485  # arredondamento comercial (round() do Python faz 3.484)
    assert resultado.training_kcal is None  # o treino é ignorado no modo embutido


def test_explicito_mantem_base_e_atividades_em_parcelas_separadas():
    """Spec §21: base de rotina (TMB × fator de não-exercício) + treino somado à parte."""
    resultado = ee.tdee(2020.0, ActivityLevel.ACTIVE, ee.TdeeMode.EXPLICIT_TRAINING, 428.0)
    assert resultado.activity_factor == 1.5
    assert resultado.training_kcal == 428.0
    assert resultado.kcal == pytest.approx(2020.0 * 1.5 + 428.0)


def test_tdee_to_dict_dos_modos_legados_tem_exatamente_as_chaves_de_antes():
    """Nada novo vai para `inputs_json`: `base_kcal` é só property, fora do `to_dict`."""
    antes = {"kcal", "mode", "activity_factor", "bmr_kcal", "training_kcal", "limitations"}
    for modo in (ee.TdeeMode.EXPLICIT_TRAINING, ee.TdeeMode.INCLUDED_IN_FACTOR, ee.TdeeMode.AUTO_INTERVALS):
        dados = ee.tdee(1780.0, ActivityLevel.MODERATE, modo, 300.0).to_dict()
        assert set(dados) == antes


@pytest.mark.parametrize("nome", sorted(_CENARIOS))
def test_gasto_total_reconstroi_pelas_parcelas_exibidas(nome):
    """Spec §20.7: base + treino/extras (o que o modo soma) = gasto total, nos 3 modos."""
    entrada, _ = _CENARIOS[nome]
    comp = _dia(*entrada)
    gasto = comp.target.tdee
    assert gasto.base_kcal == pytest.approx(gasto.bmr_kcal * gasto.activity_factor, rel=1e-12)
    assert gasto.base_kcal + (gasto.training_kcal or 0.0) == pytest.approx(gasto.kcal, rel=1e-12)


def test_base_kcal_e_none_quando_falta_tmb_ou_nivel():
    assert ee.tdee(None, ActivityLevel.MODERATE, ee.TdeeMode.EXPLICIT_TRAINING, 0.0).base_kcal is None
    assert ee.tdee(1780.0, None, ee.TdeeMode.INCLUDED_IN_FACTOR, 0.0).base_kcal is None


# --- Composição do gasto (`target.tdee_breakdown`) com o payload REAL de `_target_detail` -----------------------------


@pytest.mark.parametrize("nome", sorted(_CENARIOS))
def test_tdee_breakdown_da_previa_fecha_o_total_exatamente(nome):
    from app import nutrition_plans as plans_mod

    entrada, (gasto, _t, _f, _m, _p, _c, _g, _h) = _CENARIOS[nome]
    comp = _dia(*entrada)
    alvo = plans_mod._target_detail(comp, HOJE)
    comp_dict = alvo["tdee_breakdown"]
    assert comp_dict is not None
    assert sum(parte["kcal"] for parte in comp_dict["parts"]) == comp_dict["total_kcal"]  # exatamente como emitidos
    assert comp_dict["total_kcal"] == math.floor(gasto + 0.5)  # o mesmo gasto total que a tela mostra
    assert comp_dict["parts"][0]["code"] == "base" and all(p["label"] for p in comp_dict["parts"])
    codigos = [p["code"] for p in comp_dict["parts"]]
    modo = entrada[1]
    sincronizado = entrada[4]
    if modo == "included_in_factor" or (modo == "auto_intervals" and not sincronizado):
        assert codigos == ["base"] and len(comp_dict["notes"]) == 1  # sem parcela de treino, com a nota
        assert comp_dict["notes"][0]["code"] == ("auto_fallback" if modo == "auto_intervals" else "training_included_in_factor")
    else:
        assert comp_dict["notes"] == []
        treino = bool(entrada[2])
        assert ("training" in codigos) == treino
        assert ("extras" in codigos) == bool(entrada[3] and sincronizado)
        if modo == "auto_intervals" and treino:  # força e extras separados: o `training_kcal` do tdee soma os dois
            partes = {p["code"]: p["kcal"] for p in comp_dict["parts"]}
            assert partes["training"] == 354 and partes["extras"] == 320 and partes["base"] == 2492  # força líquida do repouso (60 min)
    # o `to_dict` gravado (inputs_json) e o hash não levam a composição
    assert "tdee_breakdown" not in comp.target.to_dict() and "tdee_breakdown" not in comp.target.tdee.to_dict()


def test_tdee_breakdown_e_none_sem_tmb_ou_fator_e_os_rotulos_distinguem_a_base():
    from app.domain.nutrition import labels as lb

    assert ee.tdee_breakdown(ee.tdee(None, ActivityLevel.MODERATE, ee.TdeeMode.EXPLICIT_TRAINING, 0.0), 0, ee.TdeeBasis.CLASSIC) is None
    assert ee.tdee_breakdown(ee.tdee(1780.0, None, ee.TdeeMode.INCLUDED_IN_FACTOR, 0.0), 0, ee.TdeeBasis.CLASSIC) is None
    assert lb.tdee_breakdown_public(None) is None
    rotulos = set(lb.TDEE_BREAKDOWN_BASE_LABELS.values())
    assert len(rotulos) == len(ee.BreakdownBase)


def test_tdee_breakdown_arredonda_na_base_para_a_soma_fechar():
    resultado = ee.tdee(1780.5, ActivityLevel.MODERATE, ee.TdeeMode.EXPLICIT_TRAINING, 433.6)
    quebra = ee.tdee_breakdown(resultado, 100.4, ee.TdeeBasis.AUTO_SYNCED)
    assert quebra is not None
    assert sum(p.kcal for p in quebra.parts) == quebra.total_kcal == math.floor(resultado.kcal + 0.5)
