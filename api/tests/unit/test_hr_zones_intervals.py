"""Dono das zonas de FC, ramo Intervals.icu (M98 C2): `domain/hr_zones.py`.

Formato confirmado no `raw_json` do Intervals.icu (2026-10-26, na quase
totalidade das atividades): `icu_hr_zones` = limites SUPERIORES de 7 zonas em bpm (o último é
a FCmáx), `icu_hr_zone_times` = segundos em cada zona, na mesma ordem. O
`GET /athlete/{id}` devolve o mesmo em `sportSettings[].hr_zones`.

Rodar: cd api && python -m pytest tests/unit/test_hr_zones_intervals.py
"""

from __future__ import annotations

import pytest

from app.domain import hr_zones as hz

# Cenário sintético baseado em caso real: LTHR 156, FCmáx 180.
LIMITES_7 = [125, 135, 145, 155, 160, 170, 180]


def test_mapa_7_para_5_soma_as_tres_de_cima_na_z5():
    assert hz.INTERVALS_ZONE_MAP[7] == (1, 2, 3, 4, 5, 5, 5)
    assert hz.INTERVALS_ZONE_MAP[5] == (1, 2, 3, 4, 5)
    for mapa in hz.INTERVALS_ZONE_MAP.values():
        assert set(mapa) == set(hz.HR_ZONE_NUMBERS)
        assert list(mapa) == sorted(mapa)


def test_zonas_do_intervals_7_zonas_em_bpm():
    zonas = hz.zones_from_intervals(LIMITES_7)
    assert [(z.zone, z.lower_bpm, z.upper_bpm) for z in zonas] == [
        (1, 90, 125),  # a Z1 começa no mesmo % da FCmáx do fallback (50% de 180)
        (2, 126, 135),
        (3, 136, 145),
        (4, 146, 155),
        (5, 156, 180),  # Z5a + Z5b + Z5c do Intervals
    ]
    assert hz.INTERVALS_Z1_LOWER_PERCENT == hz.MAX_HR_ZONE_BOUNDS_PERCENT[0][0]


def test_zonas_do_intervals_5_zonas_uma_para_uma():
    zonas = hz.zones_from_intervals([130, 145, 160, 175, 190])
    assert [(z.lower_bpm, z.upper_bpm) for z in zonas] == [(95, 130), (131, 145), (146, 160), (161, 175), (176, 190)]


@pytest.mark.parametrize(
    "limites",
    [
        None,
        [],
        [125, 135, 145],
        [125, 135, 145, 155, 160, 170],
        [135, 125, 145, 155, 160, 170, 180],
        ["a"] * 7,
        [60, 70, 80, 90, 200],  # Z1 terminaria abaixo do início da Z1
    ],
)
def test_zonas_do_intervals_fora_do_mapa_ou_invalidas_viram_sem_zonas(limites):
    assert hz.zones_from_intervals(limites) == ()


def test_tempo_em_zona_do_intervals_vira_z1_a_z5():
    # Cenário sintético (VirtualRun): Z5a/Z5b/Z5c somam na Z5.
    assert hz.app_zone_seconds([500, 200, 180, 170, 100, 130, 90]) == (500, 200, 180, 170, 320)
    assert hz.app_zone_seconds([10, 20, 30, 40, 50]) == (10, 20, 30, 40, 50)
    for invalido in (None, [], [1, 2, 3], [1, -2, 3, 4, 5], [1.5, 2, 3, 4, 5], "x"):
        assert hz.app_zone_seconds(invalido) is None
    assert hz.parse_intervals_zone_seconds([1.0, "2", 3]) == [1, 2, 3]


def test_precedencia_com_zonas_do_intervals_e_fcmax_do_esporte():
    zonas = hz.resolve_athlete_zones(hz.zones_from_intervals(LIMITES_7), manual_max_hr_bpm=190, intervals_max_hr_bpm=180)
    assert zonas.source is hz.ZoneSource.INTERVALS and zonas.max_hr_bpm == 180
    # Zonas do Intervals inválidas: cai na FCmáx manual.
    fallback = hz.resolve_athlete_zones(hz.zones_from_intervals([1, 2]), manual_max_hr_bpm=190)
    assert fallback.source is hz.ZoneSource.MANUAL_MAX_HR


def test_esporte_de_referencia():
    corrida = {"types": ["Run", "VirtualRun"], "hr_zones_bpm": LIMITES_7}
    bike = {"types": ["Ride"], "hr_zones_bpm": [120, 130, 140, 150, 155, 160, 165]}
    natacao = {"types": ["Swim"], "hr_zones_bpm": None}
    assert hz.pick_reference_sport([bike, natacao, corrida]) is corrida
    assert hz.pick_reference_sport([natacao, bike]) is bike
    outro = {"types": ["Other"], "hr_zones_bpm": LIMITES_7}
    assert hz.pick_reference_sport([natacao, outro]) is outro
    assert hz.pick_reference_sport([natacao]) is None
