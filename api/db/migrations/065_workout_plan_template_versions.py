"""065 - versionamento do Modelo de Treino (PR1 do plano de versoes).

    workout_plan_template_versions                      (NOVA)
      id                           BIGINT PK
      template_id                  BIGINT NOT NULL -> workout_plan_templates(id) ON DELETE CASCADE
      version_number               INT NOT NULL          1, 2, 3... por Modelo
      training_type_id             BIGINT NULL           cabecalho ESTRUTURAL
      volume_mode                  VARCHAR(20) NOT NULL    da versao (o que o
      template_type                VARCHAR(40) NOT NULL    motor e o dashboard
      grid_rows, grid_cols         TINYINT NULL            leem)
      methodology_intent           ENUM(056) NOT NULL DEFAULT 'standard'
      methodology_target_group_ids JSON NULL             alvos da intencao
      effective_from               DATETIME NULL         inicio (inclusivo); NULL = desde sempre
      superseded_at                DATETIME NULL         fim (exclusivo); NULL = vigente
      created_by_user_id           BIGINT NULL           autor da versao
      reverted_from_version_id     BIGINT NULL           reversao (PR4)
      change_summary               VARCHAR(255) NULL
      created_at                   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      current_template_id          BIGINT GENERATED ALWAYS AS
                                   (IF(superseded_at IS NULL, template_id, NULL)) VIRTUAL
      UNIQUE uq_wptv_current (current_template_id)
      UNIQUE uq_wptv_template_number (template_id, version_number)

    workout_plan_template_slots
      + version_id                 BIGINT NULL -> workout_plan_template_versions(id) ON DELETE CASCADE
      + KEY idx_wpts_template (template_id)
      - UNIQUE uq_wpts_template_sequence (template_id, sequence)
      + UNIQUE uq_wpts_version_sequence (version_id, sequence)

    workout_plan_templates
      + deleted_at                 DATETIME NULL         exclusao logica

Por que: o `PUT /workout-plan-templates/{id}` apagava e recriava slots e itens
no lugar. Nada do passado sobrava, e os dashboards releem fichas antigas com a
estrutura de HOJE (a coluna PLANEJADO do `/dashboard/engine` compara a ficha
de julho com o slot editado em setembro). A partir desta migracao cada edicao
ESTRUTURAL vira uma versao nova e as linhas de uma versao nunca mudam.

Mesma gramatica de vigencia da 049 (atribuicao) e da 052 (metas): intervalo
semiaberto `[effective_from, superseded_at)` e a invariante "UMA vigente por
Modelo" no BANCO, pela coluna gerada + UNIQUE (uma tupla com NULL nao colide
em indice UNIQUE, entao o historico guarda quantas versoes superadas quiser).

`template_id` NAO muda: atribuicoes (049), `generation_runs`, sequencia de
slots (`resolve_next_slot`) e aderencia dependem dele. Os itens herdam a
versao pelo `slot_id` - nao precisam de coluna propria.

--- Ordem dos statements (e por que) ---------------------------------------

1. `CREATE TABLE` da tabela de versoes (a FK do passo 7 aponta para ela).
2. `deleted_at` em `workout_plan_templates`.
3. `version_id` + `idx_wpts_template (template_id)` em slots. O indice entra
   ANTES do DROP do UNIQUE antigo e nao e decoracao: `fk_wpts_template` usa
   `uq_wpts_template_sequence` como indice de apoio e o InnoDB recusa o DROP
   do ultimo indice que atende a FK (errno 150). Mesma licao da 049 e da 052.
4. Backfill da v1: `INSERT ... SELECT` de UMA linha por Modelo, com o
   cabecalho de hoje e a intencao da 056 (alvos agregados por
   `JSON_ARRAYAGG`). `effective_from NULL` = vigente desde sempre: o banco nao
   sabe desde quando a estrutura de hoje vale (o DELETE fisico apagou), e
   datar o inicio deixaria o passado anterior SEM estrutura nenhuma - mesma
   decisao da 052. `NOT EXISTS` torna o statement reexecutavel.
5. `UPDATE slots JOIN versions SET version_id` - cada slot existente passa a
   pertencer a v1 do seu Modelo. `WHERE version_id IS NULL`: reexecutavel.
6. `DROP` do UNIQUE antigo (ele proibiria a mesma `sequence` em duas versoes).
7. `UNIQUE (version_id, sequence)` - o que o antigo protegia, agora por versao.
8. FK `fk_wpts_version` (CASCADE): apagar a versao leva os slots, e o CASCADE
   ja existente de slots leva os itens.

`version_id` nasce NULL-able: o PR2 (066) repete o passo 5 para slots gravados
pelo codigo ANTIGO na janela de deploy (entre esta migracao e o update das
Lambdas) e so entao vira NOT NULL.

Nenhuma FK nova para `exercise_catalog` (a 028 recusa). As colunas de autor e
de reversao ficam SEM FK fisica de proposito: sao historico - apagar um
usuario ou uma versao nao pode reescrever quem fez o que.

--- `down` ----------------------------------------------------------------

Reversivel na FORMA, nao no DADO. `plan_down` PARA antes do primeiro
statement quando:
- algum Modelo ja tem mais de uma versao - recriar o UNIQUE
  `(template_id, sequence)` falharia, e deve falhar, porque nao ha estado
  antigo equivalente para aquele historico;
- algum Modelo foi excluido logicamente - sem `deleted_at` ele voltaria a
  aparecer na lista como se nunca tivesse sido apagado.

--- Estados e idempotencia ------------------------------------------------

Cada statement tem guarda propria em `plan()`, que so LE `information_schema`:
- producao hoje -> 8 statements (os dois de backfill escrevem dado);
- estado convergido -> 0 statements;
- estado PARCIAL -> so o que falta, na mesma ordem (o backfill e reexecutavel
  e entra enquanto a forma final dos slots nao estiver completa);
- coluna/indice/FK com outra forma, tabela do baseline ausente ou ENGINE !=
  InnoDB -> `TemplateVersioningError` ANTES do primeiro statement;
- `migrate --dry-run` de um banco ATRASADO: as colunas/tabelas que o backfill
  le e que nascem em migracoes anteriores ainda pendentes (016/038/056/062)
  podem faltar no momento do plano - ausencia e tolerada, tipo errado nao.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TEMPLATES = "workout_plan_templates"
SLOTS = "workout_plan_template_slots"
VERSIONS = "workout_plan_template_versions"
INTENT = "workout_plan_template_methodology_intent"
INTENT_TARGETS = "workout_plan_template_methodology_intent_targets"

COL_DELETED_AT = "deleted_at"
COL_VERSION_ID = "version_id"
COL_CURRENT = "current_template_id"

IDX_SLOTS_TEMPLATE = "idx_wpts_template"
IDX_SLOTS_LEGACY_UNIQUE = "uq_wpts_template_sequence"
IDX_SLOTS_VERSION_SEQUENCE = "uq_wpts_version_sequence"
FK_SLOTS_VERSION = "fk_wpts_version"

IDX_VERSIONS_CURRENT = "uq_wptv_current"
IDX_VERSIONS_NUMBER = "uq_wptv_template_number"
FK_VERSIONS_TEMPLATE = "fk_wptv_template"

# Espelho de `intelligence.methodology_volume.INTENT_KINDS` / 056 (a migracao
# nao importa codigo do app).
INTENT_KINDS = ("standard", "specialization", "maintenance", "cutting")
INTENT_COLUMN_TYPE = "enum(" + ",".join(f"'{k}'" for k in INTENT_KINDS) + ")"

# Expressao da coluna gerada, como NOS a escrevemos.
CURRENT_EXPRESSION = "IF(superseded_at IS NULL, template_id, NULL)"

BACKFILL_SUMMARY = "v1 (backfill da migracao 065)"

# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

CREATE_VERSIONS_SQL = f"""
CREATE TABLE IF NOT EXISTS {VERSIONS} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  template_id BIGINT NOT NULL,
  version_number INT NOT NULL,
  training_type_id BIGINT NULL,
  volume_mode VARCHAR(20) NOT NULL DEFAULT 'percent_budget',
  template_type VARCHAR(40) NOT NULL DEFAULT 'full_body',
  grid_rows TINYINT NULL,
  grid_cols TINYINT NULL,
  methodology_intent ENUM({", ".join(f"'{k}'" for k in INTENT_KINDS)}) NOT NULL DEFAULT 'standard',
  methodology_target_group_ids JSON NULL,
  effective_from DATETIME NULL,
  superseded_at DATETIME NULL,
  created_by_user_id BIGINT NULL,
  reverted_from_version_id BIGINT NULL,
  change_summary VARCHAR(255) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  {COL_CURRENT} BIGINT GENERATED ALWAYS AS ({CURRENT_EXPRESSION}) VIRTUAL,
  UNIQUE KEY {IDX_VERSIONS_CURRENT} ({COL_CURRENT}),
  UNIQUE KEY {IDX_VERSIONS_NUMBER} (template_id, version_number),
  CONSTRAINT {FK_VERSIONS_TEMPLATE} FOREIGN KEY (template_id) REFERENCES {TEMPLATES}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

