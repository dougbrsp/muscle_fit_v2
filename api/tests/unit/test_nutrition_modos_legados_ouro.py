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

Exceção documentada (ADR 0009, `nutrition-v4`, 2026-11-08): a proteína do corte passou a g/kg de peso total. Nenhum cenário
daqui é cutting, então só o hash de TODOS mudou (versão do motor no hash); gasto, meta e macros ficam como estavam.

Exceção documentada (ADR 0016, `nutrition-v5`, 2026-11-17): a kcal INFORMADA (Intervals e treino do app vinculado) entra
ATIVA no explícito e no automático, sem o desconto do repouso (a MET segue bruta). `explicit_treino`: 353,83 -> 428;
`auto_sync_extras`: 673,83 -> 748 (428 + 200 + 120; os extras não têm duração e já não descontavam). Mudaram gasto,
meta e macros desses cenários e o hash de TODOS (versão do motor no hash).

Exceção documentada (L53, `nutrition-v7`, 2026-11-25): o ciclo com compensação de gordura respeita a faixa de fração de
gordura da estratégia. Nenhum cenário daqui tem ciclo; só o hash de TODOS mudou (versão do motor no hash).

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
        # ADR 0016 (nutrition-v5): kcal informada entra ativa, sem o desconto de 74,17 (TMB/1440 × 60).
        (2920.0, 428.0, 1.4, 2920.0, 160.0, 387.5, 81.11111111111111, "ed1d0acf144be5bf293ae836f574ab2accfa9a36333c653dde07f2b0eb70a5dc")),
    "maintenance/explicit_descanso": (("maintenance", "explicit_training", [], [], False),
        (2492.0, 0.0, 1.4, 2492.0, 160.0, 307.25, 69.22222222222223, "432b810bb73287b0eaec2e2d83e85af5876fa3b7b3e26974ce8f8e92e579041f")),
    "maintenance/embutido_treino": (("maintenance", "included_in_factor", FORCA, [], False),
        (2759.0, None, 1.55, 2759.0, 160.0, 357.3125, 76.63888888888889, "d6fc961939c30a67d69acd418a8414f8d731144047de3e3b8ff4cd255e99a99d")),
    "maintenance/embutido_descanso": (("maintenance", "included_in_factor", [], [], False),
        (2759.0, None, 1.55, 2759.0, 160.0, 357.3125, 76.63888888888889, "47a0394a3de20859fe58da0cad64963dbdd30e2cbad01b59f3cf25adeab8d7ae")),
    "maintenance/auto_sync_extras": (("maintenance", "auto_intervals", FORCA, EXTRAS, True),
        # ADR 0016 (nutrition-v5): kcal informada entra ativa, sem o desconto de 74,17 (TMB/1440 × 60).
        (3240.0, 748.0, 1.4, 3240.0, 160.0, 447.5, 90.0, "224723f8966ad2fecf4ac4882558142e750465e824488ed51af98678475cb565")),
    "maintenance/auto_sync_descanso": (("maintenance", "auto_intervals", [], [], True),
        (2492.0, 0.0, 1.4, 2492.0, 160.0, 307.25, 69.22222222222223, "8e1ee6ababc00bf181777b7d6ba4264da81a7f36c49d22b5794b311946aba4e1")),
    "maintenance/auto_fallback": (("maintenance", "auto_intervals", FORCA, EXTRAS, False),
        (3070.5, None, 1.725, 3070.5, 160.0, 415.71875, 85.29166666666667, "fdf67de0138686513cf1be8c95269723b3638c8714813500e57def1bd510c6db")),
    "bulking/explicit_treino": (("bulking", "explicit_training", FORCA, [], False),
        # ADR 0016 (nutrition-v5): kcal informada entra ativa, sem o desconto de 74,17 (TMB/1440 × 60).
        (2920.0, 428.0, 1.4, 3212.0, 160.0, 442.25, 89.22222222222223, "7cf0351e28730b8bc04ed82e6bfbe6b3467976ec6420220ac755c915e1ae6826")),
    "bulking/explicit_descanso": (("bulking", "explicit_training", [], [], False),
        (2492.0, 0.0, 1.4, 2741.2, 160.0, 353.97499999999997, 76.14444444444445, "c5cb477218414af687498a62079fea6689c1b4bf2cb59f49e599f5560ae623d8")),
    "bulking/embutido_treino": (("bulking", "included_in_factor", FORCA, [], False),
        (2759.0, None, 1.55, 3034.9, 160.0, 409.04375000000005, 84.30277777777778, "e07a01a6dfee4808e7ffd8dda15d082f3a402614703b667ddee4916491cab1de")),
    "bulking/embutido_descanso": (("bulking", "included_in_factor", [], [], False),
        (2759.0, None, 1.55, 3034.9, 160.0, 409.04375000000005, 84.30277777777778, "3261d01adb241b29d9396cda9342020d45b78cda8d6775277cfee92c4dbabd9d")),
    "bulking/auto_sync_extras": (("bulking", "auto_intervals", FORCA, EXTRAS, True),
        # ADR 0016 (nutrition-v5): kcal informada entra ativa, sem o desconto de 74,17 (TMB/1440 × 60).
        (3240.0, 748.0, 1.4, 3564.0, 160.0, 508.25, 99.0, "c1c6ece58653de92a8a0e5b829df4ae1ec0766fe8f6a0218cf9c73ae60f0c095")),
    "bulking/auto_sync_descanso": (("bulking", "auto_intervals", [], [], True),
        (2492.0, 0.0, 1.4, 2741.2, 160.0, 353.97499999999997, 76.14444444444445, "045b3adffc979edee35a1af493fcfa389bcbe1835609595dc5eb884705bceef5")),
    "bulking/auto_fallback": (("bulking", "auto_intervals", FORCA, EXTRAS, False),
        (3070.5, None, 1.725, 3377.55, 160.0, 473.29062500000003, 93.82083333333334, "d743a87d65a7b61f527064b3f9ede8e0557d3ccf2f5887de6fc098058d07c1e8")),
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
    assert alvo.engine_version == "nutrition-v7"


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
        # uma parcela por atividade do Intervals (`activity_<n>`), com o nome do Intervals
        assert any(c.startswith("activity_") for c in codigos) == bool(entrada[3] and sincronizado)
        if modo == "auto_intervals" and treino:  # força e extras separados: o `training_kcal` do tdee soma os dois
            partes = {p["code"]: p["kcal"] for p in comp_dict["parts"]}
            assert partes["training"] == 428 and partes["activity_0"] + partes["activity_1"] == 320 and partes["base"] == 2492
            assert [p["label"] for p in comp_dict["parts"][2:]] == ["Esteira", "Caminhada"]  # ADR 0016: força informada entra ativa (428)
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
