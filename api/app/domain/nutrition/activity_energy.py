"""Energia das atividades importadas do Intervals.icu para o modo adaptativo: normalização ativa × bruta, deduplicação
e agregação por dia local.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda `api/tests/unit/test_guarda_fontes_da_verdade.py`).
Spec: `.omc/research/NUTRICAO-spec-2026-11-05.md` §3.3, §4, §5, §13 e §19; plano `.omc/plans/nutricao-adaptativo-2026-11-05.md`.

Escopo. Este módulo vale SÓ para o modo adaptativo futuro. Os três modos legados (`explicit_training`,
`included_in_factor`, `auto_intervals`) continuam com `energy_expenditure.extra_activity_kcal`/`resolve_training_kcal`
e NÃO usam nada daqui (testes-ouro em `tests/unit/test_nutrition_modos_legados_ouro.py`).

Fronteiras:
- A CLASSIFICAÇÃO da atividade (força, cardio, outra) é de `app/intervals_activity_kind.py`; aqui o tipo chega como texto
  já classificado e só participa da deduplicação. Nenhum padrão de nome é duplicado.
- Sem SQL, sem FastAPI, sem I/O. O serviço lê as linhas, monta `RawActivity` e chama `active_kcal_by_day`.
- Não importa `adaptive_energy` (e ele não importa este módulo): a ponte é o dicionário `dia -> kcal ativa | None`.

Calorias. `active` entra como veio. `gross` (total do exercício, com o repouso dentro) desconta o repouso que a TMB
já contém: `kcal_ativa = max(0, kcal − TMB/1440 × minutos)`. `unknown` (decisão D4 do plano) é tratada como bruta e
recebe a marca `reduced_precision`. Os minutos do desconto são o TEMPO DECORRIDO da atividade: o repouso corre durante
toda a janela que o relógio gravou, inclusive pausas, e o desconto maior é o lado conservador (não infla o gasto). O
chamador entrega `duration_seconds` com `elapsed_time_seconds` e, só se faltar, `moving_time_seconds`.

Dia local. A atividade pertence ao dia em que COMEÇOU (a que atravessa a meia-noite não se divide). `start` chega em
horário local naive (`start_date_local` do Intervals); a conversão para o fuso do usuário é do chamador.

Dia confirmado. Dia sem atividade só é descanso (zero) se a sincronização o cobre; senão é `None` (nunca zero por
ausência de dado, spec §19). Critério por dia (`day_confirmed`): o usuário já acompanhava (`tracking_started_on`) e
houve sincronização depois do fim do dia local mais a folga `DAY_CONFIRMATION_LAG_HOURS`. Dia em andamento nunca é
confirmado, porque ainda não houve sincronização depois do seu fim.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Final, Iterable, Mapping, Optional, Sequence

from .anthropometrics import positive_number

MINUTES_PER_DAY: Final[float] = 1440.0
SECONDS_PER_MINUTE: Final[float] = 60.0

# --- Validade da kcal ---------------------------------------------------------------------------------------
# Teto de kcal de UMA atividade: acima disso o número é tratado como erro do relógio (maratona ~3.000-4.000 kcal;
# ultra de várias horas passa disso, mas esse caso se corrige à mão no Lote E). Default de desenho (Tech Lead).
MAX_PLAUSIBLE_ACTIVITY_KCAL: Final[float] = 5000.0

# --- Deduplicação (spec §4.2) -------------------------------------------------------------------------------
FINGERPRINT_SOURCE_DEFAULT: Final[str] = "intervals_icu"
# Arredondamentos do fingerprint: início e duração ao minuto (o mesmo treino ressincronizado muda só os segundos);
# kcal de 5 em 5 (absorve a diferença de arredondamento do relógio). Defaults de desenho.
FINGERPRINT_START_ROUND_MINUTES: Final[int] = 1
FINGERPRINT_DURATION_ROUND_MINUTES: Final[int] = 1
FINGERPRINT_KCAL_ROUND: Final[float] = 5.0
# "Possível duplicata": mesmo tipo, início a até 10 min um do outro e duração dentro de 20% da maior. Só sinaliza.
POSSIBLE_DUPLICATE_START_TOLERANCE_MINUTES: Final[float] = 10.0
POSSIBLE_DUPLICATE_DURATION_TOLERANCE_RATIO: Final[float] = 0.20

# --- Dia confirmado -----------------------------------------------------------------------------------------
# A sincronização roda 1x por dia às 05:45 BRT (`infra/lambda_intervals_sync.tf`). Folga de 5 h depois da meia-noite
# do dia seguinte: o sync das 05:45 já confirma o dia anterior; um sync às 00:10 (antes de o relógio subir os treinos
# da noite) não confirma. Ligada ao horário do cron (mesmo critério de `energy_expenditure.INTERVALS_SYNC_MAX_AGE_MINUTES`).
DAY_CONFIRMATION_LAG_HOURS: Final[float] = 5.0

# Janela que CADA execução do sync reprocessa (`intervals_sync.DEFAULT_SYNC_DAYS` e a variável `intervals_sync_days` da
# infra, 30): o dia dentro dela, contado do último sync, foi buscado de novo no Intervals e é confiável, mesmo que o
# Intervals tenha ficado desconectado antes. Espelho com teste de paridade (o domínio não importa o app).
INTERVALS_SYNC_WINDOW_DAYS: Final[int] = 30
# Fora dessa janela não há prova de que o dia foi buscado (o job só reprocessa a janela recente, e não há registro de
# execuções). Dia sem atividade FORA da janela só vale como descanso confirmado se a sequência de dias sem atividade que
# o contém tem no máximo este tamanho; uma lacuna maior fica NÃO confirmada, nunca zero. DENTRO da janela de 30 dias o
# teto NÃO vale: com o Intervals conectado e sem treinos subindo (relógio parado), até 30 dias seguidos viram zero
# confirmado. Limitação a fechar no Lote C (registro de execuções do sync, com migração).
# Default de desenho (Tech Lead): 7 dias cobre folga e viagem curtas e é raro como descanso real de quem treina.
MAX_CONFIRMED_REST_STREAK_DAYS: Final[int] = 7

ROUNDING_DIGITS_PUBLIC: Final[int] = 1

# Dias de atividade que a API devolve para exibição (o mais recente primeiro). Contrato com o frontend: 7.
SHADOW_DISPLAY_DAYS: Final[int] = 7


class CalorieMode(str, Enum):
    """O que o número de kcal do relógio representa."""

    ACTIVE = "active"
    GROSS = "gross"
    UNKNOWN = "unknown"


_CALORIE_MODE_ALIASES: Final[Mapping[str, CalorieMode]] = {
    "active": CalorieMode.ACTIVE,
    "gross": CalorieMode.GROSS,
    "total": CalorieMode.GROSS,
    "unknown": CalorieMode.UNKNOWN,
}


def normalize_calorie_mode(value: Any) -> CalorieMode:
    """Texto -> modo da kcal; qualquer valor desconhecido, vazio ou ausente vira `UNKNOWN` (nunca levanta)."""
    if isinstance(value, CalorieMode):
        return value
    return _CALORIE_MODE_ALIASES.get(str(value or "").strip().lower(), CalorieMode.UNKNOWN)


class ActivityFlag(str, Enum):
    """Marcas por atividade (estáveis; a tela e o motor leem estes códigos)."""

    INVALID_KCAL = "invalid_kcal"  # ausente, <= 0 ou acima do teto: fora do total
    MISSING_DURATION = "missing_duration"  # kcal bruta sem duração: não dá para descontar o repouso; fora do total
    MISSING_BMR = "missing_bmr"  # kcal bruta sem TMB: não dá para descontar o repouso; fora do total
    REDUCED_PRECISION = "reduced_precision"  # modo da kcal desconhecido (D4): tratada como bruta
    DUPLICATE_EXTERNAL_ID = "duplicate_external_id"  # duplicata certa (mesmo id externo): fora do total
    DUPLICATE_FINGERPRINT = "duplicate_fingerprint"  # duplicata certa (mesmo fingerprint): fora do total
    POSSIBLE_DUPLICATE = "possible_duplicate"  # só sinalizada: CONTA no total


@dataclass(frozen=True)
class RawActivity:
    """Linha crua do Intervals já em tipos Python (o serviço monta; o motor não conhece colunas).

    `start` é o horário local naive de início; `kind` é a classe de `intervals_activity_kind.classify`; `calorie_mode`
    é o texto configurado (`active`/`gross`/`unknown`; ausente = `unknown`)."""

    id: str
    start: datetime
    duration_seconds: Any = None
    kind: str = "other"
    name: Optional[str] = None
    calories_kcal: Any = None
    calorie_mode: Any = None
    external_id: Optional[str] = None
    source: str = FINGERPRINT_SOURCE_DEFAULT


@dataclass(frozen=True)
class NormalizedActivity:
    """Atividade normalizada (spec §3.3). `active_kcal` é None quando a kcal não pôde ser usada; `counts_in_total`
    diz se entra na soma do dia (kcal válida e não é duplicata certa)."""

    id: str
    external_id: Optional[str]
    start: datetime
    duration_minutes: Optional[float]
    kind: str
    name: Optional[str]
    reported_kcal: Optional[float]
    calorie_mode: CalorieMode
    active_kcal: Optional[float]
    flags: tuple[ActivityFlag, ...] = ()
    duplicate_of: Optional[str] = None
    possible_duplicate_of: tuple[str, ...] = ()
    source: str = FINGERPRINT_SOURCE_DEFAULT

    @property
    def local_date(self) -> date:
        """Dia local em que a atividade COMEÇOU (a que atravessa a meia-noite fica neste dia)."""
        return self.start.date()

    @property
    def is_certain_duplicate(self) -> bool:
        return self.duplicate_of is not None

    @property
    def counts_in_total(self) -> bool:
        return self.active_kcal is not None and not self.is_certain_duplicate

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "external_id": self.external_id,
            "start": self.start.isoformat(),
            "local_date": self.local_date.isoformat(),
            "duration_minutes": _rounded(self.duration_minutes),
            "kind": self.kind,
            "name": self.name,
            "reported_kcal": _rounded(self.reported_kcal),
            "calorie_mode": self.calorie_mode.value,
            "active_kcal": _rounded(self.active_kcal),
            "counts_in_total": self.counts_in_total,
            "flags": [flag.value for flag in self.flags],
            "duplicate_of": self.duplicate_of,
            "possible_duplicate_of": list(self.possible_duplicate_of),
            "source": self.source,
        }


def _rounded(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), ROUNDING_DIGITS_PUBLIC)


def _clean_text(value: Any) -> Optional[str]:
    texto = str(value).strip() if value is not None else ""
    return texto or None


# ---------------------------------------------------------------------------
# Normalização (spec §5)
# ---------------------------------------------------------------------------


def resting_kcal(bmr_kcal: Any, duration_minutes: Any) -> Optional[float]:
    """Repouso da janela da atividade: TMB/1440 por minuto × minutos. Sem TMB ou duração positivas -> None."""
    tmb = positive_number(bmr_kcal)
    minutos = positive_number(duration_minutes)
    if tmb is None or minutos is None:
        return None
    return tmb / MINUTES_PER_DAY * minutos


def activity_duration_seconds(elapsed_seconds: Any, moving_seconds: Any) -> Optional[float]:
    """Duração da atividade para o desconto do repouso: o tempo DECORRIDO; se faltar (ausente ou <= 0), o tempo em
    movimento; sem nenhum, `None`. O serviço de leitura só entrega as duas colunas e chama esta função."""
    decorrido = positive_number(elapsed_seconds)
    return decorrido if decorrido is not None else positive_number(moving_seconds)


def normalize_activity(raw: RawActivity, bmr_kcal: Any = None) -> NormalizedActivity:
    """Uma atividade crua -> normalizada. Nunca levanta: kcal inválida ou impossível de normalizar vira
    `active_kcal=None` com a marca do motivo. A deduplicação é um passo à parte (`deduplicate`)."""
    segundos = positive_number(raw.duration_seconds)
    minutos = None if segundos is None else segundos / SECONDS_PER_MINUTE
    modo = normalize_calorie_mode(raw.calorie_mode)
    informada = positive_number(raw.calories_kcal)
    marcas: list[ActivityFlag] = []
    ativa: Optional[float] = None
    if informada is None or informada > MAX_PLAUSIBLE_ACTIVITY_KCAL:
        marcas.append(ActivityFlag.INVALID_KCAL)
    elif modo == CalorieMode.ACTIVE:
        ativa = informada
    else:
        if modo == CalorieMode.UNKNOWN:
            marcas.append(ActivityFlag.REDUCED_PRECISION)
        repouso = resting_kcal(bmr_kcal, minutos)
        if repouso is None:
            marcas.append(ActivityFlag.MISSING_DURATION if minutos is None else ActivityFlag.MISSING_BMR)
        else:
            ativa = max(0.0, informada - repouso)
    return NormalizedActivity(
        id=str(raw.id),
        external_id=_clean_text(raw.external_id),
        start=raw.start,
        duration_minutes=minutos,
        kind=str(raw.kind or "other").strip().lower() or "other",
        name=_clean_text(raw.name),
        reported_kcal=informada,
        calorie_mode=modo,
        active_kcal=ativa,
        flags=tuple(marcas),
        source=_clean_text(raw.source) or FINGERPRINT_SOURCE_DEFAULT,
    )


# ---------------------------------------------------------------------------
# Deduplicação (spec §4.2)
# ---------------------------------------------------------------------------


def _bucket(value: float, size: float) -> int:
    return int(math.floor(value / size + 0.5))


def activity_fingerprint(activity: NormalizedActivity) -> str:
    """Impressão da atividade para detectar duplicata sem id externo: fonte + início + duração + tipo + kcal, cada um
    arredondado pelas constantes `FINGERPRINT_*`. Usa a kcal INFORMADA (o que o relógio mandou), não a normalizada."""
    inicio_min = (activity.start - datetime(1970, 1, 1, tzinfo=activity.start.tzinfo)).total_seconds() / SECONDS_PER_MINUTE
    partes = (
        activity.source,
        str(_bucket(inicio_min, FINGERPRINT_START_ROUND_MINUTES)),
        "-" if activity.duration_minutes is None else str(_bucket(activity.duration_minutes, FINGERPRINT_DURATION_ROUND_MINUTES)),
        activity.kind,
        "-" if activity.reported_kcal is None else str(_bucket(activity.reported_kcal, FINGERPRINT_KCAL_ROUND)),
    )
    return hashlib.sha256("|".join(partes).encode("utf-8")).hexdigest()


def _possible_duplicates(a: NormalizedActivity, b: NormalizedActivity) -> bool:
    if a.kind != b.kind:
        return False
    if abs((a.start - b.start).total_seconds()) / SECONDS_PER_MINUTE > POSSIBLE_DUPLICATE_START_TOLERANCE_MINUTES:
        return False
    if a.duration_minutes is None or b.duration_minutes is None:
        return False
    maior = max(a.duration_minutes, b.duration_minutes)
    return abs(a.duration_minutes - b.duration_minutes) <= POSSIBLE_DUPLICATE_DURATION_TOLERANCE_RATIO * maior


def deduplicate(activities: Iterable[NormalizedActivity]) -> tuple[NormalizedActivity, ...]:
    """Marca duplicatas e devolve as atividades em ordem cronológica (empate: ordem de entrada); a primeira de cada
    grupo é a que fica.

    1. Mesmo id externo -> duplicata CERTA (`DUPLICATE_EXTERNAL_ID`, `duplicate_of` = a primeira): fora do total.
    2. Mesmo fingerprint e o par não tem dois ids externos distintos (dois ids diferentes são atividades diferentes) ->
       duplicata CERTA (`DUPLICATE_FINGERPRINT`): fora do total.
    3. Mesmo tipo, início próximo e duração semelhante -> `POSSIBLE_DUPLICATE` nas DUAS (`possible_duplicate_of`): só
       sinaliza, CONTA no total (excluir sem certeza esconderia treino real; a exclusão manual é do Lote E)."""
    ordenadas = sorted(enumerate(activities), key=lambda par: (par[1].start, par[0]))
    mantidas: list[NormalizedActivity] = []
    resultado: list[NormalizedActivity] = []
    por_id_externo: dict[str, NormalizedActivity] = {}
    impressoes: list[tuple[str, NormalizedActivity]] = []
    indice_resultado: dict[str, int] = {}

    for _, atividade in ordenadas:
        original = None
        marca = None
        if atividade.external_id is not None and atividade.external_id in por_id_externo:
            original, marca = por_id_externo[atividade.external_id], ActivityFlag.DUPLICATE_EXTERNAL_ID
        else:
            impressao = activity_fingerprint(atividade)
            for chave, anterior in impressoes:
                dois_ids = atividade.external_id is not None and anterior.external_id is not None
                if chave == impressao and not dois_ids:
                    original, marca = anterior, ActivityFlag.DUPLICATE_FINGERPRINT
                    break
        if original is not None and marca is not None:
            resultado.append(replace(atividade, flags=(*atividade.flags, marca), duplicate_of=original.id))
            continue

        possiveis = tuple(anterior.id for anterior in mantidas if _possible_duplicates(anterior, atividade))
        nova = atividade
        if possiveis:
            nova = replace(
                atividade,
                flags=(*atividade.flags, ActivityFlag.POSSIBLE_DUPLICATE),
                possible_duplicate_of=possiveis,
            )
            for id_anterior in possiveis:  # a anterior também é sinalizada, apontando para esta
                pos = indice_resultado[id_anterior]
                anterior = resultado[pos]
                ja = ActivityFlag.POSSIBLE_DUPLICATE in anterior.flags
                resultado[pos] = replace(
                    anterior,
                    flags=anterior.flags if ja else (*anterior.flags, ActivityFlag.POSSIBLE_DUPLICATE),
                    possible_duplicate_of=(*anterior.possible_duplicate_of, nova.id),
                )
        indice_resultado[nova.id] = len(resultado)
        resultado.append(nova)
        mantidas.append(nova)
        if nova.external_id is not None:
            por_id_externo[nova.external_id] = nova
        impressoes.append((activity_fingerprint(nova), nova))
    return tuple(resultado)


# ---------------------------------------------------------------------------
# Agregação por dia local (spec §13 e §19)
# ---------------------------------------------------------------------------


def day_confirmed(day: date, last_sync_at: Optional[datetime], tracking_started_on: Optional[date] = None) -> bool:
    """O dia local `day` está coberto pela sincronização? Verdadeiro quando o usuário já acompanhava
    (`tracking_started_on` <= `day`; sem a data informada não se afirma nada) e a última sincronização é de pelo menos
    `DAY_CONFIRMATION_LAG_HOURS` depois do fim do dia. `last_sync_at` e `day` estão no MESMO fuso (o chamador
    converte). Função pura: dia de hoje ou futuro dá sempre falso."""
    if last_sync_at is None or tracking_started_on is None or day < tracking_started_on:
        return False
    fim_do_dia = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=last_sync_at.tzinfo)
    return last_sync_at >= fim_do_dia + timedelta(hours=DAY_CONFIRMATION_LAG_HOURS)


@dataclass(frozen=True)
class DayActivity:
    """O dia local: `active_kcal` é a soma das atividades válidas e não duplicadas quando o dia está confirmado;
    descanso confirmado = 0.0; dia não confirmado = None (a pendência de sincronização, nunca zero por ausência)."""

    day: date
    confirmed: bool
    active_kcal: Optional[float]
    activities: tuple[NormalizedActivity, ...] = ()

    @property
    def counted(self) -> tuple[NormalizedActivity, ...]:
        return tuple(a for a in self.activities if a.counts_in_total)

    @property
    def has_possible_duplicates(self) -> bool:
        return any(ActivityFlag.POSSIBLE_DUPLICATE in a.flags for a in self.activities)

    @property
    def reduced_precision(self) -> bool:
        return any(ActivityFlag.REDUCED_PRECISION in a.flags for a in self.counted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "confirmed": self.confirmed,
            "active_kcal": _rounded(self.active_kcal),
            "counted_activities": len(self.counted),
            "has_possible_duplicates": self.has_possible_duplicates,
            "reduced_precision": self.reduced_precision,
            "activities": [a.to_dict() for a in self.activities],
        }


def _rest_gap_too_long(day: date, activity_days: Sequence[date], horizon_start: date, last_sync_day: date) -> bool:
    """O dia SEM atividade está numa sequência de dias sem atividade maior que `MAX_CONFIRMED_REST_STREAK_DAYS`?
    A sequência vai da atividade anterior (ou do início do horizonte lido) à seguinte (ou ao dia do último sync)."""
    anteriores = [d for d in activity_days if d < day]
    seguintes = [d for d in activity_days if d > day]
    inicio = (max(anteriores) + timedelta(days=1)) if anteriores else horizon_start
    fim = (min(seguintes) - timedelta(days=1)) if seguintes else last_sync_day
    return (fim - inicio).days + 1 > MAX_CONFIRMED_REST_STREAK_DAYS


def summarize_days(
    activities: Sequence[RawActivity],
    bmr_kcal: Any,
    days: Iterable[date],
    last_sync_at: Optional[datetime],
    tracking_started_on: Optional[date] = None,
) -> tuple[DayActivity, ...]:
    """Normaliza, deduplica e agrega por dia local, na ordem de `days`. Atividade fora de `days` é ignorada. O total do
    dia soma só `counts_in_total`; a lista de exibição traz TODAS (inclusive duplicatas e inválidas, com as marcas).

    Trava da lacuna (spec §19, nunca zero por ausência): dia SEM atividade fora da janela do último sync
    (`INTERVALS_SYNC_WINDOW_DAYS`) só é descanso confirmado se a sequência de dias sem atividade que o contém é de no
    máximo `MAX_CONFIRMED_REST_STREAK_DAYS`; senão fica não confirmado (None). Limite: no começo do horizonte lido a
    sequência é medida a partir do primeiro dia lido (a atividade anterior não foi carregada)."""
    lista = tuple(days)
    normalizadas = deduplicate(normalize_activity(item, bmr_kcal) for item in activities)
    por_dia: dict[date, list[NormalizedActivity]] = {}
    for atividade in normalizadas:
        por_dia.setdefault(atividade.local_date, []).append(atividade)
    resumo: list[DayActivity] = []
    dias_com_atividade = sorted(por_dia)
    dia_do_sync = last_sync_at.date() if last_sync_at is not None else None
    limite_da_janela = None if dia_do_sync is None else dia_do_sync - timedelta(days=INTERVALS_SYNC_WINDOW_DAYS)
    inicio_horizonte = min(lista) if lista else None
    for dia in lista:
        do_dia = tuple(por_dia.get(dia, ()))
        confirmado = day_confirmed(dia, last_sync_at, tracking_started_on)
        if (
            confirmado
            and not do_dia
            and dia_do_sync is not None
            and inicio_horizonte is not None
            and dia < limite_da_janela  # type: ignore[operator]
            and _rest_gap_too_long(dia, dias_com_atividade, inicio_horizonte, dia_do_sync)
        ):
            confirmado = False
        total = float(sum(a.active_kcal or 0.0 for a in do_dia if a.counts_in_total)) if confirmado else None
        resumo.append(DayActivity(day=dia, confirmed=confirmado, active_kcal=total, activities=do_dia))
    return tuple(resumo)


def active_kcal_by_day(
    activities: Sequence[RawActivity],
    bmr_kcal: Any,
    days: Iterable[date],
    last_sync_at: Optional[datetime],
    tracking_started_on: Optional[date] = None,
) -> dict[date, Optional[float]]:
    """Saída simples para o gasto adaptativo: dia local -> kcal ativa (float; 0.0 = descanso confirmado) ou None
    quando o dia não está confirmado. Mesma regra de `summarize_days`."""
    return {
        item.day: item.active_kcal
        for item in summarize_days(activities, bmr_kcal, days, last_sync_at, tracking_started_on)
    }