ADD_DELETED_AT_SQL = f"ALTER TABLE {TEMPLATES} ADD COLUMN {COL_DELETED_AT} DATETIME NULL"

ADD_SLOT_COLUMNS_SQL = (
    f"ALTER TABLE {SLOTS} "
    f"ADD COLUMN {COL_VERSION_ID} BIGINT NULL, "
    f"ADD KEY {IDX_SLOTS_TEMPLATE} (template_id)"
)
ADD_VERSION_ID_SQL = f"ALTER TABLE {SLOTS} ADD COLUMN {COL_VERSION_ID} BIGINT NULL"
ADD_SLOTS_TEMPLATE_KEY_SQL = f"ALTER TABLE {SLOTS} ADD KEY {IDX_SLOTS_TEMPLATE} (template_id)"

# v1 de cada Modelo que ainda nao tem versao nenhuma. A intencao vem da 056
# (sem linha = 'standard'); os alvos, agregados num array JSON - a ORDEM do
# `JSON_ARRAYAGG` nao e garantida, e por isso quem compara versoes ordena.
BACKFILL_VERSIONS_SQL = f"""
INSERT INTO {VERSIONS} (
  template_id, version_number, training_type_id, volume_mode, template_type,
  grid_rows, grid_cols, methodology_intent, methodology_target_group_ids,
  effective_from, superseded_at, created_by_user_id, change_summary
)
SELECT
  t.id, 1, t.training_type_id, t.volume_mode, t.template_type,
  t.grid_rows, t.grid_cols, COALESCE(mi.intent, 'standard'),
  COALESCE(
    (SELECT JSON_ARRAYAGG(mit.muscle_group_id) FROM {INTENT_TARGETS} mit WHERE mit.template_id = t.id),
    JSON_ARRAY()
  ),
  NULL, NULL, t.created_by_user_id, '{BACKFILL_SUMMARY}'
FROM {TEMPLATES} t
LEFT JOIN {INTENT} mi ON mi.template_id = t.id
WHERE NOT EXISTS (SELECT 1 FROM {VERSIONS} v WHERE v.template_id = t.id)
"""

