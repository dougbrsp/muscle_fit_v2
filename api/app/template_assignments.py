"""Vigencia da atribuicao de Modelo de Treino: UMA por usuario por vez, com
historico (M23; migracao 049).

Antes da 049, "qual e o Modelo do usuario?" era respondido em QUATRO lugares
com a mesma query copiada (`analytics._resolve_latest_assigned_template_id`,
`training_types.resolve_active_training_type_for_user`,
`muscle_summary_service.ASSIGNED_TEMPLATE_HEAD_SQL`,
`workout_plan_templates.list_my_assigned_templates`), todas com
`ORDER BY assigned_at DESC LIMIT 1`. Esse `LIMIT 1` era um DESEMPATE SILENCIOSO:
o banco permitia N modelos atribuidos ao mesmo tempo e a leitura escolhia um
sem dizer que escolheu. Este modulo e o unico lugar que responde essa
pergunta agora.

Duas perguntas, nao uma:

- `resolve_active_template_id(user_id)` - qual e o Modelo VIGENTE agora
  (`unassigned_at IS NULL`). E o que a geracao de ficha e o "o que treinar
  hoje" precisam.
- `resolve_template_id_at(user_id, momento)` - qual era o Modelo vigente
  NAQUELE INSTANTE, isto e, a linha cujo intervalo `[assigned_at,
  unassigned_at)` contem o momento. E o que um numero HISTORICO do dashboard
  precisa para nao ser medido contra a meta de um modelo que ainda nem
  existia.

A segunda muda numeros ja exibidos e e o comportamento PERMANENTE: toda janela
resolve o Modelo da epoca (versao/data). A flag `DASHBOARD_PLAN_VERSION_BOUNDARY`
foi aposentada em 2026-10-08 (COD-01); nao ha mais caminho "vigente hoje" para
numeros historicos.

Intervalo SEMIABERTO `[assigned_at, unassigned_at)`: o instante em que um
modelo e encerrado e o instante em que o proximo comeca (a 049 escreve
`unassigned_at` = `assigned_at` da seguinte, e `encerrar_vigente` usa o mesmo
`NOW()` das duas escritas). Fechado dos dois lados, esse instante pertenceria
a dois modelos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional, Sequence

from . import request_memo
from .date_helpers import to_date
from .db import transaction

# ---------------------------------------------------------------------------
# SQL (um lugar so)
# ---------------------------------------------------------------------------

# Vigente AGORA. `t.is_active = 1` mantem o filtro que as quatro copias
# antigas ja faziam: um Modelo desativado pelo admin nao volta a guiar
# ninguem so por continuar atribuido.
ACTIVE_ASSIGNMENT_SQL = """
SELECT a.id, a.template_id, a.user_id, a.assigned_by_user_id, a.assigned_at,
       a.unassigned_at, t.name AS template_name, t.training_type_id, t.volume_mode
FROM workout_plan_template_assignments a
JOIN workout_plan_templates t ON t.id = a.template_id
WHERE a.user_id = %s AND a.unassigned_at IS NULL AND t.is_active = 1
LIMIT 1
"""

# Vigente NAQUELE instante. O UNIQUE de `active_user_id` (049) garante no
# maximo uma linha aberta; o backfill garante que as encerradas nao se
# sobrepoem. Ainda assim o `ORDER BY` fica explicito: sem ele, um historico
# importado a mao com sobreposicao devolveria uma linha ARBITRARIA - o mesmo
# desempate silencioso que este modulo existe para acabar.
#
# PR3 (versionamento): `training_type_id`/`volume_mode` sao os da VERSAO do
# Modelo vigente no MESMO instante (JOIN, sem statement extra); sem versao
# naquele instante, os do cabecalho de hoje.
ASSIGNMENT_AT_SQL = """
SELECT a.id, a.template_id, a.user_id, a.assigned_by_user_id, a.assigned_at,
       a.unassigned_at, t.name AS template_name,
       CASE WHEN v.id IS NULL THEN t.training_type_id ELSE v.training_type_id END AS training_type_id,
       CASE WHEN v.id IS NULL THEN t.volume_mode ELSE v.volume_mode END AS volume_mode
FROM workout_plan_template_assignments a
JOIN workout_plan_templates t ON t.id = a.template_id
LEFT JOIN workout_plan_template_versions v
       ON v.template_id = a.template_id
      AND (v.effective_from IS NULL OR v.effective_from <= %s)
      AND (v.superseded_at IS NULL OR v.superseded_at > %s)
