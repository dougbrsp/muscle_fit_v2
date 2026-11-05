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

TDEE (`tdee`): `EXPLICIT_TRAINING` = TMB × fator de NÃO-exercício + kcal do
treino do dia (padrão; evita dupla contagem); `INCLUDED_IN_FACTOR` = TMB ×
fator clássico (treino embutido). TEF já embutido nos fatores.

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Mapping, Optional

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
# Dia com sessão informada e outra sem kcal: o total soma a informada e a MET só da duração da que não tem kcal.
LIMIT_TRAINING_PARTIALLY_ESTIMATED = "training_kcal_partially_estimated"
LIMIT_MISSING_BMR = "missing_bmr"
LIMIT_MISSING_ACTIVITY_LEVEL = "missing_activity_level"


# --- Atividades extras do Intervals (cardio, caminhada, outras) -----------------------------------------------
# Decisão do usuário (2026-11-05): o gasto do dia inclui o cardio e as caminhadas do Intervals, além do treino de
# força. O SERVIÇO classifica a atividade em `app/intervals_activity_kind.py` (dono de "o que é cardio/força") e
# entrega a classe; aqui se decide o que soma (cardio e outras; só as classes listadas). Força nunca entra por esta via (já é o treino de `activities`;
# somar de novo duplicaria). Atividade sem kcal informada não soma e não é estimada.
EXTRA_ACTIVITY_KINDS: Final[frozenset[str]] = frozenset({"cardio", "other"})  # = `intervals_activity_kind.CARDIO/OTHER` (teste de paridade)
EXTRA_ACTIVITY_KCAL_NATURE: Final[TrainingKcalNature] = TrainingKcalNature.REPORTED


@dataclass(frozen=True)
class ExtraActivity:
    """Uma atividade do Intervals do dia que NÃO é o treino de `activities`: classe (do dono da classificação),
    kcal informada (pode faltar) e o rótulo para a tela (nome ou tipo)."""

    kind: str
    calories_kcal: Any = None
    label: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "calories_kcal": positive_number(self.calories_kcal), "label": self.label}


@dataclass(frozen=True)
class ExtraActivityKcal:
    """kcal das atividades extras do dia, a lista do que somou e a natureza (informada = observada)."""

    kcal: float = 0.0
    items: tuple[ExtraActivity, ...] = ()
    kcal_nature: Optional[TrainingKcalNature] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal": self.kcal,
            "kcal_nature": self.kcal_nature.value if self.kcal_nature else None,
            "items": [item.to_dict() for item in self.items],
        }


NO_EXTRA_ACTIVITY_KCAL: Final[ExtraActivityKcal] = ExtraActivityKcal()


def extra_activity_kcal(activities: Any = ()) -> ExtraActivityKcal:
    """Soma a kcal informada das atividades que não são força. Sem kcal (None, 0, inválida) não soma."""
    somadas = tuple(
        item
        for item in activities
        if item.kind in EXTRA_ACTIVITY_KINDS and positive_number(item.calories_kcal) is not None
    )
    if not somadas:
        return NO_EXTRA_ACTIVITY_KCAL
    total = float(sum(positive_number(item.calories_kcal) or 0.0 for item in somadas))
    return ExtraActivityKcal(kcal=total, items=somadas, kcal_nature=EXTRA_ACTIVITY_KCAL_NATURE)


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


DEFAULT_TDEE_MODE: Final[TdeeMode] = TdeeMode.EXPLICIT_TRAINING


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal": self.kcal,
            "kcal_nature": self.kcal_nature.value if self.kcal_nature else None,
            "nature": self.nature.value if self.nature else None,
            "quality": self.quality.value if self.quality else None,
            "limitations": list(self.limitations),
        }


NO_TRAINING_KCAL: Final[TrainingKcal] = TrainingKcal(
    kcal=None, kcal_nature=None, nature=None, quality=None, limitations=(LIMIT_NO_TRAINING_KCAL,)
)


def resolve_training_kcal(
    reported_kcal: Any = None,
    weight_kg: Any = None,
    duration_min: Any = None,
    met: Any = None,
) -> TrainingKcal:
    """Primeira natureza disponível na ordem de `TRAINING_KCAL_NATURE_PRIORITY`.
    `reported_kcal` é o número de `activities.calories_kcal`, seja qual for a
    proveniência. Sem kcal informada e sem duração -> `NO_TRAINING_KCAL`."""
    for natureza in TRAINING_KCAL_NATURE_PRIORITY:
        if natureza == TrainingKcalNature.REPORTED:
            kcal = positive_number(reported_kcal)
        else:
            kcal = training_kcal_met(weight_kg, duration_min, met)
        if kcal is not None:
            return TrainingKcal(
                kcal=kcal,
                kcal_nature=natureza,
                nature=NATURE_BY_TRAINING_KCAL_NATURE[natureza],
                quality=QUALITY_BY_TRAINING_KCAL_NATURE[natureza],
            )
    return NO_TRAINING_KCAL


@dataclass(frozen=True)
class SessionKcal:
    """Uma sessão de treino concluída do dia: kcal informada (pode faltar) e duração em minutos (pode faltar)."""

    calories_kcal: Any = None
    duration_min: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"calories_kcal": positive_number(self.calories_kcal), "duration_min": positive_number(self.duration_min)}


