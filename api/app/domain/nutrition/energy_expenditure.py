"""Gasto energético: kcal do treino de força e TDEE.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrões `met_compendium` e
`kcal_treino_prioridade`). Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`
§3.2 e §7.

kcal do treino, por prioridade de NATUREZA (`TRAINING_KCAL_NATURE_PRIORITY`):
1. `reported` — kcal informada (o número `activities.calories_kcal`), natureza
   OBSERVED, qualidade alta.
2. `met` — MET × 3,5 × kg / 200 × minutos (Compendium 2024; Herrmann 2024):
   02050 vigoroso 6,0; 02052 agachamento/terra 5,0; 02054 múltiplos
   exercícios 8–15 reps 3,5 (default); 02055 circuito 5,8. Natureza ESTIMATED,
   calculada na leitura e nunca gravada (decisão E6 do plano).

Natureza × proveniência. Este motor classifica só a NATUREZA do número
(`reported` observado × `met` estimado). A PROVENIÊNCIA da kcal informada
(digitada à mão × sync do Intervals.icu) tem dono próprio,
`api/app/domain/calories_source.py` (Lote L0), e não é vocabulário deste
pacote: o motor consome `activities.calories_kcal` qualquer que seja a
proveniência. Por isso o enum é `TrainingKcalNature` (antes `TrainingKcalSource`,
renomeado em 2026-10-16 para não sugerir proveniência) e o campo de
`TrainingKcal` é `kcal_nature`. A natureza comum (`nature`: OBSERVED ×
ESTIMATED) e a qualidade (`quality`) usam o vocabulário de todos os motores,
`DataNature`/`DataQuality` de `domain/data_nature.py`, e não um enum próprio.

Decisão D-K (2026-10-15, Tech Lead): **sem Keytel**. A fórmula de FC (Keytel
2005) não entra neste motor porque a FC média e a kcal do treino chegam juntas
do sync do Intervals (150/150 atividades `WeightTraining` medidas em produção
em 2026-10-15 tinham kcal); "FC sem kcal" não ocorre, e a fórmula foi validada
em aeróbio. Fallback único = MET. Se um dia a natureza disponível mudar, a
prioridade muda AQUI e em nenhum consumidor.

TDEE (`tdee`): `EXPLICIT_TRAINING` = TMB × fator de NÃO-exercício + kcal LÍQUIDA do
treino do dia; `INCLUDED_IN_FACTOR` = TMB × fator clássico (treino embutido). TEF já
embutido nos fatores.

Desconto do repouso da sessão (ADR 0008, `nutrition-v3`): o fator de rotina já cobre a TMB das 24 h, então a kcal do
treino (informada ou MET, ambas brutas: incluem o repouso da janela) entra LÍQUIDA, `kcal − TMB/1440 × minutos`, piso 0.
A regra é a mesma do motor adaptativo (decisão D4) e vive em `activity_energy.net_of_resting_kcal`: `active` entra como
veio; `gross`/`unknown` descontam; a MET é sempre bruta. Sem duração: a kcal fica como está (não se inventa nem se
descarta o treino) e sai a limitação `LIMIT_REST_NOT_DISCOUNTED`. O modo do relógio do modo explícito/automático é o
`DEFAULT_TDEE_CALORIE_MODE` (`unknown`: o plano só guarda `watch_calorie_mode` no adaptativo).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Final, Mapping, Optional

from . import activity_energy, adaptive_energy
from ...intervals_activity_kind import STRENGTH as STRENGTH_ACTIVITY_KIND  # classe do dono (módulo puro)
from ..data_nature import DataNature, DataQuality  # vocabulário comum (dono: domain/data_nature.py)
from .anthropometrics import ACTIVITY_FACTORS, NON_EXERCISE_ACTIVITY_FACTORS, ActivityLevel, non_negative_number, positive_number

# --- Compendium of Physical Activities 2024 (códigos de condicionamento) -----
MET_STRENGTH_VIGOROUS: Final[float] = 6.0  # 02050
MET_STRENGTH_SQUAT_DEADLIFT: Final[float] = 5.0  # 02052
MET_STRENGTH_MULTIPLE_EXERCISES: Final[float] = 3.5  # 02054 (8–15 reps) = default
MET_STRENGTH_CIRCUIT: Final[float] = 5.8  # 02055
DEFAULT_STRENGTH_MET: Final[float] = MET_STRENGTH_MULTIPLE_EXERCISES

# kcal/min = MET × 3,5 × kg / 200.
MET_KCAL_FORMULA_FACTOR: Final[float] = 3.5
MET_KCAL_DIVISOR: Final[float] = 200.0


class SessionKind(str, Enum):
    VIGOROUS = "vigorous"
    SQUAT_DEADLIFT = "squat_deadlift"
    MULTIPLE_EXERCISES = "multiple_exercises"
    CIRCUIT = "circuit"


MET_BY_SESSION_KIND: Final[Mapping[SessionKind, float]] = {
    SessionKind.VIGOROUS: MET_STRENGTH_VIGOROUS,
    SessionKind.SQUAT_DEADLIFT: MET_STRENGTH_SQUAT_DEADLIFT,
    SessionKind.MULTIPLE_EXERCISES: MET_STRENGTH_MULTIPLE_EXERCISES,
    SessionKind.CIRCUIT: MET_STRENGTH_CIRCUIT,
}


class TrainingKcalNature(str, Enum):
    """Natureza da kcal do treino: informada (observada) ou estimada por MET.
    Não é proveniência (manual × Intervals): essa é de `domain/calories_source.py`."""

    REPORTED = "reported"
    MET = "met"


# Ordem de prioridade das naturezas da kcal do treino (D-K: sem `heart_rate`).
TRAINING_KCAL_NATURE_PRIORITY: Final[tuple[TrainingKcalNature, ...]] = (
    TrainingKcalNature.REPORTED,
    TrainingKcalNature.MET,
)

NATURE_BY_TRAINING_KCAL_NATURE: Final[Mapping[TrainingKcalNature, DataNature]] = {
    TrainingKcalNature.REPORTED: DataNature.OBSERVED,
    TrainingKcalNature.MET: DataNature.ESTIMATED,
}

QUALITY_BY_TRAINING_KCAL_NATURE: Final[Mapping[TrainingKcalNature, DataQuality]] = {
    TrainingKcalNature.REPORTED: DataQuality.HIGH,
    TrainingKcalNature.MET: DataQuality.LOW,
}

LIMIT_NO_TRAINING_KCAL = "training_kcal_unavailable"
# Treino ou atividade extra sem duração: o repouso da janela não pôde ser descontado e a kcal entrou como está.
LIMIT_REST_NOT_DISCOUNTED = "training_rest_not_discounted"
# Dia com sessão informada e outra sem kcal: o total soma a informada e a MET só da duração da que não tem kcal.
LIMIT_TRAINING_PARTIALLY_ESTIMATED = "training_kcal_partially_estimated"
# Plano `auto_intervals` sem nível de atividade no PERFIL: o fallback (fator com exercício) não tem de onde vir e
# não se inventa fator; bloqueia salvar/publicar (a API devolve este código com o rótulo do motor).
LIMIT_AUTO_REQUIRES_PROFILE_LEVEL = "auto_intervals_requires_profile_activity_level"
LIMIT_MISSING_BMR = "missing_bmr"
LIMIT_MISSING_ACTIVITY_LEVEL = "missing_activity_level"
# Plano adaptativo sem a estimativa do dia (nem a de fórmula, por falta de TMB): o gasto não é calculável.
LIMIT_ADAPTIVE_ESTIMATE_MISSING = "adaptive_estimate_missing"


# --- Atividades do Intervals que NÃO são a sessão do app ------------------------------------------------------
# Decisão do usuário (2026-11-08): TUDO que entra pelo Intervals, com o respectivo tipo ("Morning Walk", "Weight
# Training"...), é gasto de atividade. A classe de `app/intervals_activity_kind.py` só dá o tipo para a tela e o
# breakdown: NÃO filtra o que soma. A dupla contagem com o treino do app é evitada na ORIGEM: o serviço só entrega aqui
# as atividades SEM vínculo (`activity_intervals_link`) com uma sessão de `activities`; a vinculada já é a sessão (que
# soma uma vez, com a kcal do Intervals). Atividade sem kcal informada não soma e não é estimada (limitação).
LIMIT_ACTIVITY_WITHOUT_KCAL = "extra_activity_without_kcal"
EXTRA_ACTIVITY_FALLBACK_LABEL: Final[str] = "Atividade do Intervals"
EXTRA_ACTIVITY_KCAL_NATURE: Final[TrainingKcalNature] = TrainingKcalNature.REPORTED
# O rótulo (nome da atividade) vem do Intervals, texto livre: o motor limita o tamanho que a API devolve e grava.
EXTRA_ACTIVITY_LABEL_MAX_CHARS: Final[int] = 60

# O que o número de kcal do relógio representa no modo explícito/automático: o plano só guarda `watch_calorie_mode` no
# adaptativo; ausente = `unknown` = bruta (desconta o repouso, como o adaptativo).
DEFAULT_TDEE_CALORIE_MODE: Final[activity_energy.CalorieMode] = activity_energy.CalorieMode.UNKNOWN
# A kcal por MET (`training_kcal_met`) é sempre bruta (inclui 1 MET de repouso).
MET_KCAL_CALORIE_MODE: Final[activity_energy.CalorieMode] = activity_energy.CalorieMode.GROSS


def _net_kcal(kcal: float, calorie_mode: Any, duration_min: Any, bmr_kcal: Any) -> tuple[float, float, bool]:
    """(kcal líquida, desconto aplicado, repouso_não_descontado). Regra em `activity_energy.net_of_resting_kcal`. Sem
    duração (ou sem TMB em modo que desconta) mantém a bruta; o aviso só sai com TMB presente (sem TMB o gasto já é
    desconhecido e tem a própria limitação)."""
    repouso = activity_energy.resting_kcal(bmr_kcal, duration_min)
    liquida = activity_energy.net_of_resting_kcal(kcal, calorie_mode, repouso)
    if liquida is None:
        return kcal, 0.0, positive_number(bmr_kcal) is not None
    return liquida, kcal - liquida, False


# Motivos de uma atividade do Intervals não somar (`ExtraLine.excluded_reason`; rótulos em `labels.py`).
EXCLUDED_DUPLICATE: Final[str] = "duplicate"  # duplicata certa (mesmo id externo/impressão) ou mesmo treino da sessão do app
EXCLUDED_ABOVE_CEILING: Final[str] = "above_ceiling"  # kcal acima de `activity_energy.MAX_PLAUSIBLE_ACTIVITY_KCAL`
EXCLUDED_NO_KCAL: Final[str] = "no_kcal"  # sem kcal informada: não soma e não é estimada
# Treino que cruza com outro do Intervals por horário/duração (`activity_energy.deduplicate`, só sinaliza): CONTA e avisa.
LIMIT_POSSIBLE_DOUBLE_COUNT = "possible_double_count"
# A sessão do app entra na deduplicação como atividade de FORÇA, a classe do dono da classificação (módulo puro).
SESSION_ACTIVITY_KIND: Final[str] = STRENGTH_ACTIVITY_KIND
_SESSION_ID_PREFIX: Final[str] = "session:"
_ITEM_ID_PREFIX: Final[str] = "item:"
# Atividade sem horário (contexto antigo/testes): início sintético distinto por posição, para a deduplicação não casar.
_NO_START_EPOCH: Final[datetime] = datetime(1970, 1, 1)


@dataclass(frozen=True)
class ExtraActivity:
    """Uma atividade do Intervals do dia que NÃO é a sessão do app (sem vínculo): classe (do dono da classificação, só
    para exibição), kcal informada (pode faltar), o rótulo para a tela (nome ou tipo) e, para a deduplicação do dono
    (`activity_energy.deduplicate`), o id, o id externo e o início (todos opcionais)."""

    kind: str  # tipo para exibição (classe do dono da classificação); não decide o que soma
    calories_kcal: Any = None
    label: Optional[str] = None
    # Duração em segundos (decorrido, com o movimento de reserva: `activity_energy.activity_duration_seconds`); sem ela
    # o repouso não é descontado.
    duration_seconds: Any = None
    start: Optional[datetime] = None
    external_id: Optional[str] = None

    @property
    def duration_min(self) -> Optional[float]:
        segundos = positive_number(self.duration_seconds)
        return None if segundos is None else segundos / activity_energy.SECONDS_PER_MINUTE

    def to_dict(self) -> dict[str, Any]:
        rotulo = None if self.label is None else str(self.label).strip()[:EXTRA_ACTIVITY_LABEL_MAX_CHARS] or None
        dados: dict[str, Any] = {"kind": self.kind, "calories_kcal": positive_number(self.calories_kcal), "label": rotulo}
        if self.duration_min is not None:
            dados["duration_min"] = self.duration_min
        if self.start is not None:
            dados["start"] = self.start.isoformat()
        if self.external_id is not None:
            dados["external_id"] = str(self.external_id)
        return dados


@dataclass(frozen=True)
class ExtraLine:
    """O que o motor decidiu de UMA atividade do Intervals: kcal bruta informada, desconto do repouso, kcal LÍQUIDA que
    somou (0 se excluída) e o motivo da exclusão (`None` = somou)."""

    item: ExtraActivity
    gross_kcal: float = 0.0
    resting_discount_kcal: float = 0.0
    net_kcal: float = 0.0
    excluded_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.item.to_dict(),
            "gross_kcal": self.gross_kcal,
            "resting_discount_kcal": self.resting_discount_kcal,
            "net_kcal": self.net_kcal,
            "excluded_reason": self.excluded_reason,
        }


@dataclass(frozen=True)
class ExtraActivityKcal:
    """kcal das atividades do Intervals do dia (LÍQUIDAS do repouso), cada atividade com a decisão do motor e a natureza
    (informada = observada). `gross_kcal`/`resting_discount_kcal` são da soma do que contou."""

    kcal: float = 0.0
    items: tuple[ExtraActivity, ...] = ()
    kcal_nature: Optional[TrainingKcalNature] = None
    gross_kcal: Optional[float] = None
    resting_discount_kcal: float = 0.0
    limitations: tuple[str, ...] = ()
    lines: tuple[ExtraLine, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        itens = [linha.to_dict() for linha in self.lines] if self.lines else [item.to_dict() for item in self.items]
        return {
            "kcal": self.kcal,
            "kcal_nature": self.kcal_nature.value if self.kcal_nature else None,
            "gross_kcal": self.gross_kcal if self.gross_kcal is not None else self.kcal,
            "resting_discount_kcal": self.resting_discount_kcal,
            "items": itens,
        }


NO_EXTRA_ACTIVITY_KCAL: Final[ExtraActivityKcal] = ExtraActivityKcal()


def _session_activities(sessions: Any) -> list[activity_energy.RawActivity]:
    """Sessões do app COM horário, como atividades cruas para a deduplicação do dono contra as do Intervals."""
    cruas: list[activity_energy.RawActivity] = []
    for indice, sessao in enumerate(sessions):
        inicio = getattr(sessao, "start", None)
        if inicio is None:
            continue
        minutos = positive_number(sessao.duration_min)
        cruas.append(
            activity_energy.RawActivity(
                id=f"{_SESSION_ID_PREFIX}{indice}",
                start=inicio,
                duration_seconds=None if minutos is None else minutos * activity_energy.SECONDS_PER_MINUTE,
                kind=SESSION_ACTIVITY_KIND,
                calories_kcal=sessao.calories_kcal,
                calorie_mode=DEFAULT_TDEE_CALORIE_MODE,
                source="app",
            )
        )
    return cruas


def extra_activity_kcal(
    activities: Any = (), bmr_kcal: Any = None, calorie_mode: Any = DEFAULT_TDEE_CALORIE_MODE, sessions: Any = ()
) -> ExtraActivityKcal:
    """Soma a kcal de TODA atividade do Intervals recebida (qualquer tipo), LÍQUIDA do repouso da janela de cada uma
    (mesma regra do treino: `_net_kcal`). Usa a normalização e a deduplicação do dono (`activity_energy`):
    - sem kcal: não soma e não é estimada (`EXCLUDED_NO_KCAL`, `LIMIT_ACTIVITY_WITHOUT_KCAL`);
    - acima de `MAX_PLAUSIBLE_ACTIVITY_KCAL`: não soma (`EXCLUDED_ABOVE_CEILING`);
    - duplicata certa (mesmo id externo/impressão) ou mesmo treino de uma sessão do app com horário (início próximo,
      duração parecida): não soma (`EXCLUDED_DUPLICATE`; a sessão é a que conta);
    - possível duplicata entre atividades do Intervals: CONTA e avisa (`LIMIT_POSSIBLE_DOUBLE_COUNT`);
    - sem duração: entra como está, com `LIMIT_REST_NOT_DISCOUNTED`."""
    itens = tuple(activities)
    if not itens:
        return NO_EXTRA_ACTIVITY_KCAL
    cruas = [
        activity_energy.RawActivity(
            id=f"{_ITEM_ID_PREFIX}{i}",
            start=item.start if item.start is not None else _NO_START_EPOCH + timedelta(days=i),
            duration_seconds=item.duration_seconds,
            kind=str(item.kind),
            name=item.label,
            calories_kcal=item.calories_kcal,
            calorie_mode=calorie_mode,
            external_id=None if item.external_id is None else str(item.external_id),
        )
        for i, item in enumerate(itens)
    ]
    normalizadas = [activity_energy.normalize_activity(crua, bmr_kcal) for crua in cruas]
    normalizadas += [activity_energy.normalize_activity(crua, bmr_kcal) for crua in _session_activities(sessions)]
    por_id = {atividade.id: atividade for atividade in activity_energy.deduplicate(normalizadas)}
    linhas: list[ExtraLine] = []
    liquido = 0.0
    bruto = 0.0
    sem_desconto = False
    possivel = False
    sem_kcal = False
    for i, item in enumerate(itens):
        atividade = por_id[f"{_ITEM_ID_PREFIX}{i}"]
        informada = positive_number(item.calories_kcal)
        if informada is None:
            sem_kcal = True
            linhas.append(ExtraLine(item, excluded_reason=EXCLUDED_NO_KCAL))
            continue
        if informada > activity_energy.MAX_PLAUSIBLE_ACTIVITY_KCAL:
            linhas.append(ExtraLine(item, gross_kcal=informada, excluded_reason=EXCLUDED_ABOVE_CEILING))
            continue
        da_sessao = any(outro.startswith(_SESSION_ID_PREFIX) for outro in atividade.possible_duplicate_of)
        if atividade.is_certain_duplicate or da_sessao:
            linhas.append(ExtraLine(item, gross_kcal=informada, excluded_reason=EXCLUDED_DUPLICATE))
            continue
        possivel = possivel or bool(atividade.possible_duplicate_of)
        kcal, desconto, faltou = _net_kcal(informada, calorie_mode, item.duration_min, bmr_kcal)
        liquido += kcal
        bruto += informada
        sem_desconto = sem_desconto or faltou
        linhas.append(ExtraLine(item, gross_kcal=informada, resting_discount_kcal=desconto, net_kcal=kcal))
    limitacoes = (
        *((LIMIT_ACTIVITY_WITHOUT_KCAL,) if sem_kcal else ()),
        *((LIMIT_POSSIBLE_DOUBLE_COUNT,) if possivel else ()),
        *((LIMIT_REST_NOT_DISCOUNTED,) if sem_desconto else ()),
    )
    contou = any(linha.excluded_reason is None for linha in linhas)
    return ExtraActivityKcal(
        kcal=liquido,
        items=itens,
        kcal_nature=EXTRA_ACTIVITY_KCAL_NATURE if contou else None,
        gross_kcal=bruto,
        resting_discount_kcal=bruto - liquido,
        limitations=limitacoes,
        lines=tuple(linhas),
    )


def activity_total_kcal(training_kcal: Any, extra_kcal: Any) -> Optional[float]:
    """Gasto de atividade do dia: treino + extras. Treino desconhecido (`None`, dia de treino sem kcal nem duração)
    continua desconhecido; sem extra devolve o treino como está."""
    treino = non_negative_number(training_kcal)
    if treino is None:
        return None
    return treino + (non_negative_number(extra_kcal) or 0.0)


class TdeeMode(str, Enum):
    EXPLICIT_TRAINING = "explicit_training"
    INCLUDED_IN_FACTOR = "included_in_factor"
    # Automático pelo Intervals (2026-11-05): com a conta do Intervals conectada e sincronizada, TMB × fator de rotina
    # fora do exercício + treino de força + cardio/caminhadas medidos; sem conexão ou com a sincronização parada,
    # cai no fator que INCLUI exercício (`INCLUDED_IN_FACTOR`), ignorando treino e extras. Só este modo soma os extras.
    AUTO_INTERVALS = "auto_intervals"
    # Balanço energético adaptativo (2026-11-06, Lote C): o gasto do dia vem da estimativa pronta de
    # `adaptive_energy.DayEnergyEstimate` (alimentação + peso de tendência + Intervals como delta), NÃO de fator × TMB.
    ADAPTIVE_BALANCE = "adaptive_balance"


DEFAULT_TDEE_MODE: Final[TdeeMode] = TdeeMode.EXPLICIT_TRAINING
# Modos cujo gasto SOMA as atividades extras do Intervals (cardio, caminhada, outras): o fator de não-exercício não cobre
# exercício, então excluí-las subconta (decisão do usuário, 2026-11-08). No automático só com o Intervals sincronizado.
TDEE_MODES_WITH_EXTRA_ACTIVITIES: Final[frozenset[TdeeMode]] = frozenset({TdeeMode.EXPLICIT_TRAINING, TdeeMode.AUTO_INTERVALS})


# Rotina fora do exercício (modo automático): nível padrão do plano que não escolheu; é o fator de NÃO-exercício
# (`NON_EXERCISE_ACTIVITY_FACTORS`) de "leve" (default de desenho, o mais baixo que ainda cobre uma rotina comum).
ROUTINE_ACTIVITY_LEVEL_DEFAULT: Final[ActivityLevel] = ActivityLevel.LIGHT

# Frescor da sincronização do Intervals: a Lambda roda 1x por dia (05:45 BRT, `infra/lambda_intervals_sync.tf`);
# 36 h = 1 ciclo diário + 12 h de folga para um atraso ou uma falha isolada. Além disso, o dia é tratado como
# "sem sincronização" (fallback no fator com exercício): zero atividade só significa descanso se o dado é recente.
INTERVALS_SYNC_MAX_AGE_MINUTES: Final[int] = 36 * 60


def intervals_in_sync(connected: Any, minutes_since_sync: Any) -> bool:
    """Intervals conectado E última sincronização dentro de `INTERVALS_SYNC_MAX_AGE_MINUTES` (sem sincronização
    registrada ou idade inválida = fora de dia)."""
    if not connected:
        return False
    idade = non_negative_number(minutes_since_sync)
    return idade is not None and idade <= INTERVALS_SYNC_MAX_AGE_MINUTES


class TdeeBasis(str, Enum):
    """Como o gasto do dia foi montado (para a tela dizer a verdade): modo clássico, automático com o Intervals em dia ou
    automático em fallback (fator com exercício; treino e extras NÃO somados)."""

    CLASSIC = "classic"
    AUTO_SYNCED = "auto_synced"
    AUTO_FALLBACK = "auto_fallback"
    # Adaptativo: estimativa inicial por fórmula (confiança 0, sem gasto adaptativo ainda) ou gasto adaptativo.
    ADAPTIVE_BOOTSTRAP = "adaptive_bootstrap"
    ADAPTIVE = "adaptive"


def tdee_basis_of(mode: "TdeeMode", in_sync: Optional[bool], adaptive_bootstrap: Optional[bool] = None) -> TdeeBasis:
    if mode == TdeeMode.ADAPTIVE_BALANCE:
        return TdeeBasis.ADAPTIVE_BOOTSTRAP if adaptive_bootstrap else TdeeBasis.ADAPTIVE
    if mode != TdeeMode.AUTO_INTERVALS:
        return TdeeBasis.CLASSIC
    return TdeeBasis.AUTO_SYNCED if in_sync else TdeeBasis.AUTO_FALLBACK


def effective_tdee_basis(
    mode: "TdeeMode",
    profile_level: Optional[ActivityLevel],
    routine_level: Optional[ActivityLevel],
    in_sync: Optional[bool],
) -> tuple["TdeeMode", Optional[ActivityLevel]]:
    """Modo e nível de atividade que valem NESTE dia. Os modos clássicos seguem como estão (nível do perfil/plano). No
    automático: sincronizado = explícito com a rotina (`routine_level` ou o padrão); não sincronizado (ou desconhecido) =
    fator com exercício do perfil, sem treino nem extras."""
    if mode != TdeeMode.AUTO_INTERVALS:
        return mode, profile_level
    if in_sync:
        return TdeeMode.EXPLICIT_TRAINING, routine_level or ROUTINE_ACTIVITY_LEVEL_DEFAULT
    return TdeeMode.INCLUDED_IN_FACTOR, profile_level


def met_for_session(kind: Optional[SessionKind | str]) -> float:
    """MET da sessão pelo tipo; desconhecido/None -> default (02054)."""
    if kind is None:
        return DEFAULT_STRENGTH_MET
    try:
        return MET_BY_SESSION_KIND[SessionKind(kind)]
    except ValueError:
        return DEFAULT_STRENGTH_MET


def training_kcal_met(weight_kg: Any, duration_min: Any, met: Any = None) -> Optional[float]:
    """kcal = MET × 3,5 × kg / 200 × min. Sem peso ou duração positiva -> None."""
    peso = positive_number(weight_kg)
    minutos = positive_number(duration_min)
    if peso is None or minutos is None:
        return None
    met_valor = positive_number(met) if met is not None else DEFAULT_STRENGTH_MET
    if met_valor is None:
        return None
    return met_valor * MET_KCAL_FORMULA_FACTOR * peso / MET_KCAL_DIVISOR * minutos


@dataclass(frozen=True)
class TrainingKcal:
    """kcal do treino do dia com a natureza específica (`kcal_nature`), a
    natureza comum do pacote (`nature`) e a qualidade (None = sem dado)."""

    kcal: Optional[float]
    kcal_nature: Optional[TrainingKcalNature]
    nature: Optional[DataNature]
    quality: Optional[DataQuality]
    limitations: tuple[str, ...] = ()
    # `kcal` é a LÍQUIDA do repouso da sessão; a bruta e o desconto só vão ao `to_dict` quando houve desconto.
    # Fora da igualdade: a comparação de valores continua sobre kcal/natureza/qualidade/limitações.
    gross_kcal: Optional[float] = field(default=None, compare=False)
    resting_discount_kcal: float = field(default=0.0, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal": self.kcal,
            "kcal_nature": self.kcal_nature.value if self.kcal_nature else None,
            "nature": self.nature.value if self.nature else None,
            "quality": self.quality.value if self.quality else None,
            "limitations": list(self.limitations),
            "gross_kcal": self.gross_kcal if self.gross_kcal is not None else self.kcal,
            "resting_discount_kcal": self.resting_discount_kcal,
        }


NO_TRAINING_KCAL: Final[TrainingKcal] = TrainingKcal(
    kcal=None, kcal_nature=None, nature=None, quality=None, limitations=(LIMIT_NO_TRAINING_KCAL,)
)


def resolve_training_kcal(
    reported_kcal: Any = None,
    weight_kg: Any = None,
    duration_min: Any = None,
    met: Any = None,
    bmr_kcal: Any = None,
    calorie_mode: Any = DEFAULT_TDEE_CALORIE_MODE,
) -> TrainingKcal:
    """Primeira natureza disponível na ordem de `TRAINING_KCAL_NATURE_PRIORITY`, LÍQUIDA do repouso da sessão (`bmr_kcal`
    e `duration_min`; sem `bmr_kcal` nada é descontado, como antes).
    `reported_kcal` é o número de `activities.calories_kcal`, seja qual for a
    proveniência. Sem kcal informada e sem duração -> `NO_TRAINING_KCAL`."""
    for natureza in TRAINING_KCAL_NATURE_PRIORITY:
        if natureza == TrainingKcalNature.REPORTED:
            kcal = positive_number(reported_kcal)
        else:
            kcal = training_kcal_met(weight_kg, duration_min, met)
        if kcal is not None:
            modo = calorie_mode if natureza == TrainingKcalNature.REPORTED else MET_KCAL_CALORIE_MODE
            liquida, desconto, faltou = _net_kcal(kcal, modo, duration_min, bmr_kcal)
            return TrainingKcal(
                kcal=liquida,
                kcal_nature=natureza,
                nature=NATURE_BY_TRAINING_KCAL_NATURE[natureza],
                quality=QUALITY_BY_TRAINING_KCAL_NATURE[natureza],
                limitations=(LIMIT_REST_NOT_DISCOUNTED,) if faltou else (),
                gross_kcal=kcal,
                resting_discount_kcal=desconto,
            )
    return NO_TRAINING_KCAL


@dataclass(frozen=True)
class SessionKcal:
    """Uma sessão de treino concluída do dia: kcal informada (pode faltar) e duração em minutos (pode faltar)."""

    calories_kcal: Any = None
    duration_min: Any = None
    # Horário de início da sessão (opcional): entra na deduplicação contra as atividades do Intervals (`extra_activity_kcal`).
    start: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        dados: dict[str, Any] = {"calories_kcal": positive_number(self.calories_kcal), "duration_min": positive_number(self.duration_min)}
        if self.start is not None:
            dados["start"] = self.start.isoformat()
        return dados


@dataclass(frozen=True)
class _SessionPart:
    """Uma sessão que entrou na soma: natureza, kcal LÍQUIDA, bruta e se o repouso ficou sem descontar."""

    nature: TrainingKcalNature
    kcal: float
    gross_kcal: float
    rest_missing: bool = False


def _session_parts(
    sessions: Any, weight_kg: Any, met: Any, bmr_kcal: Any = None, calorie_mode: Any = DEFAULT_TDEE_CALORIE_MODE
) -> list[_SessionPart]:
    """kcal de cada sessão que tem como entrar, LÍQUIDA do repouso da janela dela (`_net_kcal`): a informada onde houver
    (no `calorie_mode` do relógio); MET (sempre bruta) só da duração da sem kcal; a sem kcal e sem duração (ou sem peso
    para a MET) não soma e não derruba as outras."""
    partes: list[_SessionPart] = []
    for sessao in sessions:
        informada = positive_number(sessao.calories_kcal)
        if informada is not None:
            liquida, _, faltou = _net_kcal(informada, calorie_mode, sessao.duration_min, bmr_kcal)
            partes.append(_SessionPart(TrainingKcalNature.REPORTED, liquida, informada, faltou))
            continue
        estimada = training_kcal_met(weight_kg, sessao.duration_min, met)
        if estimada is not None:
            liquida, _, faltou = _net_kcal(estimada, MET_KCAL_CALORIE_MODE, sessao.duration_min, bmr_kcal)
            partes.append(_SessionPart(TrainingKcalNature.MET, liquida, estimada, faltou))
    return partes


def resolve_sessions_kcal(
    sessions: Any = (), weight_kg: Any = None, met: Any = None, bmr_kcal: Any = None,
    calorie_mode: Any = DEFAULT_TDEE_CALORIE_MODE,
) -> TrainingKcal:
    """kcal do treino do dia SOMANDO POR SESSÃO (prioridade `TRAINING_KCAL_NATURE_PRIORITY` em cada uma). Todas
    informadas: `reported`. Nenhuma informada: `met` (MET das durações). Mistas: a soma, natureza `met` (estimada) e a
    limitação `LIMIT_TRAINING_PARTIALLY_ESTIMATED`; o dia só é `reported` se tudo for informado. Nada utilizável:
    `NO_TRAINING_KCAL`. A kcal de cada sessão é LÍQUIDA do repouso da janela (`bmr_kcal`; ver o topo do módulo)."""
    sessoes = tuple(sessions)
    partes = _session_parts(sessoes, weight_kg, met, bmr_kcal, calorie_mode)
    if not partes:
        return NO_TRAINING_KCAL
    total = float(sum(parte.kcal for parte in partes))
    bruto = float(sum(parte.gross_kcal for parte in partes))
    sem_desconto = (LIMIT_REST_NOT_DISCOUNTED,) if any(parte.rest_missing for parte in partes) else ()
    # O dia é "parcialmente estimado" pelas SESSÕES (não pelo que deu para somar): há sessão sem kcal informada junto de
    # uma informada, ou alguma sessão ficou de fora da soma (sem kcal e sem duração/peso).
    informadas = sum(1 for sessao in sessoes if positive_number(sessao.calories_kcal) is not None)
    todas_informadas = informadas == len(sessoes)
    ficou_de_fora = len(partes) < len(sessoes)
    if todas_informadas:
        natureza = TrainingKcalNature.REPORTED
    elif informadas == 0 and not ficou_de_fora:
        natureza = TrainingKcalNature.MET
    else:
        return TrainingKcal(
            kcal=total,
            kcal_nature=TrainingKcalNature.MET,
            nature=DataNature.ESTIMATED,
            quality=DataQuality.MEDIUM,
            limitations=(LIMIT_TRAINING_PARTIALLY_ESTIMATED, *sem_desconto),
            gross_kcal=bruto,
            resting_discount_kcal=bruto - total,
        )
    return replace(
        _training_kcal_of(natureza, total),
        limitations=sem_desconto,
        gross_kcal=bruto,
        resting_discount_kcal=bruto - total,
    )


def _training_kcal_of(natureza: TrainingKcalNature, kcal: Optional[float]) -> TrainingKcal:
    if kcal is None:
        return NO_TRAINING_KCAL
    return TrainingKcal(
        kcal=kcal,
        kcal_nature=natureza,
        nature=NATURE_BY_TRAINING_KCAL_NATURE[natureza],
        quality=QUALITY_BY_TRAINING_KCAL_NATURE[natureza],
    )


@dataclass(frozen=True)
class TrainingKcalComparison:
    """Linha "informado × estimado" do diário (L4): a kcal informada
    (`activities.calories_kcal`), a estimativa por MET (calculada na leitura,
    nunca gravada, E6) e a que o motor usa (prioridade de natureza)."""

    reported: TrainingKcal
    estimated: TrainingKcal
    used: TrainingKcal

    @property
    def partial(self) -> bool:
        """O dia mistura sessão informada e estimada: o usado não é nenhum dos dois lados."""
        return LIMIT_TRAINING_PARTIALLY_ESTIMATED in self.used.limitations

    def sides(self) -> tuple[tuple[TrainingKcalNature, TrainingKcal], ...]:
        """As naturezas na ordem da prioridade (`TRAINING_KCAL_NATURE_PRIORITY`) com o valor de cada uma."""
        valores = {TrainingKcalNature.REPORTED: self.reported, TrainingKcalNature.MET: self.estimated}
        return tuple((natureza, valores[natureza]) for natureza in TRAINING_KCAL_NATURE_PRIORITY)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reported": self.reported.to_dict(),
            "estimated": self.estimated.to_dict(),
            "used": self.used.to_dict(),
            "used_net_kcal": self.used.kcal,  # o valor usado, LÍQUIDO do repouso (quando o modo desconta)
            "partial": self.partial,
            # Lista pronta para a tela: código da natureza, valor e se é a usada pela prioridade.
            "sides": [
                {
                    "kcal_nature": natureza.value,
                    "value": valor.to_dict(),
                    "is_used": valor.kcal is not None and not self.partial and self.used.kcal_nature == natureza,
                }
                for natureza, valor in self.sides()
            ],
        }


def compare_sessions_kcal(
    sessions: Any = (), weight_kg: Any = None, met: Any = None, bmr_kcal: Any = None,
    calorie_mode: Any = DEFAULT_TDEE_CALORIE_MODE,
) -> TrainingKcalComparison:
    """As duas naturezas do dia por sessão e a usada (`resolve_sessions_kcal`): informada = soma das kcal informadas;
    estimada = MET das durações de todas as sessões; usada = a soma por sessão (mista = parcialmente estimada), LÍQUIDA
    do repouso (`bmr_kcal`); informada e estimada ficam brutas, para comparar com o que o relógio mostra."""
    sessoes = tuple(sessions)
    informadas = [positive_number(s.calories_kcal) for s in sessoes]
    informadas = [kcal for kcal in informadas if kcal is not None]
    estimadas = [training_kcal_met(weight_kg, s.duration_min, met) for s in sessoes]
    estimadas = [kcal for kcal in estimadas if kcal is not None]
    return TrainingKcalComparison(
        reported=_training_kcal_of(TrainingKcalNature.REPORTED, float(sum(informadas)) if informadas else None),
        estimated=_training_kcal_of(TrainingKcalNature.MET, float(sum(estimadas)) if estimadas else None),
        used=resolve_sessions_kcal(sessoes, weight_kg, met, bmr_kcal, calorie_mode),
    )


def compare_training_kcal(
    reported_kcal: Any = None,
    weight_kg: Any = None,
    duration_min: Any = None,
    met: Any = None,
    bmr_kcal: Any = None,
    calorie_mode: Any = DEFAULT_TDEE_CALORIE_MODE,
) -> TrainingKcalComparison:
    """As duas naturezas lado a lado (brutas) e a escolhida por `resolve_training_kcal` (líquida do repouso)."""
    return TrainingKcalComparison(
        reported=_training_kcal_of(TrainingKcalNature.REPORTED, positive_number(reported_kcal)),
        estimated=_training_kcal_of(TrainingKcalNature.MET, training_kcal_met(weight_kg, duration_min, met)),
        used=resolve_training_kcal(
            reported_kcal=reported_kcal, weight_kg=weight_kg, duration_min=duration_min, met=met, bmr_kcal=bmr_kcal,
            calorie_mode=calorie_mode,
        ),
    )


@dataclass(frozen=True)
class AdaptiveParts:
    """Decomposição do gasto do dia no modo adaptativo (a estimativa pronta de `adaptive_energy.DayEnergyEstimate`):
    base (adaptativa ou inicial) + ajuste pela atividade (com sinal) = gasto do dia."""

    base_kcal: float
    adjustment_kcal: float
    confidence: float
    confidence_level: str
    bootstrap: bool
    activity_pending: bool
    day_open: bool
    history_unavailable: bool = False

    def to_dict(self) -> dict[str, Any]:
        dados = {
            "base_kcal": self.base_kcal,
            "adjustment_kcal": self.adjustment_kcal,
            "confidence": self.confidence,
            "confidence_level": self.confidence_level,
            "bootstrap": self.bootstrap,
            "activity_pending": self.activity_pending,
            "day_open": self.day_open,
        }
        if self.history_unavailable:
            dados["history_unavailable"] = True
        return dados


@dataclass(frozen=True)
class TdeeResult:
    """Gasto total do dia (None = TMB ou nível de atividade ausentes)."""

    kcal: Optional[float]
    mode: TdeeMode
    activity_factor: Optional[float]
    bmr_kcal: Optional[float]
    training_kcal: Optional[float]
    limitations: tuple[str, ...] = ()
    # Só no modo adaptativo; nos outros fica `None` e NÃO entra no `to_dict` (o `inputs_json` legado não muda).
    adaptive: Optional[AdaptiveParts] = None

    @property
    def base_kcal(self) -> Optional[float]:
        """Parcela-base de rotina (TMB × fator do modo; `None` se faltar TMB ou fator). Só property: NÃO está no
        `to_dict` (que vai para `inputs_json`), então nada novo é gravado nas metas; a tela a recebe por
        `tdee_breakdown`. O gasto total se reconstrói por `base_kcal + (training_kcal or 0)` nos três modos (no
        embutido o treino é ignorado e a base é o total)."""
        if self.adaptive is not None:
            return self.adaptive.base_kcal
        if self.bmr_kcal is None or self.activity_factor is None:
            return None
        return self.bmr_kcal * self.activity_factor

    def to_dict(self) -> dict[str, Any]:
        dados: dict[str, Any] = {
            "kcal": self.kcal,
            "mode": self.mode.value,
            "activity_factor": self.activity_factor,
            "bmr_kcal": self.bmr_kcal,
            "training_kcal": self.training_kcal,
            "limitations": list(self.limitations),
        }
        if self.adaptive is not None:
            dados["adaptive"] = self.adaptive.to_dict()
        return dados


def activity_factor_for(level: Optional[ActivityLevel], mode: TdeeMode) -> Optional[float]:
    if level is None or mode == TdeeMode.ADAPTIVE_BALANCE:  # o adaptativo não usa fator × TMB
        return None
    explicito = mode in (TdeeMode.EXPLICIT_TRAINING, TdeeMode.AUTO_INTERVALS)
    tabela = NON_EXERCISE_ACTIVITY_FACTORS if explicito else ACTIVITY_FACTORS
    return tabela.get(level)


def rest_tdee(
    bmr_kcal: Any,
    profile_level: Optional[ActivityLevel],
    mode: TdeeMode = DEFAULT_TDEE_MODE,
    routine_level: Optional[ActivityLevel] = None,
) -> TdeeResult:
    """Gasto-base de um dia SEM atividade medida (o que o ritmo e o superávit multiplicam): no automático, a rotina
    fora do exercício; nos clássicos, o TDEE sem treino."""
    if mode == TdeeMode.AUTO_INTERVALS:
        return tdee(bmr_kcal, routine_level or ROUTINE_ACTIVITY_LEVEL_DEFAULT, TdeeMode.EXPLICIT_TRAINING, 0.0)
    if mode == TdeeMode.ADAPTIVE_BALANCE:
        # Sem estimativa do dia (referências do check-in e dos pisos): a estimativa inicial (mesmo fator da estimativa
        # do `adaptive_energy`: uma fonte só).
        tmb = positive_number(bmr_kcal)
        fator = adaptive_energy.BOOTSTRAP_ACTIVITY_FACTOR
        if tmb is None:
            return TdeeResult(None, TdeeMode.ADAPTIVE_BALANCE, fator, None, None, (LIMIT_MISSING_BMR,))
        return TdeeResult(tmb * fator, TdeeMode.ADAPTIVE_BALANCE, fator, tmb, None)
    return tdee(bmr_kcal, profile_level, mode, 0.0)


def adaptive_tdee_result(estimate: Any, bmr_kcal: Any) -> TdeeResult:
    """Gasto do dia no modo adaptativo a partir da estimativa PRONTA (`adaptive_energy.DayEnergyEstimate`, lida por
    duck typing: `estimated_kcal`, `base_kcal`, `adjustment_kcal`, `confidence`, `confidence_level`, `bootstrap`,
    `activity_pending`, `day_open`). Sem estimativa: gasto desconhecido + `LIMIT_ADAPTIVE_ESTIMATE_MISSING`. Não usa
    fator × TMB; a TMB só acompanha o resultado (plausibilidade, piso do plano)."""
    tmb = positive_number(bmr_kcal)
    if estimate is None:
        return TdeeResult(
            kcal=None, mode=TdeeMode.ADAPTIVE_BALANCE, activity_factor=None, bmr_kcal=tmb, training_kcal=None,
            limitations=(LIMIT_ADAPTIVE_ESTIMATE_MISSING,),
        )
    partes = AdaptiveParts(
        base_kcal=float(estimate.base_kcal),
        adjustment_kcal=float(estimate.adjustment_kcal),
        confidence=float(estimate.confidence),
        confidence_level=str(estimate.confidence_level),
        bootstrap=bool(estimate.bootstrap),
        activity_pending=bool(estimate.activity_pending),
        day_open=bool(estimate.day_open),
        history_unavailable=bool(getattr(estimate, "history_unavailable", False)),
    )
    return TdeeResult(
        kcal=float(estimate.estimated_kcal), mode=TdeeMode.ADAPTIVE_BALANCE, activity_factor=None, bmr_kcal=tmb,
        training_kcal=None, adaptive=partes,
    )


def tdee(
    bmr_kcal: Any,
    activity_level: Optional[ActivityLevel],
    mode: TdeeMode = DEFAULT_TDEE_MODE,
    training_kcal: Any = None,
    adaptive_estimate: Any = None,
) -> TdeeResult:
    """TDEE do dia. No modo explícito, o treino entra em kcal (0 em dia sem
    treino); no modo embutido, o treino é ignorado (já está no fator). O modo automático NÃO é resolvido aqui:
    quem chama usa `effective_tdee_basis` (aqui valeria como o explícito, com o nível dado). O modo adaptativo NÃO
    usa fator × TMB: recebe `adaptive_estimate` pronta (`adaptive_tdee_result`)."""
    if mode == TdeeMode.ADAPTIVE_BALANCE:
        return adaptive_tdee_result(adaptive_estimate, bmr_kcal)
    if mode == TdeeMode.AUTO_INTERVALS:
        mode = TdeeMode.EXPLICIT_TRAINING
    tmb = positive_number(bmr_kcal)
    fator = activity_factor_for(activity_level, mode)
    limitacoes: list[str] = []
    if tmb is None:
        limitacoes.append(LIMIT_MISSING_BMR)
    if fator is None:
        limitacoes.append(LIMIT_MISSING_ACTIVITY_LEVEL)
    treino = non_negative_number(training_kcal) if mode == TdeeMode.EXPLICIT_TRAINING else None
    if tmb is None or fator is None:
        return TdeeResult(
            kcal=None, mode=mode, activity_factor=fator, bmr_kcal=tmb, training_kcal=treino, limitations=tuple(limitacoes)
        )
    total = tmb * fator + (treino or 0.0)
    return TdeeResult(kcal=total, mode=mode, activity_factor=fator, bmr_kcal=tmb, training_kcal=treino)


# --- Composição do gasto para a tela (prévia do plano): parcelas prontas, sem conta no front -------------------------
BREAKDOWN_BASE: Final[str] = "base"
BREAKDOWN_STRENGTH: Final[str] = "training"  # a força, já LÍQUIDA do repouso da sessão (nome alinhado a `training_kcal`)
BREAKDOWN_EXTRAS: Final[str] = "extras"
BREAKDOWN_ACTIVITY_PREFIX: Final[str] = "activity_"  # `activity_<n>`: uma parcela por atividade do Intervals
BREAKDOWN_NOTE_INCLUDED: Final[str] = "training_included_in_factor"
BREAKDOWN_NOTE_AUTO_FALLBACK: Final[str] = "auto_fallback"
# Adaptativo: parcela com sinal do ajuste pela atividade e notas (confiança, estimativa inicial, pendências).
BREAKDOWN_ACTIVITY_ADJUSTMENT: Final[str] = "activity_adjustment"
BREAKDOWN_NOTE_ADAPTIVE_BOOTSTRAP: Final[str] = "adaptive_bootstrap"
BREAKDOWN_NOTE_ADAPTIVE_CONFIDENCE_LOW: Final[str] = "adaptive_confidence_low"
BREAKDOWN_NOTE_ADAPTIVE_CONFIDENCE_MEDIUM: Final[str] = "adaptive_confidence_medium"
BREAKDOWN_NOTE_ADAPTIVE_ACTIVITY_PENDING: Final[str] = "adaptive_activity_pending"
BREAKDOWN_NOTE_ADAPTIVE_DAY_OPEN: Final[str] = "adaptive_day_open"
BREAKDOWN_NOTE_ADAPTIVE_HISTORY_UNAVAILABLE: Final[str] = "adaptive_history_unavailable"
# As parcelas saem em kcal inteiras (a tela mostra inteiros): cada uma é arredondada e a BASE absorve a diferença, de
# modo que a soma das parcelas emitidas é EXATAMENTE o total emitido.
BREAKDOWN_KCAL_ROUNDING_STEP: Final[float] = 1.0


class BreakdownBase(str, Enum):
    """De onde vem a parcela-base: rotina (treino somado à parte), fator com exercício (embutido) ou o fallback do automático."""

    ROUTINE = "routine"
    FACTOR_WITH_EXERCISE = "factor_with_exercise"
    AUTO_FALLBACK = "auto_fallback"
    ADAPTIVE = "adaptive"  # "Gasto adaptativo médio"
    ADAPTIVE_BOOTSTRAP = "adaptive_bootstrap"  # "Estimativa inicial"


@dataclass(frozen=True)
class BreakdownPart:
    code: str
    kcal: float
    # Rótulo dinâmico (nome da atividade do Intervals); sem ele a tela usa o rótulo do código.
    label: Optional[str] = None


@dataclass(frozen=True)
class TdeeBreakdown:
    """Parcelas do gasto total do dia: base (sempre), força e extras (só as que somam), notas e o total. Saída pública
    da prévia: NÃO entra em `inputs_json`, no hash nem no `to_dict` gravado."""

    parts: tuple[BreakdownPart, ...]
    base: BreakdownBase
    notes: tuple[str, ...]
    total_kcal: float


def _round_step(value: float) -> float:
    return math.floor(value / BREAKDOWN_KCAL_ROUNDING_STEP + 0.5) * BREAKDOWN_KCAL_ROUNDING_STEP


def _activity_parts(extra_items: Any) -> list[tuple[str, float]]:
    """(rótulo, kcal líquida) das atividades do Intervals a partir de `ExtraActivityKcal.to_dict()["items"]` (ou dos
    itens gravados na meta). Item sem `net_kcal` (nada descontado) vale a própria kcal informada; sem kcal é ignorado."""
    partes: list[tuple[str, float]] = []
    for item in extra_items or ():
        liquido = non_negative_number(item.get("net_kcal"))
        kcal = liquido if liquido is not None else non_negative_number(item.get("calories_kcal"))
        if kcal is None:
            continue
        partes.append((str(item.get("label") or EXTRA_ACTIVITY_FALLBACK_LABEL), kcal))
    return partes


def tdee_breakdown(
    tdee_result: "TdeeResult", extra_kcal: Any, basis: "TdeeBasis", extra_items: Any = ()
) -> Optional[TdeeBreakdown]:
    """Decompõe o gasto do dia em parcelas que fecham o total. `tdee_result` é o do dia (modo EFETIVO: o automático
    sincronizado vem como explícito, com `training_kcal` = força + extras; o embutido e o fallback ignoram o treino);
    `extra_kcal` são as atividades do Intervals já somadas; `extra_items` (opcional, `ExtraActivityKcal.to_dict()["items"]`)
    quebra os extras em UMA parcela por atividade, com o nome do Intervals; `basis` diz se é clássico, automático ou
    fallback. `None` sem TMB ou fator (gasto desconhecido). Parcela zero é omitida; a base sempre entra e fecha o total."""
    total = tdee_result.kcal
    if total is None or tdee_result.base_kcal is None:
        return None  # inclui o adaptativo sem estimativa (sem `adaptive` e sem fator)
    total_pub = _round_step(total)
    if tdee_result.adaptive is not None:
        return _adaptive_breakdown(tdee_result.adaptive, total_pub)
    if tdee_result.mode == TdeeMode.INCLUDED_IN_FACTOR:
        fallback = basis == TdeeBasis.AUTO_FALLBACK
        return TdeeBreakdown(
            parts=(BreakdownPart(BREAKDOWN_BASE, total_pub),),
            base=BreakdownBase.AUTO_FALLBACK if fallback else BreakdownBase.FACTOR_WITH_EXERCISE,
            notes=(BREAKDOWN_NOTE_AUTO_FALLBACK if fallback else BREAKDOWN_NOTE_INCLUDED,),
            total_kcal=total_pub,
        )
    atividade = non_negative_number(tdee_result.training_kcal) or 0.0
    forca = _round_step(max(0.0, atividade - (non_negative_number(extra_kcal) or 0.0)))
    por_atividade = [(rotulo, _round_step(kcal)) for rotulo, kcal in _activity_parts(extra_items)]
    por_atividade = [(rotulo, kcal) for rotulo, kcal in por_atividade if kcal > 0]
    if por_atividade:
        extras_parcelas = [
            BreakdownPart(f"{BREAKDOWN_ACTIVITY_PREFIX}{i}", kcal, rotulo) for i, (rotulo, kcal) in enumerate(por_atividade)
        ]
    else:
        agregado = _round_step(non_negative_number(extra_kcal) or 0.0)
        extras_parcelas = [BreakdownPart(BREAKDOWN_EXTRAS, agregado)] if agregado > 0 else []
    soma_extras = sum(parte.kcal for parte in extras_parcelas)
    partes = [BreakdownPart(BREAKDOWN_BASE, total_pub - forca - soma_extras)]
    if forca > 0:
        partes.append(BreakdownPart(BREAKDOWN_STRENGTH, forca))
    partes.extend(extras_parcelas)
    return TdeeBreakdown(parts=tuple(partes), base=BreakdownBase.ROUTINE, notes=(), total_kcal=total_pub)


def _stored_number(value: Any) -> Optional[float]:
    """Número gravado -> float; texto, bool, NaN e infinito viram `None` (dado gravado ausente ou corrompido)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def tdee_result_from_stored(tdee: Mapping[str, Any]) -> Optional[TdeeResult]:
    """Reconstrói o `TdeeResult` a partir do dict gravado em `inputs_json.tdee` (chaves de `TdeeResult.to_dict()`),
    sem recalcular TMB nem fator: só relê o que foi gravado. `None` se o dict não for utilizável (sem `kcal` ou
    `mode` válidos; adaptativo com `adaptive` incompleto)."""
    if not isinstance(tdee, Mapping):
        return None
    try:
        modo = TdeeMode(tdee.get("mode"))
    except ValueError:
        return None
    kcal = _stored_number(tdee.get("kcal"))
    if kcal is None:
        return None
    adaptativo = None
    bruto = tdee.get("adaptive")
    if modo == TdeeMode.ADAPTIVE_BALANCE or bruto is not None:
        if not isinstance(bruto, Mapping):
            return None
        base = _stored_number(bruto.get("base_kcal"))
        ajuste = _stored_number(bruto.get("adjustment_kcal"))
        confianca = _stored_number(bruto.get("confidence"))
        if base is None or ajuste is None or confianca is None:
            return None
        adaptativo = AdaptiveParts(
            base_kcal=base,
            adjustment_kcal=ajuste,
            confidence=confianca,
            confidence_level=str(bruto.get("confidence_level") or ""),
            bootstrap=bool(bruto.get("bootstrap")),
            activity_pending=bool(bruto.get("activity_pending")),
            day_open=bool(bruto.get("day_open")),
            history_unavailable=bool(bruto.get("history_unavailable")),
        )
    return TdeeResult(
        kcal=kcal,
        mode=modo,
        activity_factor=_stored_number(tdee.get("activity_factor")),
        bmr_kcal=_stored_number(tdee.get("bmr_kcal")),
        training_kcal=_stored_number(tdee.get("training_kcal")),
        adaptive=adaptativo,
    )


