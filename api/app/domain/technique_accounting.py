"""Contabilização de técnicas avançadas (Fase 5) - regra ÚNICA, pura.

Cada aplicação de técnica (mini-bloco de rest-pause, drop, reps além da falha)
vale uma FRAÇÃO de série de trabalho. Esta regra alimenta (a partir dos PRs
4-6 da Fase 5) volume por grupo, faixa EXG, recuperação, progressão e edição
do Modelo; aqui ela só existe - nenhum consumidor a lê ainda.

Sem banco: quem chama entrega as linhas já carregadas e o catálogo
(`advanced_techniques`, migrações 031/060) como mapeamento `code -> linha`.

Realizado, com reps lançadas (drop-set e rest-pause)::

    ref_reps  = reps da série de trabalho âncora; sem âncora ou sem reps ->
                topo da faixa prescrita (reps_target)
    esforço   = 1 se RPE da aplicação >= RPE da âncora (ou >= 9);
                senão effort_points(rpe_app) / effort_points(rpe_ref)
    equiv_app = (reps_app / ref_reps) × esforço
    bloco     = min(Σ equiv_app das aplicações da mesma âncora, teto do catálogo)

Família além da falha (forçadas, negativas, parciais, cheat): uma linha
`forced_rep` com as reps extras, ligada à âncora::

    equiv = reps_extras / ref_reps × peso da família  (forçadas/negativas 1,0;
            parciais/cheat 0,5), limitado pelo teto

Planejado, e realizado sem reps (histórico, Zepp, registro sem dado)::

    equiv = aplicações × activation_set_equivalent, limitado pelo teto

FST-7 e GVT: só preenchem o número de séries no Modelo (`default_sets_count`);
as séries são de trabalho comuns e o equivalente EXTRA é sempre 0.

Recuperação: `recovery_factor(technique_code, set_type)` vem do catálogo (M76
B5: técnica sem fator calibrado vale 1,00; o fator por `set_type` do modelo de
recuperação só cobre séries que não são técnica).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..set_types import (
    DROP_SET,
    FORCED_REP,
    PREPARATORY_SET_TYPES,
    REST_PAUSE,
    TECHNIQUE_APPLICATION_SET_TYPES,
    WORKING,
    normalize_set_type,
)
from .training_history import parse_reps_range

# Técnicas cujo realizado é ponderado pelas reps E pelo esforço.
REPS_WEIGHTED_CODES: frozenset[str] = frozenset({"drop_set", "rest_pause"})

# Família além da falha: peso das reps extras (decisão do plano da Fase 5).
BEYOND_FAILURE_WEIGHTS: Mapping[str, float] = {
    "forced_reps": 1.0,
    "negative_reps": 1.0,
    "partial_reps": 0.5,
    "cheat_reps": 0.5,
}

# Técnicas que só preenchem o número de séries (decisão 5).
SETS_ONLY_CODES: frozenset[str] = frozenset({"fst7", "gvt"})

# `set_type` da aplicação -> técnica padrão quando nada mais a identifica
# (mesmo mapeamento do backfill da migração 060).
DEFAULT_CODE_BY_SET_TYPE: Mapping[str, str] = {
    DROP_SET: "drop_set",
    REST_PAUSE: "rest_pause",
    FORCED_REP: "forced_reps",
}

# `set_type` que cada técnica grava (espelho do seed da 031; o catálogo, quando
# informado, prevalece).
SET_TYPE_BY_CODE: Mapping[str, str] = {
    "drop_set": DROP_SET,
    "rest_pause": REST_PAUSE,
    "forced_reps": FORCED_REP,
    "negative_reps": FORCED_REP,
    "partial_reps": FORCED_REP,
    "cheat_reps": FORCED_REP,
    "fst7": WORKING,
    "gvt": WORKING,
}

# Teto quando o catálogo não tem o valor (linha não calibrada): uma série cheia.
DEFAULT_MAX_EQUIVALENT = 1.0
# Esforço "à falha": acima disto a aplicação vale o esforço cheio.
FULL_EFFORT_RPE = 9.0
# RPE de referência quando a âncora não tem RPE (técnica é levada à falha).
DEFAULT_REFERENCE_RPE = 10.0


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class TechniqueCoefficients:
    code: str
    activation_set_equivalent: float = 0.0
    max_equivalent_per_anchor: float = DEFAULT_MAX_EQUIVALENT
    recovery_cost_factor: Optional[float] = None
    default_sets_count: Optional[int] = None
    set_type: Optional[str] = None


def coefficients_for(code: Optional[str], catalog: Optional[Mapping[str, Any]] = None) -> TechniqueCoefficients:
    """Coeficientes de `code` a partir do catálogo (`code -> linha`, dict ou
    `TechniqueCoefficients`). Código fora do catálogo vale 0; teto ausente
    (linha não calibrada) vira `DEFAULT_MAX_EQUIVALENT`."""
    code = (code or "").strip()
    row = (catalog or {}).get(code)
    if isinstance(row, TechniqueCoefficients):
        return row
    if row is None:
        return TechniqueCoefficients(code=code, max_equivalent_per_anchor=0.0, set_type=SET_TYPE_BY_CODE.get(code))
    cap = _float(row.get("max_equivalent_per_anchor"))
    sets = row.get("default_sets_count")
    return TechniqueCoefficients(
        code=code,
        activation_set_equivalent=_float(row.get("activation_set_equivalent")) or 0.0,
        max_equivalent_per_anchor=DEFAULT_MAX_EQUIVALENT if cap is None else cap,
        recovery_cost_factor=_float(row.get("recovery_cost_factor")),
        default_sets_count=None if sets is None else int(sets),
        set_type=row.get("set_type") or SET_TYPE_BY_CODE.get(code),
    )


# ---------------------------------------------------------------------------
# Derivação do vínculo (gravação sem technique_code/anchor_set_number)
# ---------------------------------------------------------------------------


def derive_technique_code(
    set_type: Any,
    plan_technique: Optional[str] = None,
    catalog: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Técnica de uma linha de aplicação sem código explícito.

    A técnica do item da ficha vale quando grava o MESMO `set_type` (ficha com
    `negative_reps` + linha `forced_rep` -> `negative_reps`); senão, o padrão
    do `set_type`. Série que não é aplicação de técnica -> None."""
    kind = normalize_set_type(set_type)
    if kind not in TECHNIQUE_APPLICATION_SET_TYPES:
        return None
    if plan_technique:
        plan_set_type = coefficients_for(plan_technique, catalog).set_type
        if plan_set_type and normalize_set_type(plan_set_type) == kind:
            return plan_technique
    return DEFAULT_CODE_BY_SET_TYPE.get(kind)


