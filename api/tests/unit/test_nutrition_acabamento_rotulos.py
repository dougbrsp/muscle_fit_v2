"""Rótulos do acabamento do #194 (#138, #131): o dono é `labels.py`; a tela só lê o espelho."""

from __future__ import annotations

from app.domain import nutrition
from app.domain.nutrition import activity_energy as ae
from app.domain.nutrition import labels as lb


def test_classificacao_da_kcal_reaproveita_codigo_da_correcao():
    assert ae.KCAL_CLASSIFICATION_MANUAL == ae.CORRECTION_ACTION_RECLASSIFY
    assert ae.KCAL_CLASSIFICATION_AUTO not in ae.CORRECTION_ACTIONS
    assert lb.ACTIVITY_KCAL_CLASSIFICATION_LABELS == {ae.KCAL_CLASSIFICATION_AUTO: "Automático", ae.KCAL_CLASSIFICATION_MANUAL: "Manual"}


def test_origem_da_atividade_indexada_pelo_valor_do_dono():
    assert lb.ACTIVITY_SOURCE_LABELS == {ae.FINGERPRINT_SOURCE_DEFAULT: "Intervals"}


def test_aviso_de_baixa_confianca_no_notice_labels():
    assert lb.NOTICE_LABELS[lb.NOTICE_MEAL_ITEM_LOW_CONFIDENCE] == "Estimativa a conferir"


def test_exportacao_do_pacote_traz_rotulos_e_codigos():
    bloco = nutrition.export_for_frontend()
    rotulos = bloco["adaptive_energy_labels"]
    codigos = bloco["adaptive_codes"]
    assert rotulos["activity_kcal_classification"] == dict(lb.ACTIVITY_KCAL_CLASSIFICATION_LABELS)
    assert rotulos["activity_source"] == dict(lb.ACTIVITY_SOURCE_LABELS)
    assert codigos["kcal_classification_auto"] == ae.KCAL_CLASSIFICATION_AUTO
    assert codigos["kcal_classification_manual"] == ae.KCAL_CLASSIFICATION_MANUAL
    assert codigos["activity_source_intervals"] == ae.FINGERPRINT_SOURCE_DEFAULT
    assert codigos["notice_meal_item_low_confidence"] == lb.NOTICE_MEAL_ITEM_LOW_CONFIDENCE
    assert bloco["notices"][codigos["notice_meal_item_low_confidence"]] == "Estimativa a conferir"
