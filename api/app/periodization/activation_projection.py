"""Projecao de ativacao do ciclo contra as metas do perfil - parte PURA
(Fase 4, PR D).

Ate o PR D este calculo morava inteiro dentro de `validation._check_activation`,
misturado com as leituras de banco (metas do perfil, preview de ativacao,
nomes dos grupos). Aqui fica SO o calculo: recebe o que ja foi carregado e
devolve a projecao por grupo e os warnings, sem I/O, sem FastAPI, sem
`datetime.now()`. Quem carrega continua sendo `validation.py` (e, no PR E, o
endpoint de metodologia, que alimenta o motor de
`intelligence/methodology_volume.py` com os MESMOS dados carregados).

Contrato preservado (payload identico ao que `_check_activation` sempre
produziu - provado por oraculo literal em
`tests/unit/test_activation_projection.py`):

- Modo B: soma `total_equivalent` por grupo em TODOS os dias da janela do
  preview (7 dias a partir do 1o slot de treino desde a Fase 7 -
  `methodology_volume.evaluation_window_days`), na MESMA ordem de iteracao
  (soma de float nao e associativa - a ordem e parte do contrato), e
  classifica contra a meta semanal com `classify_weekly_volume_status` (M76
  Lote 4a, E11: a mesma regua - e a mesma tolerancia de +-5% do alvo - de
  todas as telas). A janela ja e a semana: a meta entra sem escala. Grupo da
  meta sem nome carregado e pulado; grupos percorridos em ordem de id.
- Modo A: nao ha serie no modelo; a comparacao e a soma de percentuais que
  `validate_template_volume_coverage` ja classifica (90%/110%); `BALANCED` nao
  vira warning.

Os arredondamentos (`round(_, 2)`) e as chaves de cada warning sao os do
codigo original, na mesma ordem - o frontend do PR-17 le esse contrato.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from ..intelligence.methodology_volume import weekly_window_days
from ..intelligence.volume_service import classify_weekly_volume_status

# Codigos do relatorio (espelho dos de `validation.py`, que os reexporta; ficam
# aqui para o modulo puro nao importar o validador, que importa FastAPI).
ACTIVATION_BELOW_MIN = "ACTIVATION_BELOW_MIN"
ACTIVATION_ABOVE_MAX = "ACTIVATION_ABOVE_MAX"
ACTIVATION_COVERAGE = "ACTIVATION_COVERAGE"

# Janela padrao quando o preview nao informa `window_days`: a semana do dono
# da normalizacao (M76 Lote 4a).
DEFAULT_WINDOW_DAYS = weekly_window_days()

# Estados de `classify_weekly_volume_status` que viram warning.
_WEEKLY_BELOW_MIN = "BELOW_MIN"
_WEEKLY_ABOVE_MAX = "ABOVE_MAX"

# Status da projecao por grupo. `WITHIN_TARGET` nao gera warning.
PROJECTION_BELOW_MIN = "BELOW_MIN"
PROJECTION_ABOVE_MAX = "ABOVE_MAX"
PROJECTION_WITHIN_TARGET = "WITHIN_TARGET"

# Status de cobertura do Modo A que NAO gera warning.
COVERAGE_BALANCED = "BALANCED"


@dataclass(frozen=True)
class ActivationProjectionInputs:
    """Tudo o que o calculo do Modo B precisa, JA CARREGADO.

    - `preview_days`: `days` da resposta de `preview_activation` (cada dia com
      `groups: [{muscle_group, total_equivalent, ...}]`);
    - `window_days`: `window_days` da mesma resposta (None -> 7);
    - `targets`: `muscle_group_id -> {"min": ..., "max": ...}` (series semanais
      vigentes do perfil, `get_training_type_muscle_group_targets`);
    - `muscle_group_names`: `muscle_group_id -> nome` dos grupos da meta.
    """

    preview_days: tuple[Mapping[str, Any], ...]
    window_days: Optional[int]
    targets: Mapping[int, Mapping[str, float]]
    muscle_group_names: Mapping[int, str]


@dataclass(frozen=True)
class ActivationProjectionRow:
    """Projecao de UM grupo da meta contra a meta escalada pela janela.

    `projected`, `minimum` e `maximum` ja vem arredondados a 2 casas, como o
    warning original entregava."""

    muscle_group_id: int
    muscle_group_name: str
    projected: float
    minimum: float
    maximum: float
    window_days: int
    status: str


def sum_projected_by_group(preview_days: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """`nome do grupo -> soma de total_equivalent` em todos os dias da janela.

    Mesma ordem de iteracao (dia a dia, grupo a grupo) e mesma acumulacao
    (`get(name, 0.0) + float(...)`) do codigo original, para a soma de float
    sair bit a bit igual."""
    projected: dict[str, float] = {}
    for day in preview_days or []:
        for group in day.get("groups") or []:
            projected[group["muscle_group"]] = projected.get(group["muscle_group"], 0.0) + float(
                group["total_equivalent"]
            )
    return projected


def project_activation(inputs: ActivationProjectionInputs) -> tuple[ActivationProjectionRow, ...]:
    """Modo B: projecao por grupo da meta, em ordem de `muscle_group_id`.

    Grupo da meta sem nome carregado e pulado (comportamento original). O
    status sai de `classify_weekly_volume_status` (E11): so os extremos
    (`BELOW_MIN`/`ABOVE_MAX`) viram warning; os estados intermediarios (com a
    tolerancia de +-5% ao redor do alvo) sao `WITHIN_TARGET`. A meta nao e
    escalada: a janela do preview ja e a semana (M76 Lote 4a; antes
    `x window_days/7`, que valia 1 desde a Fase 7)."""
    window_days = inputs.window_days or DEFAULT_WINDOW_DAYS
    projected = sum_projected_by_group(inputs.preview_days)
    rows: list[ActivationProjectionRow] = []
    for group_id, values in sorted(inputs.targets.items()):
        name = inputs.muscle_group_names.get(group_id)
        if not name:
            continue
        total = round(projected.get(name, 0.0), 2)
        minimum = round(float(values["min"]), 2)
        maximum = round(float(values["max"]), 2)
        # Meta sem alvo explicito: o ponto medio da faixa (so a tolerancia do
        # alvo depende dele; os extremos que viram warning nao).
        raw_target = values.get("target")
        target = float(raw_target) if raw_target is not None else (minimum + maximum) / 2.0
        weekly = classify_weekly_volume_status(total, minimum, min(max(target, minimum), maximum), maximum)
        if weekly == _WEEKLY_BELOW_MIN:
            status = PROJECTION_BELOW_MIN
        elif weekly == _WEEKLY_ABOVE_MAX:
            status = PROJECTION_ABOVE_MAX
        else:
            status = PROJECTION_WITHIN_TARGET
        rows.append(
            ActivationProjectionRow(
                muscle_group_id=group_id,
                muscle_group_name=name,
                projected=total,
                minimum=minimum,
                maximum=maximum,
                window_days=window_days,
                status=status,
            )
        )
    return tuple(rows)


def activation_target_warnings(rows: Iterable[ActivationProjectionRow]) -> list[dict[str, Any]]:
    """Linhas fora da meta -> warnings do relatorio, com as MESMAS chaves e a
    MESMA ordem de chaves que `ValidationReport.warning(code, **fields)`
    sempre gravou (`code` primeiro)."""
    warnings: list[dict[str, Any]] = []
    for row in rows:
        if row.status == PROJECTION_BELOW_MIN:
            warnings.append(
                {
                    "code": ACTIVATION_BELOW_MIN,
                    "muscle_group_id": row.muscle_group_id,
                    "muscle_group_name": row.muscle_group_name,
                    "projected": row.projected,
                    "min": row.minimum,
                    "window_days": row.window_days,
                }
            )
        elif row.status == PROJECTION_ABOVE_MAX:
            warnings.append(
                {
                    "code": ACTIVATION_ABOVE_MAX,
                    "muscle_group_id": row.muscle_group_id,
                    "muscle_group_name": row.muscle_group_name,
                    "projected": row.projected,
                    "max": row.maximum,
                    "window_days": row.window_days,
                }
            )
    return warnings


def coverage_warnings(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Modo A: entradas de `validate_template_volume_coverage` -> warnings
    `ACTIVATION_COVERAGE`, pulando `BALANCED`. Chaves e ordem originais."""
    warnings: list[dict[str, Any]] = []
    for entry in entries or []:
        if entry["status"] == COVERAGE_BALANCED:
            continue
        warnings.append(
            {
                "code": ACTIVATION_COVERAGE,
                "muscle_group_id": entry["muscle_group_id"],
                "muscle_group_name": entry["muscle_group_name"],
                "sum_pct_min": entry["sum_pct_min"],
                "sum_pct_max": entry["sum_pct_max"],
                "status": entry["status"],
            }
        )
    return warnings


__all__ = [
    "ACTIVATION_ABOVE_MAX",
    "ACTIVATION_BELOW_MIN",
    "ACTIVATION_COVERAGE",
    "ActivationProjectionInputs",
    "ActivationProjectionRow",
    "COVERAGE_BALANCED",
    "DEFAULT_WINDOW_DAYS",
    "PROJECTION_ABOVE_MAX",
    "PROJECTION_BELOW_MIN",
    "PROJECTION_WITHIN_TARGET",
    "activation_target_warnings",
    "coverage_warnings",
    "project_activation",
    "sum_projected_by_group",
]