def derive_anchor_set_number(set_number: Optional[int], previous_sets: Iterable[tuple[int, Any]]) -> Optional[int]:
    """Última série `working` ANTERIOR a `set_number` (todas, se None) entre
    `(set_number, set_type)` já gravadas do mesmo exercício."""
    candidates = [
        int(number)
        for number, kind in previous_sets
        if number is not None
        and normalize_set_type(kind) == WORKING
        and (set_number is None or int(number) < int(set_number))
    ]
    return max(candidates) if candidates else None


# ---------------------------------------------------------------------------
# Regra de contabilização
# ---------------------------------------------------------------------------


def _effort_points(rpe: float) -> float:
    # Import tardio: a regra continua pura e o domínio não cria ciclo com
    # `intelligence` quando o volume passar a importar este módulo (PR 4).
    from ..intelligence.muscle_recovery_service import MuscleRecoveryService

    return MuscleRecoveryService.effort_points(rpe)


def effort_factor(rpe_application: Any, rpe_reference: Any) -> float:
    """1 se a aplicação chegou ao esforço da âncora (ou >= 9); senão a razão
    dos pontos de esforço. Sem RPE da aplicação -> 1 (técnica é à falha)."""
    app = _float(rpe_application)
    if app is None:
        return 1.0
    ref = _float(rpe_reference)
    if ref is None:
        ref = DEFAULT_REFERENCE_RPE
    if app >= ref or app >= FULL_EFFORT_RPE:
        return 1.0
    return min(1.0, _effort_points(app) / _effort_points(ref))


def reference_reps(anchor_reps: Any, reps_target: Any = None) -> Optional[int]:
    """Reps da âncora; sem âncora ou sem reps -> topo da faixa prescrita."""
    reps = _float(anchor_reps)
    if reps is not None and reps > 0:
        return int(reps)
    _low, high = parse_reps_range(reps_target)
    return high if high and high > 0 else None


@dataclass(frozen=True)
class TechniqueApplication:
    """Uma linha de aplicação (`exercise_sets`) ligada a uma âncora."""

    technique_code: Optional[str]
    reps: Optional[int] = None
    rpe: Optional[float] = None
    set_type: Optional[str] = None


