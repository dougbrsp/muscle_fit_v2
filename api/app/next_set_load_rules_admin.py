"""Cadastro das regras de carga da próxima série (regras de carga, PR R2).

    GET  /next-set-load-rules/published   qualquer autenticado: conjunto em vigor
    GET  /next-set-load-rules/draft       admin: o rascunho (ou null)
    PUT  /next-set-load-rules/draft       admin: grava o rascunho (cria ou substitui)
    POST /next-set-load-rules/publish     admin: `{draft_id, expected_published_version}`
    GET  /next-set-load-rules/versions    admin: histórico (rascunho + publicadas)
    POST /next-set-load-rules/describe    admin: conjunto -> textos, sem gravar
    POST /next-set-load-rules/simulate    admin: `{ruleset | null, facts}` -> sugestão

Paths top-level na api-core (catch-all `{proxy+}`, sem Terraform). Nenhuma
regra aqui: a forma é validada pelo Pydantic do loader
(`next_set_load_rules.py`, `extra="forbid"`), a semântica SÓ pelo dono
(`next_set_load.validate_ruleset`/`ruleset_errors`: erro -> 422 com os
códigos; avisos `WARN_*` voltam sem bloquear), os textos por
`describe_rule`/`describe_ruleset` e a simulação por `evaluate_ruleset`.
Concorrência na publicação -> 409 (`publish_conflict`, com a versão vigente).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from . import next_set_load_rules as rules
from .db import transaction
from .domain import next_set_load as nsl
from .security import Identity, get_current_identity, require_admin

router = APIRouter(prefix="/next-set-load-rules", tags=["next-set-load-rules"])


class DescribeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ruleset: rules.RuleSetIn


class SimulateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ruleset: Optional[rules.RuleSetIn] = None  # None = o conjunto em vigor
    facts: rules.SetFactsIn


def _error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message, **extra})


def _split_codes(ruleset: nsl.RuleSet) -> tuple[list[str], list[str]]:
    codes = nsl.validate_ruleset(ruleset)
    warnings = [code for code in codes if code.startswith(nsl.RULESET_WARNING_PREFIX)]
    return nsl.ruleset_errors(ruleset), warnings


def _convert(payload: rules.RuleSetIn, *, source: str = nsl.RULESET_SOURCE_DRAFT) -> nsl.RuleSet:
    try:
        return rules.ruleset_from_input(payload, source=source)
    except ValueError as exc:
        raise _error(422, "invalid_ruleset", str(exc), codes=[]) from exc


def _validated(payload: rules.RuleSetIn, *, source: str = nsl.RULESET_SOURCE_DRAFT) -> tuple[nsl.RuleSet, list[str]]:
    ruleset = _convert(payload, source=source)
    errors, warnings = _split_codes(ruleset)
    if errors:
        raise _error(422, "invalid_ruleset", "Conjunto de regras fora das regras do motor.", codes=errors)
    return ruleset, warnings


def _published_payload(ruleset: nsl.RuleSet, stored: Optional[rules.StoredRuleSet]) -> dict[str, Any]:
    return {
        "id": stored.id if stored else None,
        "version": ruleset.version,
        "published_at": rules.iso_value(stored.published_at) if stored else None,
        "source": nsl.RULESET_SOURCE_PUBLISHED if stored else nsl.RULESET_SOURCE_DEFAULT,
        "ruleset": rules.ruleset_payload(ruleset),
        "summary": nsl.describe_ruleset(ruleset),
    }


def _draft_payload(stored: Optional[rules.StoredRuleSet]) -> dict[str, Any]:
    if stored is None:
        return {"draft": None, "warnings": []}
    _errors, warnings = _split_codes(stored.ruleset)
    return {"draft": stored.to_dict(), "warnings": warnings}


@router.get("/published")
def get_published(identity: Identity = Depends(get_current_identity)):
    """O conjunto que a avaliação de série usa AGORA (sem linha publicada,
    tabela ausente ou falha de leitura = o padrão em código, versão 0)."""
    ruleset, stored = rules.resolve_published_autocommit()
    return _published_payload(ruleset, stored)


@router.get("/draft")
def get_draft(identity: Identity = Depends(require_admin)):
    with transaction() as conn:
        return _draft_payload(rules.load_draft(conn))


@router.put("/draft")
def put_draft(payload: rules.DraftIn, identity: Identity = Depends(require_admin)):
    ruleset, _warnings = _validated(payload)
    notes = (payload.notes or "").strip() or None
    try:
        with transaction() as conn:
            stored = rules.save_draft(conn, ruleset, notes=notes, user_id=identity.user_id)
    except rules.RuleSetConflictError as exc:
        raise _error(409, exc.code, exc.message) from exc
    return _draft_payload(stored)


@router.post("/publish")
def post_publish(payload: rules.PublishIn, identity: Identity = Depends(require_admin)):
    try:
        with transaction() as conn:
            stored = rules.publish(conn, payload.draft_id, payload.expected_published_version, identity.user_id)
    except rules.RuleSetConflictError as exc:
        raise _error(409, exc.code, exc.message, current_version=exc.current_version) from exc
    except rules.RuleSetNotFoundError as exc:
        raise _error(404, "draft_not_found", "Rascunho não encontrado; recarregue.") from exc
    except rules.RuleSetInvalidError as exc:
        raise _error(422, "invalid_ruleset", "Rascunho fora das regras do motor; corrija e salve.", codes=exc.codes) from exc
    return {"published": stored.to_dict()}


@router.get("/versions")
def get_versions(identity: Identity = Depends(require_admin)):
    with transaction() as conn:
        return {"versions": [stored.to_dict() for stored in rules.list_versions(conn)]}


@router.post("/describe")
def post_describe(payload: DescribeIn, identity: Identity = Depends(require_admin)):
    """Prévia ao vivo da tela: textos e códigos do dono, sem gravar e sem 422
    por regra semântica (a tela mostra os códigos)."""
    ruleset = _convert(payload.ruleset)
    errors, warnings = _split_codes(ruleset)
    return {
        "texts": [nsl.describe_rule(rule, ruleset) for rule in ruleset.rules],
        "summary": nsl.describe_ruleset(ruleset),
        "errors": errors,
        "warnings": warnings,
    }


@router.post("/simulate")
def post_simulate(payload: SimulateIn, identity: Identity = Depends(require_admin)):
    """Uma série pelos fatos dados contra o conjunto enviado (validado pelo
    dono) ou, sem conjunto, o em vigor. `result` null = fora da regra."""
    if payload.ruleset is None:
        ruleset, _stored = rules.resolve_published_autocommit()
    else:
        ruleset, _warnings = _validated(payload.ruleset)
    result = nsl.evaluate_ruleset(payload.facts.to_facts(), ruleset)
    return {
        "ruleset_version": ruleset.version,
        "source": ruleset.source,
        "result": result.to_dict() if result is not None else None,
    }
