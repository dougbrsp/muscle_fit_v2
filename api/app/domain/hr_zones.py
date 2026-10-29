"""Zonas de frequência cardíaca do atleta (M98 C0; plano
`docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md` §4.1).

Dono ÚNICO de:

- o vocabulário das zonas do app (Z1–Z5): quais números de zona existem e o
  rótulo de cada uma — a prescrição de cardio (`domain/cardio_prescription.py`)
  valida a zona-alvo por aqui;
- a PRECEDÊNCIA da fonte das zonas: Intervals.icu (configuração do atleta) →
  FCmáx informada manualmente → sem zonas (decisão do usuário 2026-10-26);
- o FALLBACK por FCmáx manual: o percentual da FCmáx que delimita cada zona
  (`MAX_HR_ZONE_BOUNDS_PERCENT`, constantes nomeadas);
- a classificação de uma FC numa zona;
- a validação da FCmáx manual do perfil (`validate_manual_max_hr`, M98 C1) e
  a forma das zonas resolvidas na API (`athlete_zones_payload`).

M98 C2 — zonas do Intervals.icu (`GET /athlete/{id}`, `sportSettings[]`):

- o Intervals guarda, por esporte, o LIMITE SUPERIOR de cada zona em bpm
  (`hr_zones`, o último é a FCmáx) e devolve, por atividade, os segundos em
  cada zona (`icu_hr_zone_times`, na mesma ordem). O padrão é o modelo de 7
  zonas por LTHR (Z1–Z4, Z5a, Z5b, Z5c);
- o MAPEAMENTO dessas zonas para Z1–Z5 do app é daqui
  (`INTERVALS_ZONE_MAP`): Z1–Z4 do Intervals viram Z1–Z4 do app e as três de
  cima (Z5a/Z5b/Z5c = Z5–Z7) somam na Z5 do app. Quantidade de zonas fora do
  mapa = sem zonas do Intervals (cai na FCmáx manual), nunca palpite;
- o início da Z1 (o Intervals não tem): `INTERVALS_Z1_LOWER_PERCENT` da FCmáx
  do esporte, o mesmo início da Z1 do fallback manual;
- qual esporte representa o atleta no editor: o primeiro de
  `INTERVALS_REFERENCE_SPORT_TYPES` que o atleta tiver configurado; senão o
  primeiro esporte com zonas válidas.

Nenhum percentual de FCmáx, limite de zona ou "N% da FCmáx" mora fora daqui
(py, ts, prompt, RAG). Mapa: `docs/motores/FONTES_DA_VERDADE.md` §3.4. Guarda:
`api/tests/unit/test_guarda_fontes_da_verdade.py` (`zonas_fc_paralelas`).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Mapping, Optional, Sequence

# Zonas do app: Z1 (mais leve) .. Z5 (máxima).
HR_ZONE_MIN: Final[int] = 1
HR_ZONE_MAX: Final[int] = 5
HR_ZONE_NUMBERS: Final[tuple[int, ...]] = tuple(range(HR_ZONE_MIN, HR_ZONE_MAX + 1))
HR_ZONE_LABEL_PREFIX: Final[str] = "Z"

# Fallback por FCmáx manual: (início, fim) de cada zona em % da FCmáx, de Z1 a
# Z5. Contíguas: o fim de uma é o início da seguinte; Z5 vai até a FCmáx.
# Modelo de 5 zonas por % da FCmáx (aprovado pelo usuário em 27/10/2026, plano §7).
MAX_HR_ZONE_BOUNDS_PERCENT: Final[tuple[tuple[int, int], ...]] = (
    (50, 60),
    (60, 70),
    (70, 80),
    (80, 90),
    (90, 100),
)

# M98 C2 — mapeamento das zonas do Intervals para as do app, pela QUANTIDADE
# de zonas configuradas no Intervals: a posição i (0 = Z1 do Intervals) vira
# a zona `INTERVALS_ZONE_MAP[n][i]` do app. 7 zonas (padrão do Intervals, LTHR:
# Z1–Z4, Z5a, Z5b, Z5c): as três de cima somam na Z5 do app. 5 zonas: 1 para 1.
# Da C2 (aprovado pelo usuário em 27/10/2026, plano §7).
INTERVALS_ZONE_MAP: Final[dict[int, tuple[int, ...]]] = {
    7: (1, 2, 3, 4, 5, 5, 5),
    5: (1, 2, 3, 4, 5),
}

# Início da Z1 do app quando a fonte é o Intervals (que não tem limite
# inferior): o mesmo % da FCmáx que abre a Z1 no fallback manual.
INTERVALS_Z1_LOWER_PERCENT: Final[int] = MAX_HR_ZONE_BOUNDS_PERCENT[0][0]

# Esporte do Intervals que representa o atleta nas zonas do editor (ordem de
# preferência; o cardio do app não tem equipamento). Sem nenhum deles: o
# primeiro esporte com zonas válidas.
INTERVALS_REFERENCE_SPORT_TYPES: Final[tuple[str, ...]] = ("Run", "VirtualRun", "Walk", "Ride", "VirtualRide")

# FCmáx manual aceita (bpm). Fora da faixa = sem FCmáx (nada de zona inventada).
MANUAL_MAX_HR_MIN_BPM: Final[int] = 100
MANUAL_MAX_HR_MAX_BPM: Final[int] = 230


class ZoneSource(str, Enum):
    """De onde vieram as zonas do atleta."""

    INTERVALS = "intervals"
    MANUAL_MAX_HR = "manual_max_hr"
    NONE = "none"


# Ordem de precedência (a primeira disponível vence).
ZONE_SOURCE_PRECEDENCE: Final[tuple[ZoneSource, ...]] = (ZoneSource.INTERVALS, ZoneSource.MANUAL_MAX_HR)


@dataclass(frozen=True)
class HrZone:
    """Uma zona em bpm: `[lower_bpm, upper_bpm]` (inclusive nas duas pontas;
    a zona seguinte começa em `upper_bpm + 1`)."""

    zone: int
    lower_bpm: int
    upper_bpm: int

    @property
    def label(self) -> str:
        return zone_label(self.zone)


@dataclass(frozen=True)
class AthleteZones:
    """Zonas resolvidas pela precedência. `zones` vazio quando `source` é NONE."""

    source: ZoneSource
    zones: tuple[HrZone, ...]
    max_hr_bpm: Optional[int] = None


class HrZonesError(ValueError):
    """Zonas recebidas fora da forma do dono."""


def is_valid_zone(value: Any) -> bool:
    """Número inteiro de zona do app (Z1..Z5). `bool` não conta."""
    if value is None or isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number.is_integer() and int(number) in HR_ZONE_NUMBERS


def zone_number(value: Any) -> Optional[int]:
    """Zona válida como `int`; senão None."""
    return int(float(value)) if is_valid_zone(value) else None


def zone_label(zone: int) -> str:
    """Rótulo da zona ("Z2")."""
    return f"{HR_ZONE_LABEL_PREFIX}{int(zone)}"


def normalize_max_hr(value: Any) -> Optional[int]:
    """FCmáx manual como `int` dentro de `[MIN, MAX]`; senão None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    bpm = int(math.floor(number + 0.5))
    return bpm if MANUAL_MAX_HR_MIN_BPM <= bpm <= MANUAL_MAX_HR_MAX_BPM else None


