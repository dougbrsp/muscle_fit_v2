"""Política do agente de cardio (IA-06 c, decisão do usuário 2026-10-08).

Dono ÚNICO dos números de domínio da recomendação de cardio. Antes eles
estavam escritos à mão no `SYSTEM_PROMPT` de `cardio_agent_ai.py`; o prompt
agora só interpola estas constantes (mesmo texto renderizado, byte a byte).

Mudou um limiar, uma duração ou o protocolo? Mude AQUI, suba o
`PROMPT_VERSION` do cardio e regenere o snapshot (`docs/ia/EVALS.md`).
Mapa: `docs/motores/FONTES_DA_VERDADE.md` (§3.4). Guarda:
`api/tests/unit/test_guarda_fontes_da_verdade.py` (`numeros_prompt_cardio`).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

# PAI (Amazfit): manter acima deste valor (prioridade 4 do agente). O PAI é
# acumulado de 7 dias (como no relógio e no estimado de `cardio_load`, M99).
PAI_TARGET_MIN: Final[int] = 75

# Escada de intervenção em Z2, em minutos, da menor para a maior. A ordem de
# preferência do agente é: sem cardio -> cada degrau desta escada -> HIIT.
# O primeiro e o último degrau também delimitam a recuperação ativa.
Z2_DURATION_LADDER_MINUTES: Final[tuple[int, ...]] = (20, 30, 40, 45)

# Faixa de Z2 sugerida quando aptidão/CTL/Ramp Rate caem (PASSO 3).
Z2_TREND_CORRECTION_MINUTES: Final[tuple[int, int]] = (20, 40)

# Progressão do volume do contínuo: passo, em minutos, entre uma sessão e a
# seguinte (M98 C3a; o RAG `CARDIO.md` interpola; aprovado pelo usuário em 27/10/2026).
CONTINUOUS_PROGRESSION_STEP_MINUTES: Final[int] = 5

# Zona alvo da Z2 em % do VO2max (quando não há Zona 2 personalizada).
Z2_VO2MAX_PERCENT_RANGE: Final[tuple[int, int]] = (60, 70)

# Protocolo preferencial de HIIT: N blocos de [trabalho / recuperação], em minutos.
HIIT_PREFERRED_ROUNDS: Final[int] = 5
HIIT_WORK_MINUTES: Final[int] = 2
HIIT_RECOVERY_MINUTES: Final[int] = 2

# Memória adaptativa: ocorrências semelhantes com resultado positivo antes de
# aumentar o peso de uma estratégia nas decisões futuras.
ADAPTIVE_MIN_OCCURRENCES: Final[int] = 3

# Exemplo de evidência observada citado no prompt (ilustrativo, não é meta):
# N minutos de Z2 no dia de recuperação mantendo Aptidão, PAI e Ramp Rate.
EXAMPLE_EVIDENCE_Z2_MINUTES: Final[int] = 40
EXAMPLE_EVIDENCE_FITNESS: Final[int] = 16
EXAMPLE_EVIDENCE_PAI_MIN: Final[int] = 80
EXAMPLE_EVIDENCE_RAMP_RATE_MIN: Final[Decimal] = Decimal("1.5")


def format_decimal_br(value: Decimal | float | int) -> str:
    """Número com vírgula decimal (pt-BR), sem zeros à direita: 1.5 -> "1,5"."""
    texto = format(Decimal(str(value)).normalize(), "f")
    return texto.replace(".", ",")


def range_text(bounds: tuple[int, int], sep: str = "-") -> str:
    """Faixa `(a, b)` como texto ("20-40")."""
    return f"{bounds[0]}{sep}{bounds[1]}"


def active_recovery_minutes() -> tuple[int, int]:
    """Faixa da recuperação ativa em Z2: do menor ao maior degrau da escada."""
    return (Z2_DURATION_LADDER_MINUTES[0], Z2_DURATION_LADDER_MINUTES[-1])


def preference_ladder_text() -> str:
    """Ordem de preferência numerada: sem cardio, cada degrau de Z2, HIIT."""
    passos = ["Sem cardio", *(f"{m} minutos Z2" for m in Z2_DURATION_LADDER_MINUTES), "HIIT"]
    return "; ".join(f"{i}. {passo}" for i, passo in enumerate(passos, start=1))


def hiit_protocol_text() -> str:
    """Protocolo preferencial de HIIT: "5x [2 minutos intensidade alta / 2 minutos recuperação]"."""
    return (
        f"{HIIT_PREFERRED_ROUNDS}x [{HIIT_WORK_MINUTES} minutos intensidade alta / "
        f"{HIIT_RECOVERY_MINUTES} minutos recuperação]"
    )
