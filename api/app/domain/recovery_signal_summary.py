"""Resumo dos sinais de recuperação que cortaram o teto do dia, congelado na
ficha (RPE 10 Lote 6/P9, "o banner diz o sinal", 2026-10-11).

Os sinais vêm prontos do dono da prontidão
(`MuscleRecoveryService.build_blocking_signals`, via
`ReadinessContract.blocking_signals`): código, sessão de origem e, na queda de
repetições, o exercício e as reps da 1ª e da última série de trabalho (Lote 1).
Aqui só mora o TEXTO que o usuário lê para cada código e o formato compacto
que vai para o snapshot (`prescription_json.recovery_signals`). Nenhum limiar
é decidido aqui: um sinal só existe porque o dono o emitiu.

O frontend só formata a data (`source_session_date`, `AAAA-MM-DD`) e junta os
textos no banner ("Teto 9 hoje pela recuperação: queda de repetições no
exercício Abdominal crunch (12 → 8 reps) em 03/10").

Python puro, sem I/O.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Final, Iterable, Mapping, Optional

# Chave do resumo na evidencia da decisao e no snapshot da ficha.
RECOVERY_SIGNALS_KEY = "recovery_signals"

SIGNAL_JOINT_PAIN = "JOINT_PAIN"
SIGNAL_MUSCLE_PAIN = "MUSCLE_PAIN"
SIGNAL_PERFORMANCE_DROP = "PERFORMANCE_DROP"
SIGNAL_EXCESSIVE_REP_DROP = "EXCESSIVE_REP_DROP"
SIGNAL_LOW_READINESS = "LOW_READINESS"

RECOVERY_SIGNAL_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        SIGNAL_JOINT_PAIN: "dor articular",
        SIGNAL_MUSCLE_PAIN: "dor muscular",
        SIGNAL_PERFORMANCE_DROP: "desempenho abaixo do esperado",
        SIGNAL_EXCESSIVE_REP_DROP: "queda de repetições",
        SIGNAL_LOW_READINESS: "prontidão baixa",
    }
)


def _session_date(value: Any) -> Optional[str]:
    """`2026-10-03 18:22:00` / ISO -> `2026-10-03` (data de parede gravada)."""
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" else None


def _exercise_part(item: Mapping[str, Any]) -> Optional[str]:
    name = str(item.get("exercise_name") or "").strip()
    if not name:
        return None
    first, last = item.get("first_reps"), item.get("last_reps")
    if first is not None and last is not None:
        return f"{name} ({first} → {last} reps)"
    return name


def signal_text(signal: Mapping[str, Any]) -> Optional[str]:
    """Texto de UM sinal, sem a data. `None` para código desconhecido."""
    code = str(signal.get("code") or "").upper()
    label = RECOVERY_SIGNAL_LABELS.get(code)
    if label is None:
        return None
    exercises = [part for part in (_exercise_part(item) for item in signal.get("exercises") or []) if part]
    if exercises:
        noun = "no exercício" if len(exercises) == 1 else "nos exercícios"
        return f"{label} {noun} {', '.join(exercises)}"
    return label


def summarize_signals(signals: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Sinais do contrato de prontidão -> lista compacta do snapshot:
    `{code, text, source_session_date}` na ordem do dono. Códigos sem rótulo
    (nunca emitidos hoje) ficam de fora."""
    out: list[dict[str, Any]] = []
    for signal in signals or ():
        text = signal_text(signal)
        if text is None:
            continue
        out.append(
            {
                "code": str(signal.get("code")).upper(),
                "text": text,
                "source_session_date": _session_date(signal.get("source_session_at")),
            }
        )
    return out
