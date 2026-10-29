"""Montagem do `Subject` de um dia a partir das leituras que a api-core encontra
(perfil, bioimpedância e fita métrica).

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7 e §47). Plano:
`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §3.1, §3.4 e §8.1 (Lote L2b).

Regras (a api-core só busca as séries cruas; quem escolhe e combina é este módulo):
- Seleção POR GRANDEZA (`SubjectSeries.readings_on`): para cada grandeza — peso,
  %G da bioimpedância, MLG da bioimpedância, TMB da balança, pescoço, cintura e
  quadril — vale a leitura VÁLIDA mais recente com data <= dia, escolhida
  independentemente das outras. Válida: número positivo; no %G, também dentro de
  `BODY_FAT_RANGE_VALID`. Assim um %G inválido (ou ausente) numa pesagem nova não
  esconde a MLG e a TMB válidas de uma bioimpedância anterior, e uma pesagem só de
  peso não apaga o %G anterior. Empate de data: vale a última da série.
- Sexo pelo `anthropometrics.normalize_sex` (aceita "M"/"F" do perfil antigo e
  o vocabulário do motor); idade completa no dia; nível de atividade do plano
  (override) ou do perfil.
- %G: `body_composition.resolve_body_fat` escolhe bioimpedância antes da Marinha
  dos EUA (`navy_body_fat` com pescoço/cintura/quadril e a altura do perfil).
- MLG informada: a da bioimpedância, quando existe. Sem ela, o motor deriva
  peso × (1 − %G) (`body_composition.resolved_ffm_kg`).
- Idade da MLG (`ffm_age_days`, para a escolha automática da TMB): dias entre o
  dia e a data da leitura que deu o %G/MLG. Na Marinha vale a medida MAIS
  ANTIGA entre as usadas (a conta é tão velha quanto a medida mais velha).

Módulo PURO: sem banco, sem FastAPI, sem I/O, sem relógio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Final, Mapping, Optional, Sequence

from ..body_measurement_sites import HIP_SITE, NECK_SITE, WAIST_SITE
from .anthropometrics import (
    ActivityLevel,
    Sex,
    Subject,
    age_years,
    normalize_activity_level,
    normalize_sex,
    positive_number,
)
from .body_composition import (
    BodyFat,
    BodyFatMethod,
    body_fat_is_valid,
    navy_body_fat,
    resolve_body_fat,
    resolved_fat_mass_kg,
    resolved_ffm_kg,
)


# Local da fita (vocabulário de `domain/body_measurement_sites.py`) -> campo de
# `SubjectReadings` que a fórmula da Marinha usa. A cintura é o abdômen no
# homem e a cintura na mulher (Hodgdon & Beckett 1984).
NAVY_SITE_FIELDS: Final[Mapping[str, str]] = {NECK_SITE: "neck", WAIST_SITE: "waist", HIP_SITE: "hip"}


@dataclass(frozen=True)
class Reading:
    """Um valor medido e o dia civil da medida."""

    value: Any
    local_date: Optional[date] = None

    @property
    def number(self) -> Optional[float]:
        return positive_number(self.value)


@dataclass(frozen=True)
class SubjectReadings:
    """O que a api-core leu para o dia (cada leitura = a mais recente até o dia)."""

    sex: Any = None
    birth_date: Optional[date] = None
    height_cm: Any = None
    activity_level: Any = None
    weight: Optional[Reading] = None
    bioimpedance_body_fat: Optional[Reading] = None
    bioimpedance_ffm: Optional[Reading] = None
    scale_bmr: Optional[Reading] = None
    neck: Optional[Reading] = None
    waist: Optional[Reading] = None
    hip: Optional[Reading] = None


def _positive(value: Any) -> bool:
    return positive_number(value) is not None


def latest_valid(series: Sequence[Reading], day: date, is_valid: Callable[[Any], bool] = _positive) -> Optional[Reading]:
    """Leitura válida mais recente com data <= `day` (empate: a última da série)."""
    escolhida: Optional[Reading] = None
    for leitura in series:
        if leitura.local_date is None or leitura.local_date > day or not is_valid(leitura.value):
            continue
        if escolhida is None or leitura.local_date >= escolhida.local_date:  # type: ignore[operator]
            escolhida = leitura
    return escolhida


@dataclass(frozen=True)
class SubjectSeries:
    """Séries cruas do aluno, uma por grandeza, como a api-core as lê (qualquer
    ordem; leitura sem valor pode vir e é ignorada). `sites` é indexado pelo
    código do local da fita (`domain/body_measurement_sites.py`)."""

    sex: Any = None
    birth_date: Optional[date] = None
    height_cm: Any = None
    activity_level: Any = None
    weight: tuple[Reading, ...] = ()
    bioimpedance_body_fat: tuple[Reading, ...] = ()
    bioimpedance_ffm: tuple[Reading, ...] = ()
    scale_bmr: tuple[Reading, ...] = ()
    sites: Mapping[str, tuple[Reading, ...]] = field(default_factory=dict)

    def readings_on(self, day: date) -> "SubjectReadings":
        """Leituras do dia, escolhidas POR GRANDEZA (ver o cabeçalho do módulo)."""
        locais = {
            campo: latest_valid(self.sites.get(site, ()), day) for site, campo in NAVY_SITE_FIELDS.items()
        }
        return SubjectReadings(
            sex=self.sex,
            birth_date=self.birth_date,
            height_cm=self.height_cm,
            activity_level=self.activity_level,
            weight=latest_valid(self.weight, day),
            bioimpedance_body_fat=latest_valid(self.bioimpedance_body_fat, day, body_fat_is_valid),
            bioimpedance_ffm=latest_valid(self.bioimpedance_ffm, day),
            scale_bmr=latest_valid(self.scale_bmr, day),
            **locais,
        )


def _num(reading: Optional[Reading]) -> Optional[float]:
    return None if reading is None else reading.number


def _iso(day: Optional[date]) -> Optional[str]:
    return day.isoformat() if day is not None else None


@dataclass(frozen=True)
class ResolvedSubject:
    """Sujeito do dia com as escolhas feitas (método do %G, datas, idade da MLG)."""

    day: date
    subject: Subject
    body_fat: BodyFat
    ffm_age_days: Optional[int]
    scale_bmr_kcal: Optional[float]
    weight_date: Optional[date]
    body_fat_date: Optional[date]

    def to_dict(self) -> dict[str, Any]:
        dados = self.subject.to_dict()
        dados.update(
            {
                "day": self.day.isoformat(),
                "body_fat_method": self.body_fat.method.value if self.body_fat.method else None,
                "body_fat_date": _iso(self.body_fat_date),
                "weight_date": _iso(self.weight_date),
                "ffm_age_days": self.ffm_age_days,
                "ffm_kg": resolved_ffm_kg(self.subject),
                "fat_mass_kg": resolved_fat_mass_kg(self.subject),
                "scale_bmr_kcal": self.scale_bmr_kcal,
                "limitations": list(self.body_fat.limitations),
            }
        )
        return dados


def _navy_date(sex: Optional[Sex], readings: SubjectReadings) -> Optional[date]:
    medidas = [readings.neck, readings.waist]
    if sex == Sex.FEMALE:
        medidas.append(readings.hip)
    datas = [m.local_date for m in medidas if m is not None and m.local_date is not None]
    return min(datas) if datas else None


def resolve_subject(
    readings: SubjectReadings,
    on_date: date,
    activity_level_override: Optional[ActivityLevel] = None,
) -> ResolvedSubject:
    """`Subject` de `on_date` a partir das leituras até o dia."""
    sexo = normalize_sex(readings.sex)
    altura = positive_number(readings.height_cm)
    navy = navy_body_fat(sexo, altura, _num(readings.neck), _num(readings.waist), _num(readings.hip))
    gordura = resolve_body_fat(_num(readings.bioimpedance_body_fat), navy)

    mlg_informada = _num(readings.bioimpedance_ffm)
    data_gordura: Optional[date] = None
    if gordura.method == BodyFatMethod.BIOIMPEDANCE and readings.bioimpedance_body_fat is not None:
        data_gordura = readings.bioimpedance_body_fat.local_date
    elif gordura.method == BodyFatMethod.NAVY:
        data_gordura = _navy_date(sexo, readings)
    data_mlg = readings.bioimpedance_ffm.local_date if mlg_informada is not None and readings.bioimpedance_ffm else None
    referencia = data_mlg or data_gordura
    idade_mlg = (on_date - referencia).days if referencia is not None else None

    sujeito = Subject(
        sex=sexo,
        age_years=age_years(readings.birth_date, on_date),
        height_cm=altura,
        weight_kg=_num(readings.weight),
        body_fat_percent=gordura.percent,
        fat_free_mass_kg=mlg_informada,
        activity_level=activity_level_override or normalize_activity_level(readings.activity_level),
    )
    return ResolvedSubject(
        day=on_date,
        subject=sujeito,
        body_fat=gordura,
        ffm_age_days=idade_mlg,
        scale_bmr_kcal=_num(readings.scale_bmr),
        weight_date=readings.weight.local_date if readings.weight is not None else None,
        body_fat_date=data_gordura,
    )
