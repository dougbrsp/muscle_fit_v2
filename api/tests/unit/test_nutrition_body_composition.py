"""Composição corporal (dono `app/domain/nutrition/body_composition.py`): Marinha
dos EUA, MLG, FFMI normalizado, cintura/altura, peso-alvo e escolha do %G."""

from __future__ import annotations

import json

import pytest

from app.domain.nutrition import body_composition as bc
from app.domain.nutrition.anthropometrics import Sex, Subject

# Referências em polegadas (Hodgdon & Beckett 1984) convertidas a cm:
# homem 70 in / pescoço 15 in / abdômen 34 in -> 17,51 %;
# mulher 65 in / pescoço 13 in / cintura 30 in / quadril 38 in -> 28,56 %.
H_IN = 70 * bc.CM_PER_INCH
M_IN = 65 * bc.CM_PER_INCH


@pytest.mark.parametrize(
    ("sexo", "altura", "pescoco", "cintura", "quadril", "esperado"),
    [
        (Sex.MALE, H_IN, 15 * bc.CM_PER_INCH, 34 * bc.CM_PER_INCH, None, pytest.approx(17.513, abs=1e-3)),
        (Sex.FEMALE, M_IN, 13 * bc.CM_PER_INCH, 30 * bc.CM_PER_INCH, 38 * bc.CM_PER_INCH, pytest.approx(28.556, abs=1e-3)),
        (Sex.FEMALE, M_IN, 13 * bc.CM_PER_INCH, 30 * bc.CM_PER_INCH, None, None),  # mulher exige quadril
        (Sex.OTHER, H_IN, 38.1, 86.36, None, None),
        (None, H_IN, 38.1, 86.36, None, None),
        (Sex.MALE, H_IN, None, 86.36, None, None),  # exige `neck`
        (Sex.MALE, H_IN, 90.0, 86.36, None, None),  # abdômen <= pescoço: log impossível
        (Sex.MALE, 90.0, 38.1, 86.36, None, None),  # altura fora da faixa
    ],
)
def test_navy_body_fat(sexo, altura, pescoco, cintura, quadril, esperado):
    assert bc.navy_body_fat(sexo, altura, pescoco, cintura, quadril) == esperado


def test_constantes_da_marinha():
    assert bc.NAVY_MALE == (86.010, 70.041, 36.76)
    assert bc.NAVY_FEMALE == (163.205, 97.684, 78.387)


@pytest.mark.parametrize(
    ("peso", "bf", "mlg", "mg"),
    [(80, 15, 68.0, 12.0), (60, 25, 45.0, 15.0), (80, 70, None, None), (80, 2, None, None), (None, 15, None, None)],
)
def test_ffm_e_fat_mass(peso, bf, mlg, mg):
    assert bc.ffm_kg(peso, bf) == (pytest.approx(mlg) if mlg is not None else None)
    assert bc.fat_mass_kg(peso, bf) == (pytest.approx(mg) if mg is not None else None)


def test_resolved_ffm_prefere_a_informada_e_deriva_do_percentual():
    informada = Subject(sex=Sex.MALE, age_years=35, height_cm=180.0, weight_kg=80.0, body_fat_percent=15.0, fat_free_mass_kg=66.0)
    assert bc.resolved_ffm_kg(informada) == 66.0
    assert bc.resolved_fat_mass_kg(informada) == pytest.approx(12.0)  # por %G
    derivada = Subject(sex=Sex.MALE, age_years=35, height_cm=180.0, weight_kg=80.0, body_fat_percent=15.0)
    assert bc.resolved_ffm_kg(derivada) == pytest.approx(68.0)
    so_mlg = Subject(sex=Sex.MALE, age_years=35, height_cm=180.0, weight_kg=80.0, fat_free_mass_kg=66.0)
    assert bc.resolved_fat_mass_kg(so_mlg) == pytest.approx(14.0)  # peso − MLG
    nada = Subject(sex=Sex.MALE, age_years=35, height_cm=180.0, weight_kg=80.0)
    assert bc.resolved_ffm_kg(nada) is None and bc.resolved_fat_mass_kg(nada) is None


def test_ffmi_e_normalizacao_de_kouri():
    assert bc.ffmi(68, 180) == pytest.approx(68 / 1.8**2)
    assert bc.ffmi_normalized(68, 180) == pytest.approx(bc.ffmi(68, 180))  # 1,80 m: sem ajuste
    assert bc.ffmi_normalized(68, 170) == pytest.approx(68 / 1.7**2 + 6.1 * (1.8 - 1.7))
    assert bc.ffmi_normalized(68, 190) < bc.ffmi(68, 190)
    assert bc.ffmi(None, 180) is None and bc.ffmi_normalized(68, 90) is None
    assert bc.FFMI_NATURAL_REFERENCE_MAX == 25.0


def test_cintura_altura():
    assert bc.waist_to_height(85, 180) == pytest.approx(85 / 180)
    assert bc.waist_to_height_is_healthy(bc.waist_to_height(85, 180)) is True
    assert bc.waist_to_height_is_healthy(bc.waist_to_height(95, 180)) is False
    assert bc.waist_to_height(85, None) is None and bc.waist_to_height_is_healthy(None) is None
    assert bc.WHTR_HEALTHY_MAX == 0.5


def test_peso_alvo_para_percentual_de_gordura():
    assert bc.target_weight_for_body_fat(68, 10) == pytest.approx(68 / 0.9)
    assert bc.target_weight_for_body_fat(68, 70) is None
    assert bc.target_weight_for_body_fat(None, 10) is None


def test_resolve_body_fat_segue_a_prioridade_e_valida_a_faixa():
    assert bc.BODY_FAT_METHOD_PRIORITY == (bc.BodyFatMethod.BIOIMPEDANCE, bc.BodyFatMethod.NAVY)
    bio = bc.resolve_body_fat(bioimpedance_percent=18, navy_percent=20)
    assert (bio.percent, bio.method) == (18.0, bc.BodyFatMethod.BIOIMPEDANCE)
    navy = bc.resolve_body_fat(bioimpedance_percent=None, navy_percent=20)
    assert (navy.percent, navy.method) == (20.0, bc.BodyFatMethod.NAVY)
    invalida = bc.resolve_body_fat(bioimpedance_percent=70, navy_percent=20)
    assert invalida.method == bc.BodyFatMethod.NAVY
    assert invalida.limitations == (f"{bc.LIMIT_BODY_FAT_OUT_OF_RANGE}:bioimpedance",)
    nada = bc.resolve_body_fat()
    assert nada.percent is None and nada.method is None and bc.LIMIT_BODY_FAT_MISSING in nada.limitations
    json.dumps(nada.to_dict())
    assert bc.BODY_FAT_RANGE_VALID == (3.0, 60.0)
