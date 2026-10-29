"""Camada de acesso a dados sobre RDS Data API (Aurora Serverless v2).

Substitui o `db.py` original (PyMySQL puro, conexao TCP) do backend on-premise
(`fitness-platform/api/app/db.py`). A interface publica (`get_conn()`,
`transaction()`, cursor com `.execute()/.fetchone()/.fetchall()/.lastrowid/
.rowcount`, `with _connect() as conn:` como context manager) e mantida
compativel de proposito, para que os ~30 call-sites do backend portado
continuem funcionando trocando so o import.

Baseado no shim validado em `spikes/data-api-shim/rds_data_shim.py` (ver
`docs/spike-data-api.md` e `docs/aws-migration-plan.md`, Fase 2/3). Principais
adaptacoes em relacao ao spike:

- Config via env vars da Lambda (`AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`,
  `AURORA_DATABASE`, `AWS_REGION`) em vez de parametros explicitos.
- `Connection` ganha suporte a `with conn:` (context manager), pois varios
  call-sites do backend usam `with _connect() as conn:` esperando conn de
  volta (nao um cursor) e commit implicito no autocommit=True original.
- Mantido: SET FOREIGN_KEY_CHECKS/estado de sessao so persiste dentro de uma
  transacao real do Data API (BeginTransaction/CommitTransaction) - por isso
  cada "conexao logica" abre sua propria transacao no construtor.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import sys
import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Callable, Iterable, Optional, Sequence

import boto3
from botocore.config import Config

# ---------------------------------------------------------------------------
# Client `rds-data` REUSADO por processo (F1.6, desempenho da visao geral).
#
# Antes, cada `Connection` criava `boto3.client("rds-data")` no construtor:
# ~30 ms por client em regime (0,27 s no primeiro) somados a BeginTransaction
# e Rollback - e varios servicos (`IntelligenceServiceBase.fetch_all`,
# `transaction()`) abrem UMA conexao POR CONSULTA. Numa visao geral com ~12
# conexoes, so a criacao de clients custava mais do que todo o SQL.
#
# Clients boto3 sao thread-safe e a propria AWS recomenda reutiliza-los
# (documentacao do botocore: "clients are generally thread safe"). O cache e
# por (modulo `boto3` em uso, regiao): em producao existe um unico modulo e
# uma unica regiao; nos testes o duble troca o modulo (`monkeypatch`), e um
# modulo diferente e uma chave diferente - nunca um client velho apontando
# para um duble ja encerrado. A entrada guarda o proprio modulo (referencia
# forte), entao o `id()` nunca e reaproveitado enquanto a chave existir.
#
# `_fast_fail_client` (config sem retries, caminho raro de rollback apos
# erro) continua criando o proprio client e nao passa por aqui.
# ---------------------------------------------------------------------------

_CLIENT_CACHE: dict[int, tuple[Any, dict[str, Any]]] = {}


def _shared_client(region_name: str) -> Any:
    """Client `rds-data` compartilhado para `region_name` (ver comentario acima)."""
    module = boto3
    entry = _CLIENT_CACHE.get(id(module))
    if entry is None or entry[0] is not module:
        entry = (module, {})
        _CLIENT_CACHE[id(module)] = entry
    por_regiao = entry[1]
    client = por_regiao.get(region_name)
    if client is None:
        client = module.client("rds-data", region_name=region_name)
        por_regiao[region_name] = client
    return client

__all__ = [
    "Connection",
    "Cursor",
    "fetch_rows",
    "get_conn",
    "transaction",
    "DataApiError",
    "is_duplicate_column_error",
    "is_duplicate_key_error",
    "is_unknown_key_error",
]

logger = logging.getLogger("musclefit.db_dataapi")

# Rollback de uma transacao que a Data API ja nao conhece. Fontes:
# - modelo de servico do botocore (`rds-data`, operacao RollbackTransaction):
#   `TransactionNotFoundException` ("The transaction ID wasn't found", HTTP 404);
# - AWS troubleshooting da Data API ("Transaction <id> isn't found"): o id
#   expirou (3 min ocioso) ou e invalido. Clientes/modelos antigos entregavam
#   o mesmo caso como `BadRequestException`/`NotFoundException` com essa mensagem.
# Nao ha nada a desfazer: a transacao ja foi revertida pelo servico.
# Obs.: medido na Aurora de producao, erro de SQL (1146, 1062) NAO aborta a
# transacao - este tratamento e defensivo (expiracao, falha de rede entre
# chamadas), nao o caminho comum de erro de constraint.
_TRANSACTION_GONE_CODES = frozenset({"TransactionNotFoundException"})
_TRANSACTION_GONE_LEGACY_CODES = frozenset({"BadRequestException", "NotFoundException"})
_TRANSACTION_GONE_MESSAGE_RE = re.compile(r"transaction\b.*\b(?:is not|isn't|not) found", re.IGNORECASE | re.DOTALL)


def _aws_error_code(exc: BaseException) -> Optional[str]:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    return (response.get("Error") or {}).get("Code")


def _is_transaction_gone_error(exc: BaseException) -> bool:
    code = _aws_error_code(exc)
    if code in _TRANSACTION_GONE_CODES:
        return True
    if code in _TRANSACTION_GONE_LEGACY_CODES:
        message = str((exc.response.get("Error") or {}).get("Message") or "")  # type: ignore[attr-defined]
        return bool(_TRANSACTION_GONE_MESSAGE_RE.search(message))
    return False


# Erros da propria Data API apos os quais o RollbackTransaction falha de forma
# deterministica. Evidencia de producao (musclefit-api-core,
# GET /cardio-recommendations?limit=200, 2026-09-14): ExecuteStatement ->
# UnsupportedResultException ("The result exceeds the size limit 1 MB") e o
# rollback seguinte -> InternalServerErrorException "reached max retries: 4".
# Nesses casos o rollback usa um client SEM retries: as 4 novas tentativas com
# backoff so atrasam a resposta (e consomem o timeout da Lambda/API Gateway).
# Seguro: se a unica tentativa falhar, o estado final e o mesmo de hoje apos os
# retries - a Data API descarta a transacao ao expirar; nada e commitado.
_FAST_FAIL_ROLLBACK_AFTER = frozenset({"UnsupportedResultException"})
_FAST_FAIL_ROLLBACK_CONFIG = {"total_max_attempts": 1}


def _original_aws_error_code(exc: Optional[BaseException]) -> Optional[str]:
    """Codigo AWS da excecao em andamento: dela mesma (ClientError) ou da causa
    encapsulada (`DataApiError ... from ClientError`, ver `Cursor.execute`)."""
    if exc is None:
        return None
    return _aws_error_code(exc) or _aws_error_code(getattr(exc, "__cause__", None))


# ---------------------------------------------------------------------------
# Retry de `DatabaseResumingException` (auto-pause do Aurora Serverless v2)
#
# Com `MinCapacity = 0` o cluster pausa apos `SecondsUntilAutoPause` sem uso. A
# PRIMEIRA chamada depois da pausa NAO fica apenas lenta: falha com erro duro.
# No modelo de servico do botocore (`rds-data`, 2018-08-01) a excecao e
# `DatabaseResumingException`, HTTP 400 / `senderFault: true`, documentada como
# "A request was cancelled because the Aurora Serverless v2 DB instance was
# paused. The Data API request automatically resumes the DB instance. Wait a
# few seconds and try again."
#
# O boto3 NAO retenta esse codigo: ele nao esta em `_TRANSIENT_ERROR_CODES` nem
# em `_THROTTLED_ERROR_CODES` do `botocore.retries.standard`, e o `_retry.json`
# nem tem entrada para `rds-data`. Sem o retry daqui, a primeira requisicao do
# usuario, o passo `migrate` do deploy e o sync diario do Intervals falhariam a
# cada despertar.
#
# SEGURANCA DE REEXECUCAO: o proprio modelo diz que a requisicao foi CANCELADA
# - o statement nao chegou a ser executado no banco. Por isso retentar nao pode
# duplicar um INSERT. Vale tambem para `ExecuteStatement` dentro de uma
# transacao ja aberta (`transactionId`): se a transacao nao sobreviveu ao
# despertar, a nova tentativa falha com `TransactionNotFoundException`, que NAO
# e retentada e propaga normalmente.
#
# ESCOPO: so as operacoes que o modelo declara como capazes de devolver
# `DatabaseResumingException` - `BeginTransaction` e `ExecuteStatement` (e
# `BatchExecuteStatement`, usado por `Cursor.executemany`). `CommitTransaction` e
# `RollbackTransaction` NAO declaram essa excecao (so acontecem com uma
# transacao ja aberta, ou seja, com o banco acordado), entao ficam de fora.
#
# PARAMETROS: 5 tentativas com esperas fixas e CURTAS de 5 s -> espera total de
# no maximo 20 s, com as tentativas em t = 0, 5, 10, 15 e 20 s. O resume tipico
# e de ~15 s, ou seja, termina entre a 3a e a 4a tentativa: intervalo curto
# MINIMIZA o desperdicio entre o banco ficar pronto e a tentativa que percebe
# isso (com esperas longas, o sucesso so apareceria bem depois do banco ja
# estar de pe - e esses segundos sao exatamente a folga que falta contra o
# teto). Intervalo fixo e uniforme em vez de backoff calculado porque o teto
# precisa ser PREVISIVEL: `musclefit-api-core` tem `timeout = 29` (teto do API
# Gateway REST), e sobre a espera ainda correm o cold start e a propria
# consulta do endpoint (a visao geral leva 2-4 s).
#
# Preco aceito: mais tentativas = mais linhas de log por resume (ate 4
# warnings). E deliberado - o custo de observabilidade vale menos que a folga
# de tempo ganha.
#
# SEGUNDA CAMADA: `app/migrate.py` ja tinha o seu proprio retry de retomada
# (`_connect_with_retry`), anterior a este, exclusivo do caminho de migracao.
# As duas coexistem: na pratica ESTA camada absorve a retomada primeiro e a do
# `migrate` nem chega a ver o erro (ha teste de contrato afirmando isso em
# tests/integration/dataapi/test_shim_contrato.py). O que esta camada
# acrescenta e a protecao do api-core e do sync do Intervals, que nao tinham
# nenhuma. Quem escrever teste do retry do `migrate` precisa DESLIGAR esta
# camada (`monkeypatch.setattr(db_dataapi, "_RESUME_RETRY_WAITS", ())`), senao
# mede a camada errada.
# ---------------------------------------------------------------------------

_RESUMING_ERROR_CODE = "DatabaseResumingException"

#: Esperas ENTRE tentativas. `len(...) + 1` e o numero de tentativas e a soma e
#: o teto de espera total (ver comentario acima) - alterar aqui muda os dois.
_RESUME_RETRY_WAITS: tuple[float, ...] = (5.0, 5.0, 5.0, 5.0)

#: Teto contratado com o `timeout = 29` do api-core. So documenta/verifica.
_RESUME_MAX_TOTAL_WAIT_SECONDS = 20.0


def _sleep(seconds: float) -> None:
    """Espera entre tentativas. Funcao de modulo de proposito: os testes a
    substituem (`monkeypatch.setattr(db_dataapi, "_sleep", ...)`) para rodar
    instantaneamente e conferir a soma dos intervalos."""
    time.sleep(seconds)


def _is_database_resuming_error(exc: BaseException) -> bool:
    """True so para `DatabaseResumingException` (banco pausado despertando)."""
    return _aws_error_code(exc) == _RESUMING_ERROR_CODE


def _call_with_resume_retry(operation: str, call: Callable[[], Any]) -> Any:
    """Executa `call()` retentando APENAS `DatabaseResumingException`.

    Qualquer outra excecao propaga na hora (retentar erro generico mascararia
    bug real). Esgotadas as tentativas, propaga a excecao ORIGINAL - nada e
    engolido nem reembrulhado aqui."""
    total_attempts = len(_RESUME_RETRY_WAITS) + 1
    for attempt in range(1, total_attempts + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - filtrado logo abaixo
            if not _is_database_resuming_error(exc):
                raise
            if attempt == total_attempts:
                logger.warning(
                    "dataapi %s: banco ainda despertando apos %d tentativas (espera total %.1fs); "
                    "propagando DatabaseResumingException",
                    operation,
                    total_attempts,
                    sum(_RESUME_RETRY_WAITS),
                    extra={
                        "event": "dataapi_database_resuming_exhausted",
                        "operation": operation,
                        "attempts": total_attempts,
                        "total_wait_seconds": sum(_RESUME_RETRY_WAITS),
                    },
                )
                raise
            wait = _RESUME_RETRY_WAITS[attempt - 1]
            logger.warning(
                "dataapi %s: banco pausado despertando (tentativa %d/%d); aguardando %.1fs e tentando de novo",
                operation,
                attempt,
                total_attempts,
                wait,
                extra={
                    "event": "dataapi_database_resuming_retry",
                    "operation": operation,
                    "attempt": attempt,
                    "max_attempts": total_attempts,
                    "wait_seconds": wait,
                },
            )
            _sleep(wait)
    raise AssertionError("inalcancavel: o laco sempre retorna ou propaga")


class DataApiError(Exception):
    """Erro generico da camada de dados (falha de execucao, parametro nao suportado etc).

    O Data API nao propaga o codigo de erro numerico do MySQL (ex.: 1060,
    1062) como o PyMySQL faz em `exc.args[0]` - so a mensagem de texto do
    servidor, encapsulada na excecao do boto3 (`str(exc)` inclui essa
    mensagem). Varios call-sites do backend portado faziam
    `except pymysql.err.OperationalError as exc: if exc.args[0] == 1060: ...`
    (coluna/indice ja existe - guarda de auto-migracao idempotente) ou
    `except pymysql.err.IntegrityError` (violacao de UNIQUE -> HTTP 409).
    Como nao ha codigo numerico aqui, os helpers abaixo casam pela mensagem
    de texto do MySQL, que e estavel entre PyMySQL e Data API porque ambos
    apenas repassam a mensagem original do servidor."""


def is_duplicate_column_error(exc: Exception) -> bool:
    """True se a excecao veio de um ALTER TABLE ADD COLUMN em coluna que ja existe (MySQL 1060)."""
    return "Duplicate column name" in str(exc)


def is_duplicate_key_error(exc: Exception) -> bool:
    """True se a excecao veio de um INSERT/UPDATE violando UNIQUE KEY (MySQL 1062)."""
    return "Duplicate entry" in str(exc)


def is_unknown_key_error(exc: Exception) -> bool:
    """True se a excecao veio de um DROP INDEX/DROP KEY em indice que nao existe (MySQL 1091)."""
    text = str(exc)
    return "check that column/key exists" in text or "Can't DROP" in text


def _translate_sql(sql: str, params: Optional[Sequence[Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Traduz placeholders %s (paramstyle PyMySQL/"format") -> :p0, :p1... e
    desfaz o escape %% -> % (necessario so no %-formatting do PyMySQL, nao no
    MySQL em si; ver achado documentado no spike)."""
    if not params:
        return sql.replace("%%", "%"), []

    out_chars: list[str] = []
    named_params: list[dict[str, Any]] = []
    i = 0
    n = len(sql)
    param_idx = 0
    while i < n:
        ch = sql[i]
        if ch == "%" and i + 1 < n:
            nxt = sql[i + 1]
            if nxt == "%":
                out_chars.append("%")
                i += 2
                continue
            if nxt == "s":
                if param_idx >= len(params):
                    raise DataApiError(
                        f"SQL tem mais placeholders %s do que parametros fornecidos "
                        f"(placeholder #{param_idx + 1}, {len(params)} parametros)"
                    )
                name = f"p{param_idx}"
                out_chars.append(f":{name}")
                named_params.append(_to_data_api_param(name, params[param_idx]))
                param_idx += 1
                i += 2
                continue
        out_chars.append(ch)
        i += 1

    if param_idx != len(params):
        raise DataApiError(
            f"Numero de placeholders %s ({param_idx}) nao bate com o numero de "
            f"parametros fornecidos ({len(params)})"
        )

    return "".join(out_chars), named_params