def validate_manual_max_hr(value: Any) -> Optional[int]:
    """Validação da FCmáx manual na ESCRITA (perfil do usuário, M98 C1).

    `None` -> None (limpa o campo). Número inteiro dentro de
    `[MANUAL_MAX_HR_MIN_BPM, MANUAL_MAX_HR_MAX_BPM]` -> o `int`. Qualquer outra
    coisa levanta `HrZonesError` com a mensagem para o usuário (a rota responde
    400): nada de arredondar ou cortar em silêncio um valor digitado errado."""
    if value is None:
        return None
    message = (
        f"FCmáx manual precisa ser um número inteiro entre {MANUAL_MAX_HR_MIN_BPM} e {MANUAL_MAX_HR_MAX_BPM} bpm."
    )
    if isinstance(value, bool):
        raise HrZonesError(message)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HrZonesError(message) from exc
    if not math.isfinite(number) or not number.is_integer():
        raise HrZonesError(message)
    if not MANUAL_MAX_HR_MIN_BPM <= number <= MANUAL_MAX_HR_MAX_BPM:
        raise HrZonesError(message)
    return int(number)


def zones_from_max_hr(max_hr_bpm: Any) -> tuple[HrZone, ...]:
    """Zonas em bpm a partir da FCmáx manual (fallback). FCmáx inválida -> ()."""
    max_hr = normalize_max_hr(max_hr_bpm)
    if max_hr is None:
        return ()
    zones: list[HrZone] = []
    for zone, (lower_pct, upper_pct) in zip(HR_ZONE_NUMBERS, MAX_HR_ZONE_BOUNDS_PERCENT):
        lower = int(math.floor(max_hr * lower_pct / 100 + 0.5))
        upper = int(math.floor(max_hr * upper_pct / 100 + 0.5))
        if zones:
            # Contíguas e sem sobreposição: começa logo depois da anterior.
            lower = zones[-1].upper_bpm + 1
        zones.append(HrZone(zone=zone, lower_bpm=lower, upper_bpm=upper if zone < HR_ZONE_MAX else max_hr))
    return tuple(zones)


