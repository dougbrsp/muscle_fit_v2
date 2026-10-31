"""Referencias curtas do catalogo da academia na geracao de Modelo por IA
(prompt `periodization-v15`): a LLM ESCOLHE uma opcao valida e o MOTOR monta
o trio. Dono unico desta regra (mapa `docs/motores/FONTES_DA_VERDADE.md`).

Problema medido (docs/ia/EVAL_PERIODIZACAO_2026-10-31.md): ate a v14 o prompt
mandava o `gym_catalog` e pedia, por item do Modo B, o trio
`muscle_group_id` + `mapping_id` + `equipment_id` copiado de UMA linha. O
erro dominante, nos dois provedores, era o trio montado errado (par de uma
linha com o grupo de outra, ou par fora do grupo). O validador consertava
com `EQUIPMENT_REPLACED`, e o rascunho saia com exercicios que a IA nao
escolheu.

Desenho:

1. `build_index(context)`: cada linha do catalogo da academia ganha uma
   referencia `REF_PREFIX` + numero sequencial (ex.: "E17"), na ordem
   deterministica grupo (nome, como o JSON com `sort_keys` do prompt) x
   posicao no catalogo (o ranking do recomendador). A referencia embute o
   trio: o grupo e o da CHAVE do catalogo (o PRIMARY do exercicio, a mesma
   regra de `_load_gym_pairs` do validador) e o par e o da linha. Funcao
   pura do contexto: o prompt, o schema e a resolucao chamam a mesma, entao
   nao ha estado a guardar entre a montagem e a volta (e o contexto congelado
   do eval serve sem mudanca).
2. Prompt e schema: o catalogo vai a LLM com `ref` no lugar de
   `mapping_id`/`equipment_id` (`catalog_for_prompt`), e o item do Modo B tem
   um unico campo `CATALOG_REF_FIELD`, restrito no schema a uma `enum` com as
   referencias da academia quando cabe (`schema_enum`; limites abaixo).
3. `resolve_output(raw, index)`: ANTES de qualquer validacao/persistencia,
   cada referencia vira o trio (`item_kind='equipment'`, `muscle_group_id`,
   `mapping_id`, `equipment_id`). Referencia desconhecida (so possivel sem a
   `enum`, ou com um provedor que nao a respeite) deixa o trio vazio: o
   validador remove o item (`ITEM_REMOVED`, "trio incompleto") e o
   complemento de volume preenche o grupo, como ja fazia - nada e inventado.
   A contagem fica no relatorio (`REPORT_KEY`) e no log, para medir em
   producao.

O formato gravado no banco e o rascunho entregue ao frontend NAO mudam: a
saida resolvida tem exatamente os campos da v14.

Limites de `enum` verificados na documentacao oficial (2026-10-31):
- OpenAI, Structured Outputs (strict): ate 1000 valores de `enum` no schema
  inteiro; numa `enum` de strings com MAIS de 250 valores, a soma dos
  tamanhos nao pode passar de 15.000 caracteres; ate 120.000 caracteres
  somando nomes de propriedade, valores de `enum` e `const`.
  https://developers.openai.com/api/docs/guides/structured-outputs
- Anthropic, Structured outputs: `enum` de strings suportada, sem teto
  numerico publicado; ha limites internos de tamanho da gramatica compilada
  (400 "Schema is too complex for compilation").
  https://platform.claude.com/docs/en/build-with-claude/structured-outputs
`MAX_ENUM_REFS` fica no limiar mais restritivo (250: ate ele nem a regra de
tamanho da OpenAI se aplica). O catalogo ja e limitado por
`context.MAX_CATALOG_ITEMS_PER_GROUP` por grupo; acima do teto o schema cai
para string livre e a resolucao trata a referencia desconhecida.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Final, Mapping, Optional

REF_PREFIX: Final[str] = "E"
CATALOG_REF_FIELD: Final[str] = "catalog_ref"
# Chave do relatorio de validacao com a contagem das referencias.
REPORT_KEY: Final[str] = "catalog_refs"
# Auto-fix auditavel por referencia desconhecida (o item sai na validacao).
CATALOG_REF_UNKNOWN: Final[str] = "CATALOG_REF_UNKNOWN"

ENUM_LIMITS_SOURCE_URLS: Final[tuple[str, ...]] = (
    "https://developers.openai.com/api/docs/guides/structured-outputs",
    "https://platform.claude.com/docs/en/build-with-claude/structured-outputs",
)
# OpenAI strict: acima de 250 valores numa `enum` de strings passa a valer o
# teto de 15.000 caracteres (e 1000 valores no schema inteiro).
MAX_ENUM_REFS: Final[int] = 250

# Campos do catalogo que a referencia substitui no prompt.
_PAIR_FIELDS: Final[tuple[str, ...]] = ("mapping_id", "equipment_id")
_TRIO_FIELDS: Final[tuple[str, ...]] = ("muscle_group_id", "mapping_id", "equipment_id")


@dataclass(frozen=True)
class CatalogRef:
    ref: str
    muscle_group_id: int
    mapping_id: int
    equipment_id: int


@dataclass
class ResolutionStats:
    """Contagem da volta da LLM. `unknown`: as referencias que nao existem no
    catalogo da academia (com slot e posicao, para o relatorio)."""

    total: int = 0
    resolved: int = 0
    unknown: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"total": self.total, "resolved": self.resolved, "unknown": len(self.unknown)}


def _ref_name(number: int) -> str:
    return f"{REF_PREFIX}{number}"


def _ordered_groups(gym_catalog: Mapping[str, Any]) -> list[str]:
    # A mesma ordem do JSON do prompt (`context._json` usa `sort_keys`).
    return sorted(gym_catalog)


def build_index(context: Mapping[str, Any]) -> dict[str, CatalogRef]:
    """Referencia -> trio, para o catalogo da academia do contexto. Vazio sem
    catalogo (Modo A, ou Modo B sem academia). Linha cujo grupo nao esta em
    `muscle_groups` (catalogo e cadastro dessincronizados) ou sem par fica
    sem referencia: a LLM nao tem como escolher o que o motor nao monta."""
    gym_catalog = context.get("gym_catalog") or {}
    group_ids = {str(g["name"]): int(g["id"]) for g in context.get("muscle_groups") or [] if g.get("name") is not None}
    index: dict[str, CatalogRef] = {}
    number = 0
    for group in _ordered_groups(gym_catalog):
        group_id = group_ids.get(group)
        if group_id is None:
            continue
        for row in gym_catalog[group] or []:
            if not row or row.get("mapping_id") is None or row.get("equipment_id") is None:
                continue
            number += 1
            ref = _ref_name(number)
            index[ref] = CatalogRef(ref, group_id, int(row["mapping_id"]), int(row["equipment_id"]))
    return index


def catalog_for_prompt(context: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """O catalogo que vai ao prompt: cada linha com `ref` e sem os ids do par
    (o que a LLM nao precisa copiar, nao tem como errar). Mesmos grupos, mesma
    ordem e mesmos demais campos (nomes, seguranca, tecnicas, contribuicoes,
    `is_compound`) da v14."""
    index = build_index(context)
    by_trio = {(c.muscle_group_id, c.mapping_id, c.equipment_id): c.ref for c in index.values()}
    group_ids = {str(g["name"]): int(g["id"]) for g in context.get("muscle_groups") or [] if g.get("name") is not None}
    out: dict[str, list[dict[str, Any]]] = {}
    gym_catalog = context.get("gym_catalog") or {}
    for group in _ordered_groups(gym_catalog):
        rows = []
        for row in gym_catalog[group] or []:
            if not row:
                continue
            ref = by_trio.get((group_ids.get(group), row.get("mapping_id"), row.get("equipment_id")))
            if ref is None:
                continue
            rows.append({"ref": ref, **{k: v for k, v in row.items() if k not in _PAIR_FIELDS}})
        if rows:
            out[group] = rows
    return out


def schema_enum(index: Mapping[str, CatalogRef]) -> Optional[list[str]]:
    """Valores da `enum` do campo de referencia, ou None quando a restricao
    nao cabe nos limites dos provedores (ou nao ha catalogo): o schema usa
    string livre e a resolucao trata a referencia desconhecida."""
    if not index or len(index) > MAX_ENUM_REFS:
        return None
    return list(index)


def resolve_output(raw_output: Any, index: Mapping[str, CatalogRef]) -> tuple[Any, ResolutionStats]:
    """Saida crua da LLM com cada `CATALOG_REF_FIELD` trocado pelo trio, e a
    contagem. Item sem o campo (Modo A) passa intocado. Nao muta a entrada.
    A comparacao ignora caixa e espacos (a Anthropic nao garante a caixa de
    valores de `enum`)."""
    stats = ResolutionStats()
    if not isinstance(raw_output, dict):
        return raw_output, stats
    out = copy.deepcopy(raw_output)
    by_key = {ref.strip().upper(): entry for ref, entry in index.items()}
    template = out.get("template")
    slots = template.get("slots") if isinstance(template, dict) else None
    for slot in slots if isinstance(slots, list) else []:
        items = slot.get("items") if isinstance(slot, dict) else None
        for position, item in enumerate(items if isinstance(items, list) else [], start=1):
            if not isinstance(item, dict) or CATALOG_REF_FIELD not in item:
                continue
            ref = item.pop(CATALOG_REF_FIELD)
            stats.total += 1
            entry = by_key.get(str(ref).strip().upper()) if ref is not None else None
            item["item_kind"] = "equipment"
            if entry is None:
                for name in _TRIO_FIELDS:
                    item[name] = None
                stats.unknown.append({"slot": slot.get("sequence"), "item": position, "ref": ref})
                continue
            stats.resolved += 1
            item["muscle_group_id"] = entry.muscle_group_id
            item["mapping_id"] = entry.mapping_id
            item["equipment_id"] = entry.equipment_id
    return out, stats


def attach_to_report(report: dict[str, Any], stats: ResolutionStats) -> dict[str, Any]:
    """Relatorio da validacao com a contagem (`REPORT_KEY`) e um auto-fix
    `CATALOG_REF_UNKNOWN` por referencia desconhecida (o item ja saiu como
    `ITEM_REMOVED` na validacao). Sem referencia na saida (Modo A), o
    relatorio fica como era."""
    if not stats.total:
        return report
    report[REPORT_KEY] = stats.to_dict()
    fixes = report.setdefault("auto_fixes", [])
    for entry in stats.unknown:
        fixes.append({"code": CATALOG_REF_UNKNOWN, **entry})
    return report


__all__ = [
    "CATALOG_REF_FIELD",
    "CATALOG_REF_UNKNOWN",
    "CatalogRef",
    "ENUM_LIMITS_SOURCE_URLS",
    "MAX_ENUM_REFS",
    "REF_PREFIX",
    "REPORT_KEY",
    "ResolutionStats",
    "attach_to_report",
    "build_index",
    "catalog_for_prompt",
    "resolve_output",
    "schema_enum",
]
