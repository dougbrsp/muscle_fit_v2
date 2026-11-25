"""Sugestão de perfil de resistência por IA (issue #288, lote 42). Roda no worker da Lambda
`musclefit-workout-ai` (despacho de `workout_ai_generation_core`), disparado por
`POST /resistance-profiles/suggest` (`resistance_profiles.py`).

Regra dos motores: vocabulário, esquema da saída, validação e transições são do dono
`domain/resistance_profile.py` (`export_vocabulary`, `suggestion_output_schema`, `validate_profile`,
`can_transition`); aqui só se lê o par, monta o prompt (prosa em `api/privado/prompts/resistance_profile/`),
chama o modelo leve e grava. A IA só cria `suggested`; nunca sobrescreve revisado, verificado nem rejeitado.

Um par com falha (IA, resposta inválida, gravação) não derruba o lote: entra em `failed` com o código.
Sem cota de IA por usuário: a rota é do admin do catálogo (`admin.catalogos:editar`) e o job é limitado a
`rp.SUGGEST_PAIRS_PER_JOB` pares.

`prompt_version` / mudanças:
- resistance-suggest-v1 (2026-11-23): versão inicial.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional, Sequence

from . import async_jobs, prompt_store
from .db import transaction
from .domain import resistance_profile as rp
from .openai_client import LLM_TIMEOUT_LIVE_SECONDS, call_openai_structured
from .prompt_user_data import sanitize_user_text, user_data_block, user_data_rule

logger = logging.getLogger("musclefit.resistance_profile_ai")

JOB_TYPE_SUGGEST = rp.SUGGEST_JOB_TYPE
PROMPT_VERSION = "resistance-suggest-v1"
PROMPT_FEATURE_DIR = "resistance_profile"
PROMPT_NAME = "suggest"
SCHEMA_NAME = "resistance_profile_suggestion"

# Rótulo de `llm.usage` e de roteamento do provedor: reaproveita o de `exercise_summary` (feature leve: Haiku e
# esforço baixo no Claude). Feature própria exige registro em `llm_anthropic.LIGHT_FEATURES` e no teto de saída do
# `openai_client`; fica para quando a feature ganhar env própria.
LLM_FEATURE = "exercise_summary"
MODEL_ENV_VAR = "OPENAI_RESISTANCE_PROFILE_MODEL"
DEFAULT_MODEL = "gpt-5.4-mini"
CALL_TIMEOUT_SECONDS = LLM_TIMEOUT_LIVE_SECONDS

# Orçamento = timeout da Lambda (`async_jobs`) menos a reserva (cold start, escrita do resultado do job). Cada
# chamada usa `min(CALL_TIMEOUT_SECONDS, restante)`; com menos que `MIN_CALL_SECONDS` de restante o par sai como
# `not_processed` e o admin repete.
LAMBDA_FUNCTION = "musclefit-workout-ai"
RESERVE_SECONDS = 8
MIN_CALL_SECONDS = 5
PAIR_DATA_MAX_CHARS = 2000
RAG_FILENAME = "PERFIS_RESISTENCIA.md"
RAG_MAX_CHARS = 8000
RAG_EMPTY = "(trecho indisponível)"
REASON = "Sugestão automática"

ERR_PAIR_NOT_FOUND = "pair_not_found"
ERR_AI_FAILED = "ai_failed"
ERR_INVALID_RESPONSE = "invalid_response"
ERR_WRITE_FAILED = "write_failed"
ERR_NOT_PROCESSED = "not_processed"

# Mesmos nomes de `resistance_profiles.PROFILES`/`HISTORY` (o teste confere; sem importar a rota: ciclo).
PROFILES = "equipment_exercise_resistance_profiles"
HISTORY = "equipment_exercise_resistance_profile_history"
_COMPONENTS = ("source", "mechanism", "trajectory", "profile", "line", "direction_changes")

PAIR_INPUT_SQL = (
    "SELECT eqex.id AS equipment_exercise_id, ec.name AS exercise_name, ec.body_position, ec.movement, "
    "ec.trajectory, ec.biomechanical_pattern, ec.primary_joint, ec.resistance_vector, eq.name AS equipment_name, "
    f"et.name AS equipment_type, eq.manufacturer, p.status AS profile_status FROM equipment_exercises eqex "
    f"JOIN equipment eq ON eq.id = eqex.equipment_id JOIN exercise_catalog ec ON ec.id = eqex.exercise_id "
    f"LEFT JOIN equipment_types et ON et.id = eq.equipment_type_id "
    f"LEFT JOIN {PROFILES} p ON p.equipment_exercise_id = eqex.id WHERE eqex.is_active = 1 AND eqex.id IN ({{marks}})"
)
PROFILE_FOR_UPDATE_SQL = f"SELECT id, status FROM {PROFILES} WHERE equipment_exercise_id = %s FOR UPDATE"
INSERT_SQL = (
    f"INSERT INTO {PROFILES} (equipment_exercise_id, {', '.join(_COMPONENTS)}, flags_json, confidence, status, "
    "origin, rationale, vocabulary_version, model_version, created_by) VALUES "
    f"(%s, {', '.join(['%s'] * len(_COMPONENTS))}, %s, %s, %s, %s, %s, %s, %s, %s)"
)
UPDATE_SQL = (
    f"UPDATE {PROFILES} SET {', '.join(c + ' = %s' for c in _COMPONENTS)}, flags_json = %s, confidence = %s, "
    "status = %s, origin = %s, rationale = %s, vocabulary_version = %s, model_version = %s WHERE id = %s"
)
HISTORY_INSERT_SQL = (
    f"INSERT INTO {HISTORY} (profile_id, before_json, after_json, actor_user_id, origin, reason) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)

_now = time.monotonic  # trocável nos testes


def resolve_model() -> str:
    return (os.environ.get(MODEL_ENV_VAR) or "").strip() or DEFAULT_MODEL


def _vocabulary_text() -> str:
    vocab = rp.export_vocabulary()
    linhas = []
    for campo, codigos in vocab.items():
        if campo in ("version", "origin") or not isinstance(codigos, dict):
            continue
        linhas.append(f"- {campo}: " + "; ".join(f"`{codigo}` = {rotulo}" for codigo, rotulo in codigos.items()))
    return "\n".join(linhas)


def _remaining(inicio: float) -> float:
    return async_jobs.LAMBDA_TIMEOUT_SECONDS[LAMBDA_FUNCTION] - RESERVE_SECONDS - (_now() - inicio)


def _rag_text() -> str:
    from . import rag_chunking, rag_storage

    try:
        rag_storage.ensure_rag_local()  # em produção o RAG vem do S3 para /tmp/rag (nunca levanta)
        texto = (rag_chunking.RAG_MARKDOWN_DIR / RAG_FILENAME).read_text(encoding="utf-8")
    except OSError:  # RAG indisponível não derruba a sugestão (padrão do cardio_agent_ai)
        logger.warning("RAG %s indisponível; sugestão segue sem o trecho", RAG_FILENAME)
        return RAG_EMPTY
    return rag_chunking.truncate_rag(texto, RAG_MAX_CHARS, RAG_FILENAME) or RAG_EMPTY


def _pair_block(row: dict[str, Any]) -> str:
    campos = {
        "exercicio": row.get("exercise_name"), "posicao_corporal": row.get("body_position"),
        "movimento": row.get("movement"), "trajetoria": row.get("trajectory"),
        "padrao_biomecanico": row.get("biomechanical_pattern"), "articulacao_principal": row.get("primary_joint"),
        "vetor_de_resistencia": row.get("resistance_vector"), "equipamento": row.get("equipment_name"),
        "tipo_do_equipamento": row.get("equipment_type"), "fabricante": row.get("manufacturer"),
    }
    texto = json.dumps({k: v for k, v in campos.items() if v}, ensure_ascii=False)
    return user_data_block(sanitize_user_text(texto, PAIR_DATA_MAX_CHARS))


def build_prompt(row: dict[str, Any], rag: Optional[str] = None) -> str:
    return prompt_store.render(
        PROMPT_FEATURE_DIR,
        PROMPT_NAME,
        {
            "regra_dados_usuario": user_data_rule(),
            "vocabulary_version": rp.RESISTANCE_VOCABULARY_VERSION,
            "vocabulary": _vocabulary_text(),
            "pair": _pair_block(row),
            "rag": _rag_text() if rag is None else rag,
        },
    )


def validate_response(data: Any) -> rp.ValidatedProfile:
    """Pelo dono, com origem IA e status sugerido forçados (a IA não decide revisão)."""
    if not isinstance(data, dict):
        raise rp.ResistanceProfileError("invalid_code", "response")
    return rp.validate_profile({**data, "origin": rp.ORIGIN_AI, "status": rp.STATUS_SUGGESTED})


def _rationale(data: Any) -> Optional[str]:
    texto = data.get("rationale") if isinstance(data, dict) else None
    return texto.strip()[:2000] or None if isinstance(texto, str) else None


def _write(pair_id: int, valido: rp.ValidatedProfile, rationale: Optional[str], model: str, user_id: int) -> bool:
    """Grava num par. False = a transição não é permitida (revisado/verificado/rejeitado): não sobrescreve."""
    flags = sorted(valido.flags)
    depois = {
        **{c: getattr(valido, c) for c in _COMPONENTS}, "flags": flags, "confidence": valido.confidence,
        "status": valido.status, "origin": valido.origin, "rationale": rationale,
        "vocabulary_version": rp.RESISTANCE_VOCABULARY_VERSION, "model_version": model,
    }
    valores = (
        *(getattr(valido, c) for c in _COMPONENTS), json.dumps(flags), valido.confidence, valido.status,
        valido.origin, rationale, rp.RESISTANCE_VOCABULARY_VERSION, model[:64],
    )
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(PROFILE_FOR_UPDATE_SQL, (pair_id,))
            atual = cur.fetchone()
            if not rp.can_transition(atual["status"] if atual else None, valido.status, valido.origin):
                return False
            if atual:
                profile_id = int(atual["id"])
                cur.execute(UPDATE_SQL, (*valores, profile_id))
            else:
                cur.execute(INSERT_SQL, (pair_id, *valores, user_id))
                profile_id = int(cur.lastrowid)
            cur.execute(HISTORY_INSERT_SQL, (
                profile_id, json.dumps({"status": atual["status"]}) if atual else None, json.dumps(depois),
                user_id, valido.origin, REASON,
            ))
    return True


def _load_pairs(ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(PAIR_INPUT_SQL.format(marks=", ".join(["%s"] * len(ids))), tuple(ids))
            return {int(r["equipment_exercise_id"]): r for r in cur.fetchall() or []}


def suggest_pairs(ids: Sequence[Any], user_id: int) -> dict[str, Any]:
    """`{processed, suggested, skipped, failed: [{id, code}], ...}`. Uma consulta para os N pares; cada par numa
    transação própria (falha de um não desfaz os outros)."""
    pares: list[int] = []
    for i in ids:
        if isinstance(i, int) and not isinstance(i, bool) and i > 0 and i not in pares:
            pares.append(i)
    inicio = _now()
    acima_do_teto = pares[rp.SUGGEST_PAIRS_PER_JOB:]
    pares = pares[: rp.SUGGEST_PAIRS_PER_JOB]
    out: dict[str, Any] = {
        "processed": 0, "suggested": 0, "skipped": 0, "failed": [], "model": None,
        "prompt_version": PROMPT_VERSION, "vocabulary_version": rp.RESISTANCE_VOCABULARY_VERSION,
    }
    falhas: list[dict[str, Any]] = out["failed"]
    if not pares:
        return out
    linhas = _load_pairs(pares)
    rag = _rag_text()
    for pid in pares:
        row = linhas.get(pid)
        if row is None:
            falhas.append({"id": pid, "code": ERR_PAIR_NOT_FOUND})
            continue
        out["processed"] += 1
        if not rp.can_transition(row.get("profile_status"), rp.STATUS_SUGGESTED, rp.ORIGIN_AI):
            out["skipped"] += 1  # revisado/verificado/rejeitado: a IA não toca
            continue
        restante = _remaining(inicio)
        if restante < MIN_CALL_SECONDS:
            falhas.append({"id": pid, "code": ERR_NOT_PROCESSED})
            continue
        try:
            resposta = call_openai_structured(
                build_prompt(row, rag), resolve_model(), rp.suggestion_output_schema(),
                schema_name=SCHEMA_NAME, timeout=min(CALL_TIMEOUT_SECONDS, restante), feature=LLM_FEATURE,
                user_id=user_id, prompt_version=PROMPT_VERSION,
            )
        except Exception:  # noqa: BLE001 - um par com falha não derruba o lote
            logger.exception("Falha da IA no perfil de resistência do par %s", pid)
            falhas.append({"id": pid, "code": ERR_AI_FAILED})
            continue
        out["model"] = resposta.model
        try:
            valido = validate_response(resposta.data)
        except rp.ResistanceProfileError:
            falhas.append({"id": pid, "code": ERR_INVALID_RESPONSE})
            continue
        try:
            gravou = _write(pid, valido, _rationale(resposta.data), resposta.model, user_id)
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao gravar o perfil de resistência do par %s", pid)
            falhas.append({"id": pid, "code": ERR_WRITE_FAILED})
            continue
        out["suggested" if gravou else "skipped"] += 1
    falhas.extend({"id": pid, "code": ERR_NOT_PROCESSED} for pid in acima_do_teto)  # a rota já limita; defesa
    return out


def dispatch_suggest(payload: dict[str, Any], user_id: Any, ai_usage_id: Optional[int] = None) -> dict[str, Any]:
    """Resultado do job (`GET /jobs/{id}`). `ai_usage_id` não se aplica (sem cota de IA para o admin)."""
    return suggest_pairs(payload.get("equipment_exercise_ids") or [], int(user_id))


__all__ = ["JOB_TYPE_SUGGEST", "PROMPT_VERSION", "build_prompt", "dispatch_suggest", "suggest_pairs", "validate_response"]
