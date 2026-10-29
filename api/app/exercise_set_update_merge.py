"""Round-trip das colunas por série na edição de um registro de exercício.

`PUT /exercises/logs/{id}` (tela /registro-exercicios) RECRIA as séries
(DELETE + INSERT). Antes desta regra, tudo que o cliente não reenviava era
perdido: o `set_type` virava `working` (um drop-set ou aquecimento passava a
contar como série de trabalho), e `rest_seconds`, `notes`, a frequência
cardíaca do relógio (`hr_*`) e a dor por série (`muscle_pain`/`joint_pain`)
viravam NULL.

Regra (a mais segura para clientes antigos que não mandam esses campos):

- campo PRESENTE na série enviada -> vale o valor enviado, inclusive `null`
  (é assim que o cliente limpa um campo de propósito);
- campo AUSENTE -> mantém o valor gravado da série com o mesmo `set_number`;
- sem série gravada com esse número (série nova) -> default da coluna
  (`set_type` = `working`, demais NULL).

"Presente" é a chave existir no dict. A rota monta cada série com
`model_dump(exclude_unset=True)`, então o default `working` do Pydantic não
conta como "enviado". O pareamento por `set_number` só é confiável quando o
cliente não renumera as séries; por isso o frontend passa a reenviar todos os
campos explicitamente, e o fallback fica para clientes legados.

`rpe_target_effective` e `safety_event` NÃO entram aqui: continuam sendo
recalculados pelo servidor a cada gravação (ver `update_exercise_log`).

M102 (100): os lados da série unilateral (`reps_left`/`reps_right`/
`rpe_left`/`rpe_right`) são preservados como os demais; com algum lado
presente depois da mescla, `reps`/`rpe` oficiais são recalculados pelo dono
(`domain/set_sides`, pior lado) — inclusive no PATCH.

Fase 5 (PR 2): `technique_code`/`anchor_set_number` (colunas da 059) seguem a
mesma regra. Depois da mescla, `link_technique_applications` completa o
vínculo das séries de APLICAÇÃO de técnica que ficaram sem ele (série nova,
linha legada ainda sem backfill, ou `null` explícito) com a MESMA derivação
da importação e do Modo Treino (`domain/technique_accounting.py`). Valor
presente nunca é trocado.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .advanced_techniques import ensure_valid_codes
from .domain import technique_accounting
from .domain.set_sides import SIDE_FIELDS, apply_canonical
from .domain.training_safety import RPE_TARGET_SOURCE_PROTOCOL
from .set_types import REPS_PENDING_COLUMN, is_pending_set, normalize_set_type

# Colunas de `exercise_sets` que o cliente pode reenviar e que, se omitidas,
# são preservadas da série gravada. `reps`/`weight_kg`/`rpe` ficam de fora de
# propósito: são os valores editados na tela e seguem "o que veio vale".
PRESERVED_SET_FIELDS: tuple[str, ...] = (
    "set_type",
    "rest_seconds",
    "hr_avg_bpm",
    "hr_max_bpm",
    "notes",
    "muscle_pain",
    "joint_pain",
    "technique_code",
    "anchor_set_number",
    # Lote T3 (medidas de série, 089): cliente que não reenvia a medida não
    # transforma uma série por tempo em série de reps (nem perde os segundos).
    "measure",
    "duration_seconds",
    # M102 (100): lados da série unilateral. Cliente que não os reenvia não
    # apaga os lados; depois da mescla `reps`/`rpe` oficiais são recalculados
    # pelo dono (`domain/set_sides.apply_canonical`, pior lado).
    *SIDE_FIELDS,
)


# Modo Intensivo (092): colunas que SÓ o servidor/Modo Intensivo grava. A
# edição pela tela /registro-exercicios (que recria as séries) nunca as recebe
# do cliente e as preserva da série gravada com o mesmo `set_number`; série
# nova -> NULL. `rpe_target_effective` entra aqui só para a série cujo alvo veio
# do PROTOCOLO da técnica (`rpe_target_source = 'protocol'`): a rota mantém o
# gravado nela em vez de recalcular pela prescrição (`keeps_protocol_target`).
SERVER_KEPT_SET_FIELDS: tuple[str, ...] = (
    "completed_at",
    "planned_weight_kg",
    "load_adjust_source",
    "rpe_target_source",
    "rpe_target_effective",
)

# PATCH de UMA série do Modo Intensivo (confirmar reps pendentes, corrigir
# carga/reps/RPE): os valores editados, com a mesma regra "presente vale,
# ausente mantém" deste módulo.
PATCHABLE_SET_FIELDS: tuple[str, ...] = ("reps", "weight_kg", "rpe", *SIDE_FIELDS)


def resolve_reps_pending(previous: Mapping[str, Any] | None, reps: Any) -> int:
    """Pendência da série depois de uma edição: continua pendente só se já era
    E as reps continuam vazias. Informar reps confirma; série nunca vira
    pendente por edição (só o Modo Intensivo grava a pendência)."""
    return 1 if previous is not None and is_pending_set(previous) and reps is None else 0


def keeps_protocol_target(merged: Mapping[str, Any]) -> bool:
    """A série mantém o `rpe_target_effective` gravado (alvo do protocolo)."""
    return merged.get("rpe_target_source") == RPE_TARGET_SOURCE_PROTOCOL


def merge_sets_for_update(
    incoming: Iterable[Mapping[str, Any]],
    stored: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Devolve as séries a gravar, com os campos omitidos preenchidos a partir
    das séries gravadas (pareadas por `set_number`). Função pura."""
    stored_by_number = {s.get("set_number"): s for s in stored}
    merged_sets: list[dict[str, Any]] = []
    for index, st in enumerate(incoming, start=1):
        number = st.get("set_number") or index
        previous = stored_by_number.get(number)
        merged = dict(st)
        merged["set_number"] = number
        for field in PRESERVED_SET_FIELDS:
            if field not in st:
                merged[field] = (previous or {}).get(field)
        for field in SERVER_KEPT_SET_FIELDS:
            merged[field] = (previous or {}).get(field)
        apply_canonical(merged)
        merged[REPS_PENDING_COLUMN] = resolve_reps_pending(previous, merged.get("reps"))
        merged["set_type"] = normalize_set_type(merged.get("set_type"))
        merged_sets.append(merged)
    return merged_sets