def _fraction_ms(microsecond: int) -> str:
    """Fracao de segundo para bind: milissegundos TRUNCADOS (formato
    documentado `[.FFF]` dos typeHints TIMESTAMP/TIME) ou vazio.

    Paridade com o PyMySQL, que envia `.ffffff` e deixa o MySQL arredondar
    para a precisao da coluna: para DATETIME/TIMESTAMP/TIME de precisao 0 (as
    unicas do schema) truncar em ms nao cruza a fronteira de 0,5 s
    (x >= .5 <=> floor_ms(x) >= .500), entao o valor gravado e o mesmo."""
    if not microsecond:
        return ""
    return f".{microsecond // 1000:03d}"


def _format_timedelta(value: _dt.timedelta) -> str:
    """Mesmo texto que o PyMySQL envia para `timedelta` (`[-]H:MM:SS[.fff]`)."""
    total_us = value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds
    sign = "-" if total_us < 0 else ""
    seconds, microsecond = divmod(abs(total_us), 1_000_000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}{_fraction_ms(microsecond)}"


def _to_data_api_param(name: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    if isinstance(value, bool):
        return {"name": name, "value": {"booleanValue": value}}
    if isinstance(value, int):
        return {"name": name, "value": {"longValue": value}}
    if isinstance(value, float):
        return {"name": name, "value": {"doubleValue": value}}
    if isinstance(value, Decimal):
        return {"name": name, "value": {"stringValue": str(value)}, "typeHint": "DECIMAL"}
    if isinstance(value, _dt.datetime):
        return {
            "name": name,
            "value": {"stringValue": value.strftime("%Y-%m-%d %H:%M:%S") + _fraction_ms(value.microsecond)},
            "typeHint": "TIMESTAMP",
        }
    if isinstance(value, _dt.date):
        return {"name": name, "value": {"stringValue": value.isoformat()}, "typeHint": "DATE"}
    # ATENCAO: `datetime.time` NAO e subclasse de `datetime.date` (ao contrario
    # de `datetime.datetime`), entao precisa do proprio branch - sem ele caia no
    # catch-all JSON abaixo e chegava ao MySQL como a string '"09:00:00"' (com
    # aspas duplas literais), quebrando qualquer INSERT/UPDATE em coluna TIME
    # com "Incorrect time value ... Error code: 1292" (ex.: workout_plans.plan_time).
    # Mantido DEPOIS de datetime/date de proposito: a ordem dos isinstance e o
    # que garante que datetime nao seja capturado aqui.
    if isinstance(value, _dt.time):
        return {
            "name": name,
            "value": {"stringValue": value.strftime("%H:%M:%S") + _fraction_ms(value.microsecond)},
            "typeHint": "TIME",
        }
    # `timedelta` e o tipo que o PyMySQL (e agora este shim) devolve para
    # colunas TIME: regravar um TIME lido do banco precisa funcionar. Antes caia
    # no catch-all JSON e chegava como '"9:30:00"' (erro 1292). typeHint TIME so
    # aceita `HH:MM:SS[.FFF]` [AWS]: fora de 0..24h vai como texto sem hint e o
    # MySQL converte (mesmo texto que o PyMySQL envia).
    if isinstance(value, _dt.timedelta):
        text = _format_timedelta(value)
        if _dt.timedelta(0) <= value < _dt.timedelta(hours=24):
            return {"name": name, "value": {"stringValue": text}, "typeHint": "TIME"}
        return {"name": name, "value": {"stringValue": text}}
    if isinstance(value, (bytes, bytearray)):
        return {"name": name, "value": {"blobValue": bytes(value)}}
    if isinstance(value, str):
        return {"name": name, "value": {"stringValue": value}}
    if isinstance(value, _JSON_BIND_TYPES):
        # Comportamento de producao mantido (dict/list/tuple -> texto JSON);
        # `db_pymysql` faz o MESMO antes do bind (ver `json_bind_value`).
        return {"name": name, "value": {"stringValue": json_bind_value(value)}, "typeHint": "JSON"}
    # Demais tipos (UUID, Enum nao-str...): texto, como o PyMySQL faz
    # (`escape_item` cai no encoder de str).
    return {"name": name, "value": {"stringValue": str(value)}}


_JSON_BIND_TYPES = (dict, list, tuple)


def json_bind_value(value: Any) -> str:
    """Texto JSON enviado para parametros dict/list/tuple nos DOIS backends."""
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode_field(field: dict[str, Any]) -> Any:
    if field.get("isNull"):
        return None
    for key in ("stringValue", "longValue", "doubleValue", "booleanValue", "blobValue"):
        if key in field:
            return field[key]
    if "arrayValue" in field:
        return field["arrayValue"]
    return None


# ---------------------------------------------------------------------------
# Conversao de valores lidos -> MESMOS tipos Python do PyMySQL
# ---------------------------------------------------------------------------
#
# A Data API devolve DECIMAL/DATE/DATETIME/TIMESTAMP/TIME como `stringValue` e
# TINYINT(1) como `booleanValue` (medido na Aurora real, ver
# tests/support/fake_rds_data.py). O PyMySQL (dev/CI) entrega Decimal,
# date, datetime, timedelta e int. Sem esta conversao, codigo que funciona em
# dev quebrava so em producao (ex.: `GET /analytics/training-evolution`,
# `float += str` em volume_service; `POST /workout-sessions/{id}/finish`,
# `datetime - str`). A conversao segue as mesmas regras de
# `pymysql.converters` (valor ilegal, ex. '0000-00-00', continua `str`).

_DATETIME_RE = re.compile(r"(\d{1,4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{1,2}):(\d{1,2})(?:\.(\d{1,6}))?$")
_TIMEDELTA_RE = re.compile(r"(-)?(\d{1,3}):(\d{1,2}):(\d{1,2})(?:\.(\d{1,6}))?$")


def _microseconds(fraction: Optional[str]) -> int:
    return int(fraction.ljust(6, "0")) if fraction else 0


def _parse_date(text: str) -> Any:
    try:
        return _dt.date(*[int(part) for part in text.split("-", 2)])
    except ValueError:
        return text


def _parse_datetime(text: str) -> Any:
    match = _DATETIME_RE.match(text)
    if not match:
        return _parse_date(text)
    year, month, day, hour, minute, second, fraction = match.groups()
    try:
        return _dt.datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second), _microseconds(fraction)
        )
    except ValueError:
        return _parse_date(text)