BACKFILL_SLOTS_SQL = f"""
UPDATE {SLOTS} s
JOIN {VERSIONS} v ON v.{COL_CURRENT} = s.template_id
SET s.{COL_VERSION_ID} = v.id
WHERE s.{COL_VERSION_ID} IS NULL
"""

DROP_LEGACY_UNIQUE_SQL = f"ALTER TABLE {SLOTS} DROP INDEX {IDX_SLOTS_LEGACY_UNIQUE}"
ADD_VERSION_UNIQUE_SQL = f"ALTER TABLE {SLOTS} ADD UNIQUE KEY {IDX_SLOTS_VERSION_SEQUENCE} ({COL_VERSION_ID}, sequence)"
ADD_VERSION_FK_SQL = (
    f"ALTER TABLE {SLOTS} ADD CONSTRAINT {FK_SLOTS_VERSION} "
    f"FOREIGN KEY ({COL_VERSION_ID}) REFERENCES {VERSIONS}(id) ON DELETE CASCADE"
)

# Inverso, na ordem contraria.
DROP_VERSION_FK_SQL = f"ALTER TABLE {SLOTS} DROP FOREIGN KEY {FK_SLOTS_VERSION}"
DROP_VERSION_UNIQUE_SQL = f"ALTER TABLE {SLOTS} DROP INDEX {IDX_SLOTS_VERSION_SEQUENCE}"
RESTORE_LEGACY_UNIQUE_SQL = f"ALTER TABLE {SLOTS} ADD UNIQUE KEY {IDX_SLOTS_LEGACY_UNIQUE} (template_id, sequence)"
DROP_SLOT_COLUMNS_SQL = f"ALTER TABLE {SLOTS} DROP COLUMN {COL_VERSION_ID}, DROP INDEX {IDX_SLOTS_TEMPLATE}"
DROP_VERSIONS_SQL = f"DROP TABLE IF EXISTS {VERSIONS}"
DROP_DELETED_AT_SQL = f"ALTER TABLE {TEMPLATES} DROP COLUMN {COL_DELETED_AT}"

