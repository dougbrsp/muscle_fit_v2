"""111 - Fundacao das permissoes por grupos (RBAC), ADR 0011, lote 1.

Cria as cinco tabelas e semeia dados que NINGUEM LE ainda: nenhum endpoint muda
de comportamento. `users.role` continua sendo a fonte dos guards ate o lote 2.

    access_groups          grupos (is_system=1: admin, personal_trainer, usuario;
                           `free` e criado vazio, is_system=0, sem concessoes e
                           sem membros - decisao do usuario de 10/11/2026)
    access_group_grants    concessao = grupo x recurso x acao x escopo
                           (UNIQUE group_id+resource+action: um escopo por acao)
    user_access_groups     membros (UNIQUE user_id+group_id)
    trainer_students       vinculo treinador-aluno (so o admin cria; lote 5 usa)
    access_audit_log       auditoria de grupos/membros/vinculos

FKs: so o que e dono-filho (grupo->concessoes/membros; users->membros/vinculos,
ON DELETE CASCADE). `created_by_user_id`, `actor_user_id` e `target_id` sao
auditoria de fato ja ocorrido: SEM FK (mesma decisao da 049/064). Nenhuma FK
para `exercise_catalog` (restricao da 028).

Vinculo ativo unico: `trainer_students.active_flag` e coluna gerada VIRTUAL
`IF(status = 'ativo', 1, NULL)` e o UNIQUE (trainer, student, active_flag) recusa
dois vinculos ATIVOS iguais. NULL nao colide em UNIQUE do MySQL, entao o
historico (encerrados) pode repetir o par. Indice funcional/parcial nao existem
no Aurora/MySQL 8 compativel com a Data API; este e o mesmo truque da 049.

--- Matriz semeada -----------------------------------------------------------

A matriz abaixo e um RETRATO CONGELADO (esta migracao nao importa `api/app`).
A matriz e espelhada em `api/app/access/catalog.py` e conferida por teste de
paridade. O grupo `admin` NAO recebe linhas: acesso total e implicito do
grupo de sistema, resolvido no codigo. A matriz reproduz o que os guards de
hoje concedem (`security.require_admin`, `require_admin_or_pt`,
`nutrition_access.resolve_target_user`); divergencias em relatorio do lote.

--- Idempotencia -------------------------------------------------------------

`plan(db)` so LE `information_schema`/contagens. Tabela ausente -> CREATE;
presente em formato diferente -> `AccessGroupsRbacError` ANTES do primeiro
statement. Cada seed entra so enquanto a tabela esta ausente ou VAZIA: depois o
conteudo e do admin (tela de grupos) e re-semear desfaria edicoes (concessao
removida, membro retirado). Consequencia: se alguem esvaziar por completo
`access_group_grants` ou `user_access_groups` e a 111 for reexecutada, o seed
e refeito (o runner so executa a 111 uma vez).

Cada seed e UM UNICO statement (grupos; concessoes de todos os grupos; membros
de todos os papeis), todos INSERT IGNORE. Um statement e atomico no InnoDB (ou
grava tudo ou nada), entao cada tabela so tem dois estados observaveis: vazia ou
completa. Logo, apos QUALQUER interrupcao entre statements, chamar `plan(db)` de
novo emite exatamente o que falta e nada alem disso (sem duplicar: UNIQUEs +
IGNORE). Estado completo -> nenhum statement de seed; estado completo editado
pelo admin tambem nao e re-semeado. A migracao como um todo NAO e atomica (DDL faz
commit implicito; ver `app/migrate.py`): a garantia e de convergencia por
reexecucao, nao de tudo-ou-nada. Ressalva: se `users` estiver vazia, o seed de
membros nao grava linha e a tabela segue "vazia" (inofensivo: sem usuarios nao ha
o que semear). `down` e `DROP TABLE IF EXISTS` das cinco (perde grupos,
concessoes, membros, vinculos e auditoria; o codigo anterior nao le nada disso).

Data API: um statement por chamada; sem PREPARE/SET; modulo autocontido.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Nomes
# ---------------------------------------------------------------------------

T_GROUPS = "access_groups"
T_GRANTS = "access_group_grants"
T_MEMBERS = "user_access_groups"
T_TRAINER_STUDENTS = "trainer_students"
T_AUDIT = "access_audit_log"

# Ordem de criacao (pais antes dos filhos); o down usa a inversa.
TABLES_IN_CREATE_ORDER = (T_GROUPS, T_GRANTS, T_MEMBERS, T_TRAINER_STUDENTS, T_AUDIT)

# ---------------------------------------------------------------------------
# Matriz (constantes nomeadas, faceis de comparar com o catalogo vivo)
# ---------------------------------------------------------------------------

ACTION_VER = "ver"
ACTION_EDITAR = "editar"
ACTION_EXCLUIR = "excluir"
ACTIONS = (ACTION_VER, ACTION_EDITAR, ACTION_EXCLUIR)

SCOPE_PROPRIOS = "proprios"
SCOPE_ALUNOS = "alunos"
SCOPE_TODOS = "todos"
SCOPES = (SCOPE_PROPRIOS, SCOPE_ALUNOS, SCOPE_TODOS)

GROUP_ADMIN = "admin"
GROUP_PERSONAL_TRAINER = "personal_trainer"
GROUP_USUARIO = "usuario"
GROUP_FREE = "free"

# (group_key, name, description, is_system)
SEED_GROUPS: tuple[tuple[str, str, str, int], ...] = (
    (
        GROUP_ADMIN,
        "Admin",
        "Acesso total (grupo de sistema; concessoes implicitas no codigo).",
        1,
    ),
    (
        GROUP_PERSONAL_TRAINER,
        "Personal Trainer",
        "Usuario mais Modelos, metas de ativacao e nutricao de terceiros.",
        1,
    ),
    (
        GROUP_USUARIO,
        "Usuario",
        "Dados proprios e IAs que qualquer usuario logado usa hoje.",
        1,
    ),
    (
        GROUP_FREE,
        "Free",
        "Grupo vazio, sem concessoes e sem membros (decisao de 10/11/2026).",
        0,
    ),
)

# users.role -> group_key (users.role NAO e alterado)
ROLE_TO_GROUP: tuple[tuple[str, str], ...] = (
    ("admin", GROUP_ADMIN),
    ("personal_trainer", GROUP_PERSONAL_TRAINER),
    ("regular", GROUP_USUARIO),
)

# Recursos de dados proprios: ver/editar/excluir com escopo `proprios`.
RESOURCES_DADOS_PROPRIOS: tuple[str, ...] = (
    "dashboards",
    "treino.registro",
    "treino.modo_treino",
    "treino.planos",
    "treino.preferencias",
    "treino.academias",
    "progressao",
    "volume.grupamentos",
    "corpo.indicadores",
    "corpo.checkin",
    "cardio",
    "integracoes.intervals",
    "nutricao.diario",
    "nutricao.plano",
    "nutricao.alimentos",
    "nutricao.checkin",
    "relatorios",
)

# IAs que qualquer usuario logado usa hoje (nenhum guard de papel nas rotas).
RESOURCES_IA_USUARIO: tuple[str, ...] = (
    "ia.modo_treino",
    "ia.insights",
    "ia.analise_treinos",
    "ia.geracao_plano",
    "ia.cardio",
    "ia.nutricao",
)

# Usuario: so `ver`.
USUARIO_SO_VER: tuple[tuple[str, str], ...] = (
    ("exercicios.catalogo", SCOPE_TODOS),  # catalogo global, somente leitura
    ("modelos.treino", SCOPE_PROPRIOS),  # Modelos atribuidos a ele (me_router)
)

# Personal trainer: o que `require_admin_or_pt` libera (admin ou PT) hoje.
PT_RECURSOS_TODOS: tuple[str, ...] = (
    "modelos.treino",  # workout_plan_templates (router inteiro) + cardio apply
    "modelos.metas_ativacao",  # training_types (router inteiro)
    "ia.modelo_periodizacao",  # /workout-plan-templates/periodization-draft
)

# Personal trainer: nutricao de terceiros (`resolve_target_user`/`plan_actor`),
# escopo `todos` HOJE; o lote 5 troca para `alunos`.
PT_NUTRICAO_TERCEIROS: tuple[str, ...] = (
    "nutricao.diario",
    "nutricao.plano",
    "nutricao.checkin",
    "ia.nutricao",
)

# Recursos sem NENHUMA concessao para usuario/personal_trainer (so o admin):
RESOURCES_SO_ADMIN: tuple[str, ...] = (
    "admin.usuarios",
    "admin.grupos",
    "admin.regras_carga",
    "admin.catalogos",
    "admin.seguranca_treino",
    "ia.cards_exercicio",
)


def _linhas(resources, actions, scope) -> list[tuple[str, str, str]]:
    return [(resource, action, scope) for resource in resources for action in actions]


def _build_usuario() -> tuple[tuple[str, str, str], ...]:
    rows = _linhas(
        RESOURCES_DADOS_PROPRIOS + RESOURCES_IA_USUARIO, ACTIONS, SCOPE_PROPRIOS
    )
    rows += [(resource, ACTION_VER, scope) for resource, scope in USUARIO_SO_VER]
    return tuple(rows)


def _build_personal_trainer() -> tuple[tuple[str, str, str], ...]:
    # `todos` cobre `proprios`; UNIQUE (grupo, recurso, acao) = um escopo por acao,
    # entao a linha do PT substitui a do usuario quando o recurso coincide.
    rows: dict[tuple[str, str], str] = {(r, a): s for r, a, s in _build_usuario()}
    for resource, action, scope in _linhas(
        PT_RECURSOS_TODOS + PT_NUTRICAO_TERCEIROS, ACTIONS, SCOPE_TODOS
    ):
        rows[(resource, action)] = scope
    return tuple(
        (resource, action, scope) for (resource, action), scope in rows.items()
    )


GRANTS_BY_GROUP: dict[str, tuple[tuple[str, str, str], ...]] = {
    GROUP_ADMIN: (),  # acesso total implicito no grupo de sistema
    GROUP_PERSONAL_TRAINER: _build_personal_trainer(),
    GROUP_USUARIO: _build_usuario(),
    GROUP_FREE: (),
}

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_TAIL = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"

CREATE_SQL: dict[str, str] = {
    T_GROUPS: f"""