def application_equivalent(
    application: TechniqueApplication,
    *,
    ref_reps: Optional[int],
    anchor_rpe: Any = None,
    catalog: Optional[Mapping[str, Any]] = None,
) -> float:
    """Equivalente de UMA aplicação, sem teto (o teto é por bloco)."""
    code = application.technique_code or ""
    coef = coefficients_for(code, catalog)
    if code in SETS_ONLY_CODES or coef.max_equivalent_per_anchor <= 0:
        return 0.0
    reps = _float(application.reps)
    if reps is None or ref_reps is None or ref_reps <= 0:
        # Realizado sem reps (ou sem referência): coeficiente do catálogo.
        return coef.activation_set_equivalent
    if code in BEYOND_FAILURE_WEIGHTS:
        return max(0.0, reps) / ref_reps * BEYOND_FAILURE_WEIGHTS[code]
    if code in REPS_WEIGHTED_CODES:
        return max(0.0, reps) / ref_reps * effort_factor(application.rpe, anchor_rpe)
    # Técnica nova do catálogo sem regra por reps: coeficiente planejado.
    return coef.activation_set_equivalent


@dataclass(frozen=True)
class BlockEquivalent:
    """Equivalente das aplicações de UMA âncora, por técnica e no total."""

    total: float
    by_technique: Mapping[str, float] = field(default_factory=dict)
    raw_by_technique: Mapping[str, float] = field(default_factory=dict)
    ref_reps: Optional[int] = None

    @property
    def capped(self) -> bool:
        return any(self.raw_by_technique[c] > self.by_technique[c] for c in self.by_technique)


def realized_block_equivalent(
    applications: Sequence[TechniqueApplication],
    *,
    anchor_reps: Any = None,
    anchor_rpe: Any = None,
    reps_target: Any = None,
    catalog: Optional[Mapping[str, Any]] = None,
) -> BlockEquivalent:
    """Soma das aplicações ligadas à MESMA âncora, com o teto do catálogo
    aplicado por técnica (rest-pause + forçadas na mesma série têm tetos
    próprios)."""
    ref = reference_reps(anchor_reps, reps_target)
    raw: dict[str, float] = {}
    for application in applications:
        code = application.technique_code or ""
        raw[code] = raw.get(code, 0.0) + application_equivalent(
            application, ref_reps=ref, anchor_rpe=anchor_rpe, catalog=catalog
        )
    capped = {code: min(value, max(0.0, coefficients_for(code, catalog).max_equivalent_per_anchor)) for code, value in raw.items()}
    return BlockEquivalent(total=sum(capped.values()), by_technique=capped, raw_by_technique=raw, ref_reps=ref)


def planned_equivalent(
    technique_code: Optional[str],
    applications: Any,
    catalog: Optional[Mapping[str, Any]] = None,
) -> float:
    """Planejado: aplicações × activation_set_equivalent, limitado pelo teto.
    FST-7/GVT e código fora do catálogo -> 0."""
    code = technique_code or ""
    count = _float(applications) or 0.0
    if not code or code in SETS_ONLY_CODES or count <= 0:
        return 0.0
    coef = coefficients_for(code, catalog)
    return min(count * coef.activation_set_equivalent, max(0.0, coef.max_equivalent_per_anchor))


def planned_work_sets(
    sets_count: Any,
    technique_code: Optional[str],
    applications: Any,
    catalog: Optional[Mapping[str, Any]] = None,
) -> float:
    """Séries de trabalho PLANEJADAS de um item do Modelo (Fase 5, PR 5):
    `sets_count` + `planned_equivalent` da técnica. É a unidade que o volume
    planejado (preview de ativação, metodologia EXG, Novo Dashboard) multiplica
    pelo peso da contribuição, no lugar de `sets_count` puro. Sem séries o item
    não conta: técnica sozinha não é trabalho."""
    sets = _float(sets_count) or 0.0
    if sets <= 0:
        return 0.0
    return sets + planned_equivalent(technique_code, applications, catalog)


def is_sets_only_technique(technique_code: Optional[str], catalog: Optional[Mapping[str, Any]] = None) -> bool:
    """FST-7/GVT: técnica que só preenche o número de séries do Modelo
    (`default_sets_count` no catálogo). Não tem aplicações nem pausa próprias:
    o builder oculta os dois campos, a API e o validador da IA os zeram. O
    código conhecido decide sem ler o catálogo."""
    code = (technique_code or "").strip()
    if not code:
        return False
    if code in SETS_ONLY_CODES:
        return True
    return coefficients_for(code, catalog).default_sets_count is not None