def merge_set_patch(stored: Mapping[str, Any], sent: Mapping[str, Any]) -> dict[str, Any]:
    """Série gravada + campos ENVIADOS no PATCH (`model_dump(exclude_unset=True)`).
    Só `PATCHABLE_SET_FIELDS`; presente vale (inclusive `null`), ausente
    mantém; a pendência sai de `resolve_reps_pending`. Função pura."""
    merged = dict(stored)
    for field in PATCHABLE_SET_FIELDS:
        if field in sent:
            merged[field] = sent[field]
    apply_canonical(merged)
    merged[REPS_PENDING_COLUMN] = resolve_reps_pending(stored, merged.get("reps"))
    return merged


def link_technique_applications(
    sets: list[dict[str, Any]],
    plan_technique: str | None = None,
) -> list[dict[str, Any]]:
    """Completa `technique_code`/`anchor_set_number` das aplicações de técnica
    que ficaram sem vínculo depois da mescla. Muta e devolve `sets`.

    Técnica: a do item da ficha quando grava o mesmo `set_type`, senão o
    padrão do `set_type`. Âncora: a última série `working` anterior DESTE
    envio (os `set_number` já são os que serão gravados)."""
    numbered = [(s.get("set_number"), s.get("set_type")) for s in sets]
    for st in sets:
        if st.get("set_type") not in technique_accounting.TECHNIQUE_APPLICATION_SET_TYPES:
            continue
        if not st.get("technique_code"):
            st["technique_code"] = technique_accounting.derive_technique_code(st["set_type"], plan_technique)
        if st.get("anchor_set_number") is None:
            st["anchor_set_number"] = technique_accounting.derive_anchor_set_number(st.get("set_number"), numbered)
    return sets


def validate_sent_technique_codes(sets: list[dict[str, Any]]) -> None:
    """Normaliza (`strip`, vazio -> None) e valida só os `technique_code`
    ENVIADOS: 422 fora do catálogo ativo, em vez de estourar a FK da 059. O
    valor preservado da série gravada não é revalidado (técnica desativada
    depois não bloqueia a edição). Muta `sets`."""
    for st in sets:
        if "technique_code" in st:
            st["technique_code"] = (st["technique_code"] or "").strip() or None
    ensure_valid_codes(st.get("technique_code") for st in sets)
