"""Validacao DETERMINISTICA do rascunho gerado pela LLM (PLANO_20260912.md
secao 7.1, passo 7). O coracao do PR-16.

Cascata, na ordem - cada etapa so roda se a anterior nao produziu erro que
impeca a seguinte:

1. **schema**: o JSON e relido pelo espelho pydantic (`PeriodizationOutput`).
   Fora do formato -> `SCHEMA_INVALID`, e nao ha nada mais a validar.
   1a. `0` em campo opcional com minimo 1 -> `null` (`OPTIONAL_ZERO_NULLED`).
   Qualquer `ValidationError` das etapas seguintes (a releitura no
   `WorkoutPlanTemplateIn`) tambem vira `SCHEMA_INVALID` no relatorio: nada
   escapa de `validate_draft` como excecao depois da geracao paga.
2. **referencial** (banco): `muscle_group_id` existe; no Modo B o trio
   (muscle_group_id, mapping_id, equipment_id) precisa ser uma associacao ATIVA
   da academia pedida (`gym_equipment.is_active = 1`). Tambem checa o pedido:
   `volume_mode` diferente do solicitado e erro.
3. **regras de modelo**: o MESMO `_validate_template_payload` do
   `POST /workout-plan-templates` (sequencias 1..N, descanso sem itens, maximo 3
   priorizados, faixas 0-100, exigencias de cada `item_kind`). Uma unica fonte
   de verdade: se o save aceita, o rascunho aceita; se o save recusa, o rascunho
   nao chega ao usuario com erro escondido.
4. **projecao de ativacao vs metas**: `preview_activation` (que reusa
   `_compute_slot_activation` **com o fix de ciclo > 7 do PR-13**) para o Modo B,
   e a soma de percentuais (`validate_template_volume_coverage`) para o Modo A.
   Sempre WARNING, nunca erro: meta desalinhada e assunto para o humano decidir.
5. **metodologia de volume por grupo** (Modo B, COM ou SEM perfil de metas):
   o MESMO motor da Fase 4 (`intelligence.methodology_volume`, faixas
   do cadastro de macro-grupos x frequencia, so trabalho PRIMARIO - EXG-014).
   Grupo com series diretas por sessao fora da faixa -> `METHODOLOGY_BELOW_RANGE`
   / `METHODOLOGY_ABOVE_RANGE`; fora da meta SEMANAL do macro-grupo (Fase 6)
   -> `METHODOLOGY_WEEKLY_BELOW_RANGE` / `METHODOLOGY_WEEKLY_ABOVE_RANGE`;
   slot de treino com menos de
   `SESSION_EXERCISES_MIN` itens -> `SLOT_TOO_FEW_EXERCISES`. Sempre WARNING.
   E o que o builder mostra depois no diagnostico de volume, entao o numero do
   rascunho e o numero da tela sao o mesmo.
6. **seguranca**: `training_safety` por par (exercicio, equipamento). RPE acima
   do teto do par -> clamp; tecnica invalida naquele par -> remocao.
7. **complemento de volume** (M46, Modo B): DEPOIS da seguranca e ANTES da
   metodologia, grupo abaixo da faixa ganha serie, exercicio do catalogo da
   academia ou drop set / rest-pause, nos slots da janela de 7 dias, dentro do
   teto de 9 exercicios e de 90 min por slot - ver `volume_complement.py`. A
   projecao de ativacao e a metodologia rodam sobre o resultado complementado.

Auto-fix so DETERMINISTICO. Desde 2026-09-28 (medicao do prompt v3: 9
tentativas, 3 sucessos) ele cobre tambem o que antes matava o job inteiro
mesmo com a geracao boa - trio fora do `gym_catalog` trocado pelo par mais
proximo do MESMO grupo DENTRO da academia (`EQUIPMENT_REPLACED`), numero de
slots ajustado ao `days_per_cycle` pedido (`SLOT_REMOVED`/`SLOT_ADDED`),
`sequence` renumerado, descanso com exercicio virando dia normal e excesso de
`prioritize` desmarcado. Desde 2026-10-16 tambem o rotulo de grupo de um par
ativo que nao bate com o grupo sob o qual o catalogo o lista
(`MUSCLE_GROUP_RELABELED`) e, no complemento, o corte de series de grupo acima
da faixa (`VOLUME_REDUCED`). Nada disso inventa exercicio, serie ou academia: o par
substituto sai da MESMA leitura de `gym_equipment` que valida os outros, entao
um rascunho aceito continua impossivel de conter equipamento que a academia nao
tem, e cada troca fica auditavel (`de -> para`) no relatorio. Nunca "melhorar" a
divisao, nunca inventar item, nunca chamar a LLM de novo - sem retry automatico
(custo e o teto de 120 s da Lambda).

Saida: `(draft, report)`, com `report = {errors, warnings, auto_fixes}`.
`errors` nao vazio -> o job termina `failed` COM o relatorio (nada e gravado em
`workout_plan_templates` em nenhum cenario - isso e o PR-17, disparado pelo
usuario).
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from fastapi import HTTPException
from pydantic import ValidationError

from ..domain import effective_intensity
from ..domain import training_safety as ts
from ..intelligence.methodology_volume import DEFAULT_VOLUME_COUNTING_MODE
from ..domain.set_measures import DEFAULT_MEASURE
from ..models import PeriodizationDraftIn, WorkoutPlanTemplateIn, WorkoutPlanTemplateSlotIn
from .activation_projection import (  # noqa: F401 - codigos reexportados (contrato do PR-17)
    ACTIVATION_ABOVE_MAX,
    ACTIVATION_BELOW_MIN,
    ACTIVATION_COVERAGE,
    ActivationProjectionInputs,
    activation_target_warnings,
    coverage_warnings,
    project_activation,
)
from .context import request_counting_mode
from .limits import MAX_PRIORITIZED_PER_SLOT, SESSION_EXERCISES_MIN  # noqa: F401 - reexportados
from . import cardio_plan, goal_plan
from .limits import heavy_compound_range, session_exercises_min, slot_session_goal
from .output_schema import PeriodizationOutput
from .volume_complement import (  # noqa: F401 - codigos reexportados (contrato com o frontend)
    VOLUME_EXERCISE_ADDED,
    VOLUME_GAP_UNRESOLVED,
    VOLUME_REDUCED,
    VOLUME_SETS_ABOVE_IDEAL,
    VOLUME_SETS_ADDED,
    VOLUME_TECHNIQUE_ADDED,
    complement_volume,
)

# Codigos do relatorio (contrato com o frontend do PR-17).
SCHEMA_INVALID = "SCHEMA_INVALID"
VOLUME_MODE_MISMATCH = "VOLUME_MODE_MISMATCH"
MUSCLE_GROUP_NOT_FOUND = "MUSCLE_GROUP_NOT_FOUND"
EQUIPMENT_NOT_IN_GYM = "EQUIPMENT_NOT_IN_GYM"
GYM_REQUIRED = "GYM_REQUIRED"
TEMPLATE_INVALID = "TEMPLATE_INVALID"
SLOT_COUNT_MISMATCH = "SLOT_COUNT_MISMATCH"
# ACTIVATION_BELOW_MIN / ACTIVATION_ABOVE_MAX / ACTIVATION_COVERAGE vivem em
# `activation_projection.py` (modulo puro do PR D) e sao reexportados daqui.
RPE_CLAMPED = "RPE_CLAMPED"
TECHNIQUE_REMOVED = "TECHNIQUE_REMOVED"
# Metodologia de volume por grupo (Fase 4) aplicada ao rascunho - Modo B,
# independe de perfil de metas. Payload: muscle_group_id, muscle_group_name,
# volume_class_id, volume_class_name (macro-grupo, Fase 6), size_class
# (deprecated, so ecoado), frequency_bucket, frequency, projected_sets (series
# diretas POR SESSAO, media), min, max (faixa por sessao) e evidence.
METHODOLOGY_BELOW_RANGE = "METHODOLOGY_BELOW_RANGE"
METHODOLOGY_ABOVE_RANGE = "METHODOLOGY_ABOVE_RANGE"
# Fase 6 (PR 2): eixo SEMANAL do macro-grupo. Payload: muscle_group_id,
# muscle_group_name, volume_class_id, volume_class_name, size_class, frequency,
# weekly_sets (series diretas da janela de 7 dias do motor v3), min, max (meta
# semanal) e evidence.
METHODOLOGY_WEEKLY_BELOW_RANGE = "METHODOLOGY_WEEKLY_BELOW_RANGE"
METHODOLOGY_WEEKLY_ABOVE_RANGE = "METHODOLOGY_WEEKLY_ABOVE_RANGE"
# Slot 'normal' com menos itens que uma sessao completa (`SESSION_EXERCISES_MIN`).
SLOT_TOO_FEW_EXERCISES = "SLOT_TOO_FEW_EXERCISES"

# Auto-fixes ESTRUTURAIS (2026-09-28). A medicao do prompt v3 mostrou o modelo
# entregando volume bom e, ao mesmo tempo, violando regra dura (trio fora do
# catalogo da academia, numero de slots diferente do pedido, mais de 3
# priorizados no slot). Antes disso o job inteiro virava `failed` e o usuario
# nao recebia rascunho nenhum: o trabalho bom era descartado junto com o erro.
# Agora cada violacao deterministicamente consertavel vira auto-fix AUDITAVEL
# (o `de -> para` fica no relatorio), e so o que NAO tem conserto deterministico
# continua erro.
EQUIPMENT_REPLACED = "EQUIPMENT_REPLACED"
# Par ativo da academia com `muscle_group_id` diferente do grupo sob o qual o
# catalogo o lista: o rotulo vira o do catalogo (2026-10-16). Payload: slot,
# slot_name, item, mapping_id, equipment_id, exercise_name, equipment_name,
# from / from_name (grupo da LLM), to / to_name (grupo do catalogo).
MUSCLE_GROUP_RELABELED = "MUSCLE_GROUP_RELABELED"
# 0 em campo opcional com minimo 1 (`OPTIONAL_POSITIVE_ITEM_FIELDS`) -> null
# (2026-10-16). Payload: slot, slot_name, item, fields (os campos zerados).
OPTIONAL_ZERO_NULLED = "OPTIONAL_ZERO_NULLED"
SLOT_REMOVED = "SLOT_REMOVED"
SLOT_ADDED = "SLOT_ADDED"
SLOT_SEQUENCE_RENUMBERED = "SLOT_SEQUENCE_RENUMBERED"
SLOT_TYPE_FIXED = "SLOT_TYPE_FIXED"
PRIORITIZE_UNMARKED = "PRIORITIZE_UNMARKED"
SETS_COUNT_FIXED = "SETS_COUNT_FIXED"
ITEM_REMOVED = "ITEM_REMOVED"
# Fase 5 (PR 5): FST-7/GVT (`default_sets_count` no catalogo) so preenchem o
# numero de series - aplicacoes e pausa vindas da LLM sao zeradas, com o mesmo
# helper do save do Modelo. Payload: slot, item, technique, applications,
# duration_seconds (os valores ANTERIORES).
TECHNIQUE_FIELDS_CLEARED = "TECHNIQUE_FIELDS_CLEARED"
# M97 F5 (objetivo por dia, `goal_plan.py`). Auto-fixes auditados:
# - isolador num dia de objetivo cujo isolador fica em outro objetivo (força ->
#   hipertrofia, `limits.day_item_goal`). Payload: slot, slot_name, item,
#   mapping_id, day_goal, training_goal (o do item), override (o gravado);
# - alvo de reps fora da faixa do objetivo do item -> a faixa do objetivo
#   (`prescription_defaults.reps_target_fits_goal`). Payload: slot, item,
#   training_goal, from, to.
# Técnica fora da política do objetivo reusa `TECHNIQUE_REMOVED` (reason
# `TECHNIQUE_NOT_ALLOWED_FOR_GOAL`) e RPE acima do teto do objetivo reusa
# `RPE_CLAMPED` (reason `TRAINING_GOAL`), os códigos de `training_safety`.
ISOLATION_GOAL_OVERRIDE = "ISOLATION_GOAL_OVERRIDE"
GOAL_REPS_FIXED = "GOAL_REPS_FIXED"
# Aviso: dia de objetivo com faixa de compostos pesados (`limits.
# heavy_compound_range`) fora dela. Payload: slot, slot_name, training_goal,
# compound_range, min, max.
DAY_HEAVY_COMPOUNDS_OUT_OF_RANGE = "DAY_HEAVY_COMPOUNDS_OUT_OF_RANGE"

# Teto de itens priorizados por slot (`MAX_PRIORITIZED_PER_SLOT`): o MESMO de
# `_validate_template_payload` e do prompt - dono em `limits.py` (M76, E9).

class ValidationReport:
    """Acumulador das tres listas do relatorio."""

    def __init__(self) -> None:
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.auto_fixes: list[dict[str, Any]] = []

    def error(self, code: str, **fields: Any) -> None:
        self.errors.append({"code": code, **fields})

    def warning(self, code: str, **fields: Any) -> None:
        self.warnings.append({"code": code, **fields})

    def auto_fix(self, code: str, **fields: Any) -> None:
        self.auto_fixes.append({"code": code, **fields})

    def to_dict(self) -> dict[str, Any]:
        return {"errors": self.errors, "warnings": self.warnings, "auto_fixes": self.auto_fixes}


def validate_draft(
    conn: Any,
    request: PeriodizationDraftIn,
    raw_output: dict[str, Any],
    *,
    cardio: Optional[Mapping[str, Any]] = None,
) -> tuple[Optional[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Valida a saida crua da LLM.

    Devolve `(draft, report, extras)`:
    - `draft`: dict no formato `WorkoutPlanTemplateIn` (o que o PR-17 vai
      pre-preencher no builder), ou `None` se o schema nao passou;
    - `report`: `{errors, warnings, auto_fixes}`;
    - `extras`: `{rationale, source_citations}` - o que a LLM explicou, para a UI
      mostrar ao lado do rascunho.

    `cardio` (M98 C5): o bloco `cardio` do contexto (`cardio_plan.
    build_cardio_context`); None = Modelo sem cardio pedido, e qualquer
    cardio da saida e removido com registro.
    """
    report = ValidationReport()

    # 1. schema
    try:
        parsed = PeriodizationOutput(**raw_output)
    except ValidationError as exc:
        report.error(SCHEMA_INVALID, message=_first_schema_error(exc))
        return None, report.to_dict(), {"rationale": "", "source_citations": []}

    extras = {"rationale": parsed.rationale, "source_citations": list(parsed.source_citations)}
    # 1a. zero em campo opcional "ausente ou >= 1" -> null (auto-fix), ANTES
    # de reler no `WorkoutPlanTemplateIn`, que recusaria o rascunho inteiro.
    _null_optional_zeros(parsed, report)
    try:
        return _validate_parsed(conn, request, parsed, report, extras, cardio)
    except ValidationError as exc:
        # Nenhum `ValidationError` sai daqui: a geracao paga ja aconteceu, e o
        # job termina `failed` COM o relatorio, nunca em excecao.
        report.error(SCHEMA_INVALID, message=_first_schema_error(exc))
        return None, report.to_dict(), extras


