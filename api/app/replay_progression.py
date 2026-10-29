"""Replay das trilhas de progressao como OPERACAO, executavel pelo pipeline.

O replay (secao 5.11; `docs/refatoracao-motores/ATIVACAO_MOTORES_DETERMINISTICOS.md`)
e o passo 1 obrigatorio da ativacao dos motores determinísticos: sem trilhas em
`progression_tracks` o motor prescreveria sobre estado vazio em silencio. Ate
aqui ele existia SO como script de linha de comando
(`api/scripts/rebuild_progression_tracks.py`), o que o tornava inexecutavel em
producao - onde nada e aplicado a mao.

Este modulo e a logica de operacao em volta do replay que ja existe em
`app/progression_state.py` (`rebuild_for_user`, `users_with_history`,
`state_snapshot`): ele NAO reimplementa o motor nem a persistencia. Os dois
pontos de entrada usam este modulo:

- `app.migrate.handler` com `{"action": "replay-progression", "dry_run": ...}`
  (Lambda `musclefit-migrations`, invocada por
  `.github/workflows/db-migrations.yml` -> `scripts/invoke-migrations.sh`);
- o script `scripts/rebuild_progression_tracks.py` (DEV, PyMySQL).

O que este modulo acrescenta ao `rebuild_for_user`:

1. **Leitura paginada do historico.** No caminho de PRODUCAO (shim
   `app/db_dataapi.py`) toda resposta da RDS Data API tem teto de 1 MB
   (`UnsupportedResultException`, ja observado em producao - ver
   `db_dataapi.py`). A query do historico devolve UMA LINHA POR SERIE com ~26
   colunas; com 1.484 series (volume medido na Aurora) um unico
   `fetch_last_completed_sessions(user, 10_000)` passaria desse teto. Aqui a
   leitura e feita em paginas de `page_size` SESSOES e as paginas sao
   recolhidas na mesma ordem cronologica de uma leitura unica (ver
   `fetch_sessions_paged`), entao o resultado do replay e identico nos dois
   backends - o mesmo codigo roda em DEV e em producao, sem bifurcacao.
2. **Orcamento de tempo.** A Lambda tem timeout; o replay e a unica coisa aqui
   que cresce com o historico. O orcamento e conferido ANTES de cada usuario e
   antes do commit: estourar levanta `ReplayTimeBudgetError` e nada e gravado.
3. **Tudo ou nada.** O replay inteiro roda em UMA transacao: commit unico no
   fim (ou `rollback` em `dry_run`). Falha no meio - inclusive a Lambda ser
   morta pelo timeout - nao deixa escrita parcial: sem commit, a Data API
   descarta a transacao.
4. **Relatorio comparavel.** Contagens antes/depois por usuario, sessoes
   lidas, tempo e o `fingerprint` (md5 do `state_snapshot`) que torna a
   idempotencia verificavel entre duas execucoes: rodar duas vezes tem que
   devolver o MESMO fingerprint e as MESMAS contagens.

Nao depende de flag nenhuma (`PROGRESSION_ENGINE`/`GENERATION_ENGINE`): o
replay e bootstrap de dados, e roda antes das flags de proposito.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional, Sequence

from . import progression_state as progression
from .domain.training_history import SessionRecord, TrainingHistoryRepository

logger = logging.getLogger("fitness.replay")

ConnFactory = Callable[[], Any]

# Sessoes por pagina. 10 sessoes ~ 250 KB de resposta da Data API no volume
# medido em producao (1.484 series / 60 sessoes = ~25 series por sessao, ~1 KB
# por linha no envelope JSON da Data API) - 4x de margem para o teto de 1 MB.
DEFAULT_PAGE_SIZE = 10
MIN_PAGE_SIZE = 1
PAGE_SIZE_ENV = "REPLAY_PAGE_SIZE"

# Teto duro de sessoes lidas por usuario (o mesmo do script original).
MAX_SESSIONS_PER_USER = 10_000

# Orcamento default quando nao ha `context` da Lambda (CLI/DEV).
DEFAULT_TIME_BUDGET_SECONDS = 240.0


class ReplayError(Exception):
    """Falha do replay. Nada foi gravado (a transacao nao chega ao commit)."""


class ReplayTimeBudgetError(ReplayError):
    """Orcamento de tempo estourado antes de terminar; nada foi gravado."""


class ReplayResultTooLargeError(ReplayError):
    """Uma pagina de UMA sessao ainda estourou o teto de 1 MB da Data API."""


# ---------------------------------------------------------------------------
# Leitura paginada do historico
# ---------------------------------------------------------------------------


def _is_result_too_large(exc: BaseException) -> bool:
    """Teto de 1 MB da RDS Data API. O shim encapsula o erro do boto3 em
    `DataApiError` mantendo o texto original, entao casar pela mensagem
    funciona para os dois (a `ClientError` crua e a encapsulada)."""
    text = str(exc)
    return "UnsupportedResultException" in text or "exceeds the size limit" in text


def _fetch_page(
    repo: TrainingHistoryRepository, user_id: int, limit: int, offset: int
) -> tuple[list[SessionRecord], int]:
    """Uma pagina de ate `limit` sessoes a partir de `offset`.

    Estourar 1 MB nao e erro fatal: a pagina e refeita com metade das sessoes
    (ate 1). Devolve tambem o tamanho de pagina que de fato funcionou, para o
    chamador continuar do offset certo e nao repetir o erro na pagina seguinte.
    """
    size = max(MIN_PAGE_SIZE, int(limit))
    while True:
        try:
            return repo.fetch_last_completed_sessions(user_id, size, offset=offset), size
        except Exception as exc:  # noqa: BLE001 - so o 1 MB e tratado; o resto sobe
            if not _is_result_too_large(exc):
                raise
            if size <= MIN_PAGE_SIZE:
                raise ReplayResultTooLargeError(
                    f"historico do usuario {user_id}: uma unica sessao (offset={offset}) passa do teto "
                    f"de 1 MB da RDS Data API. Nada foi gravado."
                ) from exc
            size = max(MIN_PAGE_SIZE, size // 2)
            logger.warning(
                "replay: resposta acima do teto de 1 MB da Data API; reduzindo a pagina para %s sessao(oes) "
                "(user_id=%s, offset=%s)",
                size,
                user_id,
                offset,
            )


def fetch_sessions_paged(
    conn: Any,
    user_id: int,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_sessions: int = MAX_SESSIONS_PER_USER,
) -> tuple[list[SessionRecord], int]:
    """Historico COMPLETO do usuario em ordem cronologica crescente, lido em
    paginas. Devolve `(sessoes, paginas_lidas)`.

    Equivalencia com a leitura unica: a subconsulta do historico ordena as
    sessoes por `occurred_at DESC, id DESC` e o `LIMIT/OFFSET` fatia ESSA
    ordem, enquanto o `ORDER BY` externo devolve cada pagina em ordem
    crescente. Logo a lista global crescente e a concatenacao das paginas na
    ordem INVERSA (a ultima pagina e a mais antiga) - identica, sessao a
    sessao, ao que uma leitura unica devolveria. Ha teste comparando as duas
    formas nos dois backends.
    """
    repo = TrainingHistoryRepository(conn)
    pages: list[list[SessionRecord]] = []
    size = max(MIN_PAGE_SIZE, int(page_size))
    offset = 0
    while offset < max_sessions:
        limit = min(size, max_sessions - offset)
        page, used = _fetch_page(repo, user_id, limit, offset)
        pages.append(page)
        if len(page) < used:  # pagina incompleta = fim do historico
            break
        offset += len(page)
        if used < limit:
            # a pagina foi reduzida pelo teto de 1 MB: as proximas ja nascem
            # no tamanho que funcionou, em vez de repetir o erro.
            size = used

    sessions: list[SessionRecord] = []
    seen: set[int] = set()
    for page in reversed(pages):
        for session in page:
            # Defesa contra sessao repetida entre paginas (escrita concorrente
            # deslocaria a janela do OFFSET). Mantem a primeira ocorrencia.
            if session.activity_id in seen:
                continue
            seen.add(session.activity_id)
            sessions.append(session)
    return sessions, len(pages)


def _page_size(value: Optional[int] = None) -> int:
    if value is not None:
        return max(MIN_PAGE_SIZE, int(value))
    raw = (os.environ.get(PAGE_SIZE_ENV) or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_PAGE_SIZE


# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------


@dataclass
class ReplayUserReport:
    user_id: int
    sessions: int
    pages: int
    groups: int
    tracks: int
    decisions: int
    groups_before: int
    tracks_before: int
    decisions_before: int
    fingerprint: str
    duration_ms: int
    snapshot: Optional[dict[str, Any]] = None


@dataclass
class ReplayReport:
    status: str = "ok"
    action: str = "replay-progression"
    dry_run: bool = True
    page_size: int = DEFAULT_PAGE_SIZE
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS
    users: list[ReplayUserReport] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    decisions_deleted_by_retention: Optional[int] = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fingerprint_of(snapshot: dict[str, Any]) -> str:
    """md5 do `state_snapshot` (sem ids nem `updated_at`). Duas execucoes do
    replay tem que dar o MESMO valor - e a prova de idempotencia que o
    operador ve no resumo do workflow."""
    payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def state_fingerprint(conn: Any, user_id: int) -> str:
    return fingerprint_of(progression.state_snapshot(conn, user_id))


_COUNT_TABLES = (
    ("groups_before", progression.GROUP_STATES_TABLE),
    ("tracks_before", progression.TRACKS_TABLE),
    ("decisions_before", progression.DECISIONS_TABLE),
)


def _existing_counts(conn: Any, user_id: int) -> dict[str, int]:
    """Quanto ja existe para o usuario ANTES do replay. Em producao isso e 0
    nas tres tabelas (nunca houve replay); diferente de 0 significa que o
    replay esta SUBSTITUINDO estado, nao criando."""
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for key, table in _COUNT_TABLES:
            cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id = %s", (int(user_id),))
            row = cur.fetchone()
            counts[key] = int((row or {}).get("n") or 0)
    return counts


# ---------------------------------------------------------------------------
# Operacao
# ---------------------------------------------------------------------------


def _default_conn_factory() -> Any:
    # Import tardio e reuso do retry de auto-pause do Aurora do runner de
    # migracoes (a Lambda e a mesma).
    from .migrate import _default_conn_factory as factory

    return factory()


def replay(
    user_ids: Optional[Sequence[int]] = None,
    *,
    dry_run: bool = True,
    conn_factory: Optional[ConnFactory] = None,
    page_size: Optional[int] = None,
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
    window_size: int = progression.SESSION_WINDOW,
    cleanup: bool = False,
    include_snapshot: bool = False,
) -> ReplayReport:
    """Reconstroi trilhas/fases/decisoes a partir do historico.

    `user_ids` vazio/None = todos os usuarios com historico de series.
    `dry_run=True` (default) calcula tudo e faz `rollback`: NADA e gravado.
    `cleanup` aplica a retencao de 18 meses (D15) - so fora do `dry_run`, e
    exposto apenas pelo script de DEV, nunca pela acao da Lambda.
    """
    from .migrate import _connect_with_retry

    started = time.monotonic()
    size = _page_size(page_size)
    report = ReplayReport(dry_run=bool(dry_run), page_size=size, time_budget_seconds=float(time_budget_seconds))

    def _check_budget(stage: str) -> None:
        elapsed = time.monotonic() - started
        if elapsed > float(time_budget_seconds):
            raise ReplayTimeBudgetError(
                f"orcamento de tempo do replay estourado ({elapsed:.1f}s > {float(time_budget_seconds):.1f}s) "
                f"{stage}. NADA foi gravado: a transacao nao chegou ao commit. "
                f"Rode por usuario (`user_ids`) ou aumente o timeout da Lambda."
            )

    conn = _connect_with_retry(conn_factory or _default_conn_factory)
    try:
        try:
            targets = [int(value) for value in (user_ids or [])] or progression.users_with_history(conn)
            for user_id in targets:
                _check_budget(f"antes do usuario {user_id}")
                user_started = time.monotonic()
                before = _existing_counts(conn, user_id)
                sessions, pages = fetch_sessions_paged(conn, user_id, page_size=size)
                counts = progression.rebuild_for_user(conn, user_id, window_size=window_size, sessions=sessions)
                snapshot = progression.state_snapshot(conn, user_id)
                item = ReplayUserReport(
                    user_id=int(user_id),
                    sessions=int(counts["sessions"]),
                    pages=pages,
                    groups=int(counts["groups"]),
                    tracks=int(counts["tracks"]),
                    decisions=int(counts["decisions"]),
                    fingerprint=fingerprint_of(snapshot),
                    duration_ms=int((time.monotonic() - user_started) * 1000),
                    snapshot=snapshot if include_snapshot else None,
                    **before,
                )
                report.users.append(item)
                if item.tracks == 0:
                    report.warnings.append(
                        f"usuario {user_id}: {item.sessions} sessao(oes) lida(s) e NENHUMA trilha criada "
                        "(par exercicio/equipamento nao resolveu em nenhuma serie)"
                    )
            if cleanup and not dry_run:
                report.decisions_deleted_by_retention = progression.retention_cleanup(conn)

            report.totals = {
                "users": len(report.users),
                "sessions": sum(item.sessions for item in report.users),
                "groups": sum(item.groups for item in report.users),
                "tracks": sum(item.tracks for item in report.users),
                "decisions": sum(item.decisions for item in report.users),
                "groups_before": sum(item.groups_before for item in report.users),
                "tracks_before": sum(item.tracks_before for item in report.users),
                "decisions_before": sum(item.decisions_before for item in report.users),
            }
            _check_budget("antes do commit")
            if dry_run:
                # Nada gravado: o relatorio acima e o PLANO, calculado sobre o
                # banco real e desfeito.
                conn.rollback()
            else:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    report.duration_ms = int((time.monotonic() - started) * 1000)
    for warning in report.warnings:
        logger.warning("replay: %s", warning)
    logger.info("replay: %s", json.dumps(report.to_dict(), ensure_ascii=False, default=str))
    return report