def recovery_factor(
    technique_code: Optional[str],
    set_type: Any,
    catalog: Optional[Mapping[str, Any]] = None,
) -> float:
    """Fator de recuperação da linha. Técnica: SÓ o catálogo
    (`advanced_techniques.recovery_cost_factor`, migração 060) - M76 B5: o
    modelo de recuperação não tem mais cópia própria dos fatores de técnica, e
    técnica sem fator calibrado vale 1,00. Série que não é técnica: o fator
    por `set_type` do modelo (aquecimento, ajuste, backoff, trabalho)."""
    code = technique_code or derive_technique_code(set_type)
    if code:
        factor = coefficients_for(code, catalog).recovery_cost_factor
        return 1.0 if factor is None else factor
    from ..intelligence.muscle_recovery_service import MuscleRecoveryService

    return MuscleRecoveryService.set_type_factor(normalize_set_type(set_type))


# ---------------------------------------------------------------------------
# Realizado linha a linha (Fase 5, PR 4)
# ---------------------------------------------------------------------------
#
# Os consumidores de volume realizado (overview de recuperação, Novo
# Dashboard, tendência semanal, janela de ativação, aderência) somam "uma
# unidade por linha de `exercise_sets`" multiplicada pelo peso da contribuição
# muscular. Para a técnica contar sem que cada consumidor reimplemente a regra
# do bloco, a regra devolve a UNIDADE de cada linha:
#
#   - aquecimento/ajuste (`warmup`/`feeler`) -> 0 (não geram estímulo);
#   - aplicação de técnica (`rest_pause`/`drop_set`/`forced_rep`) -> a sua
#     fatia do bloco da âncora (a soma das fatias de um bloco é exatamente
#     `realized_block_equivalent(...).total`);
#   - qualquer outra (`working`, `backoff`, legado sem tipo) -> 1.
#
# O consumidor multiplica a unidade pelo `stimulus_weight`/`fatigue_weight` da
# contribuição - a mesma conta de sempre, com a unidade no lugar do "1".


class LazyCatalog(Mapping[str, Any]):
    """Catálogo lido só quando alguém consulta uma técnica.

    Quem agrega volume cria um por requisição e o repassa: uma leitura com
    aplicação de técnica carrega o catálogo UMA vez; uma leitura sem nenhuma
    aplicação (o caso comum) não custa statement nenhum."""

    def __init__(self, loader: Any) -> None:
        self._loader = loader
        self._data: Optional[dict[str, Any]] = None

    def _loaded(self) -> dict[str, Any]:
        if self._data is None:
            self._data = dict(self._loader() or {})
        return self._data

    @property
    def loaded(self) -> bool:
        return self._data is not None

    def __getitem__(self, key: str) -> Any:
        return self._loaded()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._loaded().get(key, default)

    def __iter__(self):
        return iter(self._loaded())

    def __len__(self) -> int:
        return len(self._loaded())

    def __bool__(self) -> bool:
        # `catalog or {}` (em `coefficients_for`) não pode disparar a leitura
        # nem trocar o catálogo por `{}` antes de ele ser lido.
        return True


def row_technique_code(row: Mapping[str, Any]) -> Optional[str]:
    """Técnica de uma linha realizada: a gravada; linha de aplicação sem
    código (Zepp, legado anterior à 059) cai na derivação pelo `set_type` - a
    MESMA do backfill da 060. Série que não é aplicação e não tem código ->
    None."""
    code = row.get("technique_code")
    if code:
        return str(code)
    return derive_technique_code(row.get("set_type"))


