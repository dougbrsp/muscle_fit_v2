"""Cooldown de alerta do Novo Dashboard (`dashboard_alert_states`, migracao
051; F2.2 - plano §12 "Cooldown", §14, §17).

**O problema, medido.** Ate a F2.1 os insights nao tinham memoria: a mesma
condicao era reapresentada em toda leitura. No dataset real de DEV (usuario 1,
oito leituras de `/dashboard/insights` entre 2026-08-14 e 2026-09-25), 19 dos
21 cartoes da ultima leitura sao o MESMO `PERFORMANCE_DROP_WITH_CONTEXT_EVENT`
(severidade INFO, confianca LOW) repetido nas 5 leituras que cobrem 21 dias -
e o `reassess_after` que eles proprios declaram venceu 15 a 19 dias antes.
Com `overview_max_next_actions = 2`, as duas vagas da home eram ocupadas por
associacoes de tres semanas atras. Ruido nao e so incomodo: ele empurra para
fora o alerta que importa.

**O desenho, em cinco decisoes.**

1. **A identidade e a `alert_key`, nao a impressao.** A impressao
   (`insight_rules.build_fingerprint`) inclui TODAS as chaves da comparacao, e
   algumas mudam sozinhas. Medido: `EXERCISES_REGRESSING` produziu QUATRO
   impressoes nas 8 leituras (a lista `exercise_ids` muda a cada exercicio que
   entra ou sai) e `RECOMPOSITION_COMPATIBLE` /
   `DATA_QUALITY_UNMAPPED_SETS` trazem `date_to` na chave, ou seja, impressao
   NOVA todo dia. Cooldown por impressao nunca silenciaria nenhuma das tres.
   Por isso cada CODIGO declara, em `insight_rules.COOLDOWN_KEYS_BY_CODE`, o
   subconjunto de chaves que identifica o ALVO; a `alert_key` e construida com
   ele. Tupla vazia = o alvo e global ("o seu treino", "o seu corpo") e a
   chave e o proprio codigo. A impressao continua gravada, como EVIDENCIA do
   que foi visto por ultimo.

2. **O cooldown reinicia quando o EPISODIO fecha.** A condicao deixou de
   aparecer por `alert_state_resolution_gap_days` (proposta: 30) -> o estado e
   marcado `resolved_at` e a proxima ocorrencia e tratada como PRIMEIRO aviso:
   `first_seen_at` novo, sem cooldown herdado. "Seu ombro voltou a ficar abaixo
   do minimo depois de dois meses em ordem" e noticia, nao repeticao. A
   deteccao e LAZY (acontece quando a chave reaparece) e, para o estado
   gravado nao mentir para a tela, ha tambem uma varredura de 1 statement por
   leitura que fecha os episodios vencidos que NAO reapareceram.

3. **Piorar FURA o cooldown.** Se a severidade de agora for pior que a da
   ultima emissao (ordem `INFO < ATTENTION < ACTION`), o insight e mostrado
   mesmo dentro do cooldown, o cooldown e recalculado a partir de agora e a
   telemetria registra `escalated`. A razao e a propria finalidade do
   cooldown: ele existe para calar a REPETICAO da mesma noticia, e "piorou"
   nao e a mesma noticia. Um cooldown que silencia uma condicao que se agrava
   deixou de reduzir ruido e passou a causar dano. O caminho inverso NAO fura:
   um alerta que MELHOROU (severidade menor) continua silenciado - melhora nao
   e alerta, e ela aparece pelo desaparecimento do cartao.

4. **A duracao nao e um numero novo.** Ela e o `reassess_after` que cada regra
   ja declara ("reavalie depois de um ciclo", "depois de 7 dias"), limitado
   pelo piso da severidade e pelo teto geral (`metrics_registry`). Sem o piso,
   as 19 impressoes acima teriam cooldown ZERO, porque o `reassess_after`
   delas esta no passado. Piso (INFO 14d, ATTENTION 7d, ACTION 3d), teto (60d)
   e intervalo de resolucao (30d) sao decisao de PRODUTO, aprovada pelo usuario
   em 2026-09-25 com a medicao ao lado no registry - nunca ajustada em silencio
   aqui.

   O teto e o que faz um alerta silenciado VOLTAR quando a condicao continua
   verdadeira: as 19 associacoes medidas reaparecem no D+15. Isso e escolha
   explicita do usuario (2026-09-25, "mantem o retorno"), nao efeito colateral -
   a alternativa oferecida era piso INFO = teto, com a associacao de confianca
   LOW nunca mais voltando enquanto nada mudasse.

5. **A unidade e o DIA, nao a requisicao.** Um alerta mostrado hoje continua
   visivel em todas as leituras de hoje. Abrir a home JA e uma leitura (a visao
   geral emite `next_actions`); se o cooldown contasse por requisicao, a tela de
   insights aberta em seguida apareceria VAZIA - duas telas da mesma sessao, a
   segunda parecendo que nada esta acontecendo. Por isso a segunda leitura do
   mesmo dia devolve EXATAMENTE a primeira resposta (o motivo fica gravado em
   `last_reason`, nao e recalculado) e nada e contado duas vezes. Todos os
   limiares do cooldown sao em dias; a granularidade da decisao acompanha.

**Escrita numa leitura.** `GET /analytics/dashboard/overview` passa a gravar:
sem registrar o que foi mostrado nao existe cooldown. Sao 3 statements por
leitura (1 SELECT do estado do usuario, 1 varredura de episodios vencidos, 1
upsert multi-linha com TODAS as decisoes), e nunca mais que isso, qualquer que
seja o numero de insights - o Python decide os valores finais de cada linha e o
SQL so persiste. Falha de escrita NAO derruba a tela: o cooldown e memoria de
exibicao, e o pior caso de perde-la e voltar ao comportamento pre-F2.2.

**Janela de deploy (tabela ausente, antes da 051):** `plan/`leitura seguem
funcionando com TODOS os insights visiveis e a limitacao declarada - nunca
500, nunca DDL por aqui. `DASHBOARD_ALERT_COOLDOWN=0` desliga o cooldown sem
deploy (mesmo valor em DEV e em producao; a flag existe para ser desligada em
emergencia, nao para divergir ambiente).

Cache SO DO POSITIVO da deteccao da tabela, chaveado por SCHEMA - mesma regra
(e mesma licao, PR #182) de `context_events_schema`/`daily_checkins_schema`.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from ...observability import log_event
from .contracts import Severity
from .identity import require_user_id
from .metrics_registry import metric_version, threshold

TABLE = "dashboard_alert_states"
MIGRATION = "051"

COOLDOWN_VERSION = metric_version("alert_cooldown")

COOLDOWN_ENV = "DASHBOARD_ALERT_COOLDOWN"

TABLE_MISSING_LIMITATION = (
    "Cooldown de alertas indisponível: a tabela `dashboard_alert_states` (migração 051) ainda não "
    "existe neste banco — todos os alertas aparecem, inclusive os já vistos."
)

COOLDOWN_DISABLED_LIMITATION = (
    "Cooldown de alertas desligado nesta instalação (`DASHBOARD_ALERT_COOLDOWN=0`): todos os "
    "alertas aparecem, inclusive os já vistos."
)

SUPPRESSED_LIMITATION = (
    "Alertas já vistos e ainda no mesmo estado ficam silenciados por um período (cooldown); "
    "um alerta que PIORA de severidade aparece mesmo assim."
)

# Ordem de gravidade. Menor = mais grave (mesma convencao de
# `insight_rules._SEVERITY_RANK`, que nao e importado aqui para este modulo
# nao depender do modulo de regras - a dependencia e a do outro lado).
_SEVERITY_RANK: Mapping[Severity, int] = {
    Severity.ACTION: 0,
    Severity.ATTENTION: 1,
    Severity.INFO: 2,
}

_MIN_DAYS_THRESHOLD_BY_SEVERITY: Mapping[Severity, str] = {
    Severity.INFO: "alert_cooldown_min_days_info",
    Severity.ATTENTION: "alert_cooldown_min_days_attention",
    Severity.ACTION: "alert_cooldown_min_days_action",
}

# Razoes de decisao (contrato com a tela e com a telemetria).
REASON_FIRST_SEEN = "first_seen"
REASON_NEW_EPISODE = "new_episode"
REASON_ESCALATED = "escalated"
REASON_COOLDOWN_EXPIRED = "cooldown_expired"
REASON_COOLDOWN_ACTIVE = "cooldown_active"

EMIT_REASONS = (REASON_FIRST_SEEN, REASON_NEW_EPISODE, REASON_ESCALATED, REASON_COOLDOWN_EXPIRED)

_TABLE_EXISTS_SQL = (
    "SELECT COUNT(*) AS n FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
)

SELECT_STATES_SQL = f"""
SELECT alert_key, alert_key_hash, rule_code, fingerprint, severity, last_reason,
       first_seen_at, last_seen_at, last_emitted_at,
       emitted_count, suppressed_count, resolved_at, snoozed_until