def _parse_timedelta(text: str) -> Any:
    match = _TIMEDELTA_RE.match(text)
    if not match:
        return text
    negative, hours, minutes, seconds, fraction = match.groups()
    delta = _dt.timedelta(
        hours=int(hours), minutes=int(minutes), seconds=int(seconds), microseconds=_microseconds(fraction)
    )
    return -delta if negative else delta


def _parse_decimal(text: str) -> Any:
    try:
        return Decimal(text)
    except InvalidOperation:
        return text


def _parse_int(text: str) -> Any:
    try:
        return int(text)
    except ValueError:
        return text


def _from_string(parse: Callable[[str], Any]) -> Callable[[dict[str, Any]], Any]:
    def convert(field: dict[str, Any]) -> Any:
        text = field.get("stringValue")
        return _decode_field(field) if text is None else parse(text)

    return convert


def _integer(field: dict[str, Any]) -> Any:
    # TINYINT(1) chega como typeName BOOLEAN + booleanValue; o PyMySQL devolve 0/1.
    if "booleanValue" in field:
        return int(field["booleanValue"])
    text = field.get("stringValue")
    return _decode_field(field) if text is None else _parse_int(text)


_CONVERTERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "DECIMAL": _from_string(_parse_decimal),
    "NUMERIC": _from_string(_parse_decimal),
    "DATETIME": _from_string(_parse_datetime),
    "TIMESTAMP": _from_string(_parse_datetime),
    "DATE": _from_string(_parse_date),
    "TIME": _from_string(_parse_timedelta),
    "BOOLEAN": _integer,
    "BOOL": _integer,
    "TINYINT": _integer,
    "SMALLINT": _integer,
    "MEDIUMINT": _integer,
    "INT": _integer,
    "INTEGER": _integer,
    "BIGINT": _integer,
    "YEAR": _integer,
}


