"""Dono das zonas de FC (M98 C0): `domain/hr_zones.py`.

Vocabulário Z1–Z5, fallback por FCmáx manual, precedência Intervals → FCmáx
manual → sem zonas e classificação de uma FC numa zona.

Rodar: cd api && python -m pytest tests/unit/test_hr_zones.py
"""

from __future__ import annotations

import pytest

from app.domain import hr_zones as hz


def test_vocabulario_das_zonas():
    assert hz.HR_ZONE_NUMBERS == (1, 2, 3, 4, 5)
    assert hz.zone_label(2) == "Z2"
    assert hz.is_valid_zone(3) and hz.is_valid_zone("4") and hz.is_valid_zone(5.0)
    for invalida in (0, 6, None, True, 2.5, "Z2", float("nan")):
        assert not hz.is_valid_zone(invalida)


def test_faixas_do_fallback_sao_contiguas_e_crescentes():
    limites = hz.MAX_HR_ZONE_BOUNDS_PERCENT
    assert len(limites) == len(hz.HR_ZONE_NUMBERS)
    for (_ini, fim), (ini_seg, _fim) in zip(limites, limites[1:]):
        assert fim == ini_seg
    assert limites[-1][1] == 100


def test_zonas_pela_fcmax_manual():
    zonas = hz.zones_from_max_hr(190)
    assert [z.zone for z in zonas] == [1, 2, 3, 4, 5]
    assert (zonas[0].lower_bpm, zonas[0].upper_bpm) == (95, 114)
    assert zonas[1].lower_bpm == 115 and zonas[1].upper_bpm == 133
    assert zonas[-1].upper_bpm == 190
    for anterior, atual in zip(zonas, zonas[1:]):
        assert atual.lower_bpm == anterior.upper_bpm + 1


@pytest.mark.parametrize("fcmax", [None, 0, 99, 231, "abc", True, float("inf")])
def test_fcmax_fora_da_faixa_nao_inventa_zona(fcmax):
    assert hz.zones_from_max_hr(fcmax) == ()


def test_precedencia_intervals_vence_a_fcmax_manual():
    intervals = tuple(
        hz.HrZone(zone=n, lower_bpm=100 + 10 * n, upper_bpm=109 + 10 * n) for n in reversed(hz.HR_ZONE_NUMBERS)
    )
    zonas = hz.resolve_athlete_zones(intervals_zones=intervals, manual_max_hr_bpm=190)
    assert zonas.source is hz.ZoneSource.INTERVALS
    assert [z.zone for z in zonas.zones] == [1, 2, 3, 4, 5]  # ordenadas


def test_sem_intervals_usa_a_fcmax_manual_e_sem_nada_fica_sem_zonas():
    manual = hz.resolve_athlete_zones(intervals_zones=None, manual_max_hr_bpm="185")
    assert manual.source is hz.ZoneSource.MANUAL_MAX_HR and manual.max_hr_bpm == 185
    assert manual.zones == hz.zones_from_max_hr(185)
    nada = hz.resolve_athlete_zones()
    assert nada.source is hz.ZoneSource.NONE and nada.zones == ()
    assert hz.ZONE_SOURCE_PRECEDENCE == (hz.ZoneSource.INTERVALS, hz.ZoneSource.MANUAL_MAX_HR)


@pytest.mark.parametrize(
    "zonas",
    [
        tuple(hz.HrZone(zone=n, lower_bpm=100 + 10 * n, upper_bpm=109 + 10 * n) for n in (1, 2, 3, 4)),
        tuple(hz.HrZone(zone=n, lower_bpm=100 + 10 * n, upper_bpm=115 + 10 * n) for n in hz.HR_ZONE_NUMBERS),
    ],
)
def test_zonas_do_intervals_fora_da_forma_sao_recusadas(zonas):
    with pytest.raises(hz.HrZonesError):
        hz.resolve_athlete_zones(intervals_zones=zonas)


def test_fc_numa_zona():
    zonas = hz.zones_from_max_hr(190)
    assert hz.zone_of_hr(94, zonas) is None
    assert hz.zone_of_hr(95, zonas) == 1
    assert hz.zone_of_hr(114.5, zonas) == 1
    assert hz.zone_of_hr(120, zonas) == 2
    assert hz.zone_of_hr(250, zonas) == 5
    assert hz.zone_of_hr(120, ()) is None
    assert hz.zone_of_hr(None, zonas) is None


def test_export_para_o_frontend():
    dados = hz.export_for_frontend()
    assert dados["zone_numbers"] == [1, 2, 3, 4, 5]
    assert dados["zone_labels"]["5"] == "Z5"
    assert dados["manual_max_hr_bpm"] == {"min": hz.MANUAL_MAX_HR_MIN_BPM, "max": hz.MANUAL_MAX_HR_MAX_BPM}


# --- M98 C1: FCmáx manual do perfil e a forma das zonas na API ---------------


@pytest.mark.parametrize("valor", [hz.MANUAL_MAX_HR_MIN_BPM, hz.MANUAL_MAX_HR_MAX_BPM, 185, 185.0, "190"])
def test_fcmax_manual_valida(valor):
    assert hz.validate_manual_max_hr(valor) == int(float(valor))


def test_fcmax_manual_none_limpa():
    assert hz.validate_manual_max_hr(None) is None


@pytest.mark.parametrize(
    "valor",
    [hz.MANUAL_MAX_HR_MIN_BPM - 1, hz.MANUAL_MAX_HR_MAX_BPM + 1, 180.5, "abc", True, float("nan"), [190]],
)
def test_fcmax_manual_invalida_tem_mensagem_com_a_faixa(valor):
    with pytest.raises(hz.HrZonesError) as exc:
        hz.validate_manual_max_hr(valor)
    assert f"{hz.MANUAL_MAX_HR_MIN_BPM} e {hz.MANUAL_MAX_HR_MAX_BPM} bpm" in str(exc.value)


def test_payload_das_zonas():
    sem = hz.athlete_zones_payload(hz.resolve_athlete_zones())
    assert sem == {"source": "none", "max_hr_bpm": None, "zones": []}
    com = hz.athlete_zones_payload(hz.resolve_athlete_zones(manual_max_hr_bpm=200))
    assert com["source"] == "manual_max_hr" and com["max_hr_bpm"] == 200
    assert [(z["zone"], z["label"]) for z in com["zones"]] == [(n, f"Z{n}") for n in hz.HR_ZONE_NUMBERS]
    zonas = hz.zones_from_max_hr(200)
    assert [(z["lower_bpm"], z["upper_bpm"]) for z in com["zones"]] == [(z.lower_bpm, z.upper_bpm) for z in zonas]