MULTIPLAS_VERSOES_SQL = f"""
SELECT template_id, COUNT(*) AS n
FROM {VERSIONS}
GROUP BY template_id
HAVING COUNT(*) > 1
"""

EXCLUIDOS_SQL = f"SELECT id FROM {TEMPLATES} WHERE {COL_DELETED_AT} IS NOT NULL"

# Colunas que a 065 EXIGE encontrar (indices e FKs dependem delas): vem do
# baseline 007, entao existem em qualquer banco que chegue aqui.
REQUIRED_COLUMNS: dict[str, dict[str, str]] = {
    TEMPLATES: {"id": "bigint"},
    SLOTS: {"id": "bigint", "template_id": "bigint", "sequence": "tinyint"},
}

# Colunas lidas pelo BACKFILL que nascem em migracoes anteriores (016, 038,
# 056, 062). Num `migrate --dry-run` de um banco atrasado essas migracoes
# ainda nao rodaram de verdade, entao a coluna/tabela pode nao existir NO
# MOMENTO DO PLANO: ausente e tolerado (o runner aplica em ordem, e no
# `migrate` real elas ja existem); PRESENTE com outro tipo e erro.
BACKFILL_COLUMNS: dict[str, dict[str, str]] = {
    TEMPLATES: {
        "training_type_id": "bigint",
        "volume_mode": "varchar(20)",
        "template_type": "varchar(40)",
        "grid_rows": "tinyint",
        "grid_cols": "tinyint",
        "created_by_user_id": "bigint",
    },
    INTENT: {"template_id": "bigint", "intent": INTENT_COLUMN_TYPE},
    INTENT_TARGETS: {"template_id": "bigint", "muscle_group_id": "bigint"},
}

# coluna -> (tipo normalizado, IS_NULLABLE) das colunas NOVAS.
EXPECTED_VERSION_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "template_id": ("bigint", "NO"),
    "version_number": ("int", "NO"),
    "training_type_id": ("bigint", "YES"),
    "volume_mode": ("varchar(20)", "NO"),
    "template_type": ("varchar(40)", "NO"),
    "grid_rows": ("tinyint", "YES"),
    "grid_cols": ("tinyint", "YES"),
    "methodology_intent": (INTENT_COLUMN_TYPE, "NO"),
    "methodology_target_group_ids": ("json", "YES"),
    "effective_from": ("datetime", "YES"),
    "superseded_at": ("datetime", "YES"),
    "created_by_user_id": ("bigint", "YES"),
    "reverted_from_version_id": ("bigint", "YES"),
    "change_summary": ("varchar(255)", "YES"),
    "created_at": ("datetime", "NO"),
    COL_CURRENT: ("bigint", "YES"),
}
EXPECTED_DELETED_AT = ("datetime", "YES")
EXPECTED_VERSION_ID = ("bigint", "YES")

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")
_NAO_PALAVRA_RE = re.compile(r"[^a-z0-9_]+")


