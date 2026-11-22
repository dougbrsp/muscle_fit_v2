"""122 - Concessoes de `treino.academias.equipamentos` (issue #246).

Recurso novo (dominio `Treino`, rotulo "Acrescentar equipamento a academia"):
`POST /gym-equipment-catalog` deixa de exigir `treino.academias`/`editar` e passa
a exigir o recurso novo (so `editar` tem efeito; a matriz mostra as 3 colunas).

Compatibilidade (ninguem ganha nem perde acesso no deploy): para CADA grupo, de
sistema ou personalizado, que tem concessao em `treino.academias`, o recurso novo
recebe a mesma acao com o MESMO escopo (`INSERT ... SELECT`-equivalente feito em
Python: le as concessoes de `treino.academias` e emite `INSERT IGNORE`). `ver` e
`excluir` sao copiadas junto para o retrato dos grupos de sistema bater com
`DEFAULT_GRANTS_BY_GROUP` (teste de paridade); nenhuma rota le `ver`/`excluir`
do recurso novo. `admin` tem acesso total por codigo e nao tem linhas.

Medicao de producao (leitura, 22/11/2026): `admin` (por codigo), `usuario` e
`personal_trainer` com `treino.academias`/`editar` = `proprios`, e um grupo
personalizado vazio (sem concessoes). Efeito: 6 linhas em `proprios`; nenhum
grupo ganha ou perde acesso.

So DML em `access_group_grants`. Idempotente: `plan(db)` so LE e emite so o que
falta; concessao ja existente do recurso novo (mesmo com escopo editado) NAO e
tocada; convergido, o plano e vazio. Sem as tabelas da 111 o plano e VAZIO.

down: remove as concessoes do recurso novo (em producao o runner nao roda `down`).

Data API: um statement por chamada, SQL parametrizado, sem PREPARE/SET.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

T_GROUPS = "access_groups"
T_GRANTS = "access_group_grants"

RESOURCE = "treino.academias.equipamentos"
SOURCE_RESOURCE = "treino.academias"

ACTIONS = ("ver", "editar", "excluir")

# Retrato congelado dos grupos de sistema (paridade com o catalogo): grupo -> linhas.
GRANTS_BY_GROUP: dict[str, tuple[tuple[str, str, str], ...]] = {
    "usuario": tuple((RESOURCE, action, "proprios") for action in ACTIONS),
    "personal_trainer": tuple((RESOURCE, action, "proprios") for action in ACTIONS),
}

SELECT_TABLES_SQL = (
    "SELECT TABLE_NAME FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)"
)
SELECT_GRANTS_SQL = (
    f"SELECT group_id, action, scope FROM {T_GRANTS} WHERE resource = %s"
)
INSERT_GRANT_SQL = (
    f"INSERT IGNORE INTO {T_GRANTS} (group_id, resource, action, scope) "
    "VALUES (%s, %s, %s, %s)"
)
DELETE_GRANT_SQL = f"DELETE FROM {T_GRANTS} WHERE resource = %s"


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _tables_present(db) -> bool:
    presentes = {
        _text(row["TABLE_NAME"])
        for row in db.query(SELECT_TABLES_SQL, (T_GROUPS, T_GRANTS))
    }
    if {T_GROUPS, T_GRANTS} - presentes:
        logger.info("122: tabela(s) da 111 ausente(s). Plano vazio.")
        return False
    return True


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura; so o que falta."""
    if not _tables_present(db):
        return []
    existentes = {
        (int(row["group_id"]), _text(row["action"]))
        for row in db.query(SELECT_GRANTS_SQL, (RESOURCE,))
    }
    statements: list[tuple[str, tuple | None]] = []
    for row in db.query(SELECT_GRANTS_SQL, (SOURCE_RESOURCE,)):
        group_id, action = int(row["group_id"]), _text(row["action"])
        if (group_id, action) not in existentes:
            statements.append((INSERT_GRANT_SQL, (group_id, RESOURCE, action, _text(row["scope"]))))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    if not _tables_present(db):
        return []
    if not list(db.query(SELECT_GRANTS_SQL, (RESOURCE,))):
        return []
    return [(DELETE_GRANT_SQL, (RESOURCE,))]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
