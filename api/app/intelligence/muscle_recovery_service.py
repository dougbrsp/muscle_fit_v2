from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from typing import Any

from ..domain import e1rm
from . import wellness_freshness
from .base import IntelligenceServiceBase
from .volume_service import VolumeService

logger = logging.getLogger("fitness.intelligence")

DENSITY_REFERENCE_DOSE = 7.5

# --- Dose reduzida (M76 B2; decisão 1 do usuário em 2026-10-06) -------------
# Cada exercício de um grupo REDUCED_DOSE faz `ceil(S × 0,75)` séries, mínimo 1,
# em TODO lugar (Modo A, Modo B, Modelos por IA, motor de progressão, prompts).
# `MuscleRecoveryService.reduced_sets` é a única função que aplica a regra.
REDUCED_DOSE_MULTIPLIER = 0.75
REDUCED_DOSE_MIN_SETS = 1

# Teto rígido das horas de recuperação de UMA sessão (ver docstring da classe).
# É teto, não exigência: a maioria das sessões recupera bem antes.
RECOVERY_MAX_HOURS = 72.0

# --- Janela única do retrato de recuperação (M76 B3) ------------------------
# Quantos dias de séries o overview lê para decidir QUAIS grupos entram no
# retrato, e quantos grupos ele devolve. Geração de ficha (rascunho e
# congelamento), planner do Modo A e dashboards usam os MESMOS valores - antes
# eram 14/24 na geração e 7-28/100 no dashboard, e a tela e a ficha podiam
# discordar do mesmo grupo. O limite é alto de propósito: nunca corta grupo.
#
# 28 dias (o MAIOR dos dois valores antigos), não 14: grupo fora da janela
# some do retrato e vira READY sem sinal nenhum. Com 14, a tela de 28 dias
# perdia a dor/queda de desempenho de uma sessão de 15-28 dias atrás sem
# sessão posterior - o contrário da decisão 2 ("sinais persistem até a
# próxima sessão do grupo"). A janela só decide QUAIS grupos entram; a dose e
# os sinais vêm sempre da última sessão de cada grupo.
RECOVERY_LOOKBACK_DAYS = 28
RECOVERY_GROUP_LIMIT = 100


def _reduced_dose_sets_rule() -> str:
    """Texto da regra de séries da dose reduzida, gerado das constantes (vai
    para o contrato da tela e para os prompts - nunca escrito à mão)."""
    percent = int(round(REDUCED_DOSE_MULTIPLIER * 100))
    return (
        f"{percent}% das séries planejadas de cada exercício, arredondado para cima "
        f"(mínimo {REDUCED_DOSE_MIN_SETS})"
    )


# Coeficiente pessoal de recuperacao enquanto nao houver calibracao (B12).
NEUTRAL_PERSONAL_COEFFICIENT = 1.00

# --- Limiares de prontidao (classify_readiness / count_negative_signals /
# build_blocking_signals). Lote 1/P2 da investigacao RPE 10 (2026-10-11):
# nomeados, valores inalterados. Consumidores leem daqui
# (ex.: `dashboard/recovery_signals_service.PERFORMANCE_DROP_RATIO`).
PERFORMANCE_DROP_SIGNAL_RATIO = 0.95  # performance_ratio abaixo disso = sinal negativo
PERFORMANCE_DROP_BLOCK_RATIO = 0.90  # abaixo disso = NO_DIRECT_WORK direto
READY_MIN_ADJUSTED_READINESS = 75  # prontidao ajustada abaixo disso = REDUCED_DOSE
NO_DIRECT_WORK_BELOW_READINESS = 55  # abaixo disso = NO_DIRECT_WORK
MUSCLE_PAIN_SIGNAL_MIN = 4  # dor muscular a partir disso = sinal negativo
MUSCLE_PAIN_BLOCK_ABOVE = 6  # dor muscular acima disso = NO_DIRECT_WORK
NO_DIRECT_WORK_MIN_SIGNALS = 2  # sinais negativos a partir disso = NO_DIRECT_WORK


def muscle_pain_blocks_direct_work(muscle_pain: Any) -> bool:
    """Dor muscular alta: acima de `MUSCLE_PAIN_BLOCK_ABOVE` o grupo vai para
    NO_DIRECT_WORK (`classify_readiness`). Predicado UNICO do limiar - a
    avaliacao de serie pela IA (set-eval-v5) usa o mesmo para mandar reduzir a
    carga/encerrar o exercicio. None/invalido -> False."""
    if muscle_pain is None or isinstance(muscle_pain, bool):
        return False
    try:
        return float(muscle_pain) > MUSCLE_PAIN_BLOCK_ABOVE
    except (TypeError, ValueError):
        return False