WHERE a.user_id = %s
  AND a.assigned_at <= %s
  AND (a.unassigned_at IS NULL OR a.unassigned_at > %s)
ORDER BY a.assigned_at DESC, a.id DESC
LIMIT 1
"""

# Historico inteiro, do mais recente para o mais antigo. `LEFT JOIN` de
# proposito: um Modelo APAGADO leva a atribuicao junto (FK ON DELETE CASCADE),
# mas um desativado nao - e ele precisa continuar aparecendo no historico,
# senao o passado ficaria com um buraco sem explicacao.
HISTORY_SQL = """
SELECT a.id, a.template_id, a.user_id, a.assigned_by_user_id, a.assigned_at,
       a.unassigned_at, a.unassigned_by_user_id,
       t.name AS template_name, t.is_active AS template_is_active,
       t.training_type_id, t.volume_mode
FROM workout_plan_template_assignments a
LEFT JOIN workout_plan_templates t ON t.id = a.template_id
WHERE a.user_id = %s
ORDER BY a.assigned_at DESC, a.id DESC
"""

# Mesmo historico, com `training_type_id`/`volume_mode` da VERSAO do Modelo no
# FIM do trecho que a atribuicao governa dentro de uma janela que termina em
# `%s` (PR3): o fim e o ultimo instante da atribuicao (`unassigned_at` e
# exclusivo, entao 1 segundo antes) ou o fim da janela, o que vier primeiro -
# exatamente o fim do segmento de `segment_window`. Um statement, como antes.
HISTORY_AT_END_SQL = """
SELECT a.id, a.template_id, a.user_id, a.assigned_by_user_id, a.assigned_at,
       a.unassigned_at, a.unassigned_by_user_id,
       t.name AS template_name, t.is_active AS template_is_active,
       CASE WHEN v.id IS NULL THEN t.training_type_id ELSE v.training_type_id END AS training_type_id,
       CASE WHEN v.id IS NULL THEN t.volume_mode ELSE v.volume_mode END AS volume_mode
FROM workout_plan_template_assignments a
LEFT JOIN workout_plan_templates t ON t.id = a.template_id
LEFT JOIN workout_plan_template_versions v
       ON v.template_id = a.template_id
      AND (v.effective_from IS NULL
           OR v.effective_from <= LEAST(COALESCE(a.unassigned_at - INTERVAL 1 SECOND, %s), %s))
      AND (v.superseded_at IS NULL
           OR v.superseded_at > LEAST(COALESCE(a.unassigned_at - INTERVAL 1 SECOND, %s), %s))
WHERE a.user_id = %s
ORDER BY a.assigned_at DESC, a.id DESC
"""

# Encerra a vigente. `unassigned_at = %s` (e nao `NOW()`) para o fim de uma e
# o inicio da proxima serem o MESMO instante, sem buraco de alguns
# milissegundos entre os dois statements.
CLOSE_ACTIVE_SQL = """
UPDATE workout_plan_template_assignments
SET unassigned_at = %s, unassigned_by_user_id = %s
WHERE user_id = %s AND unassigned_at IS NULL
"""

CLOSE_ONE_SQL = """
UPDATE workout_plan_template_assignments
SET unassigned_at = %s, unassigned_by_user_id = %s
WHERE user_id = %s AND template_id = %s AND unassigned_at IS NULL
"""

# Exclusao logica do Modelo (065): encerra a vigencia de TODOS que o seguem,
# com o mesmo instante e o mesmo autor - a desatribuicao do M23 em lote.
CLOSE_TEMPLATE_SQL = """
UPDATE workout_plan_template_assignments
SET unassigned_at = %s, unassigned_by_user_id = %s
WHERE template_id = %s AND unassigned_at IS NULL
"""

INSERT_SQL = """
INSERT INTO workout_plan_template_assignments
    (template_id, user_id, assigned_by_user_id, assigned_at)
