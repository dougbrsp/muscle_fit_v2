"""Exercício laboral (M100, 2026-10-27): dono do PADRÃO laboral do catálogo.

Pedido do usuário (27/10): exercício de aquecimento/alongamento (ex.: manguito
rotador) sem progressão. No Modo Treino ele só mostra séries, reps e carga e
oferece "Concluir" ou "Pular", sem digitar nada.

Decisões do usuário (27/10):

- O laboral conta **só no tempo de treino**. Fica fora de progressão,
  recorde/e1RM, volume, ativação, recuperação e insights. Isso é garantido pelo
  `set_type='laboral'` PREPARATÓRIO de `app/set_types.py` (todo agregado já
  passa por `sql_not_preparatory`/`is_working_set`) e, no planejado, por
  `is_laboral_item` (ativação planejada do Modelo/ficha).
- Séries, reps e carga do laboral vêm de um **padrão no catálogo**
  (`exercise_catalog.laboral_default_sets/reps/load_kg`, migração 099). Este
  módulo é o ÚNICO lugar que resolve o padrão: o congelamento da ficha, o
  rascunho da geração e o admin do catálogo só consomem.

Resolução (`laboral_prescription`): padrão do catálogo > o que a linha da ficha
/ item do Modelo já tem > fallback nomeado abaixo. Carga ausente = sem carga
(peso do corpo/elástico), nunca inventada.

Python puro: nenhum acesso a banco aqui (a leitura do catálogo é de
`app/laboral_catalog.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Final, Mapping, Optional

# Fallback quando nem o catálogo nem a linha dizem quantas séries/reps.
LABORAL_FALLBACK_SETS: Final[int] = 2
LABORAL_FALLBACK_REPS: Final[int] = 15

# Limites de validação do padrão no admin do catálogo (400 fora deles).
LABORAL_MAX_SETS: Final[int] = 10
LABORAL_MAX_REPS: Final[int] = 100
LABORAL_MAX_LOAD_KG: Final[Decimal] = Decimal("200")

# Campos do catálogo (migração 099).
FLAG_FIELD: Final[str] = "is_laboral"
DEFAULT_SETS_FIELD: Final[str] = "laboral_default_sets"
DEFAULT_REPS_FIELD: Final[str] = "laboral_default_reps"
DEFAULT_LOAD_FIELD: Final[str] = "laboral_default_load_kg"
CATALOG_FIELDS: Final[tuple[str, ...]] = (FLAG_FIELD, DEFAULT_SETS_FIELD, DEFAULT_REPS_FIELD, DEFAULT_LOAD_FIELD)



def sql_catalog_columns(alias: str = "ec") -> str:
    """Colunas do laboral para o SELECT de quem lê o catálogo (`ec.is_laboral AS
    is_laboral, ...`): o nome das colunas fica só aqui. `alias` é identificador
    fixo do código."""
    if not alias.isidentifier():
        raise ValueError(f"alias SQL invalido: {alias!r}")
    return ", ".join(f"{alias}.{field} AS {field}" for field in CATALOG_FIELDS)


def sql_not_laboral(alias: str = "ec") -> str:
    """Predicado SQL: exercício do catálogo NÃO laboral (candidato de grupo:
    Modo A, recomendação por grupo, substituição)."""
    if not alias.isidentifier():
        raise ValueError(f"alias SQL invalido: {alias!r}")
    return f"{alias}.{FLAG_FIELD} = 0"


# `engine_version` do snapshot congelado de uma linha laboral.
LABORAL_ENGINE_VERSION: Final[str] = "laboral-v1"
LABORAL_PROGRESSION_NOTE: Final[str] = "Exercício laboral: sem progressão (padrão do catálogo)."


class LaboralDefaultError(ValueError):
    """Padrão laboral fora do vocabulário (mensagem pronta para o 400)."""


@dataclass(frozen=True)
class LaboralDefaults:
    sets: Optional[int] = None
    reps: Optional[int] = None
    load_kg: Optional[Decimal] = None


@dataclass(frozen=True)
class LaboralPrescription:
    sets: int
    reps: int
    load_kg: Optional[Decimal]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sets": self.sets,
            "reps": self.reps,
            "load_kg": float(self.load_kg) if self.load_kg is not None else None,
        }


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "sim"}
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


def is_laboral_flag(value: Any) -> bool:
    """`exercise_catalog.is_laboral` (TINYINT, bool, texto do Data API)."""
    return _truthy(value)


def is_laboral_item(item: Any) -> bool:
    """Item do Modelo/linha da ficha marcado como laboral (`is_laboral`)."""
    raw = item.get(FLAG_FIELD) if isinstance(item, Mapping) else getattr(item, FLAG_FIELD, None)
    return is_laboral_flag(raw)


def _positive_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(str(value).strip().split("-", 1)[0])
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _load(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().lower().replace("kg", "").replace(",", ".").strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return number if number > 0 else None


def defaults_from_row(row: Mapping[str, Any]) -> LaboralDefaults:
    """Padrão lido da linha do catálogo (campos ausentes/NULL = sem padrão)."""
    return LaboralDefaults(
        sets=_positive_int(row.get(DEFAULT_SETS_FIELD)),
        reps=_positive_int(row.get(DEFAULT_REPS_FIELD)),
        load_kg=_load(row.get(DEFAULT_LOAD_FIELD)),
    )


def laboral_prescription(
    defaults: Optional[LaboralDefaults], row: Optional[Mapping[str, Any]] = None
) -> LaboralPrescription:
    """Séries, reps e carga do laboral: catálogo > linha/item > fallback."""
    defaults = defaults or LaboralDefaults()
    row = row or {}
    sets = defaults.sets or _positive_int(row.get("sets_count")) or LABORAL_FALLBACK_SETS
    reps = defaults.reps or _positive_int(row.get("reps_target")) or LABORAL_FALLBACK_REPS
    load = defaults.load_kg if defaults.load_kg is not None else _load(row.get("load_target"))
    return LaboralPrescription(sets=int(sets), reps=int(reps), load_kg=load)


def laboral_row_fields(
    defaults: Optional[LaboralDefaults],
    row: Optional[Mapping[str, Any]],
    format_load: Callable[[Any], Optional[str]],
) -> dict[str, Any]:
    """Campos de uma linha de ficha/rascunho de exercício laboral: séries,
    reps e carga do padrão (`laboral_prescription`), sem tecnica, sem alvo de
    RPE e sem progressão. `format_load` = o formato de `load_target` da ficha
    (quem chama passa o dono do formato)."""
    prescription = laboral_prescription(defaults, row)
    load_target = format_load(prescription.load_kg) if prescription.load_kg is not None else None
    return {
        FLAG_FIELD: True,
        "sets_count": prescription.sets,
        "reps_target": str(prescription.reps),
        "load_target": load_target,
        "suggested_load_kg": float(prescription.load_kg) if prescription.load_kg is not None else None,
        "suggested_reps": prescription.reps,
        "rpe_target": None,
        "advanced_technique": None,
        "advanced_technique_applications": None,
        "advanced_technique_duration_seconds": None,
        "progression": None,
        "progression_note": LABORAL_PROGRESSION_NOTE,
    }


def clean_default_sets(value: Any) -> Optional[int]:
    return _clean_int(value, DEFAULT_SETS_FIELD, LABORAL_MAX_SETS)


def clean_default_reps(value: Any) -> Optional[int]:
    return _clean_int(value, DEFAULT_REPS_FIELD, LABORAL_MAX_REPS)


def _clean_int(value: Any, field: str, maximum: int) -> Optional[int]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise LaboralDefaultError(f"{field}: informe um número inteiro.")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise LaboralDefaultError(f"{field}: informe um número inteiro.") from exc
    if number < 1 or number > maximum:
        raise LaboralDefaultError(f"{field}: use um valor entre 1 e {maximum}.")
    return number


def clean_default_load(value: Any) -> Optional[Decimal]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise LaboralDefaultError(f"{DEFAULT_LOAD_FIELD}: informe a carga em kg.")
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise LaboralDefaultError(f"{DEFAULT_LOAD_FIELD}: informe a carga em kg.") from exc
    if number < 0 or number > LABORAL_MAX_LOAD_KG:
        raise LaboralDefaultError(f"{DEFAULT_LOAD_FIELD}: use um valor entre 0 e {LABORAL_MAX_LOAD_KG} kg.")
    return number.quantize(Decimal("0.01"))