class MuscleRecoveryService(IntelligenceServiceBase):
    """Modelo de recuperacao muscular v2 ("fullbody-no-reps-v1").

    SUBSTITUI POR COMPLETO o modelo de fadiga continua da Fase A (PR #16,
    RPE x reps x carga x tipo x fadiga residual exponencial acumulada entre
    sessoes). Esse modelo anterior produzia recuperacoes >96h apos sessoes
    normais, incompativel com o metodo real do usuario (Full Body
    frequente/diario, estrategia NO REPS em que a ultima serie pode ir a
    RPE 10 de proposito). Este modulo implementa a substituicao aprovada
    pelo tech lead/dono do produto, descrita no segundo documento externo
    ("Algoritmo de Recuperacao Muscular para Hipertrofia" - correcao).

    Conceitos centrais:
    - `session_dose`: unidade de calculo e um evento (grupo muscular,
      sessao especifica), nao mais "fadiga acumulada numa janela de dias".
    - SEM fadiga residual acumulando indefinidamente entre sessoes (isso e
      EXPLICITAMENTE proibido pelo documento - era o erro da v1). Em vez
      disso: (a) tempo decorrido desde a sessao MAIS RECENTE daquele grupo,
      comparado linearmente com as horas estimadas dessa sessao; (b) uma
      janela separada de 72h somando as doses de sessoes recentes
      (densidade), que penaliza a prontidao mas NAO estende as horas de
      recuperacao.
    - Teto rigido de 72h SEMPRE (nunca mais que isso numa sessao normal) -
      diferente da v1 que ia ate 96h.
    """

    # RPE -> pontos de esforco (interpolacao linear). RPE clampado em
    # [primeiro, último] ponto da tabela antes de interpolar.
    _EFFORT_POINTS: tuple[tuple[float, float], ...] = (
        (6.5, 0.15),
        (7.0, 0.30),
        (7.5, 0.45),
        (8.0, 0.60),
        (8.5, 0.72),
        (9.0, 0.85),
        (9.5, 0.95),
        (10.0, 1.05),
    )
    # Alias público da tabela (o espelho do FE a lê via
    # `technique_accounting.export_for_frontend`).
    EFFORT_POINTS = _EFFORT_POINTS

    # Fator de recuperação das séries que NÃO são aplicação de técnica.
    # M76 (B5): aplicação de técnica (`drop_set`, `rest_pause`, `forced_rep` e
    # o apelido `dropset`) não tem fator aqui - o `set_type` só aponta o CÓDIGO
    # da técnica (`technique_accounting.DEFAULT_CODE_BY_SET_TYPE`) e o fator é
    # o `recovery_cost_factor` do catálogo (`advanced_techniques`, migração
    # 060: forçadas 1,20, negativas 1,25...). Antes havia uma cópia aqui
    # (forced_rep 1,05) que divergia do catálogo.
    _SET_TYPE_FACTORS: dict[str, float] = {
        "warmup": 0.10,
        "working": 1.00,
        "backoff": 0.95,
        "feeler": 0.20,
    }

    _RECOMMENDATIONS: dict[str, dict[str, Any]] = {
        "READY": {
            "volume_multiplier": 1.00,
            "direct_sets_min": 2,
            "direct_sets_max": 3,
            "maximum_rpe": 10,
            "allow_failure_on_last_set": True,
            "allow_progression": True,
            "allow_indirect_work": True,
        },
        "REDUCED_DOSE": {
            # M76 B2 (decisão 1): a dose reduzida não é mais "1-2 séries
            # diretas" - é `ceil(S × 0,75)` por exercício, mínimo 1
            # (`reduced_sets`). Sem faixa fixa de séries diretas.
            "volume_multiplier": REDUCED_DOSE_MULTIPLIER,
            "direct_sets_min": None,
            "direct_sets_max": None,
            "minimum_sets": REDUCED_DOSE_MIN_SETS,
            "sets_rule": _reduced_dose_sets_rule(),
            "maximum_rpe": 9,
            "allow_failure_on_last_set": False,
            "allow_progression": False,
            "allow_indirect_work": False,
        },
        "NO_DIRECT_WORK": {
            "volume_multiplier": 0.00,
            "direct_sets_min": 0,
            "direct_sets_max": 0,
            "maximum_rpe": 8,
            "allow_failure_on_last_set": False,
            "allow_progression": False,
            "allow_indirect_work": True,
        },
    }

    _STATUS_META: dict[str, tuple[str, str]] = {
        "READY": ("green", "ideal"),
        "REDUCED_DOSE": ("yellow", "recovering"),
        "NO_DIRECT_WORK": ("red", "avoid"),
    }

    # ------------------------------------------------------------------
    # Funcoes puras (secao aprovada do documento externo)
    # ------------------------------------------------------------------

    @staticmethod
    def effort_points(rpe: float) -> float:
        points = MuscleRecoveryService._EFFORT_POINTS
        clamped = MuscleRecoveryService.clamp(float(rpe), points[0][0], points[-1][0])
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if x0 <= clamped <= x1:
                if x1 == x0:
                    return y0
                ratio = (clamped - x0) / (x1 - x0)
                return y0 + ratio * (y1 - y0)
        return points[-1][1]

    @staticmethod
    def repetition_factor(repetitions: int) -> float:
        reps = int(repetitions)
        if reps <= 4:
            return 0.90
        if reps <= 20:
            return 1.00
        if reps <= 30:
            return 1.05
        return 1.10

    @staticmethod
    def set_type_factor(set_type: str) -> float:
        """Fator das séries que não são técnica (aquecimento, ajuste, backoff,
        trabalho). Aplicação de técnica tem o fator do CATÁLOGO
        (`technique_accounting.recovery_factor`); sem catálogo calibrado ela
        vale 1,00 aqui - nunca um número próprio."""
        return MuscleRecoveryService._SET_TYPE_FACTORS.get((set_type or "").strip().lower(), 1.00)

    @staticmethod
    def estimate_rir(rpe: float) -> float:
        """RIR = 10 - RPE. Dono: `domain/e1rm.rir_from_rpe` (M76, Lote 3a)."""
        return e1rm.rir_from_rpe(rpe)

    @staticmethod
    def estimated_1rm(load_kg: float, repetitions: int, rpe: float) -> float:
        """e1RM (Epley + RIR). Dono: `domain/e1rm.estimated_1rm` (M76, Lote
        3a); aqui so mantem o contrato antigo (`0.0` quando nao ha e1RM)."""
        return e1rm.estimated_1rm(load_kg, repetitions, rpe) or 0.0

    @staticmethod
    def load_spike_factor(current_e1rm: float | None, reference_e1rm: float | None) -> float:
        # NUNCA reduzir quando current < reference (so ajusta pra cima).
        if not reference_e1rm or reference_e1rm <= 0:
            return 1.00
        if not current_e1rm or current_e1rm <= 0:
            return 1.00
        increase = float(current_e1rm) / float(reference_e1rm) - 1.0
        if increase <= 0.05:
            return 1.00
        if increase <= 0.10:
            return 1.03
        return 1.06

    @staticmethod
    def exercise_factor(is_new: bool) -> float:
        # Deliberadamente so 2 niveis (decisao do tech lead: sem terceiro
        # nivel "novo+excentrico forte" - nao existe campo eccentric_stress
        # no schema hoje).
        return 1.05 if is_new else 1.00

    @staticmethod
    def recovery_floor_hours(participation: float = 1.0) -> float:
        """Piso de recuperacao proporcional a participacao do grupo na
        sessao (1.00 = trabalhado como primario em algum exercicio da
        sessao; valor menor = so trabalhado como secundario). Substitui o
        piso fixo de 18h que antes ignorava o papel do grupo, fazendo
        qualquer participacao secundaria parecer quase tao "cansada" quanto
        uma direta."""
        return MuscleRecoveryService.clamp(18.0 * float(participation), 0.0, 18.0)

    @staticmethod
    def calculate_recovery_hours(session_dose: float, participation: float = 1.0) -> float:
        dose = float(session_dose)
        overload = max(0.0, dose - 3.0)
        hours = 14.0 + 4.0 * dose + 1.2 * (overload ** 2)
        floor_hours = MuscleRecoveryService.recovery_floor_hours(participation)
        # TETO SEMPRE 72h, nunca mais - mesmo com 9+ series todas RPE 10.
        return MuscleRecoveryService.clamp(hours, floor_hours, RECOVERY_MAX_HOURS)

    @staticmethod
    def apply_personal_coefficient(
        hours: float, coefficient: float | None, floor_hours: float = 18.0
    ) -> float:
        coeff = MuscleRecoveryService.clamp(
            float(coefficient) if coefficient is not None else 1.00, 0.90, 1.10
        )
        return MuscleRecoveryService.clamp(float(hours) * coeff, floor_hours, RECOVERY_MAX_HOURS)

    @staticmethod
    def compute_global_readiness_factor(tsb: float | None) -> float:
        """Fator global de prontidao derivado do TSB (form) mais recente do
        Intervals.icu. TSB=0 (neutro) -> fator 1.0; TSB negativo (fadiga
        acumulada) -> fator > 1.0 (estica a recuperacao); TSB positivo
        (atleta fresco/tapered) -> fator < 1.0 (encurta a recuperacao).
        Calibrado com os dados reais do usuario: TSB medio -15 -> ~1.10x,
        pior caso observado -25.91 -> perto do teto 1.15. Sem dado do
        Intervals (`tsb is None`), o fator e neutro (1.0)."""
        if tsb is None:
            return 1.0
        return MuscleRecoveryService.clamp(1.0 - float(tsb) / 150.0, 0.90, 1.15)

    @staticmethod
    def apply_global_readiness_factor(
        hours: float, tsb: float | None, floor_hours: float = 18.0
    ) -> float:
        if tsb is None:
            return hours
        factor = MuscleRecoveryService.compute_global_readiness_factor(tsb)
        return MuscleRecoveryService.clamp(float(hours) * factor, floor_hours, RECOVERY_MAX_HOURS)

    @staticmethod
    def calculate_time_recovery(elapsed_hours: float, estimated_hours: float) -> float:
        if estimated_hours is None or estimated_hours <= 0:
            return 100.0
        return MuscleRecoveryService.clamp(
            (float(elapsed_hours) / float(estimated_hours)) * 100.0, 0.0, 100.0
        )

    @staticmethod
    def calculate_density_penalty(
        dose_last_72_hours: float, reference_dose: float = DENSITY_REFERENCE_DOSE
    ) -> float:
        if reference_dose <= 0:
            return 0.0
        ratio = float(dose_last_72_hours) / float(reference_dose)
        if ratio <= 1.10:
            return 0.0
        if ratio <= 1.25:
            return 10.0
        return 20.0

    @staticmethod
    def calculate_adjusted_readiness(time_recovery: float, density_penalty: float) -> float:
        return MuscleRecoveryService.clamp(float(time_recovery) - float(density_penalty), 0.0, 100.0)

    @staticmethod
    def count_negative_signals(
        performance_ratio: float | None,
        excessive_rep_drop: bool,
        muscle_pain: float | None,
    ) -> int:
        """Extraido de classify_readiness para poder ser reutilizado por
        reconcile_recovery_pct sem duplicar a definicao de "sinal
        negativo".

        RPE alto nao e sinal negativo (decisao do usuario, 2026-09-14): o
        antigo `rpe_anticipated` (RPE acima do alvo do plano) foi removido."""
        return sum(
            [
                performance_ratio is not None and performance_ratio < PERFORMANCE_DROP_SIGNAL_RATIO,
                bool(excessive_rep_drop),
                muscle_pain is not None and muscle_pain >= MUSCLE_PAIN_SIGNAL_MIN,
            ]
        )

    @staticmethod
    def classify_readiness(
        adjusted_readiness: float,
        performance_ratio: float | None,
        excessive_rep_drop: bool,
        muscle_pain: float | None,
        joint_pain: bool | None,
    ) -> str:
        if joint_pain:
            return "NO_DIRECT_WORK"
        if muscle_pain_blocks_direct_work(muscle_pain):
            return "NO_DIRECT_WORK"

        negative_signals = MuscleRecoveryService.count_negative_signals(
            performance_ratio, excessive_rep_drop, muscle_pain
        )

        if (
            adjusted_readiness < NO_DIRECT_WORK_BELOW_READINESS
            or (performance_ratio is not None and performance_ratio < PERFORMANCE_DROP_BLOCK_RATIO)
            or negative_signals >= NO_DIRECT_WORK_MIN_SIGNALS
        ):
            return "NO_DIRECT_WORK"
        if adjusted_readiness < READY_MIN_ADJUSTED_READINESS or negative_signals == 1:
            return "REDUCED_DOSE"
        return "READY"

    @staticmethod
    def reconcile_recovery_pct(
        adjusted_readiness: float,
        readiness_status: str,
        joint_pain: bool | None,
        muscle_pain: float | None,
        negative_signals: int,
        performance_ratio: float | None,
    ) -> tuple[float, float]:
        """Garante que o numero final exibido como recovery_pct nunca
        contradiga o readiness_status escolhido por classify_readiness.

        classify_readiness considera sinais de seguranca (dor, performance,
        queda de reps) que adjusted_readiness (so tempo
        decorrido x densidade de treino) ignora por completo. Isso permite
        que um grupo saia com adjusted_readiness=100 (tempo/densidade
        perfeitos) e readiness_status=NO_DIRECT_WORK (ex.: dor articular
        relatada apos recuperacao total de tempo) - sem reconciliacao a UI
        mostraria "100% recuperado" ao lado de "Evitar treino direto", o
        que e contraditorio para quem le a tela.

        Nao reclassifica nada (classify_readiness continua sendo a unica
        fonte de verdade do status); so garante que o NUMERO caiba dentro
        da faixa implicada pelo status ja decidido:
        - NO_DIRECT_WORK -> sempre < 55. Causas mais graves (dor articular
          ou dor muscular > 6) puxam bem para baixo (teto 20). Causas por
          combinacao de sinais (2+ sinais negativos ou performance < 0.90)
          ficam num teto mais alto porem ainda abaixo de 55 (teto 45). Se a
          unica causa foi adjusted_readiness < 55 por tempo/densidade, o
          valor ja esta correto e nao e alterado.
        - REDUCED_DOSE -> sempre em [55, 75). Cobre tanto o caso em que
          adjusted_readiness ja estava nessa faixa (nao muda) quanto o
          caso sutil em que adjusted_readiness >= 75 mas ha exatamente 1
          sinal negativo isolado (precisa ser puxado para baixo, teto 74).
        - READY -> classify_readiness so retorna READY quando
          adjusted_readiness >= 75 e nao ha nenhum sinal negativo; nada a
          fazer.

        Retorna (valor_final, penalidade_aplicada_alem_de_tempo/densidade).
        """
        if readiness_status == "NO_DIRECT_WORK":
            severe = bool(joint_pain) or muscle_pain_blocks_direct_work(muscle_pain)
            if severe:
                capped = min(adjusted_readiness, 20.0)
            elif negative_signals >= NO_DIRECT_WORK_MIN_SIGNALS or (
                performance_ratio is not None and performance_ratio < PERFORMANCE_DROP_BLOCK_RATIO
            ):
                capped = min(adjusted_readiness, 45.0)
            else:
                # Unico gatilho foi adjusted_readiness < 55 por tempo/
                # densidade - ja esta coerente, nao mexe.
                capped = adjusted_readiness
            # Rede de seguranca final: independente da causa, nunca >= 55
            # quando o status e NO_DIRECT_WORK.
            capped = min(capped, 54.0)
        elif readiness_status == "REDUCED_DOSE":
            capped = MuscleRecoveryService.clamp(adjusted_readiness, 55.0, 74.0)
        else:  # READY
            capped = adjusted_readiness

        penalty = round(max(0.0, float(adjusted_readiness) - float(capped)), 1)
        return capped, penalty

    @classmethod
    def recommendation_for(cls, readiness_status: str) -> dict[str, Any]:
        return dict(cls._RECOMMENDATIONS.get(readiness_status, cls._RECOMMENDATIONS["NO_DIRECT_WORK"]))

    # ------------------------------------------------------------------
    # Dose reduzida (M76 B2) - regra ÚNICA de séries por estado
    # ------------------------------------------------------------------

    @classmethod
    def volume_multiplier_for(cls, readiness_status: str | None) -> float:
        """Multiplicador de volume do estado (READY 1,0; REDUCED_DOSE 0,75;
        NO_DIRECT_WORK 0). Estado ausente = READY (grupo sem dose registrada)."""
        recommendation = cls._RECOMMENDATIONS.get(
            readiness_status or "READY", cls._RECOMMENDATIONS["NO_DIRECT_WORK"]
        )
        return float(recommendation["volume_multiplier"])

    @classmethod
    def reduced_sets(cls, sets_count: Any, readiness_status: str | None = "REDUCED_DOSE") -> int:
        """Séries de UM exercício no estado de prontidão do grupo:
        `ceil(S × volume_multiplier)`, mínimo `REDUCED_DOSE_MIN_SETS`
        (decisão 1 do usuário, 2026-10-06). READY devolve `S`; NO_DIRECT_WORK
        devolve 0 (o grupo não tem trabalho direto). `S` ausente ou <= 0
        devolve 0.

        Bordas da dose reduzida: 1->1, 2->2, 3->3, 4->3, 5->4, 6->5, 8->6."""
        try:
            sets = int(sets_count or 0)
        except (TypeError, ValueError):
            return 0
        if sets <= 0:
            return 0
        multiplier = cls.volume_multiplier_for(readiness_status)
        if multiplier >= 1.0:
            return sets
        if multiplier <= 0.0:
            return 0
        # `- 1e-9`: 4 × 0,75 = 3,0 não pode virar 4 por erro de ponto flutuante.
        return max(REDUCED_DOSE_MIN_SETS, int(math.ceil(sets * multiplier - 1e-9)))

    @classmethod
    def reduction_policy(cls) -> dict[str, Any]:
        """Política da dose reduzida, pronta para quem precisa EXPLICAR a regra
        (contrato da tela, prompts, auditoria da ficha)."""
        recommendation = cls._RECOMMENDATIONS["REDUCED_DOSE"]
        return {
            "status": "REDUCED_DOSE",
            "volume_multiplier": float(recommendation["volume_multiplier"]),
            "minimum_sets": int(recommendation["minimum_sets"]),
            "rounding": "ceil",
            "sets_rule": recommendation["sets_rule"],
            "maximum_rpe": int(recommendation["maximum_rpe"]),
            "allow_failure_on_last_set": bool(recommendation["allow_failure_on_last_set"]),
            "allow_progression": bool(recommendation["allow_progression"]),
        }

    @classmethod
    def reduced_dose_prompt_rule(cls) -> str:
        """Regra da dose reduzida em português para prompt de IA, interpolada da
        política (nenhum número escrito à mão no prompt)."""
        policy = cls.reduction_policy()
        parts = [f"séries de cada exercício = {policy['sets_rule']}", f"rpe_target máximo {policy['maximum_rpe']}"]
        if not policy["allow_failure_on_last_set"]:
            parts.append("sem falha na última série")
        if not policy["allow_progression"]:
            parts.append("sem sinalizar progressão de carga/reps")
        return ", ".join(parts)

    @classmethod
    def status_meta_for(cls, readiness_status: str) -> tuple[str, str]:
        return cls._STATUS_META.get(readiness_status, cls._STATUS_META["NO_DIRECT_WORK"])

    # ------------------------------------------------------------------
    # Contrato de prontidao (PR-09, plano §5.8) - campos ADITIVOS expostos
    # por grupo tanto em /analytics/muscle-recovery quanto na projecao
    # usada pela geracao de ficha (recovery_projection.py).
    # ------------------------------------------------------------------

    # Dor (articular/muscular) E sinais de desempenho (queda de performance,
    # queda excessiva de reps) persistem até uma sessão posterior do mesmo
    # grupo (o VolumeService já usa sempre a ÚLTIMA sessão); a projeção futura
    # nunca os neutraliza. M76 B3 (decisão 2 do usuário, 2026-10-06): antes a
    # projeção zerava os sinais de desempenho a partir de 72h à frente,
    # diferente do retrato do dashboard.
    SIGNALS_POLICY = "signals_persist_until_superseded"

    @staticmethod
    def build_blocking_signals(
        adjusted_readiness: float,
        performance_ratio: float | None,
        excessive_rep_drop: bool,
        muscle_pain: float | None,
        joint_pain: bool | None,
        source_session_at: str | None = None,
        rep_drop_details: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Lista os sinais que impedem READY, com os MESMOS limiares de
        classify_readiness/count_negative_signals (MUSCLE_PAIN_SIGNAL_MIN,
        PERFORMANCE_DROP_SIGNAL_RATIO, READY_MIN_ADJUSTED_READINESS). Lista
        vazia <=> nenhum sinal rebaixa o grupo.

        Lote 1/P2: EXCESSIVE_REP_DROP leva `exercises` (exercicio e reps
        da 1a e da ultima serie, `VolumeService.rep_drop_details`) - insumo do
        banner "queda de repeticoes no <exercicio> em <data>" (Lote 6)."""
        signals: list[dict[str, Any]] = []

        def _add(code: str, value: Any, **extra: Any) -> None:
            signals.append({"code": code, "value": value, "source_session_at": source_session_at, **extra})

        if joint_pain:
            _add("JOINT_PAIN", True)
        if muscle_pain is not None and float(muscle_pain) >= MUSCLE_PAIN_SIGNAL_MIN:
            _add("MUSCLE_PAIN", muscle_pain)
        if performance_ratio is not None and float(performance_ratio) < PERFORMANCE_DROP_SIGNAL_RATIO:
            _add("PERFORMANCE_DROP", round(float(performance_ratio), 3))
        # RPE_ANTICIPATED nunca e emitido (removido em 2026-09-14).
        if excessive_rep_drop:
            _add("EXCESSIVE_REP_DROP", True, exercises=[dict(item) for item in rep_drop_details or []])
        if float(adjusted_readiness) < READY_MIN_ADJUSTED_READINESS:
            _add("LOW_READINESS", round(float(adjusted_readiness), 1))
        return signals

    @classmethod
    def build_readiness_contract(
        cls,
        readiness_status: str,
        adjusted_readiness: float,
        performance_ratio: float | None,
        excessive_rep_drop: bool,
        muscle_pain: float | None,
        joint_pain: bool | None,
        source_session_at: str | None = None,
        rep_drop_details: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Campos planos do contrato derivados do status JA decidido por
        classify_readiness (fonte unica de verdade) + sinais bloqueantes."""
        recommendation = cls.recommendation_for(readiness_status)
        return {
            "max_rpe": recommendation["maximum_rpe"],
            "allow_failure": bool(recommendation["allow_failure_on_last_set"]),
            "volume_multiplier": float(recommendation["volume_multiplier"]),
            "blocking_signals": cls.build_blocking_signals(
                adjusted_readiness,
                performance_ratio,
                excessive_rep_drop,
                muscle_pain,
                joint_pain,
                source_session_at,
                rep_drop_details,
            ),
            "signals_policy": cls.SIGNALS_POLICY,
        }

    # ------------------------------------------------------------------
    # Orquestracao (le dados agregados por sessao/grupo do VolumeService)
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self.volume_service = VolumeService()
        # TSB global (`intervals_wellness`, linha mais recente), cache de
        # instancia (vive uma requisicao). Chave = o `user_id` recebido
        # (inclusive `None`, que tem semantica propria - ver `_get_global_tsb`).
        self._global_tsb_by_user: dict[int | None, tuple[float | None, dict[str, Any] | None]] = {}
        # M52: o wellness lido estava velho demais (> WELLNESS_MAX_AGE_DAYS)?
        # Mesmo memo/chave do TSB acima; quem expoe o ajuste le daqui.
        self._global_wellness_stale_by_user: dict[int | None, bool] = {}

    def _get_personal_coefficient(self, muscle_group_id: int | None, user_id: int | None = None) -> float:
        """Coeficiente pessoal de recuperacao: NEUTRO (1,00) para todos.

        Ate o M76 Lote 2c este metodo lia `muscle_recovery_coefficients`, uma
        tabela SEM ESCRITOR (nenhum codigo calibra ou grava coeficiente; 0
        linhas em producao em 2026-10-07) - a leitura so custava uma ida ao
        banco para devolver 1,00 sempre. A tabela continua no schema (o drop e
        migracao destrutiva, pendencia registrada em
        `docs/motores/FONTES_DA_VERDADE.md`, B12); se um dia houver
        calibracao, ela volta a ser lida aqui, num lugar so."""
        return NEUTRAL_PERSONAL_COEFFICIENT

    def _get_global_tsb(self, user_id: int | None = None) -> tuple[float | None, dict[str, Any] | None]:
        """Busca o TSB (form) mais recente sincronizado do Intervals.icu,
        para uso como sinal global de fadiga na recuperacao muscular. Uma
        falha na integracao (tabela ausente, erro de conexao, etc.) NUNCA
        deve quebrar o calculo de recuperacao - nesse caso o fator vira
        neutro (tsb=None).

        `user_id` opcional (Multiusuario Parte 2): ver docstring de
        `IntelligenceServiceBase.get_latest_intervals_wellness` - quando
        None, mantem o comportamento anterior (sem filtro por usuario).

        Memo de INSTANCIA (M33), mesmo ciclo de vida de requisicao do
        `VolumeService`: `get_recovery_overview` ja le esta linha, e
        quem tambem quer o ajuste global exposto (`recovery_signals_service`,
        via `get_global_readiness_adjustment`) passou a poder pedi-lo na
        MESMA instancia sem uma segunda ida ao banco. O numero e o mesmo -
        so a repeticao da leitura sumiu.

        M52 - idade maxima: wellness com mais de
        `wellness_freshness.WELLNESS_MAX_AGE_DAYS` dias (contados no fuso do
        usuario) devolve `tsb=None` - fator NEUTRO, exatamente como "sem
        Intervals.icu" - e a linha continua sendo devolvida para a tela
        poder dizer de quando e o ultimo dado. Com dado fresco nada muda: o
        TSB e o mesmo de antes, entao o numero tambem."""
        if user_id in self._global_tsb_by_user:
            return self._global_tsb_by_user[user_id]
        try:
            wellness = self.get_latest_intervals_wellness(user_id=user_id)
            stale = wellness_freshness.is_row_stale(wellness)
        except Exception:
            logger.exception("Falha ao buscar intervals_wellness para o fator global de recuperacao")
            # Falha NAO e memorizada: um erro transitorio de integracao nao
            # pode congelar "sem TSB" pelo resto da requisicao (M18).
            return None, None
        tsb = wellness.get("form_tsb") if (wellness and not stale) else None
        resultado = ((float(tsb) if tsb is not None else None), wellness)
        self._global_tsb_by_user[user_id] = resultado
        self._global_wellness_stale_by_user[user_id] = stale
        return resultado

    def get_global_readiness_adjustment(self, user_id: int | None = None) -> dict[str, Any] | None:
        """Payload de transparencia (nivel raiz da resposta da API) do
        ajuste global aplicado por `apply_global_readiness_factor`. `None`
        quando nao ha dado do Intervals.icu sincronizado.

        M52: wellness velho NAO e `None` - a tela precisa avisar que existe
        dado, mas antigo demais para ser aplicado. Vem com `stale=True`,
        `factor=1.0` (o fator que de fato foi aplicado: neutro), o TSB
        ignorado em `form_tsb` e a data do ultimo wellness."""
        tsb, wellness = self._get_global_tsb(user_id=user_id)
        if not wellness:
            return None
        max_age_days = wellness_freshness.WELLNESS_MAX_AGE_DAYS
        if self._global_wellness_stale_by_user.get(user_id, False):
            ignored_tsb = wellness.get("form_tsb")
            return {
                "form_tsb": round(float(ignored_tsb), 2) if ignored_tsb is not None else None,
                "factor": 1.0,
                "wellness_date": wellness.get("wellness_date"),
                "stale": True,
                "max_age_days": max_age_days,
            }
        if tsb is None:
            return None
        return {
            "form_tsb": round(tsb, 2),
            "factor": round(self.compute_global_readiness_factor(tsb), 3),
            "wellness_date": wellness.get("wellness_date"),
            "stale": False,
            "max_age_days": max_age_days,
        }

    def _overview_datasets(
        self, days: int, user_id: int, history_days: int | None
    ) -> dict[str, Any] | None:
        """Insumos do overview lidos de forma agregada (M20).

        Devolve `None` - e o overview volta a ler serie a serie, com os
        MESMOS numeros - em dois casos: o overview ja esta no cache de
        instancia do `VolumeService` (nao ha o que ler) ou a leitura agregada
        falhou. Falhar para o caminho antigo e deliberado: a recuperacao
        alimenta a geracao de ficha e o Modo Treino, entao ela nunca pode
        deixar de responder por causa de uma otimizacao de leitura."""
        cache = getattr(self.volume_service, "_overview_cache", None)
        if cache is None:
            # `volume_service` substituido por um dublê (testes): nada a
            # otimizar, e o dublê nao sabe o que fazer com `datasets`.
            return None
        if cache.get((days, user_id, history_days)) is not None:
            return None
        from .recovery_read_model import fetch_overview_datasets

        try:
            datasets = fetch_overview_datasets(self.volume_service, days, user_id, history_days)
        except Exception:  # noqa: BLE001 - ver docstring
            logger.exception("Leitura agregada da recuperacao falhou; usando a leitura antiga")
            return None

        return datasets

    def user_now(self, user_id: int | None) -> datetime:
        """"Agora" na parede do usuario (naive, `users.timezone`) - a MESMA
        referencia com que `get_recovery_overview` mede `hours_since_trained`.
        Toda projecao de recuperacao para o instante do treino
        (`plan_date`/`plan_time`, tambem hora local) tem de partir daqui, nunca
        de `datetime.now()`: na Lambda o relogio do processo e UTC e a
        projecao perdia o deslocamento do fuso (ficha 131: 8h de antecedencia
        em vez de 11h)."""
        return self.volume_service._user_now(user_id)

    def get_recovery_overview(
        self,
        days: int | None = None,
        limit: int | None = None,
        user_id: int | None = None,
        overview_rows: list[dict[str, Any]] | None = None,
        history_days: int | None = None,
        aggregated_read: bool = True,
        now_local: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """`now_local` (opcional): o "agora" na parede do usuario (naive) contra
        o qual `hours_since_trained` e medido - repassado a
        `VolumeService.get_muscle_group_overview`. Quem vai PROJETAR o retrato
        (congelamento da ficha) obtem esse instante uma vez com `user_now` e o
        usa nos dois lados, para retrato e projecao medirem a partir do mesmo
        relogio. Omitido, o overview resolve sozinho (comportamento anterior).

        `aggregated_read` (M20, default `True`): le os insumos do overview
        com o modelo agregado (`recovery_read_model`, 3 statements) em vez das
        ~16 leituras de serie do caminho antigo. O MODELO nao muda - so a
        origem dos dados -, e a equivalencia do payload e provada campo a
        campo em `tests/integration/dataapi/
        test_dashboard_drilldown_performance.py`.
        `False` volta ao caminho antigo (usado pelo teste de equivalencia);
        sem `user_id` o caminho antigo e o unico possivel, porque a leitura
        agregada exige identidade (`require_user_id`).

        `history_days` (opcional): limita o historico lido, com a mesma
        semantica de `VolumeService._fetch_full_history_sets`.

        `overview_rows` (opcional): as linhas que
        `VolumeService.get_muscle_group_overview(days=days, limit=limit,
        user_id=user_id)` ja devolveu ao chamador. Quando informadas, a
        readiness e calculada em cima delas em vez de recomputar o MESMO
        overview aqui dentro - era isso que fazia
        `GET /analytics/dashboard-muscle-drilldown` ler o historico inteiro do
        usuario DUAS vezes por requisicao (uma direta e outra por aqui) e
        estourar o timeout de 29s do API Gateway em producao. Nenhum numero
        muda: este metodo so consome as linhas, nunca as altera. Omitir o
        parametro preserva exatamente o comportamento anterior para todos os
        demais chamadores."""
        # Fix (continuacao Fase 1 do dashboard novo, ver
        # dashboard-muscle-drilldown em analytics.py): `user_id` ja era
        # aceito e propagado para `_get_global_tsb`/`_get_personal_coefficient`
        # abaixo, mas NAO para `get_muscle_group_overview` - o "readiness" de
        # um grupo muscular era calculado sobre sets_count/session_dose de
        # TODOS os usuarios quando qualquer chamador propagava identidade,
        # vazando dado de outro usuario no calculo (nao no JSON exposto, mas
        # no numero calculado). Callers que ja passam user_id (dashboard_modules.py,
        # cardio_context_service.py) agora ficam corretos "de graca"; callers que
        # ainda nao propagam identidade (dashboard_aggregator_service.py,
        # lacuna documentada e fora do escopo desta correcao) continuam
        # exatamente iguais, pois None preserva o comportamento antigo sem filtro.
        # M76 B3: sem `days`/`limit`, a janela única do modelo - resolvida aqui
        # (não no default da assinatura) para valer o valor vigente do módulo.
        days = RECOVERY_LOOKBACK_DAYS if days is None else days
        limit = RECOVERY_GROUP_LIMIT if limit is None else limit
        if overview_rows is not None:
            rows = overview_rows
        else:
            rows = self.volume_service.get_muscle_group_overview(
                days=days,
                limit=limit,
                user_id=user_id,
                history_days=history_days,
                datasets=self._overview_datasets(days, user_id, history_days)
                if (aggregated_read and user_id is not None)
                else None,
                now_local=now_local,
            )
        items: list[dict[str, Any]] = []
        tsb, _wellness = self._get_global_tsb(user_id=user_id)

        for row in rows:
            session_dose = float(row.get("session_dose") or 0.0)
            dose_last_72h = float(row.get("dose_last_72h") or 0.0)
            has_session = session_dose > 0.0

            participation = float(row.get("session_participation") or 1.0)
            floor_hours = self.recovery_floor_hours(participation)

            coefficient = self._get_personal_coefficient(row.get("muscle_group_id"), user_id=user_id)
            base_hours = self.calculate_recovery_hours(session_dose, participation) if has_session else 0.0
            personal_hours = (
                self.apply_personal_coefficient(base_hours, coefficient, floor_hours) if has_session else 0.0
            )
            estimated_hours = (
                self.apply_global_readiness_factor(personal_hours, tsb, floor_hours) if has_session else 0.0
            )

            if row.get("hours_since_trained") is not None:
                elapsed_hours = float(row["hours_since_trained"])
            else:
                elapsed_hours = max(int(row.get("days_since_trained") or 0), 0) * 24.0

            performance_ratio = row.get("performance_ratio")
            excessive_rep_drop = bool(row.get("excessive_rep_drop"))
            muscle_pain = row.get("muscle_pain")
            joint_pain = row.get("joint_pain")

            if has_session:
                time_recovery = self.calculate_time_recovery(elapsed_hours, estimated_hours)
                density_penalty = self.calculate_density_penalty(dose_last_72h)
                adjusted_readiness = self.calculate_adjusted_readiness(time_recovery, density_penalty)
                readiness_status = self.classify_readiness(
                    adjusted_readiness,
                    performance_ratio,
                    excessive_rep_drop,
                    muscle_pain,
                    joint_pain,
                )
                negative_signals = self.count_negative_signals(
                    performance_ratio, excessive_rep_drop, muscle_pain
                )
                final_readiness, signal_penalty = self.reconcile_recovery_pct(
                    adjusted_readiness,
                    readiness_status,
                    joint_pain,
                    muscle_pain,
                    negative_signals,
                    performance_ratio,
                )
                remaining_hours = round(max(estimated_hours - elapsed_hours, 0.0), 2)
            else:
                # Grupo sem sessao registrada na janela analisada: tratado
                # como totalmente pronto (nao ha dose para se recuperar de).
                time_recovery = 100.0
                density_penalty = 0.0
                adjusted_readiness = 100.0
                readiness_status = "READY"
                final_readiness = 100.0
                signal_penalty = 0.0
                remaining_hours = 0.0

            status, guidance = self.status_meta_for(readiness_status)
            recommendation = self.recommendation_for(readiness_status)
            # Contrato aditivo (PR-09). Sem sessao o status e forcado READY,
            # entao nenhum sinal e considerado bloqueante.
            contract = self.build_readiness_contract(
                readiness_status,
                adjusted_readiness,
                performance_ratio if has_session else None,
                excessive_rep_drop if has_session else False,
                muscle_pain if has_session else None,
                joint_pain if has_session else False,
                row.get("last_session_at") or row.get("last_trained_at_ts"),
                row.get("rep_drop_details") if has_session else None,
            )

            explanation: list[str] = []
            if has_session:
                explanation.append(f"A última sessão gerou dose {session_dose:.2f}.")
                explanation.append(
                    f"Já decorreram {elapsed_hours:.1f} horas (estimativa de recuperação: {estimated_hours:.1f}h)."
                )
                if density_penalty > 0:
                    explanation.append(
                        f"Densidade de treino nas últimas 72h penaliza a prontidão em {density_penalty:.0f} pontos."
                    )
                if performance_ratio is not None and performance_ratio < PERFORMANCE_DROP_SIGNAL_RATIO:
                    explanation.append(
                        f"Performance recente abaixo do esperado (razão {performance_ratio:.2f})."
                    )
                if excessive_rep_drop:
                    explanation.append("Queda expressiva de repetições entre séries na última sessão.")
                if muscle_pain is not None and muscle_pain > 0:
                    explanation.append(f"Dor muscular relatada: {muscle_pain}/10.")
                if joint_pain:
                    explanation.append("Dor articular relatada — grupo bloqueado para trabalho direto.")
            else:
                explanation.append("Nenhuma sessão recente registrada para este grupo na janela analisada.")

            item = {**row}
            item.update(
                {
                    "recovery_pct": round(final_readiness, 1),
                    "estimated_recovery_hours": round(estimated_hours, 1),
                    "remaining_hours": remaining_hours,
                    "status": status,
                    "guidance": guidance,
                    "readiness_status": readiness_status,
                    "session_dose": round(session_dose, 3),
                    "dose_last_72h": round(dose_last_72h, 3),
                    "density_penalty": density_penalty,
                    "signal_penalty": signal_penalty,
                    "performance_ratio": round(performance_ratio, 3) if performance_ratio is not None else None,
                    "recommendation": recommendation,
                    "explanation": explanation,
                    **contract,
                    # Campos NOVOS (Fase 2 da reforma de volume), repassados
                    # de VolumeService.get_muscle_group_overview: distinguem
                    # "ultima vez treinado como PRIMARY" de "ultima vez com
                    # qualquer participacao (inclusive SECONDARY/STABILIZER)".
                    # Disponibilizados no payload para uso futuro pela UI;
                    # elapsed_hours/readiness_status acima CONTINUAM usando
                    # hours_since_trained/days_since_trained como ja
                    # calculavam antes desta mudanca - a distincao do
                    # "relogio" de recuperacao ainda nao foi aplicada ao
                    # calculo, fica para uma iteracao seguinte (decisao
                    # deliberadamente conservadora, ver prompt da Fase 2).
                    "last_direct_training_at": row.get("last_direct_training_at"),
                    "last_participation_at": row.get("last_participation_at"),
                }
            )
            items.append(item)

        return items

    def get_consecutive_day_recovery_checks(self, days: int = 7, user_id: int | None = None) -> list[dict[str, Any]]:
        """Avalia, para cada grupo muscular, todo PAR de dias-calendario
        consecutivos (D1 imediatamente antes de D2) dentro da janela de
        `days` dias em que o grupo foi treinado nos dois dias, usando a
        mesma logica de recuperacao (dose -> horas, coeficiente pessoal,
        fator global de TSB) ja calibrada em `get_recovery_overview` -
        em vez do booleano simples "ativado no dia" usado anteriormente
        pela tela /treinos (que marcava qualquer sequencia de dias
        calendario como problema, mesmo quando o intervalo real ja era
        suficiente para o grupo).

        Sem TSB historico point-in-time disponivel no sistema, usa sempre o
        TSB mais recente (mesmo padrao ja usado em outros calculos
        retroativos, ver `_get_global_tsb`).
        """
        rows = self.volume_service.get_muscle_group_overview(days=days, limit=RECOVERY_GROUP_LIMIT, user_id=user_id)
        tsb, _wellness = self._get_global_tsb(user_id=user_id)
        # Data LOCAL do usuario: `local_date` do historico e data civil local.
        window_start = self.volume_service._user_now(user_id).date() - timedelta(days=days)

        results: list[dict[str, Any]] = []

        for row in rows:
            muscle_group_id = row.get("muscle_group_id")
            muscle_group = row.get("muscle_group")
            history = row.get("activity_dose_history") or []
            if not history:
                continue

            coefficient = self._get_personal_coefficient(muscle_group_id, user_id=user_id)

            by_day: dict[date, dict[str, Any]] = {}
            for entry in history:
                local_date_raw = entry.get("local_date")
                if not local_date_raw:
                    continue
                try:
                    entry_date = date.fromisoformat(str(local_date_raw)[:10])
                except ValueError:
                    continue
                if entry_date < window_start:
                    continue

                trained_at: datetime | None = None
                if entry.get("trained_at"):
                    try:
                        trained_at = datetime.fromisoformat(str(entry["trained_at"]))
                    except ValueError:
                        trained_at = None

                # M76 B7: a dose que vale é a da SESSÃO mais recente do dia
                # (a mesma unidade do resumo, `get_recovery_overview`), não a
                # soma do dia. O histórico vem em ordem cronológica; sem
                # horário, a última entrada do dia é a mais recente.
                day_bucket = by_day.setdefault(
                    entry_date, {"dose": 0.0, "participation": 1.0, "first_ts": None, "last_ts": None}
                )
                is_latest = trained_at is None or day_bucket["last_ts"] is None or trained_at >= day_bucket["last_ts"]
                if is_latest:
                    day_bucket["dose"] = float(entry.get("dose") or 0.0)
                    day_bucket["participation"] = float(entry.get("participation") or 1.0)
                if trained_at is not None:
                    if day_bucket["first_ts"] is None or trained_at < day_bucket["first_ts"]:
                        day_bucket["first_ts"] = trained_at
                    if day_bucket["last_ts"] is None or trained_at > day_bucket["last_ts"]:
                        day_bucket["last_ts"] = trained_at

            sorted_days = sorted(by_day.keys())
            for day_from, day_to in zip(sorted_days, sorted_days[1:]):
                if (day_to - day_from).days != 1:
                    # Nao sao dias-calendario consecutivos: nao gera par.
                    continue

                bucket_from = by_day[day_from]
                bucket_to = by_day[day_to]
                dose_from = bucket_from["dose"]

                end_of_day_from = bucket_from["last_ts"]
                start_of_day_to = bucket_to["first_ts"]
                if end_of_day_from is not None and start_of_day_to is not None:
                    elapsed_hours = max((start_of_day_to - end_of_day_from).total_seconds() / 3600.0, 0.0)
                    timestamp_precision = "exact"
                else:
                    # Fallback conservador: sem timestamp exato, assume
                    # meio-dia de cada local_date.
                    elapsed_hours = 24.0 * (day_to - day_from).days
                    timestamp_precision = "day_fallback"

                participation_from = bucket_from.get("participation", 1.0)
                floor_hours = self.recovery_floor_hours(participation_from)
                base_hours = self.calculate_recovery_hours(dose_from, participation_from)
                personal_hours = self.apply_personal_coefficient(base_hours, coefficient, floor_hours)
                required_hours = self.apply_global_readiness_factor(personal_hours, tsb, floor_hours)

                results.append(
                    {
                        "muscle_group_id": muscle_group_id,
                        "muscle_group": muscle_group,
                        "date_from": day_from.isoformat(),
                        "date_to": day_to.isoformat(),
                        "elapsed_hours": round(elapsed_hours, 2),
                        "required_hours": round(required_hours, 2),
                        "insufficient_recovery": elapsed_hours < required_hours,
                        "timestamp_precision": timestamp_precision,
                    }
                )

        return results