@lru_cache(maxsize=1024)
def _converters_for(type_names: tuple[str, ...]) -> tuple[Callable[[dict[str, Any]], Any], ...]:
    """Conversores por coluna, calculados uma vez por "formato" de resultado
    (tupla de typeName). JSON/VARCHAR/TEXT/DOUBLE/BLOB ja chegam no tipo do
    PyMySQL (str/float/bytes) e usam a decodificacao crua."""
    return tuple(
        _CONVERTERS.get(name.upper().replace(" UNSIGNED", "").strip(), _decode_field) for name in type_names
    )


def _column_names(column_meta: list[dict[str, Any]]) -> list[str]:
    """Nomes das chaves do dict da linha. Coluna repetida recebe o prefixo
    `<tabela>.`, como o `DictCursor` do PyMySQL (em vez de sobrescrever a
    primeira). Limite: a Data API expoe a tabela ORIGINAL (`tableName`), nao o
    alias - `SELECT a.id, b.id FROM t a JOIN t b` gera 't.id' aqui e 'b.id' no
    PyMySQL. Use alias de coluna em JOIN."""
    names: list[str] = []
    for col in column_meta:
        name = col.get("label") or col.get("name")
        if name in names:
            name = f"{col.get('tableName') or ''}.{name}"
        names.append(name)
    return names


