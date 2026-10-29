"""Complemento DETERMINISTICO de volume do rascunho gerado por IA (M46).

Medicao do prompt `periodization-v7` (DEV, 2026-10-05): 8/8 geracoes com
sucesso, mas 8,5 grupos abaixo da faixa da metodologia por geracao - a IA
entrega ~5,5 exercicios por slot, todos com <= 4 series e nenhuma tecnica.
Pedir mais volume no prompt nao fechou a conta; este passo fecha o que da para
fechar sem chamar a IA de novo, com a regra decidida pelo usuario:

Para cada grupo abaixo da faixa, so nos slots de treino DENTRO da janela de
avaliacao de 7 dias (Fase 7) que ja trabalham o grupo como PRIMARY (assim a
frequencia do grupo - e a celula da faixa - nao muda):

(a) somar serie a um exercicio do slot que ja trabalha o grupo, mantendo o
    ideal de ate 4 series por exercicio (`methodology_volume_sets_per_exercise_max`);
(b) senao, acrescentar um exercicio NOVO do catalogo da academia
    (`gym_equipment`) para o grupo, preferindo par cujo PRIMARY seja so aquele
    grupo (nao derrama volume em outro) e nunca repetindo exercicio do slot;
    o exercicio entra com o minimo por exercicio da metodologia (3 series) e,
    se isso passaria da faixa, com menos;
(c) se (b) nao couber (teto de 9 exercicios por slot, sem par no catalogo ou
    passaria da faixa): primeiro drop set / rest-pause (equivalente planejado
    da Fase 5, coeficientes do catalogo `advanced_techniques`, validado por
    `training_safety.validate_technique` - tecnica a falha so em par com
    `max_safe_rpe` 10), e so depois passar de 4 series num exercicio - nunca
    alem de 6 (`MAX_SETS_PER_EXERCISE`); exercicio que a IA ja entregou com
    mais de 6 nao e cortado, so nao recebe mais series.

Travas de toda acao (testadas no MOTOR REAL da metodologia, depois da acao):
- o deficit do grupo-alvo diminui;
- nenhum eixo (series por sessao, series semanais, exercicios por sessao) de
  NENHUM grupo passa a ABOVE_RANGE ou fica mais acima do que ja estava;
- nenhum outro grupo fica mais abaixo da faixa do que estava;
- o slot nao passa de 90 min pela MESMA formula da estimativa que o builder
  mostra (`time_estimate`, dono desde o M76 Lote 4c; o builder mostra o espelho), contando
  90 s de descanso por serie de trabalho quando o item nao tem descanso
  (`COMPLEMENT_DEFAULT_REST_SECONDS`; a tela conta 0 s nesse caso).

Ordem: primeiro o eixo POR SESSAO, depois o SEMANAL. Dentro de cada eixo, um
passo por grupo por rodada (ordem de id), ate nao haver mais passo possivel:
um grupo nao consome sozinho a capacidade do slot que outro tambem precisa.
O que sobra vira `VOLUME_GAP_UNRESOLVED`, com o motivo.

Corte de excesso (`VOLUME_REDUCED`, 2026-10-16 - eval pago do prompt v11: no
modo ponderado a resposta saiu com 6 grupos ABOVE_RANGE e o acrescimo nao
corrigia nada). DEPOIS do acrescimo, e uma vez so (nao volta a ele): grupo
ACIMA da faixa de series - por sessao, depois semanal, como o motor avalia no
modo do pedido - perde uma serie por passo, num item da janela que conta para
ele e que o acrescimo nao tocou. Piso = minimo por exercicio da metodologia
(`_SETS_PER_EXERCISE_MIN`); nunca remove item. Priorizados por ultimo; PRIMARY
do grupo antes de so-secundario; maior peso e mais series primeiro. Trava
simetrica (`_acceptable_cut`): o excesso do alvo cai, nenhum eixo de nenhum
grupo fica mais abaixo nem mais acima da faixa. Eixo cujo ABOVE a intencao da
metodologia espera (severidade `informational` do motor) nao e cortado. Custo
com teto rigido e orcamento proprio (`COMPLEMENT_REDUCTION_MAX_EVALUATIONS`
avaliacoes do motor, aceitas + rejeitadas, separado de `MAX_STEPS`). O que
o piso nao deixa fechar continua como `METHODOLOGY_*ABOVE_RANGE` na etapa de
avisos.

Modo de contagem do Modelo (Lote 5): as leituras (`MethodologyInputs`) ja vem
no modo, e TODA conta passa pelo motor (`evaluate_methodology_volume`) e pelos
pesos dele (`counted_contributions`). No modo ponderado, somar serie num
composto conta tambem nos grupos SECUNDARIOS dele (fracao = o `stimulus_weight`
cadastrado), e as travas acima medem isso - inclusive "nenhum grupo passa a
ABOVE_RANGE". O complemento continua agindo so em slot com trabalho PRIMARY do
grupo (a frequencia nao muda) e prefere, nesta ordem, item/par em que o grupo e
PRIMARY a item/par em que ele e so secundario. Em `primary_only` todo grupo
contado e PRIMARY, entao a ordem e as decisoes sao as de antes.

Puro em relacao ao banco: recebe as leituras ja feitas (metodologia, pares da
academia, seguranca) e um `safety_loader` para os pares candidatos que a
validacao ainda nao leu (uma leitura em lote, so quando ha deficit).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional

from ..domain import training_safety as ts
from ..domain.technique_accounting import planned_equivalent
from ..domain.training_goal import resolve_goal
from ..intelligence import methodology_volume as mv
from ..intelligence import methodology_volume_service as svc
from ..models import WorkoutPlanTemplateIn, WorkoutPlanTemplateSlotItemIn
from .limits import (
    COMPLEMENT_DEFAULT_REST_SECONDS,
    COMPLEMENT_MAX_EXERCISES_PER_SLOT,
    COMPLEMENT_MAX_SETS_PER_EXERCISE,
    COMPLEMENT_REDUCTION_MAX_EVALUATIONS,
    SLOT_TIME_LIMIT_SECONDS,
    complement_default_rest_seconds,
)
from .time_estimate import estimate_slot_seconds

# Codigos do relatorio (`auto_fixes`).
VOLUME_SETS_ADDED = "VOLUME_SETS_ADDED"
VOLUME_EXERCISE_ADDED = "VOLUME_EXERCISE_ADDED"
VOLUME_TECHNIQUE_ADDED = "VOLUME_TECHNIQUE_ADDED"
VOLUME_SETS_ABOVE_IDEAL = "VOLUME_SETS_ABOVE_IDEAL"
VOLUME_GAP_UNRESOLVED = "VOLUME_GAP_UNRESOLVED"
# Corte de excesso (2026-10-16): serie tirada de um exercicio do grupo ACIMA
# da faixa, depois do acrescimo. Payload: grupo, axis, slot, item, par, from,
# to, max, excess_before, excess_after, min_sets, prioritized, is_primary.
VOLUME_REDUCED = "VOLUME_REDUCED"

# Decisoes do usuario (M46) - donos em `limits.py` (M76, Lote 4c, E9); os
# nomes daqui continuam valendo (contrato dos testes e do validador).
MAX_EXERCISES_PER_SLOT = COMPLEMENT_MAX_EXERCISES_PER_SLOT
# Na conta dos 90 min DO COMPLEMENTO, serie sem `rest_seconds` descansa
# `COMPLEMENT_DEFAULT_REST_SECONDS` (decisao de 2026-10-05; ver `limits.py`).
# A tela do Modelo continua contando 0 s; a formula e a mesma, so o parametro
# `default_rest_seconds` muda.
MAX_SETS_PER_EXERCISE = COMPLEMENT_MAX_SETS_PER_EXERCISE
# Tecnicas que o complemento pode acrescentar, na ordem de preferencia.
COMPLEMENT_TECHNIQUES: tuple[str, ...] = ("drop_set", "rest_pause")
# Guarda contra laco: cada passo soma no minimo uma fracao de serie, entao o
# numero real de passos e limitado pelos deficits; isto so protege a Lambda.
MAX_STEPS = 500

AXIS_SESSION = "session"
AXIS_WEEKLY = "weekly"
_AXIS_EXERCISES = "exercises"

# Motivos de deficit sem solucao (`VOLUME_GAP_UNRESOLVED.reasons`).
REASON_NO_SLOT_IN_WINDOW = "NO_SLOT_IN_WINDOW"
REASON_EXERCISE_CAP = "EXERCISE_CAP"
REASON_TIME_LIMIT = "TIME_LIMIT"
REASON_RANGE_LIMIT = "RANGE_LIMIT"
REASON_NO_CATALOG_PAIR = "NO_CATALOG_PAIR"
REASON_NO_TECHNIQUE = "NO_TECHNIQUE"
REASON_MAX_SETS_PER_EXERCISE = "MAX_SETS_PER_EXERCISE"

_EPS = 1e-6


def _sets_ideal_max() -> int:
    from ..intelligence.methodology_volume import _SETS_PER_EXERCISE_MAX

    return int(_SETS_PER_EXERCISE_MAX)


def _sets_floor_for_reduction() -> int:
    """Piso do corte de excesso: o MINIMO por exercicio da metodologia
    (`methodology_volume_sets_per_exercise_min`, "1 exercicio ~ 3-4 series
    validas", EXG-004) - o mesmo do exercicio novo e do default de series do
    validador. O corte nunca remove item nem o deixa abaixo disso."""
    return _sets_per_new_exercise()


def _sets_per_new_exercise() -> int:
    from ..intelligence.methodology_volume import _SETS_PER_EXERCISE_MIN

    return max(1, int(_SETS_PER_EXERCISE_MIN))


@dataclass
class ComplementResult:
    """O que o passo produziu: as entradas do relatorio, na ordem."""

    auto_fixes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Entry:
    code: str
    fields: dict[str, Any]


def complement_volume(
    template: WorkoutPlanTemplateIn,
    inputs: svc.MethodologyInputs,
    gym_pairs: list[dict[str, Any]],
    safeties: Mapping[tuple[int, int], Any],
    safety_loader: Optional[Callable[[Iterable[tuple[int, int]]], Mapping[tuple[int, int], Any]]] = None,
    catalog: Any = None,
    intent: Optional[mv.MethodologyIntent] = None,
) -> ComplementResult:
    """Aplica o complemento EM `template` (os slots sao trocados no fim, de uma
    vez) e devolve as entradas de auditoria. Mesma entrada -> mesma saida.

    `intent`: a intencao da metodologia (`MethodologyIntent`) que o MOTOR usa
    na severidade; ausente = `standard` (a Periodizacao ainda nao tem campo de
    intencao no pedido). O corte de excesso nao corta eixo cujo ABOVE o motor
    marca como esperado pela intencao (`informational`)."""
    work = template.model_copy(deep=True)
    engine = _Complement(work, inputs, gym_pairs, dict(safeties), safety_loader, catalog, intent)
    engine.run()
    template.slots = work.slots
    return ComplementResult(auto_fixes=[{"code": e.code, **e.fields} for e in engine.entries])


class _Complement:
    def __init__(
        self,
        template: WorkoutPlanTemplateIn,
        inputs: svc.MethodologyInputs,
        gym_pairs: list[dict[str, Any]],
        safeties: dict[tuple[int, int], Any],
        safety_loader: Optional[Callable[[Iterable[tuple[int, int]]], Mapping[tuple[int, int], Any]]],
        catalog: Any,
        intent: Optional[mv.MethodologyIntent] = None,
    ) -> None:
        from ..intelligence.technique_volume import new_catalog

        self.template = template
        self.inputs = inputs
        self.safeties = safeties
        self.safety_loader = safety_loader
        self.catalog = catalog if catalog is not None else new_catalog()
        self.intent = intent
        self.ideal = _sets_ideal_max()
        self.new_sets = _sets_per_new_exercise()
        self.floor = _sets_floor_for_reduction()
        self.steps = 0
        # Orcamento PROPRIO do corte: avaliacoes do motor (aceitas e
        # rejeitadas), separado dos passos do acrescimo.
        self.cut_evaluations = 0
        self.cut_budget_exhausted = False

        by_id = {g.id: g for g in inputs.groups}
        self.block_of = {gid: mv._resolve_block(gid, by_id)[0] for gid in by_id}
        self.pairs = list(gym_pairs)
        self.names = {(int(p["mapping_id"]), int(p["equipment_id"])): p for p in self.pairs}

        self.entries: list[_Entry] = []
        # (slot, item_order) dos itens que este passo criou / em que pos tecnica.
        self.added_items: dict[tuple[int, int], _Entry] = {}
        self.added_techniques: set[tuple[int, int]] = set()
        # (codigo, slot, item, grupo, eixo) -> entrada do relatorio.
        self.item_entries: dict[tuple[str, int, int, int, str], _Entry] = {}

        self.groups: dict[int, mv.GroupEvaluation] = {}
        self.window_hits: dict[int, int] = {}
        self.snapshot: dict[tuple[int, str], tuple[float, float]] = {}
        self._evaluate()

    # ------------------------------------------------------------------
    # Motor
    # ------------------------------------------------------------------

    def _evaluate(self) -> None:
        self.groups, self.window_hits, self.snapshot = self._measure()

    def _measure(self) -> tuple[dict[int, mv.GroupEvaluation], dict[int, int], dict[tuple[int, str], tuple[float, float]]]:
        slot_inputs = svc.build_slot_inputs(
            self.template.slots, "equipment_series", self.inputs.canonical_ids, self.catalog
        )
        evaluation = mv.evaluate_methodology_volume(
            slot_inputs,
            self.inputs.contributions,
            self.inputs.groups,
            "equipment_series",
            self.intent,
            self.inputs.volume_classes,
            self.inputs.counting_mode,
        )
        _, hits = mv.evaluation_window(slot_inputs)
        groups = {g.muscle_group_id: g for g in evaluation.groups}
        snapshot: dict[tuple[int, str], tuple[float, float]] = {}
        for g in evaluation.groups:
            for name, axis in _axes(g).items():
                snapshot[(g.muscle_group_id, name)] = (_deficit(axis), _excess(axis))
        return groups, hits, snapshot

    def contributions_of(self, mapping_id: Optional[int]) -> dict[int, mv.GroupContribution]:
        """Bloco -> contribuicao do exercicio NO MODO das leituras: a juncao
        do motor (`methodology_volume.block_contributions`)."""
        if not mapping_id:
            return {}
        return mv.block_contributions(
            self.inputs.contributions.get(int(mapping_id)), self.inputs.counting_mode, self.block_of
        )

    def blocks_of(self, mapping_id: Optional[int]) -> frozenset[int]:
        """Blocos que o motor conta para o exercicio no modo (peso > 0)."""
        return frozenset(self.contributions_of(mapping_id))

    def is_primary_in(self, mapping_id: Optional[int], block: int) -> bool:
        contribution = self.contributions_of(mapping_id).get(block)
        return contribution is not None and contribution.is_primary

    def deficit(self, block: int, axis: str) -> float:
        return self.snapshot.get((block, axis), (0.0, 0.0))[0]

    def excess(self, block: int, axis: str) -> float:
        return self.snapshot.get((block, axis), (0.0, 0.0))[1]

    # ------------------------------------------------------------------
    # Laco principal
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._add_volume()
        self._reduce_volume()

    def _add_volume(self) -> None:
        for axis in (AXIS_SESSION, AXIS_WEEKLY):
            initial = {block: self._axis_state(block, axis) for block in sorted(self.groups) if self.deficit(block, axis) > _EPS}
            if not initial:
                continue
            self._preload_safety(initial)
            reasons: dict[int, set[str]] = {block: set() for block in initial}
            active = sorted(initial)
            while active and self.steps < MAX_STEPS:
                still: list[int] = []
                for block in active:
                    if self.deficit(block, axis) <= _EPS:
                        continue
                    ok, why = self._step(block, axis)
                    if not ok:
                        reasons[block] |= why
                        continue
                    self.steps += 1
                    if self.deficit(block, axis) > _EPS:
                        still.append(block)
                active = still
            for block, before in initial.items():
                if self.deficit(block, axis) <= _EPS:
                    continue
                after = self._axis_state(block, axis)
                group = self.groups[block]
                self.entries.append(
                    _Entry(
                        VOLUME_GAP_UNRESOLVED,
                        {
                            **_identity(group),
                            "axis": axis,
                            "min": after["min"],
                            "value_before": before["value"],
                            "value_after": after["value"],
                            "deficit_before": before["deficit"],
                            "deficit_after": after["deficit"],
                            "reasons": sorted(reasons[block]) or [REASON_RANGE_LIMIT],
                            "slots": self._window_slots(block),
                        },
                    )
                )

    def _axis_state(self, block: int, axis: str) -> dict[str, Any]:
        group = self.groups[block]
        evaluated = group.direct_sets_axis if axis == AXIS_SESSION else group.weekly_sets_axis
        value = evaluated.value_per_session if axis == AXIS_SESSION else evaluated.value_weekly
        return {"value": _round(value), "min": evaluated.range_min, "deficit": _round(self.deficit(block, axis))}

    def _preload_safety(self, blocks: Iterable[int]) -> None:
        """UMA leitura em lote da seguranca dos pares candidatos que a etapa de
        seguranca ainda nao leu (so os do catalogo da academia que trabalham
        algum grupo abaixo)."""
        if self.safety_loader is None:
            return
        wanted = set(blocks)
        missing = sorted(
            {
                (int(p["mapping_id"]), int(p["equipment_id"]))
                for p in self.pairs
                if self.blocks_of(p["mapping_id"]) & wanted
            }
            - set(self.safeties)
        )
        if missing:
            self.safeties.update(self.safety_loader(missing))

    # ------------------------------------------------------------------
    # Um passo: (a) -> (b) -> (c tecnica) -> (c series acima do ideal)
    # ------------------------------------------------------------------

    def _window_slots(self, block: int) -> list[int]:
        group = self.groups.get(block)
        if group is None:
            return []
        return [s.slot_sequence for s in group.sessions if self.window_hits.get(s.slot_sequence, 0) > 0]

    def _candidate_slots(self, block: int) -> list[Any]:
        """Slots da janela que ja trabalham o grupo como PRIMARY (exercicio
        direto: a frequencia do grupo nao muda), do mais fraco (menos series
        no grupo) para o mais forte; empate pela ordem do ciclo. No modo
        ponderado, slot so com secundario do grupo fica de fora."""
        group = self.groups[block]
        sessions = sorted(
            (
                s
                for s in group.sessions
                if self.window_hits.get(s.slot_sequence, 0) > 0 and s.direct_exercises > 0
            ),
            key=lambda s: (s.direct_sets, s.slot_sequence),
        )
        by_seq = {slot.sequence: slot for slot in self.template.slots}
        return [by_seq[s.slot_sequence] for s in sessions if s.slot_sequence in by_seq]

    def _group_items(self, slot: Any, block: int) -> list[Any]:
        """Itens do slot que contam para o bloco; os PRIMARY dele primeiro (no
        modo ponderado o secundario soma so uma fracao), depois pela ordem."""
        return sorted(
            (
                item
                for item in slot.items
                if item.item_kind == "equipment" and item.mapping_id and block in self.blocks_of(item.mapping_id)
            ),
            key=lambda item: (not self.is_primary_in(item.mapping_id, block), item.item_order),
        )

    def _step(self, block: int, axis: str) -> tuple[bool, set[str]]:
        reasons: set[str] = set()
        slots = self._candidate_slots(block)
        if not slots:
            return False, {REASON_NO_SLOT_IN_WINDOW}

        # (a) mais uma serie, dentro do ideal de ate 4 por exercicio.
        for slot in slots:
            items = sorted(
                (i for i in self._group_items(slot, block) if (i.sets_count or 0) < self.ideal),
                key=lambda i: (not self.is_primary_in(i.mapping_id, block), i.sets_count or 0, i.item_order),
            )
            for item in items:
                if self._try_sets(slot, item, block, axis, reasons, above_ideal=False):
                    return True, reasons

        # (b) exercicio novo do catalogo da academia.
        for slot in slots:
            if self._try_new_exercise(slot, block, axis, reasons):
                return True, reasons

        # (c) tecnica antes de passar de 4 series.
        for slot in slots:
            if self._try_technique(slot, block, axis, reasons):
                return True, reasons
        for slot in slots:
            items = sorted(
                (i for i in self._group_items(slot, block) if (i.sets_count or 0) >= self.ideal),
                key=lambda i: (not self.is_primary_in(i.mapping_id, block), i.sets_count or 0, i.item_order),
            )
            for item in items:
                if (item.sets_count or 0) >= MAX_SETS_PER_EXERCISE:
                    reasons.add(REASON_MAX_SETS_PER_EXERCISE)
                    continue
                if self._try_sets(slot, item, block, axis, reasons, above_ideal=True):
                    return True, reasons
        return False, reasons

    # --- acoes ---------------------------------------------------------

    def _attempt(self, slot: Any, apply: Callable[[], None], undo: Callable[[], None], block: int, axis: str, reasons: set[str]) -> bool:
        before_time = estimate_slot_seconds(slot.items, COMPLEMENT_DEFAULT_REST_SECONDS, self._item_rest)
        apply()
        after_time = estimate_slot_seconds(slot.items, COMPLEMENT_DEFAULT_REST_SECONDS, self._item_rest)
        if after_time > SLOT_TIME_LIMIT_SECONDS and after_time > before_time:
            undo()
            reasons.add(REASON_TIME_LIMIT)
            return False
        groups, hits, snapshot = self._measure()
        if not _acceptable(self.snapshot, snapshot, (block, axis)):
            undo()
            reasons.add(REASON_RANGE_LIMIT)
            return False
        self.groups, self.window_hits, self.snapshot = groups, hits, snapshot
        return True

    def _try_sets(self, slot: Any, item: Any, block: int, axis: str, reasons: set[str], *, above_ideal: bool) -> bool:
        before_sets = int(item.sets_count or 0)
        deficit_before = self.deficit(block, axis)

        def apply() -> None:
            item.sets_count = before_sets + 1

        def undo() -> None:
            item.sets_count = before_sets

        if not self._attempt(slot, apply, undo, block, axis, reasons):
            return False
        key = (slot.sequence, item.item_order)
        added = self.added_items.get(key)
        if added is not None and not above_ideal and added.fields["axis"] == axis:
            # Serie a mais num exercicio que o proprio complemento criou: a
            # entrada do exercicio novo passa a dizer o numero final.
            added.fields["sets"] = item.sets_count
            added.fields["deficit_after"] = _round(self.deficit(block, axis))
            return True
        code = VOLUME_SETS_ABOVE_IDEAL if above_ideal else VOLUME_SETS_ADDED
        self._record_item(code, slot, item, block, axis, deficit_before, before_sets, item.sets_count, extra={"ideal": self.ideal, "max_sets": MAX_SETS_PER_EXERCISE} if above_ideal else None)
        return True

    def _try_new_exercise(self, slot: Any, block: int, axis: str, reasons: set[str]) -> bool:
        exercises = sum(1 for item in slot.items if item.item_kind != "cardio")
        if exercises >= MAX_EXERCISES_PER_SLOT:
            reasons.add(REASON_EXERCISE_CAP)
            return False
        used_mappings = {int(i.mapping_id) for i in slot.items if i.mapping_id}
        used_canonicals = {
            c for c in (self.inputs.canonical_ids.get(m) for m in used_mappings) if c is not None
        }
        candidates = []
        for index, pair in enumerate(self.pairs):
            contributions = self.contributions_of(pair["mapping_id"])
            if block not in contributions:
                continue
            mapping_id = int(pair["mapping_id"])
            canonical = self.inputs.canonical_ids.get(mapping_id)
            if mapping_id in used_mappings or (canonical is not None and canonical in used_canonicals):
                continue
            # 0 = so este grupo (nao derrama volume em outro); 1 = PRIMARY dele
            # com outros grupos; 2 = so secundario dele (modo ponderado).
            if set(contributions) == {block}:
                rank = 0 if contributions[block].is_primary else 2
            else:
                rank = 1 if contributions[block].is_primary else 2
            candidates.append((rank, index, pair))
        if not candidates:
            reasons.add(REASON_NO_CATALOG_PAIR)
            return False
        candidates.sort(key=lambda c: (c[0], c[1]))

        reference = next(iter(self._group_items(slot, block)), None)
        deficit_before = self.deficit(block, axis)
        for exclusive, _index, pair in candidates:
            for sets in range(self.new_sets, 0, -1):
                item = self._new_item(slot, pair, sets, reference)

                def apply(item: Any = item) -> None:
                    slot.items.append(item)

                def undo() -> None:
                    slot.items.pop()

                if not self._attempt(slot, apply, undo, block, axis, reasons):
                    continue
                entry = self._record_item(
                    VOLUME_EXERCISE_ADDED,
                    slot,
                    item,
                    block,
                    axis,
                    deficit_before,
                    None,
                    None,
                    extra={"sets": sets, "exclusive_primary": exclusive == 0},
                )
                self.added_items[(slot.sequence, item.item_order)] = entry
                return True
        return False

    def _new_item(self, slot: Any, pair: Mapping[str, Any], sets: int, reference: Any) -> WorkoutPlanTemplateSlotItemIn:
        key = (int(pair["mapping_id"]), int(pair["equipment_id"]))
        rpe = getattr(reference, "rpe_target", None)
        safety = self.safeties.get(key)
        if rpe and safety is not None:
            from ..domain.effective_intensity import clamp_rpe_text

            rpe, _ = clamp_rpe_text(rpe, safety.max_safe_rpe)
        return WorkoutPlanTemplateSlotItemIn(
            item_order=len(slot.items) + 1,
            item_kind="equipment",
            muscle_group_id=int(pair["muscle_group_id"]),
            mapping_id=key[0],
            equipment_id=key[1],
            sets_count=sets,
            reps_target=getattr(reference, "reps_target", None),
            rpe_target=rpe,
            prioritize=False,
        )

    def _item_rest(self, item: Any) -> int:
        """Descanso default do item na conta de tempo: o do objetivo dele
        (override > objetivo do Modelo, M97 F5; `limits`)."""
        return complement_default_rest_seconds(item, getattr(self.template, "training_goal", None))

    def _goal_allows(self, item: Any, code: str) -> bool:
        """M97 F5: tecnica dentro da politica do objetivo do item (forca nao
        recebe drop set/rest-pause do complemento; `training_safety`)."""
        goal = resolve_goal(getattr(item, "training_goal", None), getattr(self.template, "training_goal", None))
        return ts.goal_allows_technique(code, goal)

    def _try_technique(self, slot: Any, block: int, axis: str, reasons: set[str]) -> bool:
        any_option = False
        for item in self._group_items(slot, block):
            if not (item.sets_count or 0) > 0:
                continue
            key = (slot.sequence, item.item_order)
            options: list[tuple[str, int]] = []
            if key in self.added_techniques:
                code = item.advanced_technique
                apps = int(item.advanced_technique_applications or 0)
                if planned_equivalent(code, apps + 1, self.catalog) > planned_equivalent(code, apps, self.catalog) + _EPS:
                    options.append((code, apps + 1))
            elif item.advanced_technique is None:
                safety = self.safeties.get((int(item.mapping_id), int(item.equipment_id)))
                if safety is not None:
                    for code in COMPLEMENT_TECHNIQUES:
                        if (
                            ts.validate_technique(code, safety).valid
                            and self._goal_allows(item, code)
                            and planned_equivalent(code, 1, self.catalog) > _EPS
                        ):
                            options.append((code, 1))
            for code, apps in options:
                any_option = True
                previous = (item.advanced_technique, item.advanced_technique_applications)
                deficit_before = self.deficit(block, axis)

                def apply(code: str = code, apps: int = apps) -> None:
                    item.advanced_technique = code
                    item.advanced_technique_applications = apps

                def undo(previous: tuple = previous) -> None:
                    item.advanced_technique, item.advanced_technique_applications = previous

                if not self._attempt(slot, apply, undo, block, axis, reasons):
                    continue
                self.added_techniques.add(key)
                entry = self._record_item(
                    VOLUME_TECHNIQUE_ADDED,
                    slot,
                    item,
                    block,
                    axis,
                    deficit_before,
                    None,
                    None,
                    extra={"technique": code},
                )
                entry.fields["applications"] = apps
                entry.fields["equivalent"] = _round(planned_equivalent(code, apps, self.catalog))
                entry.fields["deficit_after"] = _round(self.deficit(block, axis))
                return True
        if not any_option:
            reasons.add(REASON_NO_TECHNIQUE)
        return False

    # ------------------------------------------------------------------
    # Corte de excesso (2026-10-16): DEPOIS do acrescimo, nunca volta a ele
    # ------------------------------------------------------------------

    def _cuttable(self, block: int, axis: str) -> bool:
        """Eixo ACIMA da faixa cujo ABOVE o motor nao marca como esperado pela
        intencao (`_severity_for`: na especializacao, o alvo acima e
        `informational`)."""
        if self.excess(block, axis) <= _EPS:
            return False
        evaluated = _axes(self.groups[block]).get(axis)
        return evaluated is not None and evaluated.severity is not mv.MethodologySeverity.INFORMATIONAL

    def _reduce_volume(self) -> None:
        """Grupo ACIMA da faixa de series (por sessao, depois semanal) perde
        UMA serie por passo, um passo por grupo por rodada (ordem de id), ate
        entrar na faixa ou nao haver corte aceito.

        Candidatos: itens de equipamento dos slots da janela que contam para o
        grupo, com mais series que o piso (`_sets_floor_for_reduction`) e que
        o acrescimo NAO tocou (nao desfaz o que acabou de somar). Ordem: nao
        priorizados antes dos priorizados (`prioritize` so e cortado sem
        alternativa), PRIMARY do grupo antes de so-secundario, maior peso no
        grupo, mais series, ordem do ciclo e do slot.

        Trava de cada corte, no MOTOR REAL: o excesso do alvo diminui; nenhum
        eixo de nenhum grupo fica mais acima nem mais abaixo da faixa do que
        estava (um grupo WITHIN nao cai para BELOW; o secundario cortado nao
        quebra o primario do item). Termina: cada passo aceito tira uma serie e
        o piso limita os passos. Teto RIGIDO de custo: no maximo
        `COMPLEMENT_REDUCTION_MAX_EVALUATIONS` avaliacoes do motor por chamada,
        contando cortes aceitos e rejeitados, num orcamento proprio (o
        acrescimo gasta o dele, `MAX_STEPS`). Estourou: o corte para onde
        esta, e o excesso que sobrar continua como `METHODOLOGY_*ABOVE_RANGE`."""
        for axis in (AXIS_SESSION, AXIS_WEEKLY):
            active = [block for block in sorted(self.groups) if self._cuttable(block, axis)]
            while active and not self.cut_budget_exhausted:
                still: list[int] = []
                for block in active:
                    if not self._cuttable(block, axis):
                        continue
                    if not self._cut_step(block, axis):
                        continue
                    if self._cuttable(block, axis):
                        still.append(block)
                active = still

    def _touched_by_addition(self) -> set[tuple[int, int]]:
        touched = set(self.added_items) | set(self.added_techniques)
        touched |= {(key[1], key[2]) for key in self.item_entries if key[0] != VOLUME_REDUCED}
        return touched

    def _cut_candidates(self, block: int) -> list[tuple[Any, Any, bool]]:
        touched = self._touched_by_addition()
        candidates = []
        for slot in self.template.slots:
            if self.window_hits.get(slot.sequence, 0) <= 0:
                continue
            for item in slot.items:
                if item.item_kind != "equipment" or not item.mapping_id:
                    continue
                if (item.sets_count or 0) <= self.floor or (slot.sequence, item.item_order) in touched:
                    continue
                contribution = self.contributions_of(item.mapping_id).get(block)
                if contribution is None:
                    continue
                key = (
                    bool(item.prioritize),
                    not contribution.is_primary,
                    -float(contribution.weight),
                    -int(item.sets_count or 0),
                    slot.sequence,
                    item.item_order,
                )
                candidates.append((key, slot, item, contribution.is_primary))
        candidates.sort(key=lambda c: c[0])
        return [(slot, item, primary) for _key, slot, item, primary in candidates]

    def _cut_step(self, block: int, axis: str) -> bool:
        for slot, item, primary in self._cut_candidates(block):
            if self.cut_evaluations >= COMPLEMENT_REDUCTION_MAX_EVALUATIONS:
                self.cut_budget_exhausted = True
                return False
            self.cut_evaluations += 1
            before_sets = int(item.sets_count or 0)
            excess_before = self.excess(block, axis)
            item.sets_count = before_sets - 1
            groups, hits, snapshot = self._measure()
            if not _acceptable_cut(self.snapshot, snapshot, (block, axis)):
                item.sets_count = before_sets
                continue
            self.groups, self.window_hits, self.snapshot = groups, hits, snapshot
            self._record_cut(slot, item, block, axis, excess_before, before_sets, primary)
            return True
        return False

    def _record_cut(self, slot: Any, item: Any, block: int, axis: str, excess_before: float, sets_from: int, primary: bool) -> None:
        """Uma entrada por (slot, item, grupo, eixo): cortes seguidos no mesmo
        item viram uma entrada so, com o `from` do primeiro e o `to` do ultimo."""
        key = (VOLUME_REDUCED, slot.sequence, item.item_order, block, axis)
        entry = self.item_entries.get(key)
        if entry is not None:
            entry.fields["to"] = item.sets_count
            entry.fields["excess_after"] = _round(self.excess(block, axis))
            return
        pair = self.names.get((int(item.mapping_id), int(item.equipment_id))) or {}
        group = self.groups[block]
        evaluated = _axes(group)[axis]
        entry = _Entry(
            VOLUME_REDUCED,
            {
                **_identity(group),
                "axis": axis,
                "slot": slot.sequence,
                "slot_name": slot.name,
                "item": item.item_order,
                "mapping_id": int(item.mapping_id),
                "equipment_id": int(item.equipment_id),
                "exercise_name": pair.get("exercise_name"),
                "equipment_name": pair.get("equipment_name"),
                "max": evaluated.range_max,
                "from": sets_from,
                "to": item.sets_count,
                "excess_before": _round(excess_before),
                "excess_after": _round(self.excess(block, axis)),
                "min_sets": self.floor,
                "prioritized": bool(item.prioritize),
                "is_primary": primary,
            },
        )
        self.entries.append(entry)
        self.item_entries[key] = entry

    # --- auditoria -----------------------------------------------------

    def _record_item(
        self,
        code: str,
        slot: Any,
        item: Any,
        block: int,
        axis: str,
        deficit_before: float,
        sets_from: Optional[int],
        sets_to: Optional[int],
        extra: Optional[dict[str, Any]] = None,
    ) -> _Entry:
        """Uma entrada por (codigo, slot, item, grupo): passos seguidos no mesmo
        item viram uma entrada so, com o `from` do primeiro e o `to`/deficit
        depois do ultimo."""
        key = (code, slot.sequence, item.item_order, block, axis)
        deficit_after = _round(self.deficit(block, axis))
        entry = self.item_entries.get(key)
        if entry is not None:
            if "to" in entry.fields:
                entry.fields["to"] = sets_to
            entry.fields["deficit_after"] = deficit_after
            return entry
        pair = self.names.get((int(item.mapping_id), int(item.equipment_id))) or {}
        group = self.groups[block]
        evaluated = group.direct_sets_axis if axis == AXIS_SESSION else group.weekly_sets_axis
        fields: dict[str, Any] = {
            **_identity(group),
            "axis": axis,
            "slot": slot.sequence,
            "slot_name": slot.name,
            "item": item.item_order,
            "mapping_id": int(item.mapping_id),
            "equipment_id": int(item.equipment_id),
            "exercise_name": pair.get("exercise_name"),
            "equipment_name": pair.get("equipment_name"),
            "min": evaluated.range_min,
            "deficit_before": _round(deficit_before),
            "deficit_after": deficit_after,
        }
        if sets_from is not None or sets_to is not None:
            fields["from"] = sets_from
            fields["to"] = sets_to
        if extra:
            fields.update(extra)
        entry = _Entry(code, fields)
        self.entries.append(entry)
        self.item_entries[key] = entry
        return entry


# ---------------------------------------------------------------------------
# Auxiliares puros
# ---------------------------------------------------------------------------


def _axes(group: mv.GroupEvaluation) -> dict[str, mv.AxisEvaluation]:
    return {
        AXIS_SESSION: group.direct_sets_axis,
        AXIS_WEEKLY: group.weekly_sets_axis,
        _AXIS_EXERCISES: group.direct_exercises_axis,
    }


def _value(axis: mv.AxisEvaluation) -> Optional[float]:
    return axis.value_weekly if axis.value_weekly is not None else axis.value_per_session


def _deficit(axis: mv.AxisEvaluation) -> float:
    if axis.status is not mv.MethodologyVolumeStatus.BELOW_RANGE or axis.range_min is None:
        return 0.0
    return max(0.0, float(axis.range_min) - float(_value(axis) or 0.0))


def _excess(axis: mv.AxisEvaluation) -> float:
    if axis.status is not mv.MethodologyVolumeStatus.ABOVE_RANGE or axis.range_max is None:
        return 0.0
    return max(0.0, float(_value(axis) or 0.0) - float(axis.range_max))


def _acceptable(
    before: Mapping[tuple[int, str], tuple[float, float]],
    after: Mapping[tuple[int, str], tuple[float, float]],
    target: tuple[int, str],
) -> bool:
    """A acao reduz o deficit do alvo, nao poe (nem aumenta) nenhum eixo acima
    da faixa e nao deixa nenhum outro eixo mais abaixo do que estava."""
    if after.get(target, (0.0, 0.0))[0] >= before.get(target, (0.0, 0.0))[0] - _EPS:
        return False
    for key in set(before) | set(after):
        deficit_before, excess_before = before.get(key, (0.0, 0.0))
        deficit_after, excess_after = after.get(key, (0.0, 0.0))
        if excess_after > excess_before + _EPS:
            return False
        if key != target and deficit_after > deficit_before + _EPS:
            return False
    return True


def _acceptable_cut(
    before: Mapping[tuple[int, str], tuple[float, float]],
    after: Mapping[tuple[int, str], tuple[float, float]],
    target: tuple[int, str],
) -> bool:
    """Espelho de `_acceptable` para o corte: reduz o EXCESSO do alvo, nao
    deixa nenhum eixo (inclusive o do alvo) mais abaixo da faixa e nao poe
    (nem aumenta) nenhum outro eixo acima - a frequencia de um secundario pode
    mudar com o corte e mexer na celula da faixa."""
    if after.get(target, (0.0, 0.0))[1] >= before.get(target, (0.0, 0.0))[1] - _EPS:
        return False
    for key in set(before) | set(after):
        deficit_before, excess_before = before.get(key, (0.0, 0.0))
        deficit_after, excess_after = after.get(key, (0.0, 0.0))
        if deficit_after > deficit_before + _EPS:
            return False
        if key != target and excess_after > excess_before + _EPS:
            return False
    return True


def _identity(group: mv.GroupEvaluation) -> dict[str, Any]:
    return {
        "muscle_group_id": group.muscle_group_id,
        "muscle_group_name": group.muscle_group_name,
        "volume_class_id": group.volume_class_id,
        "volume_class_name": group.volume_class_name,
    }


def _round(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    rounded = round(float(value), 2)
    return int(rounded) if rounded == int(rounded) else rounded


__all__ = [
    "COMPLEMENT_TECHNIQUES",
    "ComplementResult",
    "COMPLEMENT_DEFAULT_REST_SECONDS",
    "MAX_EXERCISES_PER_SLOT",
    "MAX_SETS_PER_EXERCISE",
    "SLOT_TIME_LIMIT_SECONDS",
    "VOLUME_EXERCISE_ADDED",
    "VOLUME_GAP_UNRESOLVED",
    "VOLUME_REDUCED",
    "VOLUME_SETS_ABOVE_IDEAL",
    "VOLUME_SETS_ADDED",
    "VOLUME_TECHNIQUE_ADDED",
    "complement_volume",
]
