"""112 - Reconcilia os grupos de sistema com `users.role` (ADR 0011, lote 2A).

A 111 associou cada usuario ao grupo do seu papel UMA vez. Quem foi criado ou
teve o papel alterado depois ficou sem grupo ou no grupo antigo. Ate o fim do
lote 2, `users.role` e a verdade; esta migracao faz os grupos de sistema
(`access_groups.is_system = 1`: admin, personal_trainer, usuario) refletirem o
papel atual. Retrato congelado: nao importa `api/app`; o mapeamento papel->grupo
e repetido abaixo (teste de paridade com a 111).

Ordem do plano:
  (b) remove associacoes a grupos de SISTEMA que nao correspondem ao papel
      atual (papel fora dos tres valores: fica sem grupo de sistema);
  (a) insere a associacao ao grupo de sistema do papel para todo usuario com
      papel conhecido que nao a tenha (inclui inativos: o papel deles volta a
      valer se forem reativados).
Associacoes a grupos que NAO sao de sistema (ex.: `free`, grupos criados pelo
admin) nunca sao tocadas.

Idempotencia: `plan(db)` so LE (grupos, usuarios, associacoes) e emite
exatamente o que falta, um statement parametrizado por usuario/grupo
(DELETE por user_id+group_id; INSERT IGNORE, protegido tambem pelo UNIQUE).
Sobre o estado convergido o plano e vazio. Interrompida em qualquer ponto,
reexecutar converge. Sem DDL e sem FK nova.

Pre-requisito: a 111. Se `access_groups` e/ou `user_access_groups` nao existirem,
`plan(db)` devolve plano VAZIO (sem erro, sem escrita, com log): e o caso do
`--dry-run`, que planeja o lote sem aplicar a 111 antes. No `migrate` real a 111
e aplicada e confirmada antes de a 112 ser planejada (o runner planeja e executa
uma migracao por vez), entao a 112 ve as tabelas. Tabelas presentes mas algum dos
tres grupos de sistema ausente: `UserAccessGroupsBackfillError` antes de qualquer
escrita.

Impacto em producao: com a 111 ja aplicada (producao, 10/11/2026) o
comportamento e o mesmo de antes; sem as tabelas o plano e vazio em vez de erro
(so afeta dry-run/lotes em que a 111 ainda esta pendente). So altera linhas de `user_access_groups` (remove as de
sistema divergentes do papel, adiciona as faltantes); `users` e os guards
atuais nao mudam. Reexecutavel sem efeito. Volume esperado pequeno (4 usuarios
na conferencia de 10/11/2026; nao medido de novo).

down: NAO apaga associacoes (nao ha como distinguir as da 112 das demais);
`plan_down` e vazio.

Data API: um statement por chamada, SQL parametrizado, sem PREPARE/SET.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

T_GROUPS = "access_groups"
T_MEMBERS = "user_access_groups"

# users.role -> group_key de sistema (copia da 111; users.role NAO e alterado)
ROLE_TO_GROUP: tuple[tuple[str, str], ...] = (
    ("admin", "admin"),
    ("personal_trainer", "personal_trainer"),
    ("regular", "usuario"),
)
SYSTEM_GROUP_KEYS = tuple(group for _, group in ROLE_TO_GROUP)

SELECT_TABLES_SQL = (
    "SELECT TABLE_NAME FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)"
)
SELECT_SYSTEM_GROUPS_SQL = (
    f"SELECT id, group_key FROM {T_GROUPS} WHERE is_system = 1"
)
SELECT_USERS_SQL = "SELECT id, role FROM users"
SELECT_SYSTEM_MEMBERS_SQL = (
    f"SELECT m.user_id AS user_id, m.group_id AS group_id FROM {T_MEMBERS} m "
    f"JOIN {T_GROUPS} g ON g.id = m.group_id WHERE g.is_system = 1"
)
DELETE_MEMBER_SQL = f"DELETE FROM {T_MEMBERS} WHERE user_id = %s AND group_id = %s"
INSERT_MEMBER_SQL = f"INSERT IGNORE INTO {T_MEMBERS} (user_id, group_id) VALUES (%s, %s)"


class UserAccessGroupsBackfillError(Exception):
    """Pre-requisito (111) ausente; nenhum statement desta execucao foi aplicado."""


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura; valida o pre-requisito antes de devolver statements."""
    presentes = {
        _text(row["TABLE_NAME"])
        for row in db.query(SELECT_TABLES_SQL, (T_GROUPS, T_MEMBERS))
    }
    faltando = {T_GROUPS, T_MEMBERS} - presentes
    if faltando:
        logger.info(
            "112: tabela(s) %s ausente(s); a 111 ainda nao foi aplicada. Plano vazio.",
            sorted(faltando),
        )
        return []

    group_id_by_key = {
        _text(row["group_key"]): int(row["id"])
        for row in db.query(SELECT_SYSTEM_GROUPS_SQL, None)
    }
    ausentes = [k for k in SYSTEM_GROUP_KEYS if k not in group_id_by_key]
    if ausentes:
        raise UserAccessGroupsBackfillError(
            f"112: grupo(s) de sistema {ausentes} ausente(s) em {T_GROUPS}; "
            "rode a 111 antes. Nenhum statement executado."
        )
    system_group_ids = {group_id_by_key[k] for k in SYSTEM_GROUP_KEYS}

    role_to_group = dict(ROLE_TO_GROUP)
    # user_id -> id do grupo de sistema esperado (None: papel desconhecido)
    wanted: dict[int, int | None] = {}
    for row in db.query(SELECT_USERS_SQL, None):
        key = role_to_group.get(_text(row["role"]))
        wanted[int(row["id"])] = group_id_by_key[key] if key is not None else None

    current: set[tuple[int, int]] = {
        (int(row["user_id"]), int(row["group_id"]))
        for row in db.query(SELECT_SYSTEM_MEMBERS_SQL, None)
        if int(row["group_id"]) in system_group_ids
    }

    deletes: list[tuple[str, tuple | None]] = []
    inserts: list[tuple[str, tuple | None]] = []
    for user_id, group_id in sorted(current):
        if user_id in wanted and wanted[user_id] != group_id:
            deletes.append((DELETE_MEMBER_SQL, (user_id, group_id)))
    for user_id in sorted(wanted):
        group_id = wanted[user_id]
        if group_id is not None and (user_id, group_id) not in current:
            inserts.append((INSERT_MEMBER_SQL, (user_id, group_id)))
    return deletes + inserts


def plan_down(db) -> list[tuple[str, tuple | None]]:
    return []


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
