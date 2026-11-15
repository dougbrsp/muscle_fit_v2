"""115 - Nutricao de terceiros do `personal_trainer` passa de `todos` para `alunos` (ADR 0011, lote 5A).

Decisao do usuario (13/11/2026): no lote 5 so os dados de aluno mudam. O grupo de
sistema `personal_trainer` passa `nutricao.diario`, `nutricao.plano`,
`nutricao.checkin` e `ia.nutricao` (todas as acoes) de escopo `todos` para `alunos`
(so alunos com vinculo ativo). `modelos.treino`, `modelos.metas_ativacao`,
`modelos.tipos_treino` e `ia.modelo_periodizacao` seguem em `todos` ate o lote 6.
`usuario`, `free` e `admin` nao mudam; grupos personalizados nunca sao tocados.
Retrato congelado: nao importa `api/app`; o acumulado 111 + 113 + 114 + 115 e
conferido contra `api/app/access/catalog.py` por teste de paridade.

So DML em `access_group_grants` (sem DDL, sem FK nova): um `UPDATE` do escopo, so nas
linhas do grupo de sistema `personal_trainer` desses 4 recursos que ainda estao em
`todos`. A 111, 113 e 114 nao sao editadas.

Idempotencia: `plan(db)` so LE (tabelas, grupo de sistema, linhas ainda em `todos`)
e emite o `UPDATE` somente se houver alguma. Concessao que o admin ja editou para
outro escopo (ex.: `proprios`) ou que ja esta em `alunos` NAO e tocada; sobre o
estado convergido o plano e vazio.

Pre-requisito: a 111. Se `access_groups` e/ou `access_group_grants` nao existirem,
`plan(db)` devolve plano VAZIO (sem erro, sem escrita, com log): caso do
`--dry-run` em banco novo. Tabelas presentes mas `personal_trainer` ausente como
grupo de sistema (`is_system = 1`): `AccessGrantsPtNutricaoAlunosError` antes de
qualquer escrita.

Verificacao ao fim de `up`: relê as linhas ainda em `todos` e levanta o erro
proprio se restar alguma. Em `--dry-run` (`db.dry_run`) nada e escrito, entao a
releitura e pulada.

Impacto em producao: com a 111 e a 113 aplicadas atualiza ate 12 linhas (4 recursos
x 3 acoes). Passa a valer quando as rotas de nutricao consomem `alunos` (mesmo
lote): o PT so acessa aluno com vinculo ativo (`has_active_link`).

down: volta para `todos` as linhas desses 4 recursos do `personal_trainer` que
estiverem em `alunos`. Em producao o runner nao roda `down`.

Data API: um statement por chamada, SQL parametrizado, sem PREPARE/SET.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

T_GROUPS = "access_groups"
T_GRANTS = "access_group_grants"

SCOPE_TODOS = "todos"
SCOPE_ALUNOS = "alunos"

GROUP_PERSONAL_TRAINER = "personal_trainer"

RESOURCES = (
    "nutricao.diario",
    "nutricao.plano",
    "nutricao.checkin",
    "ia.nutricao",
)
ACTIONS = ("ver", "editar", "excluir")

# Retrato congelado das linhas ALTERADAS: group_key -> (recurso, acao, escopo novo)
GRANTS_BY_GROUP: dict[str, tuple[tuple[str, str, str], ...]] = {
    GROUP_PERSONAL_TRAINER: tuple(
        (resource, action, SCOPE_ALUNOS) for resource in RESOURCES for action in ACTIONS
    ),
}
TARGET_GROUP_KEYS = tuple(GRANTS_BY_GROUP)

_IN = ", ".join(["%s"] * len(RESOURCES))
SELECT_TABLES_SQL = (
    "SELECT TABLE_NAME FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)"
)
SELECT_SYSTEM_GROUPS_SQL = f"SELECT id, group_key FROM {T_GROUPS} WHERE is_system = 1"
SELECT_SCOPE_GRANTS_SQL = (
    f"SELECT resource, action FROM {T_GRANTS} "
    f"WHERE group_id = %s AND scope = %s AND resource IN ({_IN})"
)
UPDATE_SCOPE_SQL = (
    f"UPDATE {T_GRANTS} SET scope = %s "
    f"WHERE group_id = %s AND resource IN ({_IN}) AND scope = %s"
)


class AccessGrantsPtNutricaoAlunosError(Exception):
    """Pre-requisito (111) incompleto ou atualizacao planejada nao gravada."""


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _target_group_ids(db) -> dict[str, int] | None:
    """`group_key -> id` dos grupos de sistema alvo; None se a 111 nao foi aplicada.
    Levanta o erro proprio se faltar algum grupo de sistema alvo."""
    presentes = {
        _text(row["TABLE_NAME"])
        for row in db.query(SELECT_TABLES_SQL, (T_GROUPS, T_GRANTS))
    }
    faltando = {T_GROUPS, T_GRANTS} - presentes
    if faltando:
        logger.info(
            "115: tabela(s) %s ausente(s); a 111 ainda nao foi aplicada. Plano vazio.",
            sorted(faltando),
        )
        return None
    group_id_by_key = {
        _text(row["group_key"]): int(row["id"])
        for row in db.query(SELECT_SYSTEM_GROUPS_SQL, None)
    }
    ausentes = [k for k in TARGET_GROUP_KEYS if k not in group_id_by_key]
    if ausentes:
        raise AccessGrantsPtNutricaoAlunosError(
            f"115: grupo(s) de sistema {ausentes} ausente(s) em {T_GROUPS}; "
            "rode a 111 antes. Nenhum statement executado."
        )
    return {k: group_id_by_key[k] for k in TARGET_GROUP_KEYS}


def _com_escopo(db, group_id: int, scope: str) -> set[tuple[str, str]]:
    return {
        (_text(row["resource"]), _text(row["action"]))
        for row in db.query(SELECT_SCOPE_GRANTS_SQL, (group_id, scope, *RESOURCES))
    }


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura; so o necessario."""
    ids = _target_group_ids(db)
    if ids is None:
        return []
    statements: list[tuple[str, tuple | None]] = []
    for group_key in TARGET_GROUP_KEYS:
        group_id = ids[group_key]
        if _com_escopo(db, group_id, SCOPE_TODOS):
            statements.append(
                (UPDATE_SCOPE_SQL, (SCOPE_ALUNOS, group_id, *RESOURCES, SCOPE_TODOS))
            )
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    ids = _target_group_ids(db)
    if ids is None:
        return []
    return [
        (UPDATE_SCOPE_SQL, (SCOPE_TODOS, ids[group_key], *RESOURCES, SCOPE_ALUNOS))
        for group_key in TARGET_GROUP_KEYS
        if _com_escopo(db, ids[group_key], SCOPE_ALUNOS)
    ]


def up(db):
    statements = plan(db)
    for sql, params in statements:
        db.execute(sql, params)
    if not statements or getattr(db, "dry_run", False):
        return
    restantes = []
    for _, params in statements:
        restantes += sorted(_com_escopo(db, params[1], SCOPE_TODOS))
    if restantes:
        raise AccessGrantsPtNutricaoAlunosError(
            f"115: concessao(oes) (recurso, acao) {restantes} do personal_trainer "
            "ainda em escopo 'todos' apos o UPDATE."
        )


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