CREATE TABLE IF NOT EXISTS {T_GROUPS} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  group_key VARCHAR(64) NOT NULL,
  name VARCHAR(120) NOT NULL,
  description VARCHAR(500) NULL,
  is_system TINYINT NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_access_groups_key (group_key)
) {_TAIL}
""",
    T_GRANTS: f"""
CREATE TABLE IF NOT EXISTS {T_GRANTS} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  group_id BIGINT NOT NULL,
  resource VARCHAR(64) NOT NULL,
  action ENUM('ver','editar','excluir') NOT NULL,
  scope ENUM('proprios','alunos','todos') NOT NULL,
  UNIQUE KEY uq_agg_group_resource_action (group_id, resource, action),
  CONSTRAINT fk_agg_group FOREIGN KEY (group_id) REFERENCES {T_GROUPS}(id) ON DELETE CASCADE
) {_TAIL}
""",
    T_MEMBERS: f"""
CREATE TABLE IF NOT EXISTS {T_MEMBERS} (
  user_id BIGINT NOT NULL,
  group_id BIGINT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_by_user_id BIGINT NULL,
  UNIQUE KEY uq_uag_user_group (user_id, group_id),
  KEY idx_uag_group (group_id),
  CONSTRAINT fk_uag_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_uag_group FOREIGN KEY (group_id) REFERENCES {T_GROUPS}(id) ON DELETE CASCADE
) {_TAIL}
""",
    T_TRAINER_STUDENTS: f"""