def tdee_breakdown_from_stored(
    tdee: Mapping[str, Any], extra_kcal: Any, basis: "TdeeBasis", extra_items: Any = ()
) -> Optional[TdeeBreakdown]:
    """Composição do gasto do DIA a partir do que está GRAVADO na meta (`inputs_json.tdee`), para todos os modos.
    Mesma saída de `tdee_breakdown` (é a mesma função por baixo: nada de regra duplicada); soma das parcelas == total.
    `None` se o dict gravado estiver incompleto."""
    resultado = tdee_result_from_stored(tdee)
    return None if resultado is None else tdee_breakdown(resultado, extra_kcal, basis, extra_items)


def _adaptive_breakdown(partes: AdaptiveParts, total_pub: float) -> TdeeBreakdown:
    """Adaptativo: parcela-base (gasto adaptativo médio ou estimativa inicial) + ajuste pela atividade (com sinal,
    omitido se zero) = total. A BASE absorve a diferença de arredondamento, como nos outros modos."""
    ajuste = _round_step(partes.adjustment_kcal)
    codigo_base = BreakdownBase.ADAPTIVE_BOOTSTRAP if partes.bootstrap else BreakdownBase.ADAPTIVE
    lista = [BreakdownPart(BREAKDOWN_BASE, total_pub - ajuste)]
    if ajuste != 0:
        lista.append(BreakdownPart(BREAKDOWN_ACTIVITY_ADJUSTMENT, ajuste))
    notas: list[str] = []
    if partes.bootstrap:
        notas.append(BREAKDOWN_NOTE_ADAPTIVE_BOOTSTRAP)
    elif partes.confidence_level == adaptive_energy.ConfidenceLevel.LOW.value:
        notas.append(BREAKDOWN_NOTE_ADAPTIVE_CONFIDENCE_LOW)
    elif partes.confidence_level == adaptive_energy.ConfidenceLevel.MEDIUM.value:
        notas.append(BREAKDOWN_NOTE_ADAPTIVE_CONFIDENCE_MEDIUM)
    if partes.history_unavailable:
        notas.append(BREAKDOWN_NOTE_ADAPTIVE_HISTORY_UNAVAILABLE)
    if partes.day_open:
        notas.append(BREAKDOWN_NOTE_ADAPTIVE_DAY_OPEN)
    elif partes.activity_pending:
        notas.append(BREAKDOWN_NOTE_ADAPTIVE_ACTIVITY_PENDING)
    return TdeeBreakdown(parts=tuple(lista), base=codigo_base, notes=tuple(notas), total_kcal=total_pub)