def _rows_from_response(column_meta: list[dict[str, Any]], records: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    names = _column_names(column_meta)
    converters = _converters_for(tuple(col.get("typeName") or "" for col in column_meta))
    return [
        {name: convert(field) for name, convert, field in zip(names, converters, record)} for record in records
    ]


def _to_dbapi_description(column_meta: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Converte `columnMetadata` do Data API na 7-tupla da DBAPI 2.0
    (`name, type_code, display_size, internal_size, precision, scale, null_ok`).

    O Data API nao devolve display_size/internal_size, e o `type_code` aqui e o
    nome textual do tipo MySQL (`typeName`, ex.: "VARCHAR", "TIME") e nao o
    inteiro do protocolo que o PyMySQL expoe - os call-sites do backend usam
    apenas `desc[0]` (nome da coluna), entao a diferenca nao e observavel.
    `nullable`: 0 = NoNulls, 1 = Nullable, 2 = Unknown."""
    description: list[tuple[Any, ...]] = []
    for col in column_meta:
        nullable = col.get("nullable")
        description.append(
            (
                col.get("label") or col.get("name"),
                col.get("typeName"),
                None,
                None,
                col.get("precision"),
                col.get("scale"),
                None if nullable is None else bool(nullable),
            )
        )
    return description


# ---------------------------------------------------------------------------
# Lotes do `BatchExecuteStatement` (COD-12)
#
# Limite do servico [AWS, API_BatchExecuteStatement]: a requisicao HTTP inteira
# (cabecalhos + JSON) tem no maximo 4 MiB; nao ha teto fixo de parameterSets.
# A resposta tem teto de 1 MiB, mas para DML ela so traz `generatedFields`
# (dezenas de bytes por linha). O orcamento abaixo usa 1/4 do limite para
# absorver cabecalhos, envelope JSON e o erro da estimativa; o teto de linhas
# limita a resposta e o tempo de cada chamada.
# ---------------------------------------------------------------------------

#: Limite documentado da requisicao do Data API (so documenta/verifica).
DATA_API_MAX_REQUEST_BYTES = 4 * 1024 * 1024

#: Orcamento de SQL + parameterSets por chamada (25% do limite).
BATCH_PAYLOAD_BUDGET_BYTES = DATA_API_MAX_REQUEST_BYTES // 4

#: Teto de linhas por chamada.
BATCH_MAX_PARAMETER_SETS = 500


def _json_size_default(value: Any) -> str:
    # blobValue vai em base64 no fio: estima pelo tamanho codificado.
    if isinstance(value, (bytes, bytearray)):
        return "x" * (((len(value) + 2) // 3) * 4)
    return str(value)


def _estimated_json_bytes(value: Any) -> int:
    return len(json.dumps(value, default=_json_size_default, separators=(",", ":")).encode("utf-8"))


def _batch_chunks(sql: str, parameter_sets: list[list[dict[str, Any]]]) -> Iterable[list[list[dict[str, Any]]]]:
    """Divide `parameter_sets` em lotes CONSECUTIVOS (ordem preservada) que
    cabem em `BATCH_PAYLOAD_BUDGET_BYTES` e `BATCH_MAX_PARAMETER_SETS`.

    Uma linha sozinha acima do orcamento ainda vai, num lote proprio: o
    `execute()` linha a linha a enviaria do mesmo jeito, e o servico decide."""
    base = _estimated_json_bytes(sql)
    chunk: list[list[dict[str, Any]]] = []
    size = base
    for parameter_set in parameter_sets:
        row_size = _estimated_json_bytes(parameter_set) + 1
        if chunk and (size + row_size > BATCH_PAYLOAD_BUDGET_BYTES or len(chunk) >= BATCH_MAX_PARAMETER_SETS):
            yield chunk
            chunk, size = [], base
        chunk.append(parameter_set)
        size += row_size
    if chunk:
        yield chunk


class Cursor:
    def __init__(self, conn: "Connection"):
        self._conn = conn
        # `()` quando nao ha linhas (DML ou SELECT vazio) e `list` quando ha:
        # mesmo tipo que o `DictCursor` do PyMySQL devolve em `fetchall()`.
        self._rows: Sequence[dict[str, Any]] = ()
        self._pos = 0
        self.lastrowid: Optional[int] = None
        self.rowcount: int = -1
        # DBAPI 2.0: None enquanto nao houve execute, e tambem apos um comando
        # que nao devolve linhas (INSERT/UPDATE/DDL). `execute_statement` ja e
        # chamado com includeResultMetadata=True, entao todo SELECT preenche
        # isso (inclusive SELECT sem linhas).
        self.description: Optional[list[tuple[Any, ...]]] = None

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> "Cursor":
        translated_sql, named_params = _translate_sql(sql, params)
        kwargs: dict[str, Any] = {
            "resourceArn": self._conn.resource_arn,
            "secretArn": self._conn.secret_arn,
            "database": self._conn.database,
            "sql": translated_sql,
            "includeResultMetadata": True,
        }
        if named_params:
            kwargs["parameters"] = named_params
        if self._conn.transaction_id:
            kwargs["transactionId"] = self._conn.transaction_id

        try:
            # Retry so de `DatabaseResumingException` (auto-pause do Aurora);
            # qualquer outro erro sai daqui na primeira tentativa, como antes.
            resp = _call_with_resume_retry(
                "execute_statement", lambda: self._conn.client.execute_statement(**kwargs)
            )
        except Exception as exc:  # noqa: BLE001 - repassa com contexto do SQL
            raise DataApiError(f"execute_statement falhou para SQL: {translated_sql!r} -- {exc}") from exc

        column_meta = resp.get("columnMetadata") or []
        self.description = _to_dbapi_description(column_meta) if column_meta else None
        records = resp.get("records") or []
        # Tipos do `DictCursor` do PyMySQL (medido): SELECT com linhas -> list,
        # SELECT vazio -> (), DML/DDL -> [].
        if records:
            self._rows = _rows_from_response(column_meta, records)
        else:
            self._rows = () if column_meta else []
        self._pos = 0

        if column_meta:
            # Resultado de SELECT: PyMySQL expoe rowcount = linhas devolvidas e
            # lastrowid = None (a Data API manda numberOfRecordsUpdated = 0).
            self.rowcount = len(records)
            self.lastrowid = None
        else:
            # DML: numberOfRecordsUpdated conta linhas ENCONTRADAS (medido na
            # Aurora; db_pymysql usa CLIENT.FOUND_ROWS para o mesmo resultado).
            # lastrowid e recalculado a cada statement (0 sem generatedFields,
            # como o PyMySQL) - antes vazava o id do INSERT anterior.
            self.rowcount = resp.get("numberOfRecordsUpdated", 0)
            generated = resp.get("generatedFields") or []
            self.lastrowid = generated[0].get("longValue", 0) if generated else 0
        return self

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]) -> "Cursor":
        """Mesmo DML para varias linhas via `BatchExecuteStatement` (COD-12).

        Um HTTP por lote em vez de um por linha, DENTRO da transacao da
        conexao (`transactionId`): atomicidade igual a N `execute()`. As
        linhas sao enviadas na ordem recebida e o Data API as executa nessa
        ordem, entao ids AUTO_INCREMENT saem na mesma sequencia.

        Lotes respeitam o limite de 4 MiB por requisicao [AWS] com folga (ver
        `_batch_chunks`). Contrato igual ao `db_pymysql.Cursor.executemany`:
        so para DML sem resultado; depois dele `lastrowid` e None e
        `rowcount` e -1 (nao use - os dois backends nao concordariam)."""
        all_params = [list(params) for params in seq_of_params]
        self._rows = []
        self._pos = 0
        self.description = None
        self.lastrowid = None
        self.rowcount = -1
        if not all_params:
            return self
        translated_sql = None
        parameter_sets: list[list[dict[str, Any]]] = []
        for params in all_params:
            if not params:
                raise DataApiError("executemany exige parametros em todas as linhas")
            row_sql, named_params = _translate_sql(sql, params)
            translated_sql = row_sql if translated_sql is None else translated_sql
            parameter_sets.append(named_params)

        for chunk in _batch_chunks(translated_sql, parameter_sets):
            kwargs: dict[str, Any] = {
                "resourceArn": self._conn.resource_arn,
                "secretArn": self._conn.secret_arn,
                "database": self._conn.database,
                "sql": translated_sql,
                "parameterSets": chunk,
            }
            if self._conn.transaction_id:
                kwargs["transactionId"] = self._conn.transaction_id
            try:
                _call_with_resume_retry(
                    "batch_execute_statement",
                    lambda kwargs=kwargs: self._conn.client.batch_execute_statement(**kwargs),
                )
            except Exception as exc:  # noqa: BLE001 - repassa com contexto do SQL
                raise DataApiError(
                    f"batch_execute_statement falhou para SQL: {translated_sql!r} -- {exc}"
                ) from exc
        return self

    def fetchone(self) -> Optional[dict[str, Any]]:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list[dict[str, Any]]:
        rows = self._rows[self._pos :]
        self._pos = len(self._rows)
        return rows

    def close(self) -> None:
        # Nao ha recurso remoto por cursor no Data API (a transacao vive na
        # Connection); libera so o buffer local, por paridade com a DBAPI.
        # `exercise_mapping_canonical.query/execute` chama `cur.close()` dentro
        # de try/except - antes isso silenciava um AttributeError a cada query.
        self._rows = ()
        self._pos = 0

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Intencionalmente NAO chama close(): varios call-sites fazem
        # `with conn.cursor() as cur: ...` e so consomem o resultado depois de
        # sair do bloco. Limpar o buffer aqui mudaria comportamento existente.
        return None

    def __iter__(self) -> Iterable[dict[str, Any]]:
        return iter(self._rows)


class Connection:
    """"Conexao logica": sem socket TCP real, mas com uma `transactionId` real
    do Data API do inicio (construtor) ate `commit`/`rollback`/`close` -
    sem isso, cada `execute_statement` roda isolado e SET/estado de sessao nao
    persiste entre chamadas (achado da migracao de dados, Fase 1)."""

    def __init__(
        self, resource_arn: str, secret_arn: str, database: str, region_name: str, *, autocommit: bool = False
    ):
        self.resource_arn = resource_arn
        self.secret_arn = secret_arn
        self.database = database
        self.region_name = region_name
        self.client = _shared_client(region_name)
        self.transaction_id: Optional[str] = None
        # `autocommit=True` so para `fetch_rows` (leitura de UM statement): sem
        # BeginTransaction, cada `execute_statement` roda sem `transactionId`.
        if not autocommit:
            self._begin()

    def _begin(self) -> None:
        # Caminho MAIS provavel do despertar: toda "conexao logica" comeca aqui,
        # entao a primeira chamada depois de uma pausa costuma ser esta.
        resp = _call_with_resume_retry(
            "begin_transaction",
            lambda: self.client.begin_transaction(
                resourceArn=self.resource_arn,
                secretArn=self.secret_arn,
                database=self.database,
            ),
        )
        self.transaction_id = resp["transactionId"]

    def cursor(self) -> Cursor:
        return Cursor(self)

    def commit(self) -> None:
        if self.transaction_id:
            self.client.commit_transaction(
                resourceArn=self.resource_arn,
                secretArn=self.secret_arn,
                transactionId=self.transaction_id,
            )
            self.transaction_id = None

    def rollback(self) -> None:
        # `sys.exception()` e a excecao em tratamento no ponto da chamada - tanto
        # num `except: conn.rollback(); raise` quanto num `finally: conn.close()`
        # durante a propagacao de um erro (padrao de varios call-sites, ex.
        # cardio_recommendations.py). Ver `_rollback`.
        self._rollback(sys.exception())

    def _rollback(self, pending: Optional[BaseException]) -> None:
        """Reverte a transacao aberta. O `transaction_id` e zerado ANTES da
        chamada: qualquer que seja o desfecho, o id nao volta a ser usado
        (nem por `close()`, nem por um `execute` seguinte).

        Transacao que a Data API ja nao conhece (ex.: expirada) conta como
        revertida: log `warning` e retorno normal.

        Qualquer outra falha do rollback (ClientError, BotoCoreError...):
        - com uma excecao em andamento (`pending`): NUNCA a substitui - a falha
          vira nota em `pending` (`add_note`) + log `warning` estruturado com os
          codigos AWS (sem SQL/parametros), e a excecao original segue subindo;
        - sem excecao em andamento: propaga (nao engole erro real)."""
        transaction_id = self.transaction_id
        if not transaction_id:
            return
        self.transaction_id = None
        original_code = _original_aws_error_code(pending)
        try:
            # Dentro do try: nem a criacao do client sem retries pode mascarar `pending`.
            client = self._fast_fail_client() if original_code in _FAST_FAIL_ROLLBACK_AFTER else self.client
            client.rollback_transaction(
                resourceArn=self.resource_arn,
                secretArn=self.secret_arn,
                transactionId=transaction_id,
            )
        except Exception as exc:  # noqa: BLE001 - nunca substitui a excecao em andamento
            if not _is_transaction_gone_error(exc):
                if pending is None:
                    raise
                self._record_rollback_failure(pending, original_code, exc)
                return
            logger.warning(
                "dataapi rollback_transaction: transacao ja inexistente na Data API (ex.: expirada); tratada como revertida "
                "(aws_error_code=%s)",
                _aws_error_code(exc),
                extra={"event": "dataapi_rollback_transaction_gone", "aws_error_code": _aws_error_code(exc)},
            )

    def _fast_fail_client(self) -> Any:
        """Client `rds-data` sem retries, so para o rollback apos erros em
        `_FAST_FAIL_ROLLBACK_AFTER` (caminho raro; o client normal nao muda)."""
        return boto3.client(
            "rds-data",
            region_name=self.region_name,
            config=Config(retries=dict(_FAST_FAIL_ROLLBACK_CONFIG)),
        )

    @staticmethod
    def _record_rollback_failure(pending: BaseException, original_code: Optional[str], rollback_err: BaseException) -> None:
        rollback_code = _aws_error_code(rollback_err)
        pending.add_note(
            f"db_dataapi: rollback_transaction tambem falhou ({type(rollback_err).__name__}, "
            f"aws_error_code={rollback_code}); a Data API descarta a transacao ao expirar"
        )
        logger.warning(
            "dataapi rollback_transaction falhou durante tratamento de %s "
            "(original_aws_error_code=%s, rollback_aws_error_code=%s); excecao original preservada",
            type(pending).__name__,
            original_code,
            rollback_code,
            exc_info=rollback_err,
            extra={
                "event": "dataapi_rollback_transaction_failed",
                "original_error_type": type(pending).__name__,
                "original_aws_error_code": original_code,
                "rollback_aws_error_code": rollback_code,
            },
        )

    def close(self) -> None:
        # Paridade com "fechar uma conexao PyMySQL sem commit -> descarta" -
        # se ninguem deu commit/rollback explicito, aborta a transacao aberta
        # em vez de deixar pendurada no Aurora.
        if self.transaction_id:
            self.rollback()

    # Suporte a `with _connect() as conn:` (varios call-sites do backend
    # portado usam esse padrao esperando `conn` de volta, com commit
    # implicito ao sair sem excecao - paridade com autocommit=True do
    # `pymysql.connect(**_db_config())` original desses arquivos).
    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.commit()
        else:
            self._rollback(exc)
        self.close()


def _config_from_env() -> tuple[str, str, str, str]:
    try:
        resource_arn = os.environ["AURORA_CLUSTER_ARN"]
        secret_arn = os.environ["AURORA_SECRET_ARN"]
        database = os.environ["AURORA_DATABASE"]
    except KeyError as exc:
        raise DataApiError(
            f"Variavel de ambiente obrigatoria ausente para a camada de dados: {exc}. "
            "Esperado AURORA_CLUSTER_ARN, AURORA_SECRET_ARN, AURORA_DATABASE."
        ) from exc
    region_name = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "sa-east-1"
    return resource_arn, secret_arn, database, region_name


def get_conn() -> Connection:
    resource_arn, secret_arn, database, region_name = _config_from_env()
    return Connection(resource_arn, secret_arn, database, region_name)


def fetch_rows(sql: str, params: Optional[Sequence[Any]] = None) -> list[dict[str, Any]]:
    """Linhas de UMA leitura (`SELECT`/`WITH`/`SHOW`) fora de transacao explicita.

    PERF-05: `get_conn()` abre uma transacao (BeginTransaction) e o `close()`
    a reverte (RollbackTransaction) - numa leitura de um statement so, sao 3
    chamadas HTTP a Data API para 1 de trabalho. Aqui o statement vai sem
    `transactionId`, que a Data API executa em autocommit [AWS-2 em
    tests/support/fake_rds_data.py]: 1 chamada. Para UM statement de leitura o
    resultado e o mesmo de "BEGIN; SELECT; ROLLBACK" (mesmo isolamento, mesma
    visibilidade do que ja foi commitado). Escrita e leitura de varios
    statements que precisam do mesmo snapshot continuam em `get_conn()`.
    Paridade: `db_pymysql.fetch_rows` (mesmo contrato, autocommit)."""
    _require_read(sql)
    resource_arn, secret_arn, database, region_name = _config_from_env()
    conn = Connection(resource_arn, secret_arn, database, region_name, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall() or [])


_READ_PREFIXES = ("SELECT", "WITH", "SHOW", "(")


def _require_read(sql: str) -> None:
    """`fetch_rows` roda em autocommit: um DML ali seria gravado na hora, sem
    a transacao que o chamador esperaria. Recusa antes de enviar (igual nos
    dois backends)."""
    head = sql.lstrip().upper()
    if not head.startswith(_READ_PREFIXES):
        raise DataApiError(f"fetch_rows aceita so leitura (SELECT/WITH/SHOW): {sql.strip()[:60]!r}")


@contextmanager
def transaction():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception as exc:
        conn._rollback(exc)
        raise
    finally:
        conn.close()