CREATE TABLE IF NOT EXISTS {T_TRAINER_STUDENTS} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  trainer_user_id BIGINT NOT NULL,
  student_user_id BIGINT NOT NULL,
  status ENUM('ativo','encerrado') NOT NULL DEFAULT 'ativo',
  created_by_user_id BIGINT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at TIMESTAMP NULL DEFAULT NULL,
  active_flag TINYINT GENERATED ALWAYS AS (IF(status = 'ativo', 1, NULL)) VIRTUAL,
  UNIQUE KEY uq_ts_active_pair (trainer_user_id, student_user_id, active_flag),
  KEY idx_ts_student (student_user_id),
  CONSTRAINT fk_ts_trainer FOREIGN KEY (trainer_user_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT fk_ts_student FOREIGN KEY (student_user_id) REFERENCES users(id) ON DELETE CASCADE
) {_TAIL}
""",
    T_AUDIT: f"""
CREATE TABLE IF NOT EXISTS {T_AUDIT} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  actor_user_id BIGINT NULL,
  action VARCHAR(64) NOT NULL,
  target_type VARCHAR(64) NOT NULL,
  target_id BIGINT NULL,
  detalhe JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_aal_target (target_type, target_id),
  KEY idx_aal_actor (actor_user_id),
  KEY idx_aal_created (created_at)
) {_TAIL}
""",
}

# tabela -> {coluna: (tipo normalizado, IS_NULLABLE)}
EXPECTED_COLUMNS: dict[str, dict[str, tuple[str, str]]] = {
    T_GROUPS: {
        "id": ("bigint", "NO"),
        "group_key": ("varchar(64)", "NO"),
        "name": ("varchar(120)", "NO"),
        "description": ("varchar(500)", "YES"),
        "is_system": ("tinyint", "NO"),
        "created_at": ("timestamp", "NO"),
        "updated_at": ("timestamp", "NO"),
    },
    T_GRANTS: {
        "id": ("bigint", "NO"),
        "group_id": ("bigint", "NO"),
        "resource": ("varchar(64)", "NO"),
        "action": ("enum('ver','editar','excluir')", "NO"),
        "scope": ("enum('proprios','alunos','todos')", "NO"),
    },
    T_MEMBERS: {
        "user_id": ("bigint", "NO"),
        "group_id": ("bigint", "NO"),
        "created_at": ("timestamp", "NO"),
        "created_by_user_id": ("bigint", "YES"),
    },
    T_TRAINER_STUDENTS: {
        "id": ("bigint", "NO"),
        "trainer_user_id": ("bigint", "NO"),
        "student_user_id": ("bigint", "NO"),
        "status": ("enum('ativo','encerrado')", "NO"),
        "created_by_user_id": ("bigint", "YES"),
        "created_at": ("timestamp", "NO"),
        "ended_at": ("timestamp", "YES"),
        "active_flag": ("tinyint", "YES"),
    },
    T_AUDIT: {
        "id": ("bigint", "NO"),
        "actor_user_id": ("bigint", "YES"),
        "action": ("varchar(64)", "NO"),
        "target_type": ("varchar(64)", "NO"),
        "target_id": ("bigint", "YES"),
        "detalhe": ("json", "YES"),
        "created_at": ("timestamp", "NO"),
    },
}

# tabela -> {indice UNIQUE: colunas}
EXPECTED_UNIQUES: dict[str, dict[str, tuple[str, ...]]] = {
    T_GROUPS: {"uq_access_groups_key": ("group_key",)},
    T_GRANTS: {"uq_agg_group_resource_action": ("group_id", "resource", "action")},
    T_MEMBERS: {"uq_uag_user_group": ("user_id", "group_id")},
    T_TRAINER_STUDENTS: {
        "uq_ts_active_pair": ("trainer_user_id", "student_user_id", "active_flag")
    },
    T_AUDIT: {},
}

# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

INSERT_GROUPS_SQL = (
    f"INSERT IGNORE INTO {T_GROUPS} (group_key, name, description, is_system) VALUES "
    + ", ".join(["(%s, %s, %s, %s)"] * len(SEED_GROUPS))
)

# Membros: UM unico INSERT IGNORE ... SELECT (todos os papeis de uma vez). Papel
# fora de ROLE_TO_GROUP cai em CASE ... END = NULL e nao casa o JOIN (fica sem grupo).
BACKFILL_MEMBERS_SQL = (
    f"INSERT IGNORE INTO {T_MEMBERS} (user_id, group_id) "
    f"SELECT u.id, g.id FROM users u JOIN {T_GROUPS} g ON g.group_key = CASE u.role "
    + " ".join(["WHEN %s THEN %s"] * len(ROLE_TO_GROUP))
    + " END"
)


def _backfill_members_params() -> tuple:
    return tuple(value for pair in ROLE_TO_GROUP for value in pair)


def _insert_grants_statement() -> tuple[str, tuple]:
    """TODAS as concessoes de TODOS os grupos num unico INSERT IGNORE multi-linha:
    ou a tabela fica vazia ou fica completa (um statement e atomico no InnoDB)."""
    linhas: list[tuple[str, str, str, str]] = [
        (group_key, resource, action, scope)
        for group_key, rows in GRANTS_BY_GROUP.items()
        for resource, action, scope in rows
    ]
    sql = (
        f"INSERT IGNORE INTO {T_GRANTS} (group_id, resource, action, scope) VALUES "
        + ", ".join(
            [f"((SELECT id FROM {T_GROUPS} WHERE group_key = %s), %s, %s, %s)"]
            * len(linhas)
        )
    )
    return sql, tuple(value for linha in linhas for value in linha)


_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class AccessGroupsRbacError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise AccessGroupsRbacError(f"111: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _existing_tables(db) -> dict[str, str]:
    names = ", ".join(["%s"] * len(TABLES_IN_CREATE_ORDER))
    return {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({names})",
            TABLES_IN_CREATE_ORDER,
        )
    }


def _check_shape(db, table: str, engine: str) -> None:
    if engine.lower() != "innodb":
        _fail(f"{table} usa ENGINE={engine}, esperado InnoDB")
    columns = {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
    }
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS[table].items():
        info = columns.get(name)
        if info is None:
            _fail(f"{table}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(
                f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}"
            )
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(
                f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}"
            )
    indexes: dict[str, list[dict]] = {}
    for row in db.query(
        "SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    ):
        indexes.setdefault(_text(row["INDEX_NAME"]), []).append(row)
    for index_name, expected_cols in EXPECTED_UNIQUES[table].items():
        rows = indexes.get(index_name)
        if not rows:
            _fail(f"{table}.{index_name} nao existe")
        found = tuple(
            _text(r["COLUMN_NAME"])
            for r in sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"]))
        )
        if found != expected_cols:
            _fail(f"{table}.{index_name} cobre {found}, esperado {expected_cols}")
        if int(rows[0]["NON_UNIQUE"]) != 0:
            _fail(f"{table}.{index_name} existe mas NAO e UNIQUE")


def _is_empty(db, table: str) -> bool:
    rows = db.query(f"SELECT COUNT(*) AS n FROM {table}", None)
    return int(rows[0]["n"]) == 0


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura; valida tudo antes de devolver qualquer statement."""
    existing = _existing_tables(db)
    for table in TABLES_IN_CREATE_ORDER:
        if table in existing:
            _check_shape(db, table, existing[table])

    statements: list[tuple[str, tuple | None]] = []
    for table in TABLES_IN_CREATE_ORDER:
        if table not in existing:
            statements.append((CREATE_SQL[table], None))

    # Seed: so enquanto a tabela esta ausente ou vazia (depois o conteudo e do admin).
    seed_groups = T_GROUPS not in existing or _is_empty(db, T_GROUPS)
    seed_grants = T_GRANTS not in existing or _is_empty(db, T_GRANTS)
    seed_members = T_MEMBERS not in existing or _is_empty(db, T_MEMBERS)

    if seed_groups:
        params: list = []
        for group_key, name, description, is_system in SEED_GROUPS:
            params.extend((group_key, name, description, is_system))
        statements.append((INSERT_GROUPS_SQL, tuple(params)))
    if seed_grants:
        statements.append(_insert_grants_statement())
    if seed_members:
        statements.append((BACKFILL_MEMBERS_SQL, _backfill_members_params()))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    existing = _existing_tables(db)
    return [
        (f"DROP TABLE IF EXISTS {table}", None)
        for table in reversed(TABLES_IN_CREATE_ORDER)
        if table in existing
    ]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
