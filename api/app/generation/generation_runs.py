"""`generation_runs` (migracao 054, M16): a auditoria de COMO uma ficha foi
gerada deixa de viver so em lugar efemero.

Antes deste modulo o `generation_audit` produzido pelos motores existia em
dois lugares, os dois com prazo de validade:

- `workout_plans.raw_payload.generation_audit` - so chega ao banco se o
  usuario SALVAR o rascunho **e** o cliente devolver o `raw_payload` inteiro
  no POST. Rascunho descartado nao deixa rastro;
- o item do job no DynamoDB (`app/async_jobs.py`), com `ttl` de **6 horas**.

Ou seja: seis horas depois da geracao, o unico registro possivel e o que o
cliente eventualmente ecoou de volta. A F3.1 (`/dashboard/engine`: planejado
x gerado x executado) precisa da serie HISTORICA disso.

**Duas escritas, as duas best-effort.**

1. `record_generation_run` - **1 INSERT**, no fim de
   `plan_generation_core.dispatch_plan_generation` (o unico ponto por onde
   passam os quatro motores: Modo A, Modo B, os `_legacy` e o caminho v1).
   Nenhum SELECT: tudo o que a linha grava ja esta no rascunho em memoria. O
   caminho quente da geracao ganha exatamente um statement.
2. `link_run_to_plan` - **1 UPDATE**, em `services.create_workout_plan`, e so
   quando o `raw_payload` salvo traz um `generation_audit`. Marca qual ficha
   nasceu daquele rascunho.

As duas seguem o padrao de `_attach_volume_coverage_warnings`
(`app/workout_plan_templates.py`): import tardio no chamador, excecao
engolida com log. **Auditoria nao pode transformar uma geracao boa em erro
502, nem um save bom em 500** - a mesma regra que
`periodization/logs.finish_log` ja aplica para `template_generation_logs`.

E ela tambem nao pode mudar a ASSINATURA de quem audita. A primeira versao
deste PR acrescentou um `job_id` a `dispatch_plan_generation`; o CI mostrou o
estrago na hora: um chamador com a aridade antiga virou `TypeError` dentro do
handler da Lambda, que marcou como `failed` um job cuja geracao tinha corrido
bem. A geracao foi quebrada pela auditoria - pela porta da frente, sem passar
por nenhum `try`. Por isso o `job_id` chega pelo lado: a Lambda chama
`set_current_job_id` antes de gerar, e `record_generation_run` o consome.

**Como a ligacao rascunho -> ficha funciona sem tocar no formato do bloco.**
A geracao acontece na Lambda e devolve um rascunho; a ficha so nasce depois,
num POST separado, se o usuario salvar. Carimbar um `generation_run_id`
dentro do `generation_audit` mudaria o formato do bloco - o M16 proibe mudar
forma e destino no mesmo PR. Entao a identidade e o `audit_hash`: sha256 do
JSON CANONICO (chaves ordenadas) do proprio bloco. A geracao grava o hash; o
save recalcula o hash do `raw_payload.generation_audit` e marca a linha mais
antiga ainda sem ficha. Nenhum byte da auditoria muda.

`raw_payload.generation_audit` CONTINUA sendo gravado como hoje: o
congelamento do Modo B le `raw_payload.generation_audit.substitutions[]`
(`generation/freeze_mode_b.py`). A duplicacao e deliberada e dura ate a F3.1
estar em producao lendo `generation_runs` - ver o cabecalho da 054.
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextvars import ContextVar
from typing import Any, Mapping, Optional

logger = logging.getLogger("musclefit.generation.runs")

TABLE = "generation_runs"

# `job_id` do DynamoDB da invocacao corrente. Ele NAO entra na assinatura de
# `dispatch_plan_generation`: essa funcao e o contrato da geracao, e a
# auditoria nao tem direito de mudar contrato de quem ela audita. Quando isso
# foi tentado, o efeito apareceu na hora - um duble de teste com a aridade
# antiga virou `TypeError` DENTRO do handler da Lambda, e o handler marcou
# como `failed` um job cuja geracao tinha corrido bem. E exatamente o que o
# M16 existe para nao acontecer, so que pela porta da frente.
#
# `ContextVar` e nao variavel de modulo: o mesmo processo serve requisicoes
# concorrentes na `api-core`.
_CURRENT_JOB_ID: ContextVar[Optional[str]] = ContextVar("musclefit_generation_job_id", default=None)

INSERT_SQL = f"""
INSERT INTO {TABLE} (
    user_id, job_id, template_id, template_slot_sequence, volume_mode,
    engine_version, llm_used, plan_date, audit_hash, audit_json, request_json,
    template_version_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

# `ORDER BY id ASC LIMIT 1`: o hash NAO e unico (duas geracoes podem, em
# tese, produzir auditorias identicas - e um UNIQUE ali faria a SEGUNDA
# geracao falhar). Liga-se a linha mais antiga ainda sem ficha.
LINK_SQL = f"""
UPDATE {TABLE}
SET workout_plan_id = %s
WHERE user_id = %s AND audit_hash = %s AND workout_plan_id IS NULL
ORDER BY id ASC
LIMIT 1
"""


def set_current_job_id(job_id: Any) -> None:
    """Deixa o `job_id` da invocacao ao alcance da auditoria, sem passar por
    `dispatch_plan_generation`. Nunca levanta."""
    try:
        _CURRENT_JOB_ID.set(str(job_id) if job_id else None)
    except Exception:  # noqa: BLE001 - auditoria nunca derruba a geracao
        logger.exception("Falha ao registrar o job_id da geracao (job_id=%r)", job_id)


def pop_current_job_id() -> Optional[str]:
    """Le e CONSOME o `job_id` corrente.

    Consome de proposito: um job produz uma geracao, entao um `job_id` marca
    no maximo uma linha. Sem consumir, uma invocacao seguinte que nao tivesse
    job_id herdaria o da anterior no mesmo container quente da Lambda - e a
    linha nasceria com uma procedencia falsa, que e pior que nenhuma."""
    try:
        job_id = _CURRENT_JOB_ID.get(None)
        if job_id is not None:
            _CURRENT_JOB_ID.set(None)
        return job_id
    except Exception:  # noqa: BLE001 - auditoria nunca derruba a geracao
        logger.exception("Falha ao ler o job_id da geracao")
        return None


def canonical_json(audit: Any) -> str:
    """JSON canonico do bloco: chaves ordenadas e separadores fixos, para que
    o mesmo conteudo produza sempre o mesmo texto - dos dois lados da
    ligacao.

    O hash e calculado AQUI, em Python, e nunca a partir da coluna: uma coluna
    JSON do MySQL guarda a forma binaria e normaliza a ordem das chaves por
    conta propria, entao o texto que volta do banco nao e o texto que entrou.
    O conteudo gravado e o bloco inteiro, sem campo acrescentado nem removido."""
    return json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def audit_hash(audit: Any) -> str:
    """sha256 hex do JSON canonico do `generation_audit`."""
    return hashlib.sha256(canonical_json(audit).encode("utf-8")).hexdigest()


def extract_audit(raw_payload: Any) -> Optional[dict[str, Any]]:
    """`generation_audit` de um `raw_payload`, ou None quando nao ha.

    Ficha criada a mao, ficha antiga e slot de descanso (`_empty_rest_day_draft`,
    que nao produz auditoria) caem aqui - e nesses casos nao ha nada a
    persistir nem a ligar."""
    if not isinstance(raw_payload, Mapping):
        return None
    audit = raw_payload.get("generation_audit")
    return audit if isinstance(audit, Mapping) else None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _first_int(*values: Any) -> Optional[int]:
    """Primeiro valor que vira inteiro, em ordem de preferencia.

    Explicitamente `is not None` e nao `or`: `slot_sequence` e um numero de
    posicao, e um `0` legitimo cairia para o proximo candidato num `or`."""
    for value in values:
        convertido = _as_int(value)
        if convertido is not None:
            return convertido
    return None


def record_generation_run(
    draft: Any,
    *,
    user_id: Optional[int],
    job_id: Optional[str] = None,
    request: Any = None,
) -> Optional[int]:
    """Grava a auditoria do rascunho recem-gerado. **1 INSERT, zero SELECT.**

    Devolve o id da linha, ou None quando nada foi gravado (sem auditoria,
    sem usuario, ou falha - a falha NUNCA sobe: uma ficha boa nao pode virar
    502 porque a auditoria nao coube no banco)."""
    try:
        if not isinstance(draft, Mapping) or user_id is None:
            return None
        audit = extract_audit(draft.get("raw_payload"))
        if audit is None:
            return None

        from ..db import transaction

        job = job_id if job_id else pop_current_job_id()
        params = (
            int(user_id),
            str(job)[:36] if job else None,
            _first_int(audit.get("template_id"), draft.get("template_id")),
            _first_int(audit.get("slot_sequence"), draft.get("template_slot_sequence")),
            _stringify(audit.get("volume_mode"), 40),
            _stringify(audit.get("engine_version"), 40),
            1 if audit.get("llm_used") else 0,
            draft.get("plan_date") or None,
            audit_hash(audit),
            canonical_json(audit),
            json.dumps(_request_as_dict(request), ensure_ascii=False, default=str) if request is not None else None,
            # 066: versao do Modelo lida nesta geracao (fora do `audit_json`
            # de proposito: o hash da auditoria liga run e ficha e nao muda).
            _first_int(draft.get("template_version_id")),
        )
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(INSERT_SQL, params)
                return int(cur.lastrowid)
    except Exception:  # noqa: BLE001 - auditoria nunca derruba a geracao (M16, exigencia 1)
        logger.exception("Falha ao persistir generation_runs (user_id=%s, job_id=%s)", user_id, job_id)
        return None


def link_run_to_plan(conn: Any, *, user_id: Optional[int], plan_id: int, raw_payload: Any) -> None:
    """Liga a ficha recem-salva a linha de `generation_runs` que a originou.
    **1 UPDATE**, e so quando o `raw_payload` traz um `generation_audit`.

    Recebe a conexao ABERTA do `create_workout_plan` de proposito: abrir uma
    segunda transacao so para a auditoria custaria outra ida ao banco no
    caminho do save. A excecao continua engolida - uma ficha salva com
    sucesso nao pode virar 500 por causa da ligacao."""
    try:
        audit = extract_audit(raw_payload)
        if audit is None or user_id is None:
            return
        with conn.cursor() as cur:
            cur.execute(LINK_SQL, (int(plan_id), int(user_id), audit_hash(audit)))
    except Exception:  # noqa: BLE001 - auditoria nunca derruba o save da ficha
        logger.exception("Falha ao ligar generation_runs ao plano id=%s", plan_id)


def _stringify(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _request_as_dict(request: Any) -> Any:
    """`WorkoutPlanGenerateIn` (pydantic v2), dict, ou o que vier - sem
    importar `models` aqui, para nao acoplar a auditoria ao schema de
    entrada."""
    dump = getattr(request, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return request


__all__ = [
    "TABLE",
    "audit_hash",
    "canonical_json",
    "extract_audit",
    "link_run_to_plan",
    "pop_current_job_id",
    "record_generation_run",
    "set_current_job_id",
]