def _validated_intervals_zones(zones: Sequence[HrZone]) -> tuple[HrZone, ...]:
    ordered = tuple(sorted(zones, key=lambda z: z.zone))
    if tuple(z.zone for z in ordered) != HR_ZONE_NUMBERS:
        raise HrZonesError(f"zonas do Intervals precisam cobrir {zone_label(HR_ZONE_MIN)}–{zone_label(HR_ZONE_MAX)}")
    for previous, current in zip(ordered, ordered[1:]):
        if current.lower_bpm <= previous.upper_bpm:
            raise HrZonesError("zonas do Intervals sobrepostas ou fora de ordem")
    if any(z.lower_bpm > z.upper_bpm for z in ordered):
        raise HrZonesError("zona do Intervals com início acima do fim")
    return ordered


def _non_negative_ints(values: Any) -> Optional[list[int]]:
    """Lista não vazia de inteiros >= 0 (aceita inteiro em float/str); senão None."""
    if not isinstance(values, (list, tuple)) or not values:
        return None
    out: list[int] = []
    for value in values:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number) or number < 0 or not number.is_integer():
            return None
        out.append(int(number))
    return out


def parse_intervals_zone_seconds(value: Any) -> Optional[list[int]]:
    """Tempo em zona de uma atividade do Intervals (`icu_hr_zone_times`):
    segundos (inteiros >= 0) por zona do Intervals, na ordem. Forma inválida
    -> None (nada é inventado)."""
    return _non_negative_ints(value)


def app_zone_seconds(intervals_zone_seconds: Any) -> Optional[tuple[int, ...]]:
    """Segundos em Z1..Z5 do app a partir do tempo em zona do Intervals, pelo
    `INTERVALS_ZONE_MAP`. Quantidade de zonas fora do mapa ou forma inválida
    -> None."""
    seconds = parse_intervals_zone_seconds(intervals_zone_seconds)
    if seconds is None:
        return None
    mapping = INTERVALS_ZONE_MAP.get(len(seconds))
    if mapping is None:
        return None
    totals = dict.fromkeys(HR_ZONE_NUMBERS, 0)
    for zone, value in zip(mapping, seconds):
        totals[zone] += value
    return tuple(totals[zone] for zone in HR_ZONE_NUMBERS)


def zones_from_intervals(upper_bounds_bpm: Any) -> tuple[HrZone, ...]:
    """Zonas Z1..Z5 do app em bpm a partir dos limites SUPERIORES das zonas do
    Intervals (`sportSettings[].hr_zones`; o último é a FCmáx do esporte).

    Zona do Intervals k = `[limite(k-1) + 1, limite(k)]`; a Z1 começa em
    `INTERVALS_Z1_LOWER_PERCENT` da FCmáx. Zonas do Intervals que o mapa junta
    numa zona do app viram uma faixa só. Limites que não crescem, quantidade
    fora do mapa ou forma inválida -> () (sem zonas do Intervals)."""
    bounds = _non_negative_ints(upper_bounds_bpm)
    if bounds is None:
        return ()
    mapping = INTERVALS_ZONE_MAP.get(len(bounds))
    if mapping is None or any(b <= a for a, b in zip(bounds, bounds[1:])):
        return ()
    max_hr = bounds[-1]
    z1_lower = int(math.floor(max_hr * INTERVALS_Z1_LOWER_PERCENT / 100 + 0.5))
    if z1_lower > bounds[0]:
        return ()
    lowers = [z1_lower, *(b + 1 for b in bounds[:-1])]
    ranges: dict[int, tuple[int, int]] = {}
    for zone, lower, upper in zip(mapping, lowers, bounds):
        current = ranges.get(zone)
        ranges[zone] = (current[0], upper) if current else (lower, upper)
    return tuple(HrZone(zone=z, lower_bpm=ranges[z][0], upper_bpm=ranges[z][1]) for z in HR_ZONE_NUMBERS)