VALUES (%s, %s, %s, %s)
"""


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assignment:
    """Uma linha do historico de vigencia. `unassigned_at is None` = VIGENTE."""

    id: int
    template_id: int
    user_id: int
    assigned_at: Optional[datetime]
    unassigned_at: Optional[datetime]
    template_name: Optional[str] = None
    training_type_id: Optional[int] = None
    volume_mode: Optional[str] = None
    assigned_by_user_id: Optional[int] = None
    unassigned_by_user_id: Optional[int] = None
    template_is_active: Optional[int] = None

    @property
    def is_current(self) -> bool:
        return self.unassigned_at is None


def _row_to_assignment(row: Any) -> Optional[Assignment]:
    if not row:
        return None
    return Assignment(
        id=int(row["id"]),
        template_id=int(row["template_id"]),
        user_id=int(row["user_id"]),
        assigned_at=row.get("assigned_at"),
        unassigned_at=row.get("unassigned_at"),
        template_name=row.get("template_name"),
        training_type_id=row.get("training_type_id"),
        volume_mode=row.get("volume_mode"),
        assigned_by_user_id=row.get("assigned_by_user_id"),
        unassigned_by_user_id=row.get("unassigned_by_user_id"),
        template_is_active=row.get("template_is_active"),
    )


# Memo por requisicao (PERF-01, `request_memo`): a MESMA leitura (sql + params) repetida
# por servicos diferentes da mesma chamada GET vira uma so. As quatro escritas deste
# modulo chamam `forget(MEMO_NAME)`.
MEMO_NAME = "template_assignments"


def _query_one(sql: str, params: tuple) -> Optional[dict[str, Any]]:
    def load() -> Optional[dict[str, Any]]:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()

    return request_memo.memoize((MEMO_NAME, "one", sql, params), load)


def _query_all(sql: str, params: tuple) -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall() or [])

    return request_memo.memoize((MEMO_NAME, "all", sql, params), load)


def resolve_active_assignment(user_id: int) -> Optional[Assignment]:
    """Atribuicao VIGENTE agora, ou `None` se o usuario nao tem nenhuma."""
    return _row_to_assignment(_query_one(ACTIVE_ASSIGNMENT_SQL, (user_id,)))


def resolve_active_template_id(user_id: int) -> Optional[int]:
    assignment = resolve_active_assignment(user_id)
    return assignment.template_id if assignment is not None else None


def _as_moment(moment: date | datetime, timezone_name: Optional[str] = None) -> datetime:
    """Uma DATA vira o FIM daquele dia (23:59:59) DO USUARIO, expresso no
    relogio do banco (UTC) - nao o comeco.

    Motivo: o chamador tipico passa `window.date_to`, o ultimo dia da janela.
    Se um modelo foi atribuido as 14h desse dia, ele JA e o modelo daquele dia
    - resolver pelo comeco do dia devolveria o anterior e a janela terminaria
    medida contra um modelo que o usuario ja tinha trocado.

    Relogio (M76 Lote 2b): `assigned_at`, `effective_from`/`superseded_at` e
    as vigencias de metas nascem de `NOW()` do banco (UTC). O fim do dia D de
    Sao Paulo e D+1 02:59:59 UTC; comparar com D 23:59:59 deixava uma edicao
    feita entre 21h e 24h de Brasilia fora do proprio dia (o teste
    `test_com_a_flag_desligada_os_leitores_mostram_a_vigente` falhava nessa
    faixa). Dono da conversao: `time_windows.user_day_end_db`.

    `timezone_name` default = `DEFAULT_TIMEZONE` (fuso de todos os usuarios
    hoje); um `datetime` ja e um instante do relogio do banco e passa direto."""
    if isinstance(moment, datetime):
        return moment
    from .intelligence.dashboard.time_windows import user_day_end_db
    from .timezones import DEFAULT_TIMEZONE

    return user_day_end_db(moment, timezone_name or DEFAULT_TIMEZONE)


def resolve_assignment_at(user_id: int, moment: date | datetime) -> Optional[Assignment]:
    """Atribuicao vigente NAQUELE instante - a fronteira de versao do plano
    (§4 do plano do Novo Dashboard). Independe da flag: quem chama esta
    funcao esta pedindo a resolucao por data explicitamente."""
    at = _as_moment(moment)
    return _row_to_assignment(_query_one(ASSIGNMENT_AT_SQL, (at, at, user_id, at, at)))


def resolve_template_id_for_window(user_id: int, date_to: Optional[date | datetime]) -> Optional[int]:
    """`template_id` que governa uma janela que TERMINA em `date_to`.

    Sem `date_to` devolve o vigente hoje. Com ele, devolve o vigente no fim
    da janela: e o modelo que o usuario seguia quando aquela janela fechou.

    Por que o FIM da janela e nao cada dia dela: uma janela pode atravessar
    duas versoes do plano, e somar meta parcial de duas seria uma metrica
    nova, nao uma correcao. O fim da janela responde "contra qual plano este
    periodo deve ser lido" com uma regra so e sem inventar aritmetica; quem
    precisa VER que a janela atravessa uma troca usa `segment_window`."""
    if date_to is None:
        return resolve_active_template_id(user_id)
    assignment = resolve_assignment_at(user_id, date_to)
    if assignment is not None:
        return assignment.template_id
    # Janela inteiramente ANTERIOR a primeira atribuicao: nao havia modelo
    # naquela epoca. Cair no vigente de hoje seria voltar a medir o passado
    # contra um plano que ainda nao existia - exatamente o defeito do M23.
    return None


def list_assignment_history(user_id: int, *, window_end: Optional[date | datetime] = None) -> list[Assignment]:
    """Historico inteiro, do mais recente para o mais antigo. O primeiro item
    e a vigente quando ela existe.

    `window_end` (PR3): `training_type_id`/`volume_mode` de cada atribuicao
    passam a ser os da versao do Modelo no fim do trecho que ela governa
    numa janela que termina em `window_end` (`HISTORY_AT_END_SQL`)."""
    if window_end is None:
        rows = _query_all(HISTORY_SQL, (user_id,))
    else:
        fim = _as_moment(window_end)
        rows = _query_all(HISTORY_AT_END_SQL, (fim, fim, fim, fim, user_id))
    return [a for a in (_row_to_assignment(row) for row in rows) if a is not None]


@dataclass(frozen=True)
class WindowSegment:
    """Pedaco de uma janela governado por UMA atribuicao. `assignment is None`
    = trecho em que o usuario nao tinha Modelo nenhum."""

    date_from: date
    date_to: date
    assignment: Optional[Assignment]


def _db_instant_as_user_date(value: Any) -> Optional[date]:
    """Dia do USUARIO de um instante do relogio do banco (`assigned_at`/
    `unassigned_at`, UTC) - a mesma base de `_as_moment`, para o segmento e a
    resolucao por data concordarem sobre de quem e o dia da troca. `date`
    puro (sem hora) e string so de data passam como estao."""
    if isinstance(value, datetime):
        from .intelligence.dashboard.time_windows import db_instant_to_user_date
        from .timezones import DEFAULT_TIMEZONE

        return db_instant_to_user_date(value, DEFAULT_TIMEZONE)
    if isinstance(value, str) and len(value.strip()) > 10:
        try:
            return _db_instant_as_user_date(datetime.fromisoformat(value.strip().replace(" ", "T")))
        except ValueError:
            pass
    return _as_date(value)


# `DATETIME`/`DATE`/string ISO -> `date` (`date_helpers.to_date`, COD-06; a
# mesma funcao de `intelligence/dashboard/coercion.to_date`).
#
# A string entra na conta de proposito: os dois backends devolvem a MESMA
# coluna em tipos diferentes. O shim da RDS Data API ja converte `DATETIME`
# de volta para `datetime` (`db_dataapi._parse_datetime`), entao na pratica
# so chegam objetos aqui - mas um chamador que monte um `Assignment` a
# partir de uma linha crua (ou de JSON) passaria string, e devolver `None`
# ali viraria `date.min` na chamada abaixo: uma vigencia comecando no ano 1,
# silenciosamente.
_as_date = to_date


def segment_window(
    user_id: int,
    date_from: date,
    date_to: date,
    history: Optional[Sequence[Assignment]] = None,
) -> list[WindowSegment]:
    """Quebra `[date_from, date_to]` nos trechos governados por cada
    atribuicao, do mais antigo para o mais novo.

    Funcao PURA quando `history` e passada (e e assim que os testes a usam):
    toda a regra de fronteira fica testavel sem banco. Uma janela que nao
    atravessa troca nenhuma devolve UM segmento - e esse e o caso comum."""
    if history is None:
        # PR3: cada trecho le `training_type_id`/`volume_mode` da versao do
        # Modelo no FIM dele (uma resolucao por janela, no mesmo statement do
        # historico).
        history = list_assignment_history(user_id, window_end=date_to)
    if date_from > date_to:
        return []

    # Do mais antigo para o mais novo. Intervalos EM DIAS CIVIS: o ultimo dia
    # de uma vigencia e o dia ANTERIOR ao da troca, porque `unassigned_at` e
    # exclusivo e, quando a troca acontece no meio do dia, o dia inteiro fica
    # com o modelo que ENTROU (mesma regra de `_as_moment`). Uma atribuicao
    # que nasceu e morreu no mesmo dia nao cobre dia civil nenhum e por isso
    # nao vira segmento - ela continua no historico, que e quem guarda o
    # fato de ela ter existido.
    intervalos: list[tuple[date, date, Assignment]] = []
    for assignment in sorted(history, key=lambda a: (_db_instant_as_user_date(a.assigned_at) or date.min, a.id)):
        inicio = _db_instant_as_user_date(assignment.assigned_at)
        if inicio is None:
            # Atribuicao sem `assigned_at` legivel nao tem inicio de vigencia,
            # e portanto nao governa dia nenhum. Cair em `date.min` a faria
            # governar o passado inteiro - um erro caro e mudo. Fica de fora.
            continue
        fim_exclusivo = _db_instant_as_user_date(assignment.unassigned_at)
        ultimo_dia = date.max if fim_exclusivo is None else fim_exclusivo - timedelta(days=1)
        if ultimo_dia >= inicio:
            intervalos.append((inicio, ultimo_dia, assignment))

    segments: list[WindowSegment] = []
    cursor = date_from
    for inicio, ultimo_dia, assignment in intervalos:
        if ultimo_dia < cursor or inicio > date_to:
            continue
        if inicio > cursor:
            segments.append(WindowSegment(date_from=cursor, date_to=inicio - timedelta(days=1), assignment=None))
            cursor = inicio
        trecho_fim = min(ultimo_dia, date_to)
        segments.append(WindowSegment(date_from=cursor, date_to=trecho_fim, assignment=assignment))
        if trecho_fim >= date_to:
            return segments
        cursor = trecho_fim + timedelta(days=1)

    if cursor <= date_to:
        segments.append(WindowSegment(date_from=cursor, date_to=date_to, assignment=None))
    return segments


# ---------------------------------------------------------------------------
# Escrita (usada pelos endpoints de atribuicao)
# ---------------------------------------------------------------------------


def db_now(cur: Any) -> datetime:
    """Instante do RELOGIO DO BANCO, lido uma vez e reusado nos dois
    statements de uma troca.

    Por que nao `datetime.now()` do Python: `assigned_at` sempre nasceu de
    `CURRENT_TIMESTAMP` (DEFAULT da coluna, desde o baseline 007), isto e, do
    relogio do MySQL. Misturar os dois relogios faria uma vigencia encerrada
    "antes" de ter comecado sempre que a API e o banco estivessem alguns
    segundos fora de sincronia - e a comparacao `assigned_at <= momento <
    unassigned_at`, que e o coracao da resolucao por data, passaria a mentir.

    Por que nao `NOW()` direto nos dois UPDATE/INSERT: `NOW()` e constante
    DENTRO de um statement, nao entre statements. Lido uma vez, o mesmo valor
    fecha a vigencia anterior e abre a seguinte, sem buraco nem sobreposicao."""
    cur.execute("SELECT NOW() AS agora")
    row = cur.fetchone()
    return row["agora"] if isinstance(row, dict) else row[0]


def close_active_assignment(cur: Any, user_id: int, *, moment: datetime, by_user_id: Optional[int]) -> int:
    """Encerra a atribuicao vigente do usuario (se houver). Devolve quantas
    linhas fechou - 0 ou 1, porque o UNIQUE da 049 nao deixa passar de uma.
    Recebe o CURSOR (e nao abre transacao propria) para encerrar a anterior e
    abrir a nova serem atomicos: um instante com zero modelos vigentes e um
    estado que nenhum leitor deve conseguir observar."""
    request_memo.forget(MEMO_NAME)
    cur.execute(CLOSE_ACTIVE_SQL, (moment, by_user_id, user_id))
    return int(cur.rowcount or 0)


def close_assignment_of_template(
    cur: Any, user_id: int, template_id: int, *, moment: datetime, by_user_id: Optional[int]
) -> int:
    """Encerra a vigencia de UM modelo especifico (a desatribuicao explicita).
    Devolve 0 quando aquele modelo nao era o vigente daquele usuario."""
    request_memo.forget(MEMO_NAME)
    cur.execute(CLOSE_ONE_SQL, (moment, by_user_id, user_id, template_id))
    return int(cur.rowcount or 0)


def close_assignments_of_template_for_everyone(
    cur: Any, template_id: int, *, moment: datetime, by_user_id: Optional[int]
) -> int:
    """Encerra a vigencia de um Modelo para todos os usuarios (exclusao logica
    do Modelo, 065). Devolve quantas atribuicoes fechou. Nenhuma linha e
    apagada: o historico de quem seguiu o Modelo fica."""
    request_memo.forget(MEMO_NAME)
    cur.execute(CLOSE_TEMPLATE_SQL, (moment, by_user_id, template_id))
    return int(cur.rowcount or 0)


def insert_assignment(
    cur: Any, template_id: int, user_id: int, *, assigned_by_user_id: int, moment: datetime
) -> None:
    request_memo.forget(MEMO_NAME)
    cur.execute(INSERT_SQL, (template_id, user_id, assigned_by_user_id, moment))
