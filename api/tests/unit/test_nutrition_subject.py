"""Sujeito do dia (dono `app/domain/nutrition/subject.py`, L2b): sexo do perfil
antigo ("M"/"F"), idade no dia, %G da bioimpedância antes da Marinha, MLG e a
idade da leitura (escolha automática da TMB)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.domain.nutrition import bmr as bm
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex
from app.domain.nutrition.body_composition import BodyFatMethod, navy_body_fat
from app.domain.nutrition.subject import Reading, SubjectReadings, resolve_subject

DIA = date(2026, 10, 17)
PERFIL = dict(sex="M", birth_date=date(1990, 10, 18), height_cm=180, activity_level="moderate")


def test_perfil_antigo_m_f_e_idade_no_dia():
    r = resolve_subject(SubjectReadings(**PERFIL, weight=Reading(80, date(2026, 10, 16))), DIA)
    assert r.subject.sex == Sex.MALE
    assert r.subject.age_years == 35  # faz 36 amanhã
    assert r.subject.height_cm == 180.0 and r.subject.weight_kg == 80.0
    assert r.subject.activity_level == ActivityLevel.MODERATE
    assert r.body_fat.method is None and r.ffm_age_days is None
    assert r.weight_date == date(2026, 10, 16)
    json.dumps(r.to_dict())


def test_override_do_plano_vence_o_perfil():
    r = resolve_subject(SubjectReadings(**PERFIL), DIA, activity_level_override=ActivityLevel.LIGHT)
    assert r.subject.activity_level == ActivityLevel.LIGHT


def test_bioimpedancia_antes_da_marinha_e_idade_da_mlg():
    leituras = SubjectReadings(
        **PERFIL,
        weight=Reading(80, DIA),
        bioimpedance_body_fat=Reading(18.0, date(2026, 9, 17)),
        bioimpedance_ffm=Reading(65.6, date(2026, 9, 17)),
        scale_bmr=Reading(1750, date(2026, 9, 17)),
        neck=Reading(40, DIA),
        waist=Reading(85, DIA),
    )
    r = resolve_subject(leituras, DIA)
    assert r.body_fat.method == BodyFatMethod.BIOIMPEDANCE and r.subject.body_fat_percent == 18.0
    assert r.subject.fat_free_mass_kg == 65.6
    assert r.ffm_age_days == 30
    assert r.scale_bmr_kcal == 1750.0
    assert bm.select_formula(r.subject, bm.BmrFormula.AUTO, r.ffm_age_days) == bm.BmrFormula.KATCH_MCARDLE


def test_sem_bioimpedancia_a_marinha_entra_com_a_medida_mais_antiga():
    leituras = SubjectReadings(
        **PERFIL,
        weight=Reading(80, DIA),
        neck=Reading(40, date(2026, 8, 1)),
        waist=Reading(85, date(2026, 10, 10)),
    )
    r = resolve_subject(leituras, DIA)
    assert r.body_fat.method == BodyFatMethod.NAVY
    assert r.subject.body_fat_percent == pytest.approx(navy_body_fat(Sex.MALE, 180, 40, 85))
    assert r.subject.fat_free_mass_kg is None  # o motor deriva peso × (1 − %G)
    assert r.body_fat_date == date(2026, 8, 1)
    assert r.ffm_age_days == (DIA - date(2026, 8, 1)).days
    assert r.ffm_age_days > bm.FFM_FRESHNESS_MAX_DAYS
    assert bm.select_formula(r.subject, bm.BmrFormula.AUTO, r.ffm_age_days) == bm.BmrFormula.MIFFLIN_ST_JEOR


def test_mulher_na_marinha_precisa_do_quadril():
    base = dict(sex="F", birth_date=date(1990, 1, 1), height_cm=165, weight=Reading(60, DIA))
    sem_quadril = resolve_subject(SubjectReadings(**base, neck=Reading(32, DIA), waist=Reading(70, DIA)), DIA)
    assert sem_quadril.body_fat.percent is None
    com_quadril = resolve_subject(
        SubjectReadings(**base, neck=Reading(32, DIA), waist=Reading(70, DIA), hip=Reading(95, date(2026, 10, 1))), DIA
    )
    assert com_quadril.body_fat.method == BodyFatMethod.NAVY
    assert com_quadril.body_fat_date == date(2026, 10, 1)


def test_locais_da_marinha_sao_do_vocabulario_da_fita():
    from app.domain.body_measurement_sites import MEASUREMENT_SITES
    from app.domain.nutrition.subject import NAVY_SITE_FIELDS

    assert set(NAVY_SITE_FIELDS) <= set(MEASUREMENT_SITES)
    assert set(NAVY_SITE_FIELDS.values()) <= set(SubjectReadings.__dataclass_fields__)


def test_series_escolhem_por_grandeza_a_leitura_valida_mais_recente():
    from app.domain.nutrition.subject import SubjectSeries

    series = SubjectSeries(
        **PERFIL,
        weight=(Reading(82, date(2026, 9, 1)), Reading(80, date(2026, 10, 10)), Reading(79, date(2026, 10, 20))),
        # %G de 95 em 10/10 é inválido: não esconde o de 18 em 01/09 nem a MLG/TMB de lá.
        bioimpedance_body_fat=(Reading(18.0, date(2026, 9, 1)), Reading(95.0, date(2026, 10, 10))),
        bioimpedance_ffm=(Reading(65.6, date(2026, 9, 1)),),
        scale_bmr=(Reading(1750, date(2026, 9, 1)), Reading(None, date(2026, 10, 10))),
        sites={"neck": (Reading(40, date(2026, 8, 1)),), "waist": (Reading(0, date(2026, 10, 1)),)},
    )
    leituras = series.readings_on(DIA)
    assert leituras.weight == Reading(80, date(2026, 10, 10))  # a de 20/10 é depois do dia
    assert leituras.bioimpedance_body_fat == Reading(18.0, date(2026, 9, 1))
    assert leituras.bioimpedance_ffm.value == 65.6 and leituras.scale_bmr.value == 1750
    assert leituras.neck.value == 40 and leituras.waist is None and leituras.hip is None
    r = resolve_subject(leituras, DIA)
    assert r.body_fat.method == BodyFatMethod.BIOIMPEDANCE and r.ffm_age_days == (DIA - date(2026, 9, 1)).days


def test_leitura_mais_recente_empate_vale_a_ultima_da_serie():
    from app.domain.nutrition.subject import latest_valid

    serie = (Reading(80, DIA), Reading(81, DIA), Reading(70, None))
    assert latest_valid(serie, DIA).value == 81
    assert latest_valid((), DIA) is None


def test_leituras_vazias_ou_invalidas_viram_none():
    r = resolve_subject(SubjectReadings(sex="xyz", height_cm="abc", weight=Reading(0, DIA)), DIA)
    assert r.subject.sex is None and r.subject.height_cm is None and r.subject.weight_kg is None
    assert r.subject.age_years is None and r.subject.activity_level is None
