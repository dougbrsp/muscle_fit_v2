"""Entrada da medida de uma série gravada (Lote T3 das medidas de série,
2026-10-20): a ponte entre os payloads (`ExerciseSetIn`, `SessionSetIn`,
edição em `PUT /exercises/logs/{id}`) e o dono `domain/set_measures.py`.

Nada aqui é regra: a forma válida de cada medida é `validate_set_measure`; o
valor gravado é `normalize_measure`. Medida AUSENTE = `reps` (o relógio antigo
e os importadores só mandam reps) e NUNCA é deduzida do item da ficha.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .domain.set_measures import normalize_measure, validate_set_measure

SET_MEASURE_ERROR_PREFIX = "set_measure_invalid"


class SetMeasureInputError(ValueError):
    """Série com forma inválida para a medida (vira 422). `codes` são os
    códigos estáveis do dono (`SET_VALIDATION_CODES`)."""

    def __init__(self, codes: list[str], set_number: Any = None) -> None:
        self.codes = list(codes)
        self.set_number = set_number
        where = f" (série {set_number})" if set_number is not None else ""
        super().__init__(f"{SET_MEASURE_ERROR_PREFIX}{where}: {', '.join(self.codes)}")

    def detail(self) -> dict[str, Any]:
        """Corpo do 422 das rotas que validam fora do Pydantic."""
        return {"message": str(self), "codes": self.codes, "set_number": self.set_number}


def ensure_valid_set_measure(measure: Any, reps: Any, duration_seconds: Any, *, set_number: Any = None) -> None:
    """Levanta `SetMeasureInputError` quando a série não tem a forma da medida."""
    codes = validate_set_measure(measure, reps, duration_seconds)
    if codes:
        raise SetMeasureInputError(codes, set_number)


def set_measure_columns(values: Mapping[str, Any]) -> tuple[str, Optional[int]]:
    """`(measure, duration_seconds)` a gravar em `exercise_sets` a partir de
    uma série JÁ validada: medida ausente vira `reps`."""
    duration = values.get("duration_seconds")
    return normalize_measure(values.get("measure")).value, (None if duration is None else int(duration))


__all__ = [
    "SET_MEASURE_ERROR_PREFIX",
    "SetMeasureInputError",
    "ensure_valid_set_measure",
    "set_measure_columns",
]
