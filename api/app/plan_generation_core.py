"""Logica de geracao de plano de treino (`POST /workout-plans/generate`),
extraida de `workout_plans.py` para ser reutilizavel tanto pelo endpoint
FastAPI (que so cria o job e retorna 202) quanto pelo handler da Lambda
`musclefit-plan-generator` (`lambda_plan_generator.py`), que roda esta
mesma logica de forma assincrona - ver docs/aws-migration-plan.md, Fase 3,
padrao 202 + job_id + DynamoDB.

**PR-18 (PLANO_20260912.md, secoes 6, 9 e 15):** a geracao SO acontece a
partir de um Modelo de Treino. `template_id` e obrigatorio no payload
(`WorkoutPlanGenerateIn`) e o modelo precisa estar ativo e atribuido ao
usuario - senao, 422/404 (ver `generation/template_request.py`), validados
ja no request sincrono.

Os branches legados `plan_type == 'abc'` e o fallback Full Body ja foram
removidos deste arquivo. O que resta de legado aqui e o caminho
do Modelo v1 (sem `training_type_id` ou com `USE_TEMPLATE_VOLUME_ENGINE_V2`
desligada), que registra `generation.fallback` e sai junto com o resto do
legado depois de 30 dias de `generation.fallback = 0` (a contar de
2026-10-07, ou seja, a partir de 2026-11-06; decisao 4 de
docs/motores/FONTES_DA_VERDADE.md).

Mantido o comportamento exato do endpoint sincrono anterior (mesmas
excecoes - `HTTPException`, `WorkoutPlanGenerationError`, `ValueError`):
como agora ninguem devolve uma resposta HTTP diretamente a partir desta
funcao (o job assincrono so grava a excecao serializada como texto no
DynamoDB), nao ha necessidade de trocar os tipos de excecao usados - so
`str(exc)` do texto do erro é aproveitado (ver `lambda_plan_generator.py`).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException

from .generation.fallback_log import log_generation_fallback
from .generation.template_request import resolve_template_and_slot
from .models import WorkoutPlanGenerateIn
from .workout_plan_ai import WorkoutPlanGenerationError
from .workout_plan_templates_engine import generate_plan_draft_from_template

logger = logging.getLogger("musclefit.plan_generation")

# Mesma flag/env var de sempre (movida de workout_plans.py para ca, unica
# dona da logica de dispatch agora) - ver comentario original no historico
# de workout_plans.py.
USE_TEMPLATE_VOLUME_ENGINE_V2 = os.getenv("USE_TEMPLATE_VOLUME_ENGINE_V2", "true").strip().lower() == "true"

# `reason_code` do `generation.fallback` emitido pelo dispatcher.
FALLBACK_TEMPLATE_V1 = "TEMPLATE_WITHOUT_TRAINING_TYPE"
FALLBACK_TEMPLATE_ENGINE_DISABLED = "TEMPLATE_VOLUME_ENGINE_V2_DISABLED"


def dispatch_plan_generation(payload: WorkoutPlanGenerateIn, user_id: int) -> dict[str, Any]:
    """Gera o rascunho e PERSISTE a auditoria da geracao (M16).

    Este e o unico ponto por onde passam os quatro caminhos de geracao (Modo
    A, Modo B, os `_legacy` e o v1 legado), entao e aqui que a escrita em
    `generation_runs` mora - uma vez, e nao quatro.

    **A assinatura NAO mudou com o M16, e isso e deliberado.** A primeira
    versao acrescentou um `job_id` aqui, e o resultado foi imediato: um duble
    de teste com a aridade antiga virou `TypeError` dentro do handler da
    Lambda, que marcou como `failed` um job cuja geracao tinha corrido bem.
    Uma auditoria que muda o contrato de quem ela audita quebra a geracao pela
    porta da frente. O `job_id` chega pelo lado
    (`generation_runs.set_current_job_id`, chamado pela Lambda).

    Import tardio e excecao engolida DENTRO de `record_generation_run`, no
    mesmo padrao de `_attach_volume_coverage_warnings`
    (`workout_plan_templates.py`): se a persistencia da auditoria falhar, a
    ficha e gerada assim mesmo. O rascunho e o produto; a auditoria e sobre
    ele.
    """
    draft = _dispatch_plan_generation(payload, user_id)
    try:
        from .generation.generation_runs import record_generation_run

        record_generation_run(draft, user_id=user_id, request=payload)
    except Exception:  # noqa: BLE001 - nem o IMPORT da auditoria pode derrubar a geracao
        logger.exception("Falha ao persistir a auditoria da geracao (M16); a ficha segue")
    return draft


def _dispatch_plan_generation(payload: WorkoutPlanGenerateIn, user_id: int) -> dict[str, Any]:
    """Gera o rascunho a partir do Modelo de Treino do payload.

    O `volume_mode` do modelo escolhe o motor: `equipment_series` = Modo B
    (`generation/mode_b.py`), qualquer outro = Modo A
    (`generation/mode_a.py`). Cada motor decide internamente, pela flag
    `GENERATION_ENGINE`, entre o caminho deterministico e o legado.
    """
    plan_date = payload.plan_date.isoformat() if payload.plan_date else None
    plan_time = payload.plan_time.isoformat() if payload.plan_time else None

    template, template_slot = resolve_template_and_slot(
        payload.template_id, user_id, payload.template_slot_sequence
    )

    if template.get("volume_mode") == "equipment_series":
        from .generation.mode_b import generate_plan_draft_from_equipment_template

        try:
            return generate_plan_draft_from_equipment_template(
                template=template,
                slot=template_slot,
                gym_id=payload.gym_id,
                gym_name=payload.gym_name,
                plan_date=plan_date,
                user_id=user_id,
                min_rest_seconds=payload.min_rest_seconds,
                max_rest_seconds=payload.max_rest_seconds,
                plan_time=plan_time,
            )
        except WorkoutPlanGenerationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    if USE_TEMPLATE_VOLUME_ENGINE_V2 and template.get("training_type_id"):
        # PR-13: o Modo A (`volume_mode='percent_budget'`) passa por
        # `generation/mode_a.py`, que decide entre o motor deterministico e
        # o legado (`generation/mode_a_legacy.py`, o antigo
        # `workout_plan_templates_engine_v2.py`) pela flag
        # `GENERATION_ENGINE` - exatamente como o Modo B faz desde o PR-12.
        from .generation.mode_a import generate_plan_draft_from_template_a

        try:
            return generate_plan_draft_from_template_a(
                template=template,
                slot=template_slot,
                gym_id=payload.gym_id,
                gym_name=payload.gym_name,
                plan_date=plan_date,
                user_id=user_id,
                min_rest_seconds=payload.min_rest_seconds,
                max_rest_seconds=payload.max_rest_seconds,
                plan_time=plan_time,
            )
        except WorkoutPlanGenerationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Modelo v1 (sem `training_type_id`, ou com a flag desligada): caminho
    # legado mantido ate o PR-19.
    #
    # Secao 14: este E o caminho alternativo ao pretendido no dispatcher - todo
    # modelo criado depois do PR-13 tem `training_type_id`, entao a metrica
    # `generation.fallback` com este `reason_code` e esperada ZERO em producao.
    # Se aparecer, ou sobrou modelo v1 vivo ou `USE_TEMPLATE_VOLUME_ENGINE_V2`
    # foi desligado sem ninguem perceber.
    log_generation_fallback(
        reason_code=(
            FALLBACK_TEMPLATE_ENGINE_DISABLED
            if not USE_TEMPLATE_VOLUME_ENGINE_V2
            else FALLBACK_TEMPLATE_V1
        ),
        user_id=user_id,
        template_id=template.get("id"),
    )
    try:
        return generate_plan_draft_from_template(
            template=template,
            slot=template_slot,
            gym_id=payload.gym_id,
            gym_name=payload.gym_name,
            plan_date=plan_date,
            user_id=user_id,
            min_rest_seconds=payload.min_rest_seconds,
            max_rest_seconds=payload.max_rest_seconds,
            plan_time=plan_time,
        )
    except WorkoutPlanGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
