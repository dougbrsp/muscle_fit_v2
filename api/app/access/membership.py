"""Escrita de `user_access_groups` (ADR 0011, pre-condicoes do lote 2).

Unico modulo que grava na tabela de membros. Todas as funcoes recebem o cursor
da transacao do chamador e nunca abrem transacao propria: a associacao entra ou
sai junto com o INSERT/UPDATE de `users`. Importar nao acessa banco.
"""

from __future__ import annotations

from typing import Any, Final, Optional

from .catalog import GROUP_ADMIN, GROUP_PERSONAL_TRAINER, GROUP_USUARIO

# Papel (`users.role`) -> chave do grupo de sistema. Espelha `ROLE_TO_GROUP` da
# migracao 111; um teste de paridade compara as duas. Papel fora daqui nao tem
# grupo.
ROLE_TO_SYSTEM_GROUP: Final[dict[str, str]] = {
    "admin": GROUP_ADMIN,
    "personal_trainer": GROUP_PERSONAL_TRAINER,
    "regular": GROUP_USUARIO,
}


class MissingSystemGroupError(RuntimeError):
    """Grupo de sistema ausente em `access_groups` (ex.: migracao 111 nao aplicada)."""


class InvalidMembershipTargetError(ValueError):
    """user_id invalido (None, 0, negativo, bool ou nao inteiro) para associar a grupo."""


class LastAdminError(Exception):
    """A operacao deixaria o sistema sem nenhum admin ativo."""


def system_group_key_for_role(role: Any) -> Optional[str]:
    """Chave do grupo de sistema do papel, ou None para papel desconhecido."""
    if not isinstance(role, str):
        return None
    return ROLE_TO_SYSTEM_GROUP.get(role)


def _cell(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[0]


def _system_group_id(cur: Any, group_key: str) -> int:
    cur.execute(
        "SELECT id FROM access_groups WHERE group_key = %s AND is_system = 1",
        (group_key,),
    )
    row = cur.fetchone()
    if not row:
        raise MissingSystemGroupError(f"Grupo de sistema '{group_key}' não encontrado em access_groups.")
    return int(_cell(row, "id"))


def _require_valid_user_id(user_id: Any) -> int:
    """Exige inteiro estritamente positivo (rejeita None, 0, negativo e bool)."""
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise InvalidMembershipTargetError(f"user_id inválido para associação a grupo: {user_id!r}.")
    return user_id


def _insert_membership(cur: Any, user_id: int, group_id: int, created_by_user_id: Optional[int]) -> None:
    # Idempotente pelo UNIQUE uq_uag_user_group (user_id, group_id); sem IGNORE,
    # erro de FK/NOT NULL propaga e desfaz a transacao do chamador.
    cur.execute(
        "INSERT INTO user_access_groups (user_id, group_id, created_by_user_id) VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE user_id = user_id",
        (user_id, group_id, created_by_user_id),
    )


def assign_role_group(cur: Any, user_id: int, role: Any, created_by_user_id: Optional[int] = None) -> Optional[str]:
    """Associa o usuario ao grupo de sistema do papel. Devolve a chave do grupo,
    ou None quando o papel nao tem grupo (nada e gravado). Levanta
    MissingSystemGroupError se o grupo nao existe no banco e
    InvalidMembershipTargetError se user_id nao e inteiro positivo."""
    user_id = _require_valid_user_id(user_id)
    group_key = system_group_key_for_role(role)
    if group_key is None:
        return None
    group_id = _system_group_id(cur, group_key)
    _insert_membership(cur, user_id, group_id, created_by_user_id)
    return group_key


def switch_role_group(cur: Any, user_id: int, new_role: Any, created_by_user_id: Optional[int] = None) -> Optional[str]:
    """Troca de papel: remove as associacoes a grupos de sistema que nao sao o do
    papel novo e insere a correta. Grupos que nao sao de sistema ficam intactos.
    Papel desconhecido: remove os grupos de sistema e nao insere nenhum.
    Levanta InvalidMembershipTargetError (sem statement) se user_id e invalido."""
    user_id = _require_valid_user_id(user_id)
    group_key = system_group_key_for_role(new_role)
    if group_key is None:
        cur.execute(
            "DELETE FROM user_access_groups WHERE user_id = %s "
            "AND group_id IN (SELECT id FROM access_groups WHERE is_system = 1)",
            (user_id,),
        )
        return None
    group_id = _system_group_id(cur, group_key)
    cur.execute(
        "DELETE FROM user_access_groups WHERE user_id = %s "
        "AND group_id IN (SELECT id FROM access_groups WHERE is_system = 1 AND id <> %s)",
        (user_id, group_id),
    )
    _insert_membership(cur, user_id, group_id, created_by_user_id)
    return group_key


def lock_active_admin_ids(cur: Any) -> list[int]:
    """Trava (`FOR UPDATE`) e devolve os ids dos usuarios ativos no grupo de
    sistema `admin`. Deve rodar na mesma transacao da escrita que remove o admin."""
    cur.execute(
        """
        SELECT u.id AS id
        FROM users u
        JOIN user_access_groups uag ON uag.user_id = u.id
        JOIN access_groups g ON g.id = uag.group_id
        WHERE u.is_active = 1 AND g.group_key = %s AND g.is_system = 1
        ORDER BY u.id
        FOR UPDATE
        """,
        (GROUP_ADMIN,),
    )
    return [int(_cell(row, "id")) for row in cur.fetchall() or []]


def is_system_admin_member(cur: Any, user_id: int) -> bool:
    """Leitura: o usuario (ativo ou nao) pertence ao grupo de sistema `admin`?"""
    cur.execute(
        """
        SELECT 1 AS member
        FROM user_access_groups uag
        JOIN access_groups g ON g.id = uag.group_id
        WHERE uag.user_id = %s AND g.group_key = %s AND g.is_system = 1
        LIMIT 1
        """,
        (int(user_id), GROUP_ADMIN),
    )
    return bool(cur.fetchone())


def ensure_not_last_admin(cur: Any, user_id: int, *, locked_admin_ids: Optional[list[int]] = None) -> None:
    """Levanta LastAdminError se `user_id` e o unico admin ativo. Se o alvo nao
    e admin ativo, ou ha outro admin ativo, retorna sem efeito.

    `locked_admin_ids`: ids ja travados por `lock_active_admin_ids` NESTA
    transacao (ordem ADR 0011: admins primeiro, alvo depois). Com outro admin
    na lista a remocao e segura (as linhas travadas nao mudam); sendo o alvo o
    unico da lista, recusa; lista sem o alvo e sem outro admin (alvo pode ter
    sido promovido enquanto esperavamos o lock) cai na releitura travada."""
    target = int(user_id)
    if locked_admin_ids is not None:
        others = set(locked_admin_ids) - {target}
        if others:
            return
        if target in locked_admin_ids:
            raise LastAdminError("Não é possível remover o último administrador ativo.")
    admin_ids = lock_active_admin_ids(cur)
    if target in admin_ids and len(set(admin_ids) - {target}) == 0:
        raise LastAdminError("Não é possível remover o último administrador ativo.")
