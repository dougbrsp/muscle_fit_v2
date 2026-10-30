"""Adaptador PURO dos schemas strict (formato da OpenAI) para a saida
estruturada da Anthropic (`output_config.format`, tipo `json_schema`).

Os schemas das features (avaliacao de serie, leitura da refeicao, rotulo,
cardio, periodizacao, descricao de execucao) foram escritos para o strict da
OpenAI, onde todo campo e obrigatorio e "opcional" se escreve como anulavel
(`"type": ["integer", "null"]` ou `anyOf` com `{"type": "null"}`). O subconjunto
da Anthropic aceita isso, mas com LIMITES por pedido (documentacao oficial
"Structured outputs", secao de limites, consultada em 2026-10-30):

- no maximo `MAX_UNION_PARAMS` parametros com `anyOf` ou lista de tipos;
- no maximo `MAX_OPTIONAL_PARAMS` parametros opcionais (fora do `required`).

O rotulo (29 anulaveis) e a periodizacao (21 e 30 com cardio) passam do
primeiro limite. O adaptador troca o EXCEDENTE de anulaveis (na ordem do
schema) por campo OPCIONAL nao anulavel e, na volta, `restore` devolve `None`
a cada campo opcional ausente, na ordem das propriedades do schema original:
o dicionario que o chamador recebe tem a mesma forma de sempre. Sem perda de
significado - "ausente" e "null" sao o mesmo valor para os validadores.

Tambem sai do schema o que o subconjunto nao aceita (restricoes numericas e de
texto, `minItems` acima de 1, `maxItems`): o validador deterministico de cada
feature continua conferindo os limites do lado de ca. Hoje nenhum schema usa
essas palavras; a remocao existe para um schema futuro nao virar 400.

Sem I/O, sem SDK: so dicionarios.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Final, Optional

# Fonte: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
# (tabela de limites; consultada em 2026-10-30).
SCHEMA_LIMITS_SOURCE_URL: Final[str] = "https://platform.claude.com/docs/en/build-with-claude/structured-outputs"
MAX_UNION_PARAMS: Final[int] = 16
MAX_OPTIONAL_PARAMS: Final[int] = 24
# `minItems` aceito so com 0 ou 1.
MAX_SUPPORTED_MIN_ITEMS: Final[int] = 1

# Palavras que o subconjunto nao aceita (restricoes numericas, de texto e de
# lista). Saem do schema enviado; o validador da feature segue valendo.
UNSUPPORTED_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
    }
)

# Passo de caminho: nome da propriedade ou `ITEMS` (elemento de lista).
ITEMS: Final[str] = "[]"


class SchemaAdaptationError(ValueError):
    """O schema nao cabe nos limites mesmo depois da adaptacao."""


@dataclass(frozen=True)
class AdaptedSchema:
    schema: dict
    # Caminhos (tupla de passos) dos campos anulaveis que viraram opcionais.
    optional_paths: frozenset[tuple[str, ...]]
    removed_keywords: tuple[str, ...]


def _is_null(node: Any) -> bool:
    return isinstance(node, dict) and node.get("type") == "null" and len(node) == 1


def _is_union(node: Any) -> bool:
    return isinstance(node, dict) and (isinstance(node.get("type"), list) or "anyOf" in node)


def _non_null_variant(node: dict) -> Optional[dict]:
    """O mesmo no sem a alternativa `null`, ou None se nao der para tirar
    (uniao com mais de um tipo nao nulo, ou sem `null`)."""
    types = node.get("type")
    if isinstance(types, list):
        rest = [t for t in types if t != "null"]
        if len(rest) != 1 or len(rest) == len(types) or "anyOf" in node:
            return None
        out = dict(node)
        out["type"] = rest[0]
        if isinstance(out.get("enum"), list):
            out["enum"] = [v for v in out["enum"] if v is not None]
        return out
    if isinstance(types, str) or "anyOf" not in node:
        return None
    variants = node["anyOf"]
    rest = [v for v in variants if not _is_null(v)]
    if len(rest) != 1 or len(rest) == len(variants):
        return None
    merged = {k: v for k, v in node.items() if k != "anyOf"}
    merged.update(rest[0])
    return merged


def _strip(node: Any, removed: list[str]) -> Any:
    if isinstance(node, list):
        return [_strip(v, removed) for v in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in UNSUPPORTED_KEYWORDS:
            removed.append(key)
            continue
        if key == "minItems" and isinstance(value, int) and value > MAX_SUPPORTED_MIN_ITEMS:
            removed.append(key)
            continue
        if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
            out[key] = {k: _strip(v, removed) for k, v in value.items()}
        else:
            out[key] = _strip(value, removed)
    return out


def _walk(node: Any, path: tuple[str, ...], visit) -> None:
    """Percorre propriedades, itens e alternativas de `anyOf`, em ordem."""
    if not isinstance(node, dict):
        return
    for name, child in (node.get("properties") or {}).items():
        visit(node, name, child, path + (name,))
        _walk(child, path + (name,), visit)
    if "items" in node:
        _walk(node["items"], path + (ITEMS,), visit)
    for variant in node.get("anyOf") or []:
        _walk(variant, path, visit)


def _counts(schema: dict) -> tuple[int, int]:
    unions = 0
    optional = 0

    def visit(parent, name, child, _path):
        nonlocal unions, optional
        if _is_union(child):
            unions += 1
        if name not in (parent.get("required") or []):
            optional += 1

    if _is_union(schema):
        unions += 1
    _walk(schema, (), visit)
    return unions, optional


def adapt_schema(schema: dict) -> AdaptedSchema:
    """Schema aceito pela Anthropic + o que `restore` precisa para devolver
    a forma original. Levanta `SchemaAdaptationError` se nao couber."""
    removed: list[str] = []
    adapted = _strip(copy.deepcopy(schema), removed)
    unions, optional = _counts(adapted)
    excess = unions - MAX_UNION_PARAMS
    converted: set[tuple[str, ...]] = set()
    if excess > 0:
        candidates: list[tuple[dict, str, tuple[str, ...]]] = []

        def visit(parent, name, child, path):
            if name in (parent.get("required") or []) and _non_null_variant(child) is not None:
                candidates.append((parent, name, path))

        _walk(adapted, (), visit)
        # Os ULTIMOS anulaveis do schema viram opcionais: os primeiros (os do
        # topo, como nome e marca no rotulo) seguem como sempre foram.
        for parent, name, path in candidates[-excess:]:
            parent["properties"][name] = _non_null_variant(parent["properties"][name])
            parent["required"] = [r for r in parent["required"] if r != name]
            converted.add(path)
        unions, optional = _counts(adapted)
    if unions > MAX_UNION_PARAMS or optional > MAX_OPTIONAL_PARAMS:
        raise SchemaAdaptationError(
            f"schema fora dos limites da Anthropic: {unions} unioes (max {MAX_UNION_PARAMS}), "
            f"{optional} opcionais (max {MAX_OPTIONAL_PARAMS})"
        )
    return AdaptedSchema(schema=adapted, optional_paths=frozenset(converted), removed_keywords=tuple(removed))


def restore(data: Any, original: dict, optional_paths: frozenset[tuple[str, ...]]) -> Any:
    """Devolve `None` aos campos convertidos que vieram ausentes e reordena as
    chaves de cada objeto tocado pela ordem do schema original."""
    if not optional_paths:
        return data
    return _restore(data, original, (), optional_paths)


def _object_branch(node: dict) -> Optional[dict]:
    if "properties" in node:
        return node
    for variant in node.get("anyOf") or []:
        if isinstance(variant, dict) and "properties" in variant:
            return variant
    return None


def _restore(data: Any, node: Any, path: tuple[str, ...], paths: frozenset[tuple[str, ...]]) -> Any:
    if not isinstance(node, dict):
        return data
    if isinstance(data, list):
        items = node.get("items")
        if items is None:
            for variant in node.get("anyOf") or []:
                if isinstance(variant, dict) and "items" in variant:
                    items = variant["items"]
                    break
        return [_restore(v, items, path + (ITEMS,), paths) for v in data] if items is not None else data
    if not isinstance(data, dict):
        return data
    branch = _object_branch(node)
    if branch is None:
        return data
    props = branch["properties"]
    out: dict = {}
    for name, child in props.items():
        child_path = path + (name,)
        if name in data:
            out[name] = _restore(data[name], child, child_path, paths)
        elif child_path in paths:
            out[name] = None
    for name, value in data.items():
        if name not in out:
            out[name] = value
    return out


__all__ = [
    "AdaptedSchema",
    "ITEMS",
    "MAX_OPTIONAL_PARAMS",
    "MAX_SUPPORTED_MIN_ITEMS",
    "MAX_UNION_PARAMS",
    "SCHEMA_LIMITS_SOURCE_URL",
    "SchemaAdaptationError",
    "UNSUPPORTED_KEYWORDS",
    "adapt_schema",
    "restore",
]
