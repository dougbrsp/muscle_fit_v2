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

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Mapping, Optional

from . import adaptive_energy
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
# Plano `auto_intervals` sem nível de atividade no PERFIL: o fallback (fator com exercício) não tem de onde vir e
# não se inventa fator; bloqueia salvar/publicar (a API devolve este código com o rótulo do motor).
LIMIT_AUTO_REQUIRES_PROFILE_LEVEL = "auto_intervals_requires_profile_activity_level"
LIMIT_MISSING_BMR = "missing_bmr"
LIMIT_MISSING_ACTIVITY_LEVEL = "missing_activity_level"
# Plano adaptativo sem a estimativa do dia (nem a de fórmula, por falta de TMB): o gasto não é calculável.
LIMIT_ADAPTIVE_ESTIMATE_MISSING = "adaptive_estimate_missing"


# --- Atividades extras do Intervals (cardio, caminhada, outras) -----------------------------------------------
# Decisão do usuário (2026-11-05): o gasto do dia inclui o cardio e as caminhadas do Intervals, além do treino de
# força. O SERVIÇO classifica a atividade em `app/intervals_activity_kind.py` (dono de "o que é cardio/força") e
# entrega a classe; aqui se decide o que soma (cardio e outras; só as classes listadas). Força nunca entra por esta via (já é o treino de `activities`;
# somar de novo duplicaria). Atividade sem kcal informada não soma e não é estimada.
EXTRA_ACTIVITY_KINDS: Final[frozenset[str]] = frozenset({"cardio", "other"})  # = `intervals_activity_kind.CARDIO/OTHER` (teste de paridade)
EXTRA_ACTIVITY_KCAL_NATURE: Final[TrainingKcalNature] = TrainingKcalNature.REPORTED
# O rótulo (nome da atividade) vem do Intervals, texto livre: o motor limita o tamanho que a API devolve e grava.
EXTRA_ACTIVITY_LABEL_MAX_CHARS: Final[int] = 60


@dataclass(frozen=True)
class ExtraActivity:
    """Uma atividade do Intervals do dia que NÃO é o treino de `activities`: classe (do dono da classificação),
    kcal informada (pode faltar) e o rótulo para a tela (nome ou tipo)."""

    kind: str
    calories_kcal: Any = None
    label: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        rotulo = None if self.label is None else str(self.label).strip()[:EXTRA_ACTIVITY_LABEL_MAX_CHARS] or None
        return {"kind": self.kind, "calories_kcal": positive_number(self.calories_kcal), "label": rotulo}


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
    # Balanço energético adaptativo (2026-11-06, Lote C): o gasto do dia vem da estimativa pronta de
    # `adaptive_energy.DayEnergyEstimate` (alimentação + peso de tendência + Intervals como delta), NÃO de fator × TMB.
    ADAPTIVE_BALANCE = "adaptive_balance"


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
    sessoes = tuple(sessions)
    partes = _session_parts(sessoes, weight_kg, met)
    if not partes:
        return NO_TRAINING_KCAL
    total = float(sum(kcal for _, kcal in partes))
    # O dia é "parcialmente estimado" pelas SESSÕES (não pelo que deu para somar): há sessão sem kcal informada junto de
    # uma informada, ou alguma sessão ficou de fora da soma (sem kcal e sem duração/peso).
    informadas = sum(1 for sessao in sessoes if positive_number(sessao.calories_kcal) is not None)
    todas_informadas = informadas == len(sessoes)
    ficou_de_fora = len(partes) < len(sessoes)
    if todas_informadas:
        return _training_kcal_of(TrainingKcalNature.REPORTED, total)
    if informadas == 0 and not ficou_de_fora:
        return _training_kcal_of(TrainingKcalNature.MET, total)
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
BREAKDOWN_STRENGTH: Final[str] = "training"  # a força (nome alinhado a `training_kcal`)
BREAKDOWN_EXTRAS: Final[str] = "extras"
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


def tdee_breakdown(tdee_result: "TdeeResult", extra_kcal: Any, basis: "TdeeBasis") -> Optional[TdeeBreakdown]:
    """Decompõe o gasto do dia em parcelas que fecham o total. `tdee_result` é o do dia (modo EFETIVO: o automático
    sincronizado vem como explícito, com `training_kcal` = força + extras; o embutido e o fallback ignoram o treino);
    `extra_kcal` são as atividades extras já somadas; `basis` diz se é clássico, automático ou fallback. `None` sem
    TMB ou fator (gasto desconhecido). Parcela zero (força ou extras) é omitida; a base sempre entra."""
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
    extras = _round_step(non_negative_number(extra_kcal) or 0.0)
    atividade = non_negative_number(tdee_result.training_kcal) or 0.0
    forca = _round_step(max(0.0, atividade - (non_negative_number(extra_kcal) or 0.0)))
    partes = [BreakdownPart(BREAKDOWN_BASE, total_pub - forca - extras)]
    if forca > 0:
        partes.append(BreakdownPart(BREAKDOWN_STRENGTH, forca))
    if extras > 0:
        partes.append(BreakdownPart(BREAKDOWN_EXTRAS, extras))
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


def tdee_breakdown_from_stored(tdee: Mapping[str, Any], extra_kcal: Any, basis: "TdeeBasis") -> Optional[TdeeBreakdown]:
    """Composição do gasto do DIA a partir do que está GRAVADO na meta (`inputs_json.tdee`), para todos os modos.
    Mesma saída de `tdee_breakdown` (é a mesma função por baixo: nada de regra duplicada); soma das parcelas == total.
    `None` se o dict gravado estiver incompleto."""
    resultado = tdee_result_from_stored(tdee)
    return None if resultado is None else tdee_breakdown(resultado, extra_kcal, basis)


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