FROM {TABLE}
WHERE user_id = %s
"""

# Varredura dos episodios vencidos que NAO reapareceram: o estado gravado
# deixa de afirmar "aberto" para uma condicao que ninguem ve ha mais de
# `alert_state_resolution_gap_days`. Um statement, por indice de usuario.
RESOLVE_STALE_SQL = f"""
UPDATE {TABLE}
SET resolved_at = last_seen_at
WHERE user_id = %s AND resolved_at IS NULL AND last_seen_at < %s
"""

_UPSERT_COLUMNS = (
    "user_id",
    "alert_key",
    "alert_key_hash",
    "rule_code",
    "fingerprint",
    "severity",
    "last_reason",
    "first_seen_at",
    "last_seen_at",
    "last_emitted_at",
    "emitted_count",
    "suppressed_count",
    "resolved_at",
    "snoozed_until",
)

# Tudo o que muda vem de `VALUES(...)`: quem decide os valores e o Python
# (`decide`), com o estado atual em maos. O SQL nao tem regra de negocio.
_UPSERT_UPDATES = ", ".join(
    f"{column} = VALUES({column})"
    for column in _UPSERT_COLUMNS
    if column not in ("user_id", "alert_key_hash")
)

_schemas_with_table: set[str] = set()


# ---------------------------------------------------------------------------
# Flag e deteccao da tabela
# ---------------------------------------------------------------------------


def cooldown_enabled() -> bool:
    """`DASHBOARD_ALERT_COOLDOWN` desliga o cooldown sem deploy. Ligado por
    padrao (ausente = ligado) e com o MESMO valor em DEV e em producao: a flag
    e uma valvula de emergencia, nao um jeito de bifurcar ambiente."""
    raw = os.environ.get(COOLDOWN_ENV)
    if raw is None:
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def has_alert_states_table(cursor: Any) -> bool:
    """`dashboard_alert_states` ja existe NESTE schema? Sem DDL."""
    from ...load_conventions import current_schema

    schema = current_schema(cursor)
    if schema in _schemas_with_table:
        return True
    cursor.execute(_TABLE_EXISTS_SQL, (TABLE,))
    row = cursor.fetchone()
    exists = int((row or {}).get("n") or 0) > 0
    if exists:
        _schemas_with_table.add(schema)
    return exists


def reset_alert_states_table_cache() -> None:
    """Esquece a deteccao de TODOS os schemas (so importa para um teste que
    DESFAZ a 051)."""
    _schemas_with_table.clear()


# ---------------------------------------------------------------------------
# Funcoes PURAS (testadas sem banco)
# ---------------------------------------------------------------------------


def alert_key_hash(alert_key: str) -> str:
    """sha256 hex da chave. Existe so para caber num UNIQUE: a chave em texto
    nao tem teto de tamanho (a impressao mais longa medida hoje ja tem 95
    caracteres e cresce com o numero de exercicios), e truncar dentro de um
    UNIQUE transformaria dois alertas diferentes num, silenciando o segundo
    para sempre."""
    return hashlib.sha256(alert_key.encode("utf-8")).hexdigest()


def _severity(value: Any) -> Severity:
    return value if isinstance(value, Severity) else Severity(str(value))


def at_second_precision(moment: datetime) -> datetime:
    """`now` sem microssegundos.

    `DATETIME` do MySQL guarda SEGUNDOS. Sem truncar aqui, a primeira leitura
    devolveria `first_seen_at` com microssegundos (valor em memoria) e a
    seguinte o mesmo instante SEM eles (valor lido do banco) - duas respostas
    diferentes para o mesmo estado, que e exatamente o que
    `test_payload_identico_nos_dois_backends` pegou. O relogio da decisao passa
    a ter a precisao do lugar onde ele e gravado."""
    return moment.replace(microsecond=0)


def is_worse(current: Any, previous: Any) -> bool:
    """A severidade de agora e MAIS GRAVE que a da ultima emissao?"""
    return _SEVERITY_RANK[_severity(current)] < _SEVERITY_RANK[_severity(previous)]


def cooldown_until(
    *,
    now: datetime,
    severity: Any,
    reassess_after: Any,
) -> datetime:
    """Ate quando este alerta fica silencioso a partir de `now`.

    Base: o `reassess_after` que a REGRA declarou (ela sabe quando faz sentido
    olhar de novo). Limites: o piso da severidade - sem ele um `reassess_after`
    no passado daria cooldown zero, que e exatamente o caso das 19 impressoes
    medidas - e o teto geral, para nada ficar calado indefinidamente."""
    floor_days = int(threshold(_MIN_DAYS_THRESHOLD_BY_SEVERITY[_severity(severity)]))
    ceiling_days = int(threshold("alert_cooldown_max_days"))

    requested_days = floor_days
    if reassess_after is not None:
        reassess_date = getattr(reassess_after, "date", lambda: reassess_after)()
        requested_days = (reassess_date - now.date()).days

    days = max(floor_days, min(ceiling_days, requested_days))
    return now + timedelta(days=days)


def episode_is_closed(state: Mapping[str, Any], *, now: datetime) -> bool:
    """O episodio anterior ja acabou? `resolved_at` gravado, ou a condicao sem
    aparecer por mais de `alert_state_resolution_gap_days`."""
    if state.get("resolved_at") is not None:
        return True
    last_seen = state.get("last_seen_at")
    if not isinstance(last_seen, datetime):
        return False
    gap_days = int(threshold("alert_state_resolution_gap_days"))
    return (now - last_seen) > timedelta(days=gap_days)


def decide(
    insight: Mapping[str, Any],
    state: Optional[Mapping[str, Any]],
    *,
    now: datetime,
    alert_key: str,
) -> dict[str, Any]:
    """`(mostrar?, razao, linha a gravar)` para UM insight.

    Ordem das perguntas - e ela que define o comportamento:

    1. nunca visto -> MOSTRA (`first_seen`);
    2. episodio anterior encerrado -> MOSTRA como primeiro aviso do episodio
       NOVO (`new_episode`), com `first_seen_at` reiniciado;
    3. PIOROU de severidade -> MOSTRA mesmo dentro do cooldown
       (`escalated`) e recomeca o cooldown a partir de agora;
    4. ja mostrado HOJE -> MOSTRA de novo, com a resposta identica a da
       primeira leitura do dia (motivo gravado, contagens intactas);
    5. cooldown ativo (`snoozed_until` no futuro) -> SILENCIA
       (`cooldown_active`), so incrementando `suppressed_count`/`last_seen_at`;
    6. cooldown vencido -> MOSTRA (`cooldown_expired`).
    """
    now = at_second_precision(now)
    severity = _severity(insight["severity"])
    fingerprint = str(insight["fingerprint"])
    code = str(insight["code"])
    reassess_after = insight.get("reassess_after")

    def _row(
        *,
        reason: str,
        first_seen_at: datetime,
        emitted: bool,
        emitted_count: int,
        suppressed_count: int,
        last_emitted_at: datetime,
        severity_out: Severity,
        snoozed_until: Optional[datetime],
    ) -> dict[str, Any]:
        return {
            "emit": emitted,
            "reason": reason,
            "alert_key": alert_key,
            "alert_key_hash": alert_key_hash(alert_key),
            "rule_code": code,
            "fingerprint": fingerprint,
            "severity": severity_out,
            "last_reason": reason if emitted else str(state.get("last_reason") or reason),
            "first_seen_at": first_seen_at,
            "last_seen_at": now,
            "last_emitted_at": last_emitted_at,
            "emitted_count": emitted_count,
            "suppressed_count": suppressed_count,
            # A linha gravada descreve um episodio ABERTO: a condicao acabou
            # de ser observada. `resolved_at` volta a NULL num episodio novo.
            "resolved_at": None,
            "snoozed_until": snoozed_until,
        }

    def _emitting(reason: str, first_seen_at: datetime, emitted_count: int, suppressed_count: int):
        return _row(
            reason=reason,
            first_seen_at=first_seen_at,
            emitted=True,
            emitted_count=emitted_count,
            suppressed_count=suppressed_count,
            last_emitted_at=now,
            severity_out=severity,
            snoozed_until=cooldown_until(now=now, severity=severity, reassess_after=reassess_after),
        )

    if state is None:
        return _emitting(REASON_FIRST_SEEN, now, 1, 0)

    if episode_is_closed(state, now=now):
        # Episodio novo: as contagens do anterior nao se somam - elas
        # descreviam outra ocorrencia. O que se preserva e so a existencia da
        # linha (o UNIQUE e por usuario + chave).
        return _emitting(REASON_NEW_EPISODE, now, 1, 0)

    previous_emitted = int(state.get("emitted_count") or 0)
    previous_suppressed = int(state.get("suppressed_count") or 0)
    first_seen_at = state.get("first_seen_at")
    if not isinstance(first_seen_at, datetime):
        first_seen_at = now

    if is_worse(severity, state.get("severity") or Severity.INFO):
        return _emitting(REASON_ESCALATED, first_seen_at, previous_emitted + 1, previous_suppressed)

    snoozed_until = state.get("snoozed_until")
    last_emitted_at = state.get("last_emitted_at")

    # MESMA OCASIAO DE EXIBICAO. O cooldown e medido em DIAS: um alerta
    # mostrado hoje continua visivel nas outras leituras de hoje. Sem isto,
    # abrir a home (que ja e uma leitura) e depois a tela de insights mostraria
    # a segunda tela VAZIA - duas telas da mesma sessao, e a segunda parecendo
    # que nada esta acontecendo. A resposta e IDENTICA a da primeira leitura
    # (mesmo motivo gravado, mesmas contagens, mesmo `snoozed_until`), e nada
    # aqui e contado duas vezes.
    if isinstance(last_emitted_at, datetime) and last_emitted_at.date() == now.date():
        return _row(
            reason=str(state.get("last_reason") or REASON_FIRST_SEEN),
            first_seen_at=first_seen_at,
            emitted=True,
            emitted_count=previous_emitted,
            suppressed_count=previous_suppressed,
            last_emitted_at=last_emitted_at,
            severity_out=_severity(state.get("severity") or severity),
            snoozed_until=snoozed_until if isinstance(snoozed_until, datetime) else None,
        )

    if isinstance(snoozed_until, datetime) and now < snoozed_until:
        last_emitted_at = state.get("last_emitted_at")
        return _row(
            reason=REASON_COOLDOWN_ACTIVE,
            first_seen_at=first_seen_at,
            emitted=False,
            emitted_count=previous_emitted,
            suppressed_count=previous_suppressed + 1,
            last_emitted_at=last_emitted_at if isinstance(last_emitted_at, datetime) else now,
            # A severidade gravada continua sendo a da ULTIMA EMISSAO: e com
            # ela que a proxima leitura compara para saber se piorou. Gravar a
            # severidade de uma emissao que nao aconteceu apagaria o degrau.
            severity_out=_severity(state.get("severity") or severity),
            snoozed_until=snoozed_until,
        )

    return _emitting(REASON_COOLDOWN_EXPIRED, first_seen_at, previous_emitted + 1, previous_suppressed)


def cooldown_public(decision: Mapping[str, Any]) -> dict[str, Any]:
    """O bloco `cooldown` que acompanha cada insight na resposta. A tela
    precisa poder dizer POR QUE um cartao esta la (ou nao esta) - "silenciado
    ate 09/10" e informacao; um cartao que some sem explicacao e um bug aos
    olhos de quem usa."""
    return {
        "alert_key": decision["alert_key"],
        "reason": decision["reason"],
        "suppressed": not decision["emit"],
        "first_seen_at": decision["first_seen_at"],
        "last_emitted_at": decision["last_emitted_at"],
        "emitted_count": decision["emitted_count"],
        "suppressed_count": decision["suppressed_count"],
        "snoozed_until": decision["snoozed_until"],
        "metric_version": COOLDOWN_VERSION,
    }


def partition(decisions: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[str]]:
    """`(chaves mostradas, chaves silenciadas)` - para a telemetria."""
    shown = [str(item["alert_key"]) for item in decisions if item["emit"]]
    hidden = [str(item["alert_key"]) for item in decisions if not item["emit"]]
    return shown, hidden


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def load_states(conn: Any, *, user_id: Any) -> tuple[dict[str, dict[str, Any]], bool]:
    """`({alert_key_hash: estado}, tabela_disponivel)`.

    Uma consulta por usuario (o volume e de dezenas de linhas: uma por alerta
    distinto ja visto), e nunca por chave - um IN com 20 chaves custaria o
    mesmo e ainda perderia a varredura de episodios vencidos."""
    owner = require_user_id(user_id)
    try:
        with conn.cursor() as cursor:
            if not has_alert_states_table(cursor):
                return {}, False
            cursor.execute(SELECT_STATES_SQL, (owner,))
            rows = list(cursor.fetchall() or [])
    except Exception as exc:  # noqa: BLE001 - memoria de exibicao nao derruba a tela
        log_event("dashboard.alert_states_unavailable", user_id=owner, op="load", error=str(exc))
        return {}, False
    return {str(row["alert_key_hash"]): dict(row) for row in rows}, True


def persist(conn: Any, *, user_id: Any, decisions: Sequence[Mapping[str, Any]], now: datetime) -> bool:
    """Grava as decisoes: 1 varredura de episodios vencidos + 1 upsert
    multi-linha. Devolve se a escrita foi concluida.

    Nunca levanta: um erro aqui volta o hub ao comportamento pre-F2.2 (todos
    os alertas visiveis) em vez de derrubar a leitura."""
    owner = require_user_id(user_id)
    if not decisions:
        return True

    gap_days = int(threshold("alert_state_resolution_gap_days"))
    stale_before = at_second_precision(now) - timedelta(days=gap_days)

    placeholders = "(" + ", ".join(["%s"] * len(_UPSERT_COLUMNS)) + ")"
    values: list[Any] = []
    for decision in decisions:
        values.extend(
            (
                owner,
                decision["alert_key"],
                decision["alert_key_hash"],
                decision["rule_code"],
                decision["fingerprint"],
                _severity(decision["severity"]).value,
                str(decision["last_reason"])[:32],
                decision["first_seen_at"],
                decision["last_seen_at"],
                decision["last_emitted_at"],
                int(decision["emitted_count"]),
                int(decision["suppressed_count"]),
                decision["resolved_at"],
                decision["snoozed_until"],
            )
        )

    sql = (
        f"INSERT INTO {TABLE} ({', '.join(_UPSERT_COLUMNS)}) VALUES "
        + ", ".join([placeholders] * len(decisions))
        + f" ON DUPLICATE KEY UPDATE {_UPSERT_UPDATES}"
    )

    try:
        with conn.cursor() as cursor:
            cursor.execute(RESOLVE_STALE_SQL, (owner, stale_before))
            cursor.execute(sql, tuple(values))
    except Exception as exc:  # noqa: BLE001 - ver docstring
        log_event("dashboard.alert_states_unavailable", user_id=owner, op="persist", error=str(exc))
        return False
    return True


def apply_cooldown(
    conn: Any,
    *,
    user_id: Any,
    insights: Sequence[Mapping[str, Any]],
    now: datetime,
    alert_key_for,
) -> dict[str, Any]:
    """Aplica o cooldown a uma lista de insights JA ordenada e deduplicada.

    Devolve `{visible, suppressed, decisions, available, limitations}`:
    `visible` e o que a tela mostra, `suppressed` o que ficou de fora (com o
    motivo em cada um) - a resposta declara os dois, porque "sumiu" sem
    explicacao e indistinguivel de defeito.

    Tabela ausente (antes da 051) ou flag desligada -> tudo visivel, com a
    limitacao declarada e NENHUMA escrita."""
    if not cooldown_enabled():
        return {
            "visible": [dict(item) for item in insights],
            "suppressed": [],
            "decisions": [],
            "available": False,
            "limitations": [COOLDOWN_DISABLED_LIMITATION],
        }

    owner = require_user_id(user_id)
    now = at_second_precision(now)
    states, available = load_states(conn, user_id=owner)
    if not available:
        return {
            "visible": [dict(item) for item in insights],
            "suppressed": [],
            "decisions": [],
            "available": False,
            "limitations": [TABLE_MISSING_LIMITATION],
        }

    decisions: list[dict[str, Any]] = []
    visible: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []

    for insight in insights:
        key = alert_key_for(insight)
        state = states.get(alert_key_hash(key))
        decision = decide(insight, state, now=now, alert_key=key)
        decisions.append(decision)
        item = dict(insight)
        item["cooldown"] = cooldown_public(decision)
        (visible if decision["emit"] else suppressed).append(item)

    persisted = persist(conn, user_id=owner, decisions=decisions, now=now)
    shown, hidden = partition(decisions)
    log_event(
        "insight.suppressed_cooldown",
        user_id=owner,
        emitted=len(shown),
        suppressed=len(hidden),
        suppressed_keys=hidden,
        escalated=[d["alert_key"] for d in decisions if d["reason"] == REASON_ESCALATED],
        persisted=persisted,
    )

    limitations = [SUPPRESSED_LIMITATION] if suppressed else []
    return {
        "visible": visible,
        "suppressed": suppressed,
        "decisions": decisions,
        "available": True,
        "limitations": limitations,
    }


__all__ = [
    "COOLDOWN_DISABLED_LIMITATION",
    "COOLDOWN_ENV",
    "COOLDOWN_VERSION",
    "EMIT_REASONS",
    "MIGRATION",
    "REASON_COOLDOWN_ACTIVE",
    "REASON_COOLDOWN_EXPIRED",
    "REASON_ESCALATED",
    "REASON_FIRST_SEEN",
    "REASON_NEW_EPISODE",
    "SUPPRESSED_LIMITATION",
    "TABLE",
    "TABLE_MISSING_LIMITATION",
    "alert_key_hash",
    "apply_cooldown",
    "at_second_precision",
    "cooldown_enabled",
    "cooldown_public",
    "cooldown_until",
    "decide",
    "episode_is_closed",
    "has_alert_states_table",
    "is_worse",
    "load_states",
    "partition",
    "persist",
    "reset_alert_states_table_cache",
]