def _validate_parsed(
    conn: Any,
    request: PeriodizationDraftIn,
    parsed: PeriodizationOutput,
    report: ValidationReport,
    extras: dict[str, Any],
    cardio: Optional[Mapping[str, Any]] = None,
) -> tuple[Optional[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Etapas 1b em diante, sobre a saida ja relida pelo schema."""
    template = _to_template_in(parsed, request)
    # M98 C5: o cardio da LLM fica de lado ate o fim da cascata de forca (a
    # chave e o objeto do slot: o auto-fix estrutural remove/renumera slots).
    cardio_raw = {
        id(slot): (raw.sequence, raw.name, [item.model_dump() for item in raw.cardio_items])
        for slot, raw in zip(template.slots, parsed.template.slots)
    }
    template = _validate_strength(conn, request, template, report)
    # 9. cardio (M98 C5): dono da prescricao + envelope do Modelo; os itens
    # entram no FIM de cada slot, depois de tudo que mede a forca.
    cardio_plan.apply_cardio(template, cardio_raw, cardio, report.auto_fix)
    return template.model_dump(mode="json"), report.to_dict(), extras


def _validate_strength(
    conn: Any, request: PeriodizationDraftIn, template: WorkoutPlanTemplateIn, report: ValidationReport
) -> WorkoutPlanTemplateIn:
    """Cascata da forca (etapas 1b a 8), a mesma da v12."""
    if template.volume_mode != request.volume_mode:
        report.error(VOLUME_MODE_MISMATCH, expected=request.volume_mode, received=template.volume_mode)
    if len(template.slots) != request.days_per_cycle:
        # Quantidade de dias e pedido do usuario, nao metodologia: avisa e segue
        # (o auto-fix estrutural abaixo ajusta a quantidade; o aviso registra o
        # que a LLM tinha respondido).
        report.warning(SLOT_COUNT_MISMATCH, expected=request.days_per_cycle, received=len(template.slots))

    # 1b. auto-fix estrutural: tudo que `_validate_template_payload` recusaria e
    # tem conserto deterministico (ANTES do referencial, para que a troca de par
    # ja enxergue o conjunto final de slots).
    _apply_structural_auto_fixes(template, request, report)
    _suggest_grid(template)

    # 2. referencial (devolve os pares ativos da academia, que o complemento
    # de volume reaproveita sem nova leitura)
    gym_pairs = _check_references(conn, template, request, report)

    # 3. regras do save de modelo (uma unica fonte de verdade)
    _check_template_rules(template, report)

    # 3b. M97 F5: objetivo por dia (override por item, tecnica/RPE/reps do
    # objetivo). So com objetivo nao-default no pedido: sem ele, nada muda.
    compound_flags = _apply_goal_plan(conn, template, request, report, gym_pairs)

    if request.volume_mode != "equipment_series":
        # 4. projecao de ativacao vs metas (sempre warning)
        _check_activation(template, request, report)
        return template

    # 5. seguranca: clamp de RPE e remocao de tecnica invalida (auto-fix).
    # ANTES da metodologia desde a Fase 5 (PR 5): a tecnica soma series
    # equivalentes, e uma tecnica removida aqui nao pode contar la.
    safeties = _apply_safety_auto_fixes(conn, template, report)
    # Modo de contagem do volume do Modelo a gerar (Lote 5): o do pedido,
    # senao o default do motor. Complemento e metodologia usam o MESMO.
    counting_mode = request_counting_mode(request)
    # 6. complemento deterministico de volume (M46). Devolve as leituras da
    # metodologia que ele ja fez, para a etapa 8 nao ler de novo.
    methodology_inputs = _apply_volume_complement(conn, template, gym_pairs, safeties, report, counting_mode)
    # 6b. M97 F5: o exercicio que o complemento acrescentou ganha o objetivo do
    # dia (a passada e idempotente: item ja ajustado nao muda) e o dia de
    # objetivo confere a faixa de compostos pesados.
    if compound_flags is not None:
        _apply_goal_plan(conn, template, request, report, gym_pairs, compound_flags, final=True)
    # 7. projecao de ativacao vs metas (sempre warning) - sobre o rascunho ja
    # complementado, para o numero do aviso ser o do rascunho entregue.
    _check_activation(template, request, report)
    # 8. metodologia de volume por grupo e tamanho da sessao (sempre warning)
    _check_methodology(template, report, methodology_inputs, counting_mode, request)
    return template


# ---------------------------------------------------------------------------
# Etapas
# ---------------------------------------------------------------------------


# Campos do item que `_to_template_in` sempre zera (decisao do profissional,
# nao da LLM): o que a LLM mandar neles nao importa, nem para o auto-fix.
_FORCED_NULL_ITEM_FIELDS: tuple[str, ...] = ("rest_seconds",)


def _optional_positive_item_fields() -> tuple[str, ...]:
    """Campos OPCIONAIS do item do Modelo com piso >= 1 (`Ge`/`Gt` no
    `WorkoutPlanTemplateSlotItemIn`) que a saida da LLM tambem tem - derivados
    do schema, nao escritos a mao. Neles "nenhum" e `null`; 0 e recusado."""
    import types
    import typing

    from annotated_types import Ge, Gt

    from ..models import WorkoutPlanTemplateSlotItemIn
    from .output_schema import PeriodizationItemOut

    out = []
    for name, info in WorkoutPlanTemplateSlotItemIn.model_fields.items():
        if info.is_required() or name in _FORCED_NULL_ITEM_FIELDS or name not in PeriodizationItemOut.model_fields:
            continue
        args = typing.get_args(info.annotation)
        if type(None) not in args and not isinstance(info.annotation, types.UnionType):
            continue
        if any((isinstance(m, Ge) and m.ge > 0) or (isinstance(m, Gt) and m.gt >= 0) for m in info.metadata):
            out.append(name)
    return tuple(out)


OPTIONAL_POSITIVE_ITEM_FIELDS: tuple[str, ...] = _optional_positive_item_fields()


def _null_optional_zeros(parsed: PeriodizationOutput, report: ValidationReport) -> None:
    """`0` em campo opcional cujo minimo e 1 (aquecimento, feeler, pausa,
    aplicacoes/duracao da tecnica) -> `null`: na saida da LLM, 0 e "nenhum".
    Eval de 2026-10-16 (rodada 3 do caso de 4 dias): sem isso o
    `WorkoutPlanTemplateIn` recusava o rascunho inteiro e o job terminava em
    excecao depois da geracao paga. Uma entrada por item, com os campos."""
    for slot in parsed.template.slots:
        for order, item in enumerate(slot.items, start=1):
            fields = [name for name in OPTIONAL_POSITIVE_ITEM_FIELDS if getattr(item, name) == 0]
            if not fields:
                continue
            for name in fields:
                setattr(item, name, None)
            report.auto_fix(OPTIONAL_ZERO_NULLED, slot=slot.sequence, slot_name=slot.name, item=order, fields=fields)


def _to_template_in(parsed: PeriodizationOutput, request: PeriodizationDraftIn) -> WorkoutPlanTemplateIn:
    """Saida da LLM -> `WorkoutPlanTemplateIn`, o MESMO modelo do
    `POST /workout-plan-templates`. `item_order` vem da posicao no array (a LLM
    nao numera itens) e `training_type_id` vem do PEDIDO, nunca da LLM: o perfil
    de metas foi escolhido pelo usuario no modal."""
    slots = []
    for slot in parsed.template.slots:
        items = []
        for order, item in enumerate(slot.items, start=1):
            data = item.model_dump()
            data["item_order"] = order
            # Descanso por exercicio (Fase 7, Frente B) e decisao do
            # profissional, nao da LLM: o prompt pede null e aqui se garante -
            # um valor fora de 1-600 ou num item de grupo muscular recusaria o
            # Modelo inteiro.
            for name in _FORCED_NULL_ITEM_FIELDS:
                data[name] = None
            # Medida da serie (Lote T2): a geracao por IA so produz itens por
            # repeticoes - prompt e schema da saida nao falam de medida; o
            # rascunho grava o default do dono e nunca campo de tempo.
            data["measure"] = DEFAULT_MEASURE.value
            items.append(data)
        slots.append(
            {
                "sequence": slot.sequence,
                "name": slot.name,
                "slot_type": slot.slot_type,
                "weekday": slot.weekday,
                "items": items,
            }
        )
    return WorkoutPlanTemplateIn(
        name=parsed.template.name,
        description=parsed.template.description,
        training_type_id=request.training_type_id,
        volume_mode=parsed.template.volume_mode,
        # Modo de contagem do PEDIDO (Lote 5): o rascunho salvo nasce no modo
        # em que foi gerado e validado. Pedido sem modo = None, o default na
        # criacao (rascunho igual ao de antes).
        volume_counting_mode=request.volume_counting_mode,
        # M97 F5: objetivo do Modelo do PEDIDO (ausente = None, o default na
        # criacao: rascunho igual ao de antes).
        training_goal=getattr(request, "training_goal", None),
        slots=slots,
    )


def _apply_structural_auto_fixes(
    template: WorkoutPlanTemplateIn, request: PeriodizationDraftIn, report: ValidationReport
) -> None:
    """Conserto DETERMINISTICO do que `_validate_template_payload` recusaria por
    forma - nada aqui inventa exercicio, serie ou grupo muscular.

    Na ordem:
    1. slots a mais -> remove do fim, tirando primeiro os de DESCANSO (o dia de
       treino carrega o trabalho da geracao; a folga nao);
    2. slots a menos -> completa com DESCANSO (dia vazio, sem conteudo
       inventado), preservando o ciclo que o usuario pediu;
    3. `sequence` fora de 1..N (duplicado, furado ou fora de ordem) ->
       renumeracao pela posicao;
    4. slot de descanso com item que nao e cardio -> vira dia NORMAL (o conteudo
       prescrito manda; apagar exercicio seria jogar trabalho fora);
    4b. item INUTILIZAVEL (item de equipamento sem o trio, item de grupo sem
       grupo, item de grupo no Modo B) -> removido, e so ele: um item que nao
       prescreve nada nao tem conserto deterministico, mas tambem nao pode levar
       junto os outros oito exercicios do dia;
    5. mais de 3 priorizados no slot -> desmarca do 4o em diante, na ordem dos
       itens;
    6. item de equipamento sem `sets_count` valido -> minimo da metodologia.

    Cada conserto entra em `auto_fixes` com o `de -> para`: o profissional ve na
    tela o que o backend mexeu antes de salvar o modelo."""
    _fix_slot_count(template, request, report)
    _fix_slot_sequences(template, report)
    for slot in template.slots:
        _fix_rest_slot_with_items(slot, report)
        _drop_unusable_items(slot, template.volume_mode, report)
        _fix_prioritized_excess(slot, report)
        _fix_sets_count(slot, report)


def _suggest_grid(template: WorkoutPlanTemplateIn) -> None:
    """Grade sugerida para o editor (migracao 062): ate 4 colunas por linha,
    quantas linhas forem precisas. Calculada DEPOIS do ajuste da quantidade de
    slots, entao sempre comporta o ciclo final. Nao e decisao da LLM nem
    auto-fix (nada estava errado): e so a forma inicial da grade no builder."""
    count = len(template.slots)
    if count < 1:
        return
    template.grid_cols = min(count, 4)
    template.grid_rows = math.ceil(count / 4)


def _fix_slot_count(
    template: WorkoutPlanTemplateIn, request: PeriodizationDraftIn, report: ValidationReport
) -> None:
    wanted = request.days_per_cycle
    slots = template.slots
    while len(slots) > wanted:
        index = _index_of_slot_to_drop(slots)
        dropped = slots.pop(index)
        report.auto_fix(
            SLOT_REMOVED,
            slot=dropped.sequence,
            slot_name=dropped.name,
            slot_type=dropped.slot_type,
            items=len(dropped.items),
            expected=wanted,
        )
    while len(slots) < wanted:
        sequence = len(slots) + 1
        slots.append(
            WorkoutPlanTemplateSlotIn(
                sequence=sequence, name=f"Descanso {sequence}", slot_type="descanso", items=[]
            )
        )
        report.auto_fix(SLOT_ADDED, slot=sequence, slot_type="descanso", expected=wanted)


def _index_of_slot_to_drop(slots: list[Any]) -> int:
    """Qual slot sai quando ha slot demais: o ULTIMO descanso, se houver; senao
    o ultimo slot de treino (o do fim do ciclo, nunca o do meio)."""
    for index in range(len(slots) - 1, -1, -1):
        if (slots[index].slot_type or "normal") == "descanso":
            return index
    return len(slots) - 1


def _fix_slot_sequences(template: WorkoutPlanTemplateIn, report: ValidationReport) -> None:
    """`sequence` = posicao no array. A ordem que a LLM entregou e a ordem do
    ciclo; o numero e so o rotulo dela."""
    for position, slot in enumerate(template.slots, start=1):
        if slot.sequence == position:
            continue
        report.auto_fix(
            SLOT_SEQUENCE_RENUMBERED, slot=position, slot_name=slot.name, **{"from": slot.sequence, "to": position}
        )
        slot.sequence = position


def _fix_rest_slot_with_items(slot: Any, report: ValidationReport) -> None:
    if (slot.slot_type or "normal") != "descanso":
        return
    if not any(item.item_kind != "cardio" for item in slot.items):
        return
    report.auto_fix(
        SLOT_TYPE_FIXED,
        slot=slot.sequence,
        slot_name=slot.name,
        items=len(slot.items),
        **{"from": "descanso", "to": "normal"},
    )
    slot.slot_type = "normal"


def _drop_unusable_items(slot: Any, volume_mode: str, report: ValidationReport) -> None:
    """Item que nao prescreve nada sai do slot; o resto do dia fica de pe.

    Sao os tres casos em que `_validate_template_payload` recusaria o item e nao
    ha o que aproveitar dele sem INVENTAR exercicio: equipamento sem o trio,
    grupo muscular sem grupo e item de grupo muscular no Modo B (onde o item
    tem de ser de equipamento). `item_order` e renumerado depois da remocao,
    para continuar sendo a posicao no slot."""
    mantidos = []
    for item in slot.items:
        reason = _unusable_reason(item, volume_mode)
        if reason is None:
            mantidos.append(item)
            continue
        report.auto_fix(
            ITEM_REMOVED,
            slot=slot.sequence,
            slot_name=slot.name,
            item=item.item_order,
            item_kind=item.item_kind,
            muscle_group_id=item.muscle_group_id,
            reason=reason,
        )
    if len(mantidos) == len(slot.items):
        return
    for order, item in enumerate(mantidos, start=1):
        item.item_order = order
    slot.items = mantidos


def _unusable_reason(item: Any, volume_mode: str) -> Optional[str]:
    """M101b: o save do Modelo aceita item so com o grupo (exercicio livre,
    escolha do USUARIO no editor; o motor de geracao escolhe o exercicio e o
    equipamento vem junto). Aqui todo item vem da LLM, cujo prompt pede o trio
    completo copiado do `gym_catalog`: par ausente na saida dela e trio
    incompleto, como antes - o "livre" nunca nasce da IA."""
    if item.item_kind == "equipment":
        if volume_mode != "equipment_series":
            return "item de equipamento no modo percentual"
        if not item.muscle_group_id or not item.mapping_id or not item.equipment_id:
            return "trio incompleto"
        return None
    if item.item_kind == "muscle_group":
        if volume_mode == "equipment_series":
            return "item de grupo muscular no modo de equipamento"
        if not item.muscle_group_id:
            return "sem grupo muscular"
    return None


def _fix_prioritized_excess(slot: Any, report: ValidationReport) -> None:
    prioritized = [item for item in slot.items if item.prioritize]
    if len(prioritized) <= MAX_PRIORITIZED_PER_SLOT:
        return
    for item in prioritized[MAX_PRIORITIZED_PER_SLOT:]:
        report.auto_fix(
            PRIORITIZE_UNMARKED,
            slot=slot.sequence,
            slot_name=slot.name,
            item=item.item_order,
            max=MAX_PRIORITIZED_PER_SLOT,
            received=len(prioritized),
        )
        item.prioritize = False


def _fix_sets_count(slot: Any, report: ValidationReport) -> None:
    minimum = _default_sets_count()
    for item in slot.items:
        if item.item_kind != "equipment":
            continue
        if item.sets_count is not None and item.sets_count >= 1:
            continue
        report.auto_fix(
            SETS_COUNT_FIXED,
            slot=slot.sequence,
            item=item.item_order,
            **{"from": item.sets_count, "to": minimum},
        )
        item.sets_count = minimum


def _default_sets_count() -> int:
    """Series de um item de equipamento que veio sem numero: o MINIMO por
    exercicio da metodologia (a mesma constante que o prompt cita), nunca um
    numero escolhido aqui."""
    try:
        from ..intelligence.methodology_volume import _SETS_PER_EXERCISE_MIN

        return max(1, int(_SETS_PER_EXERCISE_MIN))
    except Exception:  # noqa: BLE001 - constante indisponivel nao pode derrubar a validacao
        return 3


def _check_references(
    conn: Any, template: WorkoutPlanTemplateIn, request: PeriodizationDraftIn, report: ValidationReport
) -> list[dict[str, Any]]:
    """Grupos musculares citados + pares (exercicio, equipamento) da academia.

    Grupo inexistente continua ERRO (nao ha substituto deterministico possivel).
    Par que nao esta ATIVO na academia pedida passou a ter uma chance de conserto
    (`_replace_equipment_pairs`): trocar pelo par mais proximo do MESMO grupo
    muscular DENTRO da academia. A garantia de seguranca nao muda - o par novo
    sai da mesma leitura de `gym_equipment` que valida os outros, entao um
    rascunho aceito continua impossivel de conter equipamento que a academia nao
    tem; o que muda e que o trabalho bom da geracao deixa de ser descartado por
    causa de um trio montado errado. Sem substituto: segue ERRO.

    Devolve os pares ativos da academia (vazio quando nao houve leitura), que
    o complemento de volume (M46) usa como catalogo de exercicios novos."""
    group_ids = {
        item.muscle_group_id
        for slot in template.slots
        for item in slot.items
        if item.muscle_group_id is not None
    }
    rows: list[dict[str, Any]] = []
    group_names: dict[int, Optional[str]] = {}
    equipment_items = [
        (slot, item)
        for slot in template.slots
        for item in slot.items
        if item.item_kind == "equipment" and item.mapping_id and item.equipment_id
    ]

    with conn.cursor() as cur:
        if group_ids:
            placeholders = ",".join(["%s"] * len(group_ids))
            cur.execute(
                f"SELECT id, name FROM muscle_groups WHERE id IN ({placeholders})", tuple(sorted(group_ids))
            )
            group_names = {int(row["id"]): row.get("name") for row in cur.fetchall() or []}
            existing = set(group_names)
            for missing in sorted(group_ids - existing):
                report.error(MUSCLE_GROUP_NOT_FOUND, muscle_group_id=missing)

        if equipment_items:
            if not request.gym_id:
                report.error(GYM_REQUIRED)
                return rows
            # Uma unica leitura para todos os pares: associacoes ATIVAS da
            # academia, com o grupo PRIMARIO de cada exercicio ao lado (a mesma
            # regra de `equipment_recommender._PRIMARY_GROUP_JOIN`, que e o que
            # monta o catalogo mostrado a LLM). Par ausente cobre os dois casos
            # de uma vez - equipamento inexistente e equipamento de outra
            # academia - e as linhas servem tambem de fonte de substituicao.
            rows = _load_gym_pairs(cur, request.gym_id)

    if equipment_items and request.gym_id:
        _replace_equipment_pairs(equipment_items, rows, request, report, group_names)
    return rows


def _load_gym_pairs(cur: Any, gym_id: int) -> list[dict[str, Any]]:
    """Pares ATIVOS da academia, na MESMA ordem do recomendador (`ge.id ASC`),
    com o grupo primario e os nomes de exercicio/equipamento - o suficiente para
    validar e para registrar uma troca legivel no relatorio."""
    cur.execute(
        """
        SELECT ge.id AS gym_equipment_id, ge.mapping_id, ge.equipment_id,
               pg.id AS muscle_group_id, pg.name AS muscle_group_name,
               zm.name AS exercise_name, eq.name AS equipment_name
        FROM gym_equipment ge
        JOIN exercise_catalog zm ON zm.id = ge.mapping_id
        JOIN equipment eq ON eq.id = ge.equipment_id
        JOIN muscle_groups pg ON pg.id = (
            SELECT emc.muscle_group_id
            FROM exercise_muscle_contributions emc
            WHERE emc.exercise_id = ge.mapping_id
              AND emc.role = 'PRIMARY' AND emc.is_active = 1
            ORDER BY emc.id ASC LIMIT 1
        )
        WHERE ge.gym_id = %s AND ge.is_active = 1
        ORDER BY ge.id ASC
        """,
        (gym_id,),
    )
    return [
        {
            "mapping_id": int(row["mapping_id"]),
            "equipment_id": int(row["equipment_id"]),
            "muscle_group_id": int(row["muscle_group_id"]),
            "muscle_group_name": row.get("muscle_group_name"),
            "exercise_name": row.get("exercise_name"),
            "equipment_name": row.get("equipment_name"),
        }
        for row in cur.fetchall() or []
    ]


def _replace_equipment_pairs(
    equipment_items: list[tuple[Any, Any]],
    rows: list[dict[str, Any]],
    request: PeriodizationDraftIn,
    report: ValidationReport,
    group_names: Optional[dict[int, Optional[str]]] = None,
) -> None:
    """Par fora da academia -> par do MESMO grupo muscular dentro dela; par
    ativo com o grupo trocado -> o grupo do catalogo (`_relabel_muscle_group`).

    Preferencia (deterministica, sem sorteio e sem LLM):
    1. par ainda NAO usado naquele slot (nao cria exercicio repetido no dia);
    2. MESMO exercicio (`mapping_id`) em outro equipamento da academia - e o
       caso comum, o modelo acertou o exercicio e trocou a linha do catalogo;
    3. MESMO equipamento em outro exercicio do grupo;
    4. o primeiro par do grupo na ordem do catalogo (o mesmo `ge.id ASC` que o
       recomendador usa, entao a troca cai no que o usuario tambem veria antes).

    Sem nenhum par do grupo na academia nao ha conserto: continua
    `EQUIPMENT_NOT_IN_GYM` (erro), porque inventar equipamento de outra academia
    e exatamente o que este validador existe para impedir."""
    listed = {(row["mapping_id"], row["equipment_id"]): row for row in rows}
    active = set(listed)
    by_group: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_group.setdefault(row["muscle_group_id"], []).append(row)

    used_by_slot: dict[int, set[tuple[int, int]]] = {}
    for slot, item in equipment_items:
        used_by_slot.setdefault(slot.sequence, set()).add((int(item.mapping_id), int(item.equipment_id)))

    for slot, item in equipment_items:
        pair = (int(item.mapping_id), int(item.equipment_id))
        if pair in active:
            _relabel_muscle_group(slot, item, listed[pair], report, group_names or {})
            continue
        used = used_by_slot.setdefault(slot.sequence, set())
        candidates = by_group.get(int(item.muscle_group_id or 0)) or []
        chosen = _pick_replacement(candidates, pair, used)
        if chosen is None:
            report.error(
                EQUIPMENT_NOT_IN_GYM,
                gym_id=request.gym_id,
                mapping_id=pair[0],
                equipment_id=pair[1],
                muscle_group_id=item.muscle_group_id,
            )
            continue
        report.auto_fix(
            EQUIPMENT_REPLACED,
            slot=slot.sequence,
            item=item.item_order,
            gym_id=request.gym_id,
            muscle_group_id=item.muscle_group_id,
            **{"from": {"mapping_id": pair[0], "equipment_id": pair[1]}},
            to={"mapping_id": chosen["mapping_id"], "equipment_id": chosen["equipment_id"]},
            exercise_name=chosen["exercise_name"],
            equipment_name=chosen["equipment_name"],
        )
        used.discard(pair)
        used.add((chosen["mapping_id"], chosen["equipment_id"]))
        item.mapping_id = chosen["mapping_id"]
        item.equipment_id = chosen["equipment_id"]


def _relabel_muscle_group(
    slot: Any, item: Any, row: dict[str, Any], report: ValidationReport, group_names: dict[int, Optional[str]]
) -> None:
    """Par ATIVO rotulado com outro grupo -> o grupo sob o qual o par esta no
    catalogo da academia (`MUSCLE_GROUP_RELABELED`).

    Eval de 2026-10-16 (prompt v11): a LLM devolve o par listado em "Peitoral
    inferior" com o Peitoral (pai) e o de "Costas meio" com Costas (e vice-
    versa) - irmaos do mesmo bloco da metodologia. O grupo certo e o PRIMARY
    do exercicio no cadastro, a MESMA subconsulta (`_PRIMARY_GROUP_ON` do
    recomendador) que monta o catalogo mostrado a LLM e as linhas de
    `_load_gym_pairs`. No Modo B o motor da metodologia conta pelo
    `mapping_id` (o rotulo e so eco), entao nenhum numero muda; o que muda e o
    agrupamento do preview do editor, que le `item.muscle_group_id`."""
    expected = int(row["muscle_group_id"])
    current = item.muscle_group_id
    if current is not None and int(current) == expected:
        return
    report.auto_fix(
        MUSCLE_GROUP_RELABELED,
        slot=slot.sequence,
        slot_name=slot.name,
        item=item.item_order,
        mapping_id=int(item.mapping_id),
        equipment_id=int(item.equipment_id),
        exercise_name=row.get("exercise_name"),
        equipment_name=row.get("equipment_name"),
        **{"from": current, "to": expected},
        from_name=group_names.get(int(current)) if current is not None else None,
        to_name=row.get("muscle_group_name"),
    )
    item.muscle_group_id = expected


def _pick_replacement(
    candidates: list[dict[str, Any]], pair: tuple[int, int], used: set[tuple[int, int]]
) -> Optional[dict[str, Any]]:
    """Melhor candidato pela ordem de preferencia documentada em
    `_replace_equipment_pairs`. `min` sobre uma lista ja ordenada por `ge.id` e
    estavel: empate resolve pelo primeiro do catalogo."""
    if not candidates:
        return None

    def rank(row: dict[str, Any]) -> tuple[int, int, int]:
        key = (row["mapping_id"], row["equipment_id"])
        return (
            1 if key in used else 0,
            0 if row["mapping_id"] == pair[0] else 1,
            0 if row["equipment_id"] == pair[1] else 1,
        )

    return min(candidates, key=rank)


def _check_template_rules(template: WorkoutPlanTemplateIn, report: ValidationReport) -> None:
    from ..advanced_techniques import active_codes
    from ..workout_plan_templates import _validate_template_payload, clear_sets_only_technique_fields

    try:
        _validate_template_payload(template)
    except HTTPException as exc:
        report.error(TEMPLATE_INVALID, message=str(exc.detail))
        return

    # Codigo de tecnica fora do catalogo ATIVO: auto-fix deterministico
    # (remover so o item afetado) em vez de erro - a divisao em si continua util
    # para o humano editar. Item com codigo valido nao e tocado aqui; a
    # compatibilidade com o par e conferida depois, em `_apply_safety_auto_fixes`.
    valid_codes = active_codes()
    for slot in template.slots:
        for item in slot.items:
            if item.advanced_technique is None or item.advanced_technique in valid_codes:
                continue
            report.auto_fix(
                TECHNIQUE_REMOVED,
                slot=slot.sequence,
                item=item.item_order,
                technique=item.advanced_technique,
                reason=ts.TECHNIQUE_UNKNOWN,
            )
            _clear_technique(item)

    # FST-7/GVT: o MESMO conserto do save do Modelo, registrado como auto-fix.
    for change in clear_sets_only_technique_fields(template):
        report.auto_fix(
            TECHNIQUE_FIELDS_CLEARED,
            slot=change["slot"],
            item=change["item"],
            technique=change["technique"],
            applications=change["applications"],
            duration_seconds=change["duration_seconds"],
        )


def _check_activation(
    template: WorkoutPlanTemplateIn, request: PeriodizationDraftIn, report: ValidationReport
) -> None:
    """Projecao da ativacao do ciclo contra as metas do perfil escolhido.

    Modo B: soma o `total_equivalent` por grupo na janela de `preview_activation`
    (que e `max(7, tamanho do ciclo)` desde o fix do PR-13) e compara com a meta
    semanal ESCALADA pela janela - ciclo de 9 dias entrega ~9/7 de uma semana.

    Modo A: nao ha serie nenhuma no modelo (o volume vem da % da meta), entao a
    comparacao possivel e a soma dos percentuais por grupo, exatamente a que
    `validate_template_volume_coverage` ja faz (90%/110%).

    Desde o PR D (Fase 4) esta funcao so CARREGA (metas, preview, nomes) e
    delega o CALCULO a `activation_projection` (puro): `project_activation` +
    `activation_target_warnings` no Modo B, `coverage_warnings` no Modo A. O
    payload de cada warning e o mesmo de sempre.

    Nunca levanta: sem perfil de metas, sem grupo com meta ou com qualquer falha
    de leitura, simplesmente nao ha warning de ativacao a dar."""
    if not request.training_type_id:
        return
    try:
        from ..training_types import get_training_type_muscle_group_targets

        targets = get_training_type_muscle_group_targets(request.training_type_id)
        if not targets:
            return

        if template.volume_mode == "percent_budget":
            from ..intelligence.template_volume_planner import validate_template_volume_coverage

            entries = validate_template_volume_coverage(template.model_dump(mode="json"))
            for warning in coverage_warnings(entries):
                report.warning(**warning)
            return

        inputs = _load_activation_projection_inputs(template, targets)
        for warning in activation_target_warnings(project_activation(inputs)):
            report.warning(**warning)
    except Exception as exc:  # noqa: BLE001 - projecao informativa, nunca derruba a validacao
        report.warning(ACTIVATION_COVERAGE, status="UNAVAILABLE", message=str(exc))


def _load_activation_projection_inputs(
    template: WorkoutPlanTemplateIn, targets: dict[int, dict[str, float]]
) -> ActivationProjectionInputs:
    """CARREGAMENTO do Modo B (as duas leituras que sempre existiram, na mesma
    ordem): o preview de ativacao do ciclo e os nomes dos grupos da meta. O
    calculo fica em `activation_projection.project_activation`."""
    from ..workout_plan_templates import (
        WorkoutPlanTemplatePreviewIn,
        _load_muscle_group_names,
        preview_activation,
    )

    preview = preview_activation(
        WorkoutPlanTemplatePreviewIn(slots=template.slots, volume_mode=template.volume_mode)
    )
    names = _load_muscle_group_names(set(targets))
    return ActivationProjectionInputs(
        preview_days=tuple(preview.get("days") or []),
        window_days=preview.get("window_days"),
        targets=targets,
        muscle_group_names=names,
    )


def _check_methodology(
    template: WorkoutPlanTemplateIn,
    report: ValidationReport,
    inputs: Any = None,
    counting_mode: Any = None,
    request: Optional[PeriodizationDraftIn] = None,
) -> None:
    """Modo B: sessao completa e faixas da metodologia por grupo, COM ou SEM
    perfil de metas (a lacuna do diagnostico de 2026-09-28: sem
    `training_type_id` o rascunho nao recebia aviso nenhum de volume).

    Tamanho da sessao e regra local (contagem de itens). A faixa por grupo e
    o motor PURO da Fase 4 (`evaluate_methodology_volume`) alimentado pela
    MESMA leitura do endpoint de diagnostico (`load_methodology_inputs`):
    frequencia = sessoes com trabalho direto no grupo na janela de 7 dias a
    partir do primeiro slot de treino (v3), series = soma das PRIMARY por
    sessao (EXG-014), com o equivalente planejado da tecnica (v4), comparadas com a celula (macro-grupo, frequencia) do cadastro da 057, e a soma da
    janela com a meta SEMANAL do macro-grupo (Fase 6, eixo semanal,
    que tambem vira aviso). Dos eixos por sessao, so o de SERIES vira aviso - o
    de exercicios ja esta coberto por `SLOT_TOO_FEW_EXERCISES` no nivel do
    slot e o builder mostra os dois eixos depois.

    `inputs`: as leituras que o complemento de volume (M46) ja fez - um
    superconjunto dos `mapping_id` do rascunho, entao servem igual. Ausente,
    a etapa le como sempre leu.

    `counting_mode`: o modo de contagem do Modelo a gerar (Lote 5). Decide os
    pesos e as faixas que o motor aplica; ausente = o default do motor. As
    `inputs` do complemento ja vem lidas no mesmo modo.

    Nunca levanta: falha de leitura vira `ACTIVATION_COVERAGE UNAVAILABLE`,
    como a projecao de ativacao ja faz."""
    for slot in template.slots:
        if (slot.slot_type or "normal") == "descanso":
            continue
        count = sum(1 for item in slot.items if item.item_kind != "cardio")
        # M97 F2: piso do OBJETIVO do slot (dono `limits`; hipertrofia = 6).
        # M97 F5: o objetivo do dia declarado no pedido vence o dos itens.
        declared = goal_plan.declared_day_goal(request, slot.sequence) if request is not None else None
        minimum = session_exercises_min(
            slot_session_goal(slot.items, getattr(template, "training_goal", None), declared)
        )
        if count < minimum:
            report.warning(
                SLOT_TOO_FEW_EXERCISES,
                slot=slot.sequence,
                slot_name=slot.name,
                items=count,
                min=minimum,
            )

    try:
        from ..intelligence import methodology_volume_service as svc
        from ..intelligence.methodology_volume import MethodologyVolumeStatus, evaluate_methodology_volume

        mapping_ids = svc.mapping_ids_of(template.slots)
        if not mapping_ids:
            return
        if inputs is None:
            inputs = svc.load_methodology_inputs(mapping_ids, counting_mode or DEFAULT_VOLUME_COUNTING_MODE)
        evaluation = evaluate_methodology_volume(
            svc.build_slot_inputs(template.slots, template.volume_mode, inputs.canonical_ids),
            inputs.contributions,
            inputs.groups,
            template.volume_mode,
            None,
            inputs.volume_classes,
            inputs.counting_mode,
        )
        below, above = MethodologyVolumeStatus.BELOW_RANGE, MethodologyVolumeStatus.ABOVE_RANGE
        for group in evaluation.groups:
            identity = {
                "muscle_group_id": group.muscle_group_id,
                "muscle_group_name": group.muscle_group_name,
                "volume_class_id": group.volume_class_id,
                "volume_class_name": group.volume_class_name,
                "size_class": group.size_class,
            }
            axis = group.direct_sets_axis
            if axis.status in (below, above):
                report.warning(
                    METHODOLOGY_BELOW_RANGE if axis.status is below else METHODOLOGY_ABOVE_RANGE,
                    **identity,
                    frequency_bucket=group.frequency_bucket,
                    frequency=group.frequency_raw,
                    projected_sets=axis.value_per_session,
                    min=axis.range_min,
                    max=axis.range_max,
                    evidence=axis.range_evidence,
                )
            weekly = group.weekly_sets_axis
            if weekly.status in (below, above):
                report.warning(
                    METHODOLOGY_WEEKLY_BELOW_RANGE if weekly.status is below else METHODOLOGY_WEEKLY_ABOVE_RANGE,
                    **identity,
                    frequency=group.frequency_raw,
                    weekly_sets=weekly.value_weekly,
                    min=weekly.range_min,
                    max=weekly.range_max,
                    evidence=weekly.range_evidence,
                )
    except Exception as exc:  # noqa: BLE001 - avaliacao informativa, nunca derruba a validacao
        report.warning(ACTIVATION_COVERAGE, status="UNAVAILABLE", message=f"metodologia: {exc}")


def _apply_goal_plan(
    conn: Any,
    template: WorkoutPlanTemplateIn,
    request: PeriodizationDraftIn,
    report: ValidationReport,
    gym_pairs: Optional[list[dict[str, Any]]] = None,
    compound_flags: Optional[dict[int, bool]] = None,
    *,
    final: bool = False,
) -> Optional[dict[int, bool]]:
    """Objetivo por dia (M97 F5, `goal_plan.py`). Sem objetivo nao-default no
    pedido devolve `None` e nao toca no rascunho (o de sempre).

    Por slot de treino, item a item (idempotente: a 2a passada, depois do
    complemento, so mexe no item novo):
    1. objetivo do item = o do dia (`goal_plan.item_target_goal`; isolador num
       dia de forca = hipertrofia, `ISOLATION_GOAL_OVERRIDE`); o override
       gravado e so a diferenca para o objetivo do Modelo;
    2. tecnica fora da politica do objetivo do item -> removida
       (`TECHNIQUE_REMOVED`, reason `TECHNIQUE_NOT_ALLOWED_FOR_GOAL`);
    3. RPE acima do teto do objetivo -> cortado (`RPE_CLAMPED`, reason
       `TRAINING_GOAL`);
    4. alvo de reps fora da faixa do objetivo -> a faixa do objetivo
       (`GOAL_REPS_FIXED`).
    Na passada `final`, o dia com faixa de compostos pesados
    (`limits.heavy_compound_range`) fora dela vira aviso
    (`DAY_HEAVY_COMPOUNDS_OUT_OF_RANGE`).

    A flag de composto e lida uma vez (`context.load_compound_flags`) para os
    exercicios do rascunho E da academia (o complemento pode acrescentar um
    par da academia). Falha de leitura = todos isoladores (nada sobe para
    carga maxima por engano)."""
    from ..domain.prescription_defaults import defaults_for, reps_target_fits_goal
    from ..domain.training_goal import TRAINING_GOAL_COLUMN, goal_label, resolve_goal
    from .context import load_compound_flags

    if not goal_plan.has_goal_plan(request):
        return None
    if compound_flags is None:
        mapping_ids = {
            int(item.mapping_id) for slot in template.slots for item in slot.items if item.mapping_id
        } | {int(pair["mapping_id"]) for pair in gym_pairs or []}
        try:
            compound_flags = load_compound_flags(conn, mapping_ids)
        except Exception:  # noqa: BLE001 - sem a flag, todo item e isolador
            compound_flags = {}
    header = goal_plan.template_goal(request)
    for slot in template.slots:
        if (slot.slot_type or "normal") == "descanso":
            continue
        day = goal_plan.day_goal(request, slot.sequence)
        compound_count = 0
        for item in slot.items:
            is_compound = goal_plan.compound_of(item, compound_flags)
            target = goal_plan.item_target_goal(day, item.item_kind, is_compound)
            if target is None:
                continue
            isolation = target != day and item.item_kind == goal_plan.ITEM_KIND_EQUIPMENT
            # Isolador fora do objetivo do dia: override EXPLICITO (fica no
            # objetivo dele mesmo se o objetivo do Modelo mudar depois);
            # demais itens: so a diferenca para o objetivo do Modelo.
            override = target if isolation else goal_plan.override_for(target, header)
            if item.training_goal != override:
                if isolation:
                    report.auto_fix(
                        ISOLATION_GOAL_OVERRIDE,
                        slot=slot.sequence,
                        slot_name=slot.name,
                        item=item.item_order,
                        mapping_id=item.mapping_id,
                        day_goal=day,
                        day_goal_label=goal_label(day),
                        training_goal=target,
                        training_goal_label=goal_label(target),
                        override=override,
                    )
                setattr(item, TRAINING_GOAL_COLUMN, override)
            goal = resolve_goal(item.training_goal, header).value
            if item.item_kind == goal_plan.ITEM_KIND_EQUIPMENT and is_compound and goal == day:
                compound_count += 1
            if item.advanced_technique and not ts.goal_allows_technique(item.advanced_technique, goal):
                report.auto_fix(
                    TECHNIQUE_REMOVED,
                    slot=slot.sequence,
                    item=item.item_order,
                    technique=item.advanced_technique,
                    reason=ts.TECHNIQUE_NOT_ALLOWED_FOR_GOAL,
                    training_goal=goal,
                    training_goal_label=goal_label(goal),
                )
                _clear_technique(item)
            ceiling = ts.goal_rpe_ceiling(goal)
            if ceiling is not None and item.rpe_target:
                clamped, clamps = effective_intensity.clamp_rpe_text(item.rpe_target, ceiling)
                if clamps:
                    report.auto_fix(
                        RPE_CLAMPED,
                        slot=slot.sequence,
                        item=item.item_order,
                        **{"from": item.rpe_target, "to": clamped},
                        reason=ts.REASON_TRAINING_GOAL,
                        training_goal=goal,
                        training_goal_label=goal_label(goal),
                    )
                    item.rpe_target = clamped
            if item.reps_target and not reps_target_fits_goal(item.reps_target, goal):
                fixed = defaults_for(goal).reps_target
                report.auto_fix(
                    GOAL_REPS_FIXED,
                    slot=slot.sequence,
                    item=item.item_order,
                    training_goal=goal,
                    training_goal_label=goal_label(goal),
                    **{"from": item.reps_target, "to": fixed},
                )
                item.reps_target = fixed
        compound_range = heavy_compound_range(day)
        if final and compound_range is not None and not compound_range[0] <= compound_count <= compound_range[1]:
            report.warning(
                DAY_HEAVY_COMPOUNDS_OUT_OF_RANGE,
                slot=slot.sequence,
                slot_name=slot.name,
                training_goal=day,
                training_goal_label=goal_label(day),
                compound_count=compound_count,
                min=compound_range[0],
                max=compound_range[1],
            )
    return compound_flags


def _apply_safety_auto_fixes(
    conn: Any, template: WorkoutPlanTemplateIn, report: ValidationReport
) -> dict[tuple[int, int], Any]:
    """Teto de RPE e tecnica por par, via `training_safety` (funcao unica).

    Sem usuario/readiness no contexto de um MODELO, o teto aplicado e o do PAR
    (`max_safe_rpe`, com o default 9 da D3 para par desconhecido). A composicao
    com a recuperacao acontece depois, na geracao da ficha do dia.

    Devolve a seguranca lida por par, que o complemento de volume (M46)
    reaproveita para validar a tecnica que ele acrescenta."""
    pairs = [
        (item.mapping_id, item.equipment_id)
        for slot in template.slots
        for item in slot.items
        if item.item_kind == "equipment" and item.mapping_id and item.equipment_id
    ]
    if not pairs:
        return {}
    safeties = ts.resolve_training_safety_bulk(conn, pairs)

    for slot in template.slots:
        for item in slot.items:
            if item.item_kind != "equipment" or not item.mapping_id or not item.equipment_id:
                continue
            safety = safeties.get((int(item.mapping_id), int(item.equipment_id)))
            if safety is None:
                continue

            clamped, clamps = effective_intensity.clamp_rpe_text(item.rpe_target, safety.max_safe_rpe)
            if clamps:
                report.auto_fix(
                    RPE_CLAMPED,
                    slot=slot.sequence,
                    item=item.item_order,
                    **{"from": item.rpe_target, "to": clamped},
                    reason=ts.REASON_EQUIPMENT_SAFETY,
                )
                item.rpe_target = clamped

            if item.advanced_technique:
                validation = ts.validate_technique(item.advanced_technique, safety)
                if not validation.valid:
                    report.auto_fix(
                        TECHNIQUE_REMOVED,
                        slot=slot.sequence,
                        item=item.item_order,
                        technique=item.advanced_technique,
                        reason=validation.reason,
                    )
                    _clear_technique(item)
    return safeties


def _apply_volume_complement(
    conn: Any,
    template: WorkoutPlanTemplateIn,
    gym_pairs: Optional[list[dict[str, Any]]],
    safeties: Optional[dict[tuple[int, int], Any]],
    report: ValidationReport,
    counting_mode: Any = None,
) -> Any:
    """Complemento deterministico de volume (M46) - ver `volume_complement.py`.

    `counting_mode` (Lote 5): o modo de contagem do Modelo a gerar. As leituras
    saem nele (pesos e faixas do modo), entao a serie somada num composto conta
    tambem nos grupos secundarios dele; ausente = o default do motor.

    Leituras: a da metodologia (`load_methodology_inputs`) UMA vez, com os
    `mapping_id` do rascunho E os do catalogo da academia (as PRIMARY dos
    candidatos), devolvida para `_check_methodology` reaproveitar; e, so se
    houver grupo abaixo da faixa, UMA leitura em lote da seguranca dos pares
    candidatos que a etapa de seguranca ainda nao leu. Os pares da academia vem
    de `_check_references`. Nenhuma chamada de IA.

    Nao roda quando ja ha erro (o job vai falhar de qualquer jeito) ou sem as
    leituras das etapas anteriores. Nunca levanta: qualquer falha deixa o
    rascunho como estava (o complemento trabalha numa copia) e vira aviso
    `ACTIVATION_COVERAGE UNAVAILABLE`."""
    if report.errors or gym_pairs is None or safeties is None:
        return None
    from ..intelligence import methodology_volume_service as svc

    try:
        mapping_ids = svc.mapping_ids_of(template.slots) | {int(p["mapping_id"]) for p in gym_pairs}
        if not mapping_ids:
            return None
        inputs = svc.load_methodology_inputs(mapping_ids, counting_mode or DEFAULT_VOLUME_COUNTING_MODE)
    except Exception:  # noqa: BLE001 - a etapa de metodologia tenta de novo e avisa
        return None
    try:
        result = complement_volume(
            template,
            inputs,
            gym_pairs,
            safeties,
            safety_loader=lambda pairs: ts.resolve_training_safety_bulk(conn, pairs),
        )
    except Exception as exc:  # noqa: BLE001 - complemento nunca derruba a validacao
        report.warning(ACTIVATION_COVERAGE, status="UNAVAILABLE", message=f"complemento de volume: {exc}")
        return inputs
    for entry in result.auto_fixes:
        report.auto_fix(**entry)
    return inputs


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _clear_technique(item: Any) -> None:
    """Remocao deterministica da tecnica do item: o codigo e os dois campos que
    so existem por causa dele."""
    item.advanced_technique = None
    item.advanced_technique_applications = None
    item.advanced_technique_duration_seconds = None


def _first_schema_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error.get("loc") or ())
    return f"{location or 'raiz'}: {error.get('msg')}"
