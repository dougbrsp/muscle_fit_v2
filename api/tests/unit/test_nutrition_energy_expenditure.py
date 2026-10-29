"""Gasto energético (dono `app/domain/nutrition/energy_expenditure.py`): MET do
Compendium, prioridade de natureza da kcal do treino (sem Keytel, D-K) e TDEE.
A proveniência (manual × Intervals) é de `domain/calories_source.py` (L0)."""

from __future__ import annotations

import json

import pytest

from app.domain.data_nature import DataNature, DataQuality
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition.anthropometrics import ActivityLevel

K = ee.SessionKind


@pytest.mark.parametrize(
    ("peso", "minutos", "met", "esperado"),
    [
        (80, 60, None, 294.0),  # 3,5 × 3,5 × 80 / 200 × 60
        (80, 60, 6.0, 504.0),
        (80, 60, 5.8, pytest.approx(487.2)),
        (70, 45, 5.0, pytest.approx(5.0 * 3.5 * 70 / 200 * 45)),
        (None, 60, None, None),
        (80, 0, None, None),
        (80, None, None, None),
        (80, 60, 0, None),
    ],
)
def test_training_kcal_met(peso, minutos, met, esperado):
    assert ee.training_kcal_met(peso, minutos, met) == esperado


def test_mets_do_compendium_2024():
    assert ee.MET_STRENGTH_VIGOROUS == 6.0
    assert ee.MET_STRENGTH_SQUAT_DEADLIFT == 5.0
    assert ee.MET_STRENGTH_MULTIPLE_EXERCISES == 3.5 == ee.DEFAULT_STRENGTH_MET
    assert ee.MET_STRENGTH_CIRCUIT == 5.8


@pytest.mark.parametrize(
    ("kind", "esperado"),
    [(None, 3.5), (K.CIRCUIT, 5.8), ("squat_deadlift", 5.0), ("vigorous", 6.0), ("xyz", 3.5), (K.MULTIPLE_EXERCISES, 3.5)],
)
def test_met_for_session(kind, esperado):
    assert ee.met_for_session(kind) == esperado


def test_prioridade_informada_antes_do_met_e_sem_keytel():
    assert ee.TRAINING_KCAL_NATURE_PRIORITY == (ee.TrainingKcalNature.REPORTED, ee.TrainingKcalNature.MET)
    assert [s.value for s in ee.TrainingKcalNature] == ["reported", "met"]
    assert not [nome for nome in dir(ee) if "KEYTEL" in nome.upper()], "D-K: Keytel não entra neste motor"


def test_natureza_nao_e_proveniencia():
    """O pacote não cria vocabulário de proveniência (manual × Intervals): isso é
    de `domain/calories_source.py` (L0). Aqui só a natureza observada × estimada."""
    nomes = [nome.lower() for nome in dir(ee)]
    assert not [nome for nome in nomes if "source" in nome or "intervals" in nome or "manual" in nome]
    assert ee.NATURE_BY_TRAINING_KCAL_NATURE == {
        ee.TrainingKcalNature.REPORTED: DataNature.OBSERVED,
        ee.TrainingKcalNature.MET: DataNature.ESTIMATED,
    }
    assert set(ee.QUALITY_BY_TRAINING_KCAL_NATURE) == set(ee.TrainingKcalNature)


def test_resolve_training_kcal_informada_vence():
    resultado = ee.resolve_training_kcal(reported_kcal=350, weight_kg=80, duration_min=60)
    assert resultado.kcal == 350.0
    assert resultado.kcal_nature == ee.TrainingKcalNature.REPORTED
    assert resultado.nature == DataNature.OBSERVED and resultado.quality == DataQuality.HIGH
    assert resultado.limitations == ()


def test_resolve_training_kcal_cai_para_met_quando_nao_informada():
    resultado = ee.resolve_training_kcal(reported_kcal=None, weight_kg=80, duration_min=60)
    assert resultado.kcal == 294.0
    assert resultado.kcal_nature == ee.TrainingKcalNature.MET
    assert resultado.nature == DataNature.ESTIMATED and resultado.quality == DataQuality.LOW


def test_resolve_training_kcal_zero_ou_negativa_nao_conta_como_informada():
    assert ee.resolve_training_kcal(reported_kcal=0, weight_kg=80, duration_min=60).kcal_nature == ee.TrainingKcalNature.MET
    assert ee.resolve_training_kcal(reported_kcal=-5, weight_kg=80, duration_min=60).kcal_nature == ee.TrainingKcalNature.MET