class TemplateVersioningError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise TemplateVersioningError(f"065: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    text = _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())
    return text.replace(", ", ",")


def _normalize_expression(expression) -> str:
    """Ignora o que o MySQL reescreve (crases, espacos, caixa)."""
    return _NAO_PALAVRA_RE.sub("", _text(expression).strip().lower())


EXPECTED_CURRENT_EXPRESSION = _normalize_expression(CURRENT_EXPRESSION)

_TABLES = (TEMPLATES, SLOTS, VERSIONS, INTENT, INTENT_TARGETS)


def _read(db):
    placeholders = ", ".join(["%s"] * len(_TABLES))
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            _TABLES,
        )
    }
    columns: dict[tuple[str, str], dict] = {}
    for row in db.query(
        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA, GENERATION_EXPRESSION "
        f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
        _TABLES,
    ):
        columns[(_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"]))] = row
    indexes: dict[tuple[str, str], list[dict]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE "
        f"FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
        _TABLES,
    ):
        indexes.setdefault((_text(row["TABLE_NAME"]), _text(row["INDEX_NAME"])), []).append(row)
    fks: dict[tuple[str, str], dict] = {}
    for row in db.query(
        "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
        "FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = DATABASE() "
        f"AND REFERENCED_TABLE_NAME IS NOT NULL AND TABLE_NAME IN ({placeholders})",
        _TABLES,
    ):
        fks[(_text(row["TABLE_NAME"]), _text(row["CONSTRAINT_NAME"]))] = row
    return engines, columns, indexes, fks


def _check_required(engines, columns) -> None:
    for table, required in REQUIRED_COLUMNS.items():
        if table not in engines:
            _fail(f"tabela {table} nao existe")
        if engines[table].lower() != "innodb":
            _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
        for name, expected_type in required.items():
            info = columns.get((table, name))
            if info is None:
                _fail(f"{table}.{name} nao existe")
            if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
                _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
    for table, columns_backfill in BACKFILL_COLUMNS.items():
        if table in engines and engines[table].lower() != "innodb":
            _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
        for name, expected_type in columns_backfill.items():
            info = columns.get((table, name))
            if info is not None and _normalize_type(info["COLUMN_TYPE"]) != expected_type:
                _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")


def _check_column(columns, table: str, name: str, expected: tuple[str, str]) -> bool:
    """`True` quando a coluna ja existe no formato esperado, `False` quando
    ainda nao existe. Formato divergente -> erro."""
    info = columns.get((table, name))
    if info is None:
        return False
    expected_type, expected_nullable = expected
    if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
        _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
    if _text(info["IS_NULLABLE"]) != expected_nullable:
        _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    return True


def _check_index(indexes, table: str, name: str, *, unique: bool, cols: tuple[str, ...]) -> bool:
    rows = indexes.get((table, name))
    if not rows:
        return False
    encontradas = tuple(_text(r["COLUMN_NAME"]) for r in sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"])))
    if encontradas != cols:
        _fail(f"{table}.{name} cobre {encontradas}, esperado {cols}")
    non_unique = int(rows[0]["NON_UNIQUE"])
    if unique and non_unique != 0:
        _fail(f"{table}.{name} existe mas NAO e UNIQUE")
    if not unique and non_unique != 1:
        _fail(f"{table}.{name} existe como UNIQUE, esperado indice comum")
    return True


def _check_fk(fks, table: str, name: str, *, column: str, ref_table: str) -> bool:
    row = fks.get((table, name))
    if row is None:
        return False
    if _text(row["COLUMN_NAME"]) != column or _text(row["REFERENCED_TABLE_NAME"]) != ref_table:
        _fail(
            f"{table}.{name} liga {_text(row['COLUMN_NAME'])} -> {_text(row['REFERENCED_TABLE_NAME'])}, "
            f"esperado {column} -> {ref_table}"
        )
    return True


def _check_versions_table(columns, indexes, fks) -> None:
    """A tabela de versoes nasce num `CREATE TABLE` so (atomico): se ela
    existe, existe INTEIRA - e precisa ter a forma que este arquivo descreve."""
    for name, expected in EXPECTED_VERSION_COLUMNS.items():
        if not _check_column(columns, VERSIONS, name, expected):
            _fail(f"{VERSIONS}.{name} nao existe")
    encontrada = _normalize_expression(columns[(VERSIONS, COL_CURRENT)].get("GENERATION_EXPRESSION"))
    if not encontrada:
        _fail(f"{VERSIONS}.{COL_CURRENT} existe mas NAO e coluna gerada")
    if encontrada != EXPECTED_CURRENT_EXPRESSION:
        _fail(
            f"{VERSIONS}.{COL_CURRENT} e gerada por "
            f"{_text(columns[(VERSIONS, COL_CURRENT)].get('GENERATION_EXPRESSION'))!r}, "
            f"esperado {CURRENT_EXPRESSION!r}"
        )
    if not _check_index(indexes, VERSIONS, IDX_VERSIONS_CURRENT, unique=True, cols=(COL_CURRENT,)):
        _fail(f"{VERSIONS}.{IDX_VERSIONS_CURRENT} nao existe")
    if not _check_index(
        indexes, VERSIONS, IDX_VERSIONS_NUMBER, unique=True, cols=("template_id", "version_number")
    ):
        _fail(f"{VERSIONS}.{IDX_VERSIONS_NUMBER} nao existe")
    if not _check_fk(fks, VERSIONS, FK_VERSIONS_TEMPLATE, column="template_id", ref_table=TEMPLATES):
        _fail(f"{VERSIONS}.{FK_VERSIONS_TEMPLATE} nao existe")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes, fks = _read(db)
    _check_required(engines, columns)

    tem_versions = VERSIONS in engines
    if tem_versions:
        if engines[VERSIONS].lower() != "innodb":
            _fail(f"{VERSIONS} usa ENGINE={engines[VERSIONS]}, esperado InnoDB")
        _check_versions_table(columns, indexes, fks)

    tem_deleted_at = _check_column(columns, TEMPLATES, COL_DELETED_AT, EXPECTED_DELETED_AT)
    tem_version_id = _check_column(columns, SLOTS, COL_VERSION_ID, EXPECTED_VERSION_ID)
    tem_idx_template = _check_index(indexes, SLOTS, IDX_SLOTS_TEMPLATE, unique=False, cols=("template_id",))
    tem_unique_antigo = _check_index(
        indexes, SLOTS, IDX_SLOTS_LEGACY_UNIQUE, unique=True, cols=("template_id", "sequence")
    )
    tem_unique_novo = _check_index(
        indexes, SLOTS, IDX_SLOTS_VERSION_SEQUENCE, unique=True, cols=(COL_VERSION_ID, "sequence")
    )
    tem_fk = _check_fk(fks, SLOTS, FK_SLOTS_VERSION, column=COL_VERSION_ID, ref_table=VERSIONS)

    if (tem_unique_novo or tem_fk) and not tem_version_id:
        _fail(f"{SLOTS} tem indice/FK de {COL_VERSION_ID} sem a coluna")
    if tem_fk and not tem_versions:
        _fail(f"{SLOTS}.{FK_SLOTS_VERSION} existe sem a tabela {VERSIONS}")

    statements: list[tuple[str, tuple | None]] = []

    # 1) tabela de versoes.
    if not tem_versions:
        statements.append((CREATE_VERSIONS_SQL, None))

    # 2) exclusao logica.
    if not tem_deleted_at:
        statements.append((ADD_DELETED_AT_SQL, None))

    # 3) coluna da versao + indice que apoia `fk_wpts_template` depois do DROP.
    if not tem_version_id and not tem_idx_template:
        statements.append((ADD_SLOT_COLUMNS_SQL, None))  # caminho normal: uma reconstrucao
    else:
        if not tem_version_id:
            statements.append((ADD_VERSION_ID_SQL, None))
        if not tem_idx_template:
            statements.append((ADD_SLOTS_TEMPLATE_KEY_SQL, None))

    # 4) e 5) backfill - enquanto a forma final dos slots nao estiver completa.
    #    Os dois statements sao reexecutaveis (NOT EXISTS / version_id IS NULL).
    slots_convergidos = tem_version_id and tem_unique_novo and tem_fk and not tem_unique_antigo
    if not slots_convergidos:
        statements.append((BACKFILL_VERSIONS_SQL, None))
        statements.append((BACKFILL_SLOTS_SQL, None))

    # 6) o UNIQUE antigo sai (proibiria a mesma sequence em duas versoes).
    if tem_unique_antigo:
        statements.append((DROP_LEGACY_UNIQUE_SQL, None))

    # 7) e 8) unicidade por versao e a FK.
    if not tem_unique_novo:
        statements.append((ADD_VERSION_UNIQUE_SQL, None))
    if not tem_fk:
        statements.append((ADD_VERSION_FK_SQL, None))

    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`, na ordem contraria. Para ANTES do primeiro statement
    quando ja existe historico (mais de uma versao) ou Modelo excluido."""
    engines, columns, indexes, fks = _read(db)
    if TEMPLATES not in engines or SLOTS not in engines:
        return []

    tem_versions = VERSIONS in engines
    tem_deleted_at = (TEMPLATES, COL_DELETED_AT) in columns
    tem_version_id = (SLOTS, COL_VERSION_ID) in columns
    tem_idx_template = (SLOTS, IDX_SLOTS_TEMPLATE) in indexes
    tem_unique_antigo = (SLOTS, IDX_SLOTS_LEGACY_UNIQUE) in indexes
    tem_unique_novo = (SLOTS, IDX_SLOTS_VERSION_SEQUENCE) in indexes
    tem_fk = (SLOTS, FK_SLOTS_VERSION) in fks

    if tem_versions:
        multiplas = [
            f"modelo {_text(row['template_id'])}: {_text(row['n'])} versoes"
            for row in db.query(MULTIPLAS_VERSOES_SQL, None)
        ]
        if multiplas:
            _fail(
                "ja existe historico de versoes, entao o estado anterior a 065 nao "
                f"o representa: {', '.join(multiplas)}"
            )
    if tem_deleted_at:
        excluidos = [_text(row["id"]) for row in db.query(EXCLUIDOS_SQL, None)]
        if excluidos:
            _fail(
                "ha Modelo excluido logicamente; sem deleted_at ele voltaria a aparecer: "
                f"{', '.join(excluidos)}"
            )

    statements: list[tuple[str, tuple | None]] = []
    if tem_fk:
        statements.append((DROP_VERSION_FK_SQL, None))
    if tem_unique_novo:
        statements.append((DROP_VERSION_UNIQUE_SQL, None))
    if not tem_unique_antigo:
        statements.append((RESTORE_LEGACY_UNIQUE_SQL, None))
    if tem_version_id and tem_idx_template:
        statements.append((DROP_SLOT_COLUMNS_SQL, None))
    elif tem_version_id:
        statements.append((f"ALTER TABLE {SLOTS} DROP COLUMN {COL_VERSION_ID}", None))
    elif tem_idx_template:
        statements.append((f"ALTER TABLE {SLOTS} DROP INDEX {IDX_SLOTS_TEMPLATE}", None))
    if tem_versions:
        statements.append((DROP_VERSIONS_SQL, None))
    if tem_deleted_at:
        statements.append((DROP_DELETED_AT_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