def pick_reference_sport(settings: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """Esporte do Intervals que representa o atleta: o primeiro de
    `INTERVALS_REFERENCE_SPORT_TYPES` presente em `types` com zonas válidas;
    senão o primeiro com zonas válidas (ordem recebida); senão None.

    Cada item tem `types` (lista de tipos do Intervals) e `hr_zones_bpm`."""
    valid = [item for item in settings if zones_from_intervals(item.get("hr_zones_bpm"))]
    for sport_type in INTERVALS_REFERENCE_SPORT_TYPES:
        for item in valid:
            if sport_type in (item.get("types") or ()):
                return item
    return valid[0] if valid else None


def resolve_athlete_zones(
    intervals_zones: Optional[Sequence[HrZone]] = None,
    manual_max_hr_bpm: Any = None,
    intervals_max_hr_bpm: Any = None,
) -> AthleteZones:
    """Zonas do atleta pela precedência Intervals → FCmáx manual → nenhuma.

    `intervals_zones`: as 5 zonas já em bpm (`zones_from_intervals`).
    Vazio/None = sem zonas do Intervals. `intervals_max_hr_bpm`: a FCmáx do
    esporte do Intervals, só para exibir (sem ela, o fim da Z5)."""
    if intervals_zones:
        ordered = _validated_intervals_zones(intervals_zones)
        max_hr = normalize_max_hr(intervals_max_hr_bpm) or ordered[-1].upper_bpm
        return AthleteZones(source=ZoneSource.INTERVALS, zones=ordered, max_hr_bpm=max_hr)
    manual = zones_from_max_hr(manual_max_hr_bpm)
    if manual:
        return AthleteZones(
            source=ZoneSource.MANUAL_MAX_HR, zones=manual, max_hr_bpm=normalize_max_hr(manual_max_hr_bpm)
        )
    return AthleteZones(source=ZoneSource.NONE, zones=())


def zone_of_hr(hr_bpm: Any, zones: Sequence[HrZone]) -> Optional[int]:
    """Zona de uma FC. Abaixo da Z1 -> None; acima da Z5 -> Z5; sem zonas -> None."""
    if not zones or hr_bpm is None or isinstance(hr_bpm, bool):
        return None
    try:
        hr = float(hr_bpm)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(hr):
        return None
    ordered = sorted(zones, key=lambda z: z.zone)
    if hr < ordered[0].lower_bpm:
        return None
    for zone in ordered:
        # `< upper + 1`: FC fracionária entre o fim de uma zona e o início da
        # seguinte fica na de baixo.
        if hr < zone.upper_bpm + 1:
            return zone.zone
    return ordered[-1].zone


def athlete_zones_payload(athlete: AthleteZones) -> dict[str, Any]:
    """Zonas resolvidas como a API devolve (`GET /users/me/hr-zones`): a fonte,
    a FCmáx usada (só no fallback manual) e cada zona com o rótulo e a faixa
    em bpm. Quem exibe não recalcula nada."""
    return {
        "source": athlete.source.value,
        "max_hr_bpm": athlete.max_hr_bpm,
        "zones": [
            {"zone": z.zone, "label": z.label, "lower_bpm": z.lower_bpm, "upper_bpm": z.upper_bpm}
            for z in athlete.zones
        ],
    }


def export_for_frontend() -> dict[str, Any]:
    """Seção `hr_zones` do `motor-constants.generated.json`."""
    return {
        "zone_min": HR_ZONE_MIN,
        "zone_max": HR_ZONE_MAX,
        "zone_numbers": list(HR_ZONE_NUMBERS),
        "zone_labels": {str(zone): zone_label(zone) for zone in HR_ZONE_NUMBERS},
        # M98 C1: faixa aceita da FCmáx manual (campo do perfil).
        "manual_max_hr_bpm": {"min": MANUAL_MAX_HR_MIN_BPM, "max": MANUAL_MAX_HR_MAX_BPM},
    }


__all__ = [
    "AthleteZones",
    "HR_ZONE_LABEL_PREFIX",
    "HR_ZONE_MAX",
    "HR_ZONE_MIN",
    "HR_ZONE_NUMBERS",
    "HrZone",
    "HrZonesError",
    "INTERVALS_REFERENCE_SPORT_TYPES",
    "INTERVALS_ZONE_MAP",
    "INTERVALS_Z1_LOWER_PERCENT",
    "MANUAL_MAX_HR_MAX_BPM",
    "MANUAL_MAX_HR_MIN_BPM",
    "MAX_HR_ZONE_BOUNDS_PERCENT",
    "ZONE_SOURCE_PRECEDENCE",
    "ZoneSource",
    "app_zone_seconds",
    "athlete_zones_payload",
    "export_for_frontend",
    "is_valid_zone",
    "normalize_max_hr",
    "parse_intervals_zone_seconds",
    "pick_reference_sport",
    "resolve_athlete_zones",
    "validate_manual_max_hr",
    "zone_label",
    "zone_number",
    "zone_of_hr",
    "zones_from_intervals",
    "zones_from_max_hr",
]