def _session_parts(
    sessions: Any, weight_kg: Any, met: Any
) -> list[tuple[TrainingKcalNature, float]]:
    """kcal de cada sessão que tem como entrar: a informada onde houver; MET só da duração da sem kcal; a sem kcal e
    sem duração (ou sem peso para a MET) não soma e não derruba as outras."""
    partes: list[tuple[TrainingKcalNature, float]] = []
    for sessao in sessions:
        informada = positive_number(sessao.calories_kcal)
        if informada is not None:
            partes.append((TrainingKcalNature.REPORTED, informada))
            continue
        estimada = training_kcal_met(weight_kg, sessao.duration_min, met)
        if estimada is not None:
            partes.append((TrainingKcalNature.MET, estimada))
    return partes


def resolve_sessions_kcal(sessions: Any = (), weight_kg: Any = None, met: Any = None) -> TrainingKcal:
    """kcal do treino do dia SOMANDO POR SESSÃO (prioridade `TRAINING_KCAL_NATURE_PRIORITY` em cada uma). Todas
    informadas: `reported`. Nenhuma informada: `met` (MET das durações). Mistas: a soma, natureza `met` (estimada) e a
    limitação `LIMIT_TRAINING_PARTIALLY_ESTIMATED`; o dia só é `reported` se tudo for informado. Nada utilizável:
    `NO_TRAINING_KCAL`."""
    partes = _session_parts(sessions, weight_kg, met)
    if not partes:
        return NO_TRAINING_KCAL
    total = float(sum(kcal for _, kcal in partes))
    naturezas = {natureza for natureza, _ in partes}
    if len(naturezas) == 1:
        return _training_kcal_of(next(iter(naturezas)), total)
    return TrainingKcal(
        kcal=total,
        kcal_nature=TrainingKcalNature.MET,
        nature=DataNature.ESTIMATED,
        quality=DataQuality.MEDIUM,
        limitations=(LIMIT_TRAINING_PARTIALLY_ESTIMATED,),
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


def compare_sessions_kcal(sessions: Any = (), weight_kg: Any = None, met: Any = None) -> TrainingKcalComparison:
    """As duas naturezas do dia por sessão e a usada (`resolve_sessions_kcal`): informada = soma das kcal informadas;
    estimada = MET das durações de todas as sessões; usada = a soma por sessão (mista = parcialmente estimada)."""
    sessoes = tuple(sessions)
    informadas = [positive_number(s.calories_kcal) for s in sessoes]
    informadas = [kcal for kcal in informadas if kcal is not None]
    estimadas = [training_kcal_met(weight_kg, s.duration_min, met) for s in sessoes]
    estimadas = [kcal for kcal in estimadas if kcal is not None]
    return TrainingKcalComparison(
        reported=_training_kcal_of(TrainingKcalNature.REPORTED, float(sum(informadas)) if informadas else None),
        estimated=_training_kcal_of(TrainingKcalNature.MET, float(sum(estimadas)) if estimadas else None),
        used=resolve_sessions_kcal(sessoes, weight_kg, met),
    )


def compare_training_kcal(
    reported_kcal: Any = None,
    weight_kg: Any = None,
    duration_min: Any = None,
    met: Any = None,
) -> TrainingKcalComparison:
    """As duas naturezas lado a lado e a escolhida por `resolve_training_kcal`."""
    return TrainingKcalComparison(
        reported=_training_kcal_of(TrainingKcalNature.REPORTED, positive_number(reported_kcal)),
        estimated=_training_kcal_of(TrainingKcalNature.MET, training_kcal_met(weight_kg, duration_min, met)),
        used=resolve_training_kcal(reported_kcal=reported_kcal, weight_kg=weight_kg, duration_min=duration_min, met=met),
    )


@dataclass(frozen=True)
class TdeeResult:
    """Gasto total do dia (None = TMB ou nível de atividade ausentes)."""

    kcal: Optional[float]
    mode: TdeeMode
    activity_factor: Optional[float]
    bmr_kcal: Optional[float]
    training_kcal: Optional[float]
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal": self.kcal,
            "mode": self.mode.value,
            "activity_factor": self.activity_factor,
            "bmr_kcal": self.bmr_kcal,
            "training_kcal": self.training_kcal,
            "limitations": list(self.limitations),
        }


def activity_factor_for(level: Optional[ActivityLevel], mode: TdeeMode) -> Optional[float]:
    if level is None:
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
    return tdee(bmr_kcal, profile_level, mode, 0.0)


def tdee(
    bmr_kcal: Any,
    activity_level: Optional[ActivityLevel],
    mode: TdeeMode = DEFAULT_TDEE_MODE,
    training_kcal: Any = None,
) -> TdeeResult:
    """TDEE do dia. No modo explícito, o treino entra em kcal (0 em dia sem
    treino); no modo embutido, o treino é ignorado (já está no fator). O modo automático NÃO é resolvido aqui:
    quem chama usa `effective_tdee_basis` (aqui valeria como o explícito, com o nível dado)."""
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
