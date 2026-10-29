"""Antropometria do motor de nutrição (dono `app/domain/nutrition/anthropometrics.py`).

Contrato público usado pelos Lotes L0/L2: `Sex`, `normalize_sex`,
`ActivityLevel`, fatores, `HEIGHT_CM_RANGE`, `age_years`, `bmi`, `Subject`;
desde o L2a também `sex_code`, `activity_level_code` e os códigos `INVALID_*`.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.domain.nutrition import anthropometrics as an

S = an.Sex
A = an.ActivityLevel


@pytest.mark.parametrize(
    ("raw", "esperado"),
    [
        ("M", S.MALE),
        ("m", S.MALE),
        (" masculino ", S.MALE),
        ("Homem", S.MALE),
        ("MALE", S.MALE),
        ("F", S.FEMALE),
        ("feminino", S.FEMALE),
        ("Mulher", S.FEMALE),
        ("female", S.FEMALE),
        ("outro", S.OTHER),
        ("não binário", S.OTHER),
        ("non-binary", S.OTHER),
        ("other", S.OTHER),
        (S.FEMALE, S.FEMALE),
        (None, None),
        ("", None),
        ("   ", None),
        ("abc", None),
    ],
)
def test_normalize_sex(raw, esperado):
    assert an.normalize_sex(raw) == esperado


@pytest.mark.parametrize(
    ("raw", "esperado"),
    [
        ("very_active", A.VERY_ACTIVE),
        ("Muito ativo", A.VERY_ACTIVE),
        ("sedentário", A.SEDENTARY),
        ("LIGHT", A.LIGHT),
        ("leve", A.LIGHT),
        ("moderate", A.MODERATE),
        ("ativa", A.ACTIVE),
        (A.ACTIVE, A.ACTIVE),
        (None, None),
        ("", None),
        ("x", None),
    ],
)
def test_normalize_activity_level(raw, esperado):
    assert an.normalize_activity_level(raw) == esperado


@pytest.mark.parametrize(
    ("raw", "esperado"),
    [("M", "male"), ("Feminino", "female"), ("non-binary", "other"), ("male", "male"), (S.FEMALE, "female"),
     (None, None), ("", None), ("abc", None)],
)
def test_sex_code_e_o_codigo_canonico_ou_none(raw, esperado):
    """L2a: o que a API grava e devolve em `sex` (aliases antigos viram o código)."""
    assert an.sex_code(raw) == esperado


@pytest.mark.parametrize(
    ("raw", "esperado"),
    [("very_active", "very_active"), ("Muito ativo", "very_active"), ("leve", "light"), (A.ACTIVE, "active"),
     (None, None), ("", None), ("x", None)],
)
def test_activity_level_code_e_o_codigo_canonico_ou_none(raw, esperado):
    assert an.activity_level_code(raw) == esperado


def test_codigos_de_erro_do_perfil_sao_estaveis():
    """L2a: o 400 da validação do perfil leva estes códigos (contrato da API)."""
    assert (an.INVALID_SEX, an.INVALID_ACTIVITY_LEVEL, an.INVALID_HEIGHT) == (
        "invalid_sex",
        "invalid_activity_level",
        "invalid_height_cm",
    )


def test_codigos_dos_enums_sao_snake_case_minusculo():
    assert [s.value for s in S] == ["male", "female", "other"]
    assert [a.value for a in A] == ["sedentary", "light", "moderate", "active", "very_active"]


def test_fatores_de_atividade_sao_os_do_plano():
    assert tuple(an.ACTIVITY_FACTORS[a] for a in A) == (1.2, 1.375, 1.55, 1.725, 1.9)
    assert tuple(an.NON_EXERCISE_ACTIVITY_FACTORS[a] for a in A) == (1.2, 1.3, 1.4, 1.5, 1.6)
    assert an.HEIGHT_CM_RANGE == (100.0, 250.0)


@pytest.mark.parametrize(
    ("nascimento", "referencia", "esperado"),
    [
        (date(1990, 6, 15), date(2026, 6, 14), 35),
        (date(1990, 6, 15), date(2026, 6, 15), 36),
        (date(1990, 6, 15), date(2026, 12, 31), 36),
        (date(2000, 2, 29), date(2026, 2, 28), 25),
        (None, date(2026, 1, 1), None),
        (date(1990, 1, 1), None, None),
        (date(2030, 1, 1), date(2026, 1, 1), None),
    ],
)
def test_age_years(nascimento, referencia, esperado):
    assert an.age_years(nascimento, referencia) == esperado


@pytest.mark.parametrize(
    ("peso", "altura", "esperado"),
    [
        (80, 180, pytest.approx(24.691, abs=1e-3)),
        (60, 165, pytest.approx(22.038, abs=1e-3)),
        (80, 90, None),  # abaixo da faixa de altura
        (80, 260, None),
        (0, 180, None),
        (None, 180, None),
        (80, None, None),
        ("80", "180", pytest.approx(24.691, abs=1e-3)),
        (True, 180, None),
    ],
)
def test_bmi(peso, altura, esperado):
    assert an.bmi(peso, altura) == esperado


def test_subject_to_dict_exporta_codigos_e_imc_e_e_serializavel():
    sujeito = an.Subject(
        sex=S.MALE, age_years=35, height_cm=180.0, weight_kg=80.0, body_fat_percent=15.0, activity_level=A.MODERATE
    )
    dados = sujeito.to_dict()
    assert dados["sex"] == "male" and dados["activity_level"] == "moderate"
    assert dados["bmi"] == pytest.approx(24.691, abs=1e-3)
    assert dados["fat_free_mass_kg"] is None
    json.dumps(dados)


def test_subject_sem_altura_nem_sexo_tem_imc_none():
    assert an.Subject(sex=None, age_years=None, height_cm=None, weight_kg=80.0).bmi is None
    assert an.Subject(sex=None, age_years=None, height_cm=None, weight_kg=80.0).to_dict()["sex"] is None


def test_subject_e_imutavel():
    sujeito = an.Subject(sex=S.FEMALE, age_years=30, height_cm=165.0, weight_kg=60.0)
    with pytest.raises(Exception):
        sujeito.weight_kg = 61.0  # type: ignore[misc]