def realized_set_units(
    rows: Sequence[Mapping[str, Any]],
    *,
    catalog: Optional[Mapping[str, Any]] = None,
    exercise_key: str = "exercise_id",
) -> list[float]:
    """Séries equivalentes de cada linha de `rows`, na MESMA ordem.

    `rows` são linhas de `exercise_sets` já carregadas, com `exercise_key`
    (a linha de `exercises`: um bloco nunca atravessa exercícios) e
    `set_number`, `set_type`, `reps`, `rpe`, `technique_code`,
    `anchor_set_number`. Sem `technique_code`/`anchor_set_number` (Zepp,
    legado), técnica e âncora são DERIVADAS como no backfill da 060.

    A âncora ausente do conjunto (ex.: corte por `LIMIT`) ou sem reps cai no
    fallback da própria regra (coeficiente do catálogo), nunca em erro. O
    catálogo só é consultado quando há aplicação de técnica."""
    units: list[float] = [0.0] * len(rows)
    applications_by_exercise: dict[Any, list[int]] = {}
    for index, row in enumerate(rows):
        kind = normalize_set_type(row.get("set_type"))
        if kind in PREPARATORY_SET_TYPES:
            continue
        if kind in TECHNIQUE_APPLICATION_SET_TYPES:
            applications_by_exercise.setdefault(row.get(exercise_key), []).append(index)
        else:
            units[index] = 1.0

    if not applications_by_exercise:
        return units

    siblings_by_exercise: dict[Any, list[Mapping[str, Any]]] = {}
    for row in rows:
        key = row.get(exercise_key)
        if key in applications_by_exercise:
            siblings_by_exercise.setdefault(key, []).append(row)

    for key, application_indexes in applications_by_exercise.items():
        siblings = siblings_by_exercise[key]
        working_by_number = {
            int(sibling["set_number"]): sibling
            for sibling in siblings
            if sibling.get("set_number") is not None and normalize_set_type(sibling.get("set_type")) == WORKING
        }
        numbered = [(sibling.get("set_number"), sibling.get("set_type")) for sibling in siblings]

        blocks: dict[Optional[int], list[int]] = {}
        for index in application_indexes:
            anchor = rows[index].get("anchor_set_number")
            if anchor is None:
                anchor = derive_anchor_set_number(rows[index].get("set_number"), numbered)
            blocks.setdefault(None if anchor is None else int(anchor), []).append(index)

        for anchor, indexes in blocks.items():
            anchor_row = working_by_number.get(anchor) if anchor is not None else None
            anchor_rpe = anchor_row.get("rpe") if anchor_row else None
            applications = [
                TechniqueApplication(
                    technique_code=row_technique_code(rows[i]),
                    reps=rows[i].get("reps"),
                    rpe=rows[i].get("rpe"),
                    set_type=rows[i].get("set_type"),
                )
                for i in indexes
            ]
            block = realized_block_equivalent(
                applications,
                anchor_reps=anchor_row.get("reps") if anchor_row else None,
                anchor_rpe=anchor_rpe,
                catalog=catalog,
            )
            # Cada aplicação leva a sua fatia: o equivalente cru dela escalado
            # pelo teto da técnica. A soma das fatias de um bloco é o total.
            for index, application in zip(indexes, applications):
                code = application.technique_code or ""
                raw_total = block.raw_by_technique.get(code, 0.0)
                if raw_total <= 0:
                    continue
                raw = application_equivalent(
                    application, ref_reps=block.ref_reps, anchor_rpe=anchor_rpe, catalog=catalog
                )
                units[index] = raw * block.by_technique.get(code, 0.0) / raw_total
    return units


def realized_recovery_factor(row: Mapping[str, Any], catalog: Optional[Mapping[str, Any]] = None) -> float:
    """Fator de recuperação de UMA linha realizada: `recovery_factor` com a
    técnica gravada (ou derivada, na aplicação sem código). Série sem técnica
    não consulta o catálogo."""
    code = row_technique_code(row)
    return recovery_factor(code, row.get("set_type"), catalog if code else None)


# ---------------------------------------------------------------------------
# Espelho do frontend
# ---------------------------------------------------------------------------


def export_for_frontend() -> dict[str, Any]:
    """Constantes do espelho `frontend/src/lib/technique-accounting.ts` (o
    feedback "= 0,5 série equivalente" do Modo Treino é calculado na tela, ao
    vivo). Os coeficientes por técnica continuam vindo do catálogo pela API;
    os pontos de esforço são os de `MuscleRecoveryService.EFFORT_POINTS`
    (dono real, que `effort_factor` já usa). Gerado em
    `motor-constants.generated.json` por
    `api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`."""
    from ..intelligence.muscle_recovery_service import MuscleRecoveryService

    return {
        "reps_weighted_codes": sorted(REPS_WEIGHTED_CODES),
        "beyond_failure_weights": dict(BEYOND_FAILURE_WEIGHTS),
        "sets_only_codes": sorted(SETS_ONLY_CODES),
        "default_max_equivalent": DEFAULT_MAX_EQUIVALENT,
        "full_effort_rpe": FULL_EFFORT_RPE,
        "default_reference_rpe": DEFAULT_REFERENCE_RPE,
        "effort_points": [[x, y] for x, y in MuscleRecoveryService.EFFORT_POINTS],
    }