def test_resolve_training_kcal_sem_dado_devolve_none_com_limitacao():
    resultado = ee.resolve_training_kcal()
    assert resultado is ee.NO_TRAINING_KCAL
    assert resultado.kcal is None and resultado.kcal_nature is None
    assert resultado.limitations == (ee.LIMIT_NO_TRAINING_KCAL,)
    json.dumps(resultado.to_dict())


def test_tdee_explicito_soma_treino_com_fator_de_nao_exercicio():
    resultado = ee.tdee(1755, ActivityLevel.MODERATE, ee.TdeeMode.EXPLICIT_TRAINING, training_kcal=294)
    assert resultado.kcal == pytest.approx(1755 * 1.4 + 294)
    assert resultado.activity_factor == 1.4 and resultado.training_kcal == 294.0
    sem_treino = ee.tdee(1755, ActivityLevel.MODERATE)
    assert sem_treino.kcal == pytest.approx(1755 * 1.4) and sem_treino.training_kcal is None
    assert ee.DEFAULT_TDEE_MODE == ee.TdeeMode.EXPLICIT_TRAINING


def test_tdee_embutido_usa_fator_classico_e_ignora_treino():
    resultado = ee.tdee(1755, ActivityLevel.MODERATE, ee.TdeeMode.INCLUDED_IN_FACTOR, training_kcal=294)
    assert resultado.kcal == pytest.approx(1755 * 1.55)
    assert resultado.activity_factor == 1.55 and resultado.training_kcal is None


def test_tdee_none_sem_tmb_ou_sem_nivel():
    sem_tmb = ee.tdee(None, ActivityLevel.MODERATE)
    assert sem_tmb.kcal is None and sem_tmb.limitations == (ee.LIMIT_MISSING_BMR,)
    sem_nivel = ee.tdee(1755, None)
    assert sem_nivel.kcal is None and sem_nivel.limitations == (ee.LIMIT_MISSING_ACTIVITY_LEVEL,)
    ambos = ee.tdee(None, None)
    assert set(ambos.limitations) == {ee.LIMIT_MISSING_BMR, ee.LIMIT_MISSING_ACTIVITY_LEVEL}
    json.dumps(ambos.to_dict())


# --- Diário (L4): linha "informado × estimado" ---------------------------------


def test_comparacao_informada_e_estimada_lado_a_lado():
    # 80 kg × 60 min × MET padrão 3,5 -> 294 kcal (plano §14).
    comparacao = ee.compare_training_kcal(reported_kcal=410, weight_kg=80, duration_min=60)
    assert comparacao.reported.kcal == 410 and comparacao.reported.kcal_nature == ee.TrainingKcalNature.REPORTED
    assert comparacao.reported.nature == DataNature.OBSERVED and comparacao.reported.quality == DataQuality.HIGH
    assert comparacao.estimated.kcal == pytest.approx(294.0)
    assert comparacao.estimated.kcal_nature == ee.TrainingKcalNature.MET and comparacao.estimated.nature == DataNature.ESTIMATED
    assert comparacao.used == comparacao.reported  # a informada vence (prioridade do dono)
    lados = comparacao.to_dict()["sides"]
    assert [lado["kcal_nature"] for lado in lados] == [n.value for n in ee.TRAINING_KCAL_NATURE_PRIORITY]
    assert [lado["is_used"] for lado in lados] == [True, False]
    assert lados[1]["value"]["kcal"] == pytest.approx(294.0)
    json.dumps(comparacao.to_dict())


def test_comparacao_sem_kcal_informada_usa_a_estimativa_e_sem_nada_fica_vazia():
    so_met = ee.compare_training_kcal(reported_kcal=None, weight_kg=80, duration_min=60)
    assert so_met.reported == ee.NO_TRAINING_KCAL and so_met.used == so_met.estimated
    assert [lado["is_used"] for lado in so_met.to_dict()["sides"]] == [False, True]
    sem_peso = ee.compare_training_kcal(reported_kcal=300, weight_kg=None, duration_min=60)
    assert sem_peso.estimated == ee.NO_TRAINING_KCAL and sem_peso.used.kcal == 300
    nada = ee.compare_training_kcal()
    assert nada.reported == nada.estimated == nada.used == ee.NO_TRAINING_KCAL
