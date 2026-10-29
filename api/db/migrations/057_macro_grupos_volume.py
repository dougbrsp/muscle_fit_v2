"""057 - macro-grupos de volume editáveis (Fase 6, PR 1 - "expand").

    muscle_volume_classes              (nova) um macro-grupo por linha
      id, name UNIQUE, description, sort_order,
      weekly_sets_min/max  DECIMAL NULL, weekly_source, weekly_evidence,
      created_at, updated_at

    muscle_volume_class_ranges         (nova) metas por frequência semanal
      PK (class_id, frequency_bucket), FK class_id ON DELETE CASCADE
      frequency_bucket ENUM('1x','2x','3x_plus'),
      sets_min/max, exercises_min/max (opcionais),
      sets_source/evidence, exercises_source/evidence

    muscle_groups
      + volume_class_id  BIGINT NULL -> muscle_volume_classes(id)
                                        ON DELETE RESTRICT
      + KEY idx_mg_volume_class (volume_class_id)
      + CONSTRAINT fk_mg_volume_class

Por que: o motor da Fase 4 avalia o volume com duas coisas FIXAS - as faixas
escritas em código (`METHODOLOGY_RANGES` em
`app/intelligence/methodology_volume.py`) e o porte do grupo, que só pode ser
um dos quatro valores do ENUM `size_class` da 055. O usuário quer controlar as
duas coisas numa aba admin: macro-grupos que ele cria, renomeia e exclui, cada
um com meta por treino (por frequência 1x/2x/3x+) e meta semanal, e a
associação de cada grupo muscular a um macro-grupo.

**Nada lê estas tabelas ainda.** Este PR entrega schema, seed e CRUD; o motor,
o serviço e a geração por IA passam a ler o cadastro no PR 2. Nenhuma
avaliação muda aqui.

--- Decisões do DDL -------------------------------------------------------

1. **Tabela de domínio, e não ENUM.** O oposto da 055, pelo motivo oposto:
   ali o domínio era FECHADO pela metodologia; aqui o admin cria macro-grupo
   novo, e valor novo não pode exigir DDL.

2. **`sets_*` e `weekly_sets_*` em DECIMAL(5,1).** As metas de hoje são
   inteiras, mas a meta semanal é comparada com `séries do ciclo × 7/dias do
   ciclo`, que é fracionária por natureza; aceitar uma casa decimal na meta
   evita que o admin tenha de arredondar o que quer dizer. `exercises_*` é
   contagem de exercícios, então SMALLINT.

3. **`exercises_*` e seus `*_source`/`*_evidence` aceitam NULL.** O eixo "nº de
   exercícios por sessão" passa a ser opcional por linha (premissa do plano):
   vazio = eixo não avaliado.

4. **Meta semanal NULL permitida.** Sem meta semanal, só o eixo semanal fica
   de fora da avaliação (PR 2); a meta por treino continua valendo.

5. **`volume_class_id` NULL, ON DELETE RESTRICT.** NULL = grupo sem
   macro-grupo, que continua `NOT_EVALUABLE` como o `unclassified` de hoje.
   RESTRICT, e não SET NULL: excluir um macro-grupo em uso tiraria da
   avaliação, em silêncio, todos os grupos dele. A API responde 409 antes de
   chegar na FK; a FK é a garantia no banco. A FK aponta para uma tabela
   NOVA, então não fere a regra da 028 sobre `exercise_catalog`.

6. **`ranges` ON DELETE CASCADE.** As linhas de frequência não existem sem o
   macro-grupo: são parte dele.

7. **`size_class` fica no banco, intocado.** Ele deixa de ser lido no PR 2 e
   só sai numa migração futura de "contract", depois da validação em
   produção. Com isso o deploy tolera a ordem migração -> código (a janela
   inversa: schema novo + código velho continua lendo `size_class`).

--- Seed ------------------------------------------------------------------

Os valores iniciais são os de HOJE (decisão 4 do usuário): cada célula de
`METHODOLOGY_RANGES` copiada como literal, com a mesma origem (`methodology`
ou `derived`) e a mesma evidência (os códigos EXG-0xx e o texto das propostas
D1/D2), inclusive a coluna 3x+ que o motor deriva em tempo de execução
(`_derive_3x_plus`). Literal, e não import: a migração é autocontida.
`test_migration_057_plano_aurora.py` compara cada célula semeada com
`METHODOLOGY_RANGES` - é a garantia de paridade com o comportamento atual.

  Grandes  <- size_class 'large'  semanal 18-32 (2x × 9-16)
  Pequenos <- size_class 'small'  semanal 12-24 (2x × 6-12)
  Outros   <- size_class 'abs'    semanal 12-24 (2x × 6-12)

A semanal nasce `derived`, com a evidência "2x × faixa por sessão". Os grupos
`unclassified` ficam com `volume_class_id` NULL (continuam não avaliados até o
admin associá-los).

A associação é UM statement só (`UPDATE ... JOIN ... CASE size_class`): ou os
grupos das três classes são associados juntos, ou nenhum é. Com isso a única
interrupção possível no meio do seed é entre os INSERTs, e a reexecução
completa o que falta.

**O seed é uma população DE NASCIMENTO**, como na 055: só roda quando a
coluna `volume_class_id` ainda não existe (deploy normal) ou quando ninguém
tocou no cadastro ainda - nenhum grupo associado, nenhum valor com origem
`admin` e nenhum macro-grupo com nome fora do seed. `volume_class_id` NULL não
distingue "nunca semeado" de "o admin desassociou", então a reexecução não
pode reimpor a associação num cadastro em que alguém já mexeu. Os guards do
SQL (INSERT só do que falta, UPDATE só de linha NULL) continuam no lugar.

--- Fluxo -----------------------------------------------------------------

Mesmo padrão da 055/056: `plan(db)` decide (lê `information_schema` e, das
tabelas que existem, os dados que o seed precisa), `up(db)` executa o plano,
`plan_down`/`down` são o inverso.

- nada existe                 -> 2 CREATE + 1 ALTER + 3 INSERT de macro-grupo
                                 + 9 INSERT de faixa + 1 UPDATE de associação;
- estado convergido           -> 0 statements;
- estado parcial              -> só o que falta;
- tabela/coluna em outro formato, ENGINE != InnoDB, `ranges` sem `classes`,
  `muscle_groups` sem `size_class` (055 não aplicada)
                              -> `VolumeClassesError` ANTES do primeiro
                                 statement.

`size_class`/`methodology_parent_group_id` ausentes (banco em que a 055 ainda
está pendente; o runner a aplica ANTES desta): numa execução real a ausência é
erro (falha segura). Só em DRY-RUN (`db.dry_run`), em que a 055 pendente não
cria as colunas, a ausência é aceita para o plano do lote poder ser exibido -
mesmo padrão da 033/041/042. O plano sai como o de "nada existe" (a 055 dá
porte a todo grupo, então a associação entra) e o formato é conferido de novo
na execução real. Na Aurora a 055 já foi aplicada.

`plan_down` derruba a FK, a coluna e as duas tabelas (filha primeiro). Como
nada lê o cadastro ainda, reverter perde configuração, nunca comportamento.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; módulo
autocontido (não importa código do app).
"""

from __future__ import annotations

import re

GROUPS_TABLE = "muscle_groups"
CLASSES_TABLE = "muscle_volume_classes"
RANGES_TABLE = "muscle_volume_class_ranges"

COL_VOLUME_CLASS = "volume_class_id"
COL_PARENT = "methodology_parent_group_id"
COL_SIZE_CLASS = "size_class"

FK_VOLUME_CLASS = "fk_mg_volume_class"
IDX_VOLUME_CLASS = "idx_mg_volume_class"

SOURCES = ("methodology", "derived", "admin")
BUCKETS = ("1x", "2x", "3x_plus")

SOURCE_SQL_TYPE = "ENUM(" + ", ".join(f"'{v}'" for v in SOURCES) + ")"
SOURCE_COLUMN_TYPE = "enum(" + ",".join(f"'{v}'" for v in SOURCES) + ")"
BUCKET_SQL_TYPE = "ENUM(" + ", ".join(f"'{v}'" for v in BUCKETS) + ")"
BUCKET_COLUMN_TYPE = "enum(" + ",".join(f"'{v}'" for v in BUCKETS) + ")"

CREATE_CLASSES_SQL = f"""
CREATE TABLE IF NOT EXISTS {CLASSES_TABLE} (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(80) NOT NULL,
  description VARCHAR(500) NULL,
  sort_order INT NOT NULL DEFAULT 0,
  weekly_sets_min DECIMAL(5,1) NULL,
  weekly_sets_max DECIMAL(5,1) NULL,
  weekly_source {SOURCE_SQL_TYPE} NOT NULL DEFAULT 'admin',
  weekly_evidence TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_mvc_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

CREATE_RANGES_SQL = f"""
CREATE TABLE IF NOT EXISTS {RANGES_TABLE} (
  class_id BIGINT NOT NULL,
  frequency_bucket {BUCKET_SQL_TYPE} NOT NULL,
  sets_min DECIMAL(5,1) NOT NULL,
  sets_max DECIMAL(5,1) NOT NULL,
  exercises_min SMALLINT NULL,
  exercises_max SMALLINT NULL,
  sets_source {SOURCE_SQL_TYPE} NOT NULL DEFAULT 'admin',
  sets_evidence TEXT NULL,
  exercises_source {SOURCE_SQL_TYPE} NULL,
  exercises_evidence TEXT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (class_id, frequency_bucket),
  CONSTRAINT fk_mvcr_class FOREIGN KEY (class_id) REFERENCES {CLASSES_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

ADD_VOLUME_CLASS_SQL = (
    f"ALTER TABLE {GROUPS_TABLE} ADD COLUMN {COL_VOLUME_CLASS} BIGINT NULL AFTER {COL_PARENT}, "
    f"ADD KEY {IDX_VOLUME_CLASS} ({COL_VOLUME_CLASS}), "
    f"ADD CONSTRAINT {FK_VOLUME_CLASS} FOREIGN KEY ({COL_VOLUME_CLASS}) "
    f"REFERENCES {CLASSES_TABLE}(id) ON DELETE RESTRICT"
)
ADD_VOLUME_CLASS_INDEX_SQL = f"ALTER TABLE {GROUPS_TABLE} ADD KEY {IDX_VOLUME_CLASS} ({COL_VOLUME_CLASS})"
ADD_VOLUME_CLASS_FK_SQL = (
    f"ALTER TABLE {GROUPS_TABLE} ADD CONSTRAINT {FK_VOLUME_CLASS} FOREIGN KEY ({COL_VOLUME_CLASS}) "
    f"REFERENCES {CLASSES_TABLE}(id) ON DELETE RESTRICT"
)

DROP_FK_SQL = f"ALTER TABLE {GROUPS_TABLE} DROP FOREIGN KEY {FK_VOLUME_CLASS}"
DROP_COLUMN_SQL = f"ALTER TABLE {GROUPS_TABLE} DROP COLUMN {COL_VOLUME_CLASS}"
DROP_INDEX_SQL = f"ALTER TABLE {GROUPS_TABLE} DROP INDEX {IDX_VOLUME_CLASS}"
DROP_RANGES_SQL = f"DROP TABLE IF EXISTS {RANGES_TABLE}"
DROP_CLASSES_SQL = f"DROP TABLE IF EXISTS {CLASSES_TABLE}"

EXPECTED_CLASSES_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "name": ("varchar(80)", "NO"),
    "description": ("varchar(500)", "YES"),
    "sort_order": ("int", "NO"),
    "weekly_sets_min": ("decimal(5,1)", "YES"),
    "weekly_sets_max": ("decimal(5,1)", "YES"),
    "weekly_source": (SOURCE_COLUMN_TYPE, "NO"),
    "weekly_evidence": ("text", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
}
EXPECTED_RANGES_COLUMNS: dict[str, tuple[str, str]] = {
    "class_id": ("bigint", "NO"),
    "frequency_bucket": (BUCKET_COLUMN_TYPE, "NO"),
    "sets_min": ("decimal(5,1)", "NO"),
    "sets_max": ("decimal(5,1)", "NO"),
    "exercises_min": ("smallint", "YES"),
    "exercises_max": ("smallint", "YES"),
    "sets_source": (SOURCE_COLUMN_TYPE, "NO"),
    "sets_evidence": ("text", "YES"),
    "exercises_source": (SOURCE_COLUMN_TYPE, "YES"),
    "exercises_evidence": ("text", "YES"),
    "updated_at": ("datetime", "NO"),
}

# --- Seed ------------------------------------------------------------------
#
# Cópia LITERAL das células de `METHODOLOGY_RANGES` (Fase 4) no dia desta
# migração. As evidências ficam byte a byte iguais às do motor (sem acento,
# como estão lá), porque o teste de paridade compara as strings.

_EVID_D2 = (
    "derived (proposta D2): celula {rotulo} 2x menos 1 exercicio por sessao (mesmo passo de "
    "1x->2x, EXG-019), piso 1; series pela regra de EXG-004 (3-4 por exercicio), piso 2"
)
_EVID_D1_SETS = (
    "derived (proposta D1): EXG-020 prescreve 2-3 exercicios e deixa as series 'conforme series "
    "adotadas por exercicio'; convertido pela regra de EXG-004 (3-4 series por exercicio)"
)
_EVID_D1_ABS_1X = (
    "derived (proposta D1): EXG-020 so prescreve abdomen em 2x/semana; em 1x a mesma faixa de "
    "2-3 exercicios e aplicada por falta de prescricao propria"
)

WEEKLY_EVIDENCE = "2x × faixa por sessão"

# (bucket, exercises_min, exercises_max, sets_min, sets_max,
#  exercises_source, exercises_evidence, sets_source, sets_evidence)
SeedRange = tuple[str, int, int, int, int, str, str, str, str]

# (nome, descrição, sort_order, size_class de origem, semanal min, semanal max,
#  faixas por bucket)
SEED_CLASSES: tuple[tuple[str, str, int, str, int, int, tuple[SeedRange, ...]], ...] = (
    (
        "Grandes",
        "Grupos grandes da metodologia: peitoral, dorsal, deltoides e coxas.",
        1,
        "large",
        18,
        32,
        (
            ("1x", 4, 5, 15, 20, "methodology", "EXG-015", "methodology", "EXG-015"),
            ("2x", 3, 4, 9, 16, "methodology", "EXG-016", "methodology", "EXG-016"),
            (
                "3x_plus", 2, 3, 6, 12,
                "derived", _EVID_D2.format(rotulo="grandes"),
                "derived", _EVID_D2.format(rotulo="grandes"),
            ),
        ),
    ),
    (
        "Pequenos",
        "Grupos pequenos da metodologia: bíceps, tríceps, antebraço, trapézio, panturrilha e glúteo.",
        2,
        "small",
        12,
        24,
        (
            ("1x", 3, 4, 9, 16, "methodology", "EXG-017", "methodology", "EXG-017"),
            ("2x", 2, 3, 6, 12, "methodology", "EXG-018", "methodology", "EXG-018"),
            (
                "3x_plus", 1, 2, 3, 8,
                "derived", _EVID_D2.format(rotulo="pequenos"),
                "derived", _EVID_D2.format(rotulo="pequenos"),
            ),
        ),
    ),
    (
        "Outros",
        "Abdômen e demais grupos com prescrição própria (EXG-020).",
        3,
        "abs",
        12,
        24,
        (
            ("1x", 2, 3, 6, 12, "derived", _EVID_D1_ABS_1X, "derived", _EVID_D1_SETS),
            ("2x", 2, 3, 6, 12, "methodology", "EXG-020", "derived", _EVID_D1_SETS),
            (
                "3x_plus", 1, 2, 3, 8,
                "derived", _EVID_D2.format(rotulo="abdomen"),
                "derived", _EVID_D2.format(rotulo="abdomen"),
            ),
        ),
    ),
)

SEED_NAMES = tuple(entry[0] for entry in SEED_CLASSES)

INSERT_CLASS_SQL = (
    f"INSERT INTO {CLASSES_TABLE} "
    "(name, description, sort_order, weekly_sets_min, weekly_sets_max, weekly_source, weekly_evidence) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)

# O id do macro-grupo é resolvido PELO BANCO, por nome: o plano é montado
# antes de qualquer INSERT e não conhece os ids que o AUTO_INCREMENT vai dar.
INSERT_RANGE_SQL = (
    f"INSERT INTO {RANGES_TABLE} "
    "(class_id, frequency_bucket, sets_min, sets_max, exercises_min, exercises_max, "
    "sets_source, sets_evidence, exercises_source, exercises_evidence) "
    f"SELECT id, %s, %s, %s, %s, %s, %s, %s, %s, %s FROM {CLASSES_TABLE} WHERE name = %s"
)

# Um statement só para as três classes (ver docstring). A subconsulta é na
# OUTRA tabela, então não esbarra no erro 1093 do MySQL.
ASSOCIATE_SQL = (
    f"UPDATE {GROUPS_TABLE} mg JOIN {CLASSES_TABLE} c ON c.name = "
    f"CASE mg.{COL_SIZE_CLASS} WHEN %s THEN %s WHEN %s THEN %s WHEN %s THEN %s END "
    f"SET mg.{COL_VOLUME_CLASS} = c.id WHERE mg.{COL_VOLUME_CLASS} IS NULL"
)

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class VolumeClassesError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise VolumeClassesError(f"057: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `bigint(20)` -> `bigint`; espaços do ENUM fora (o MySQL devolve sem).
    text = _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())
    return "".join(text.split())


def _read_schema(db):
    names = (GROUPS_TABLE, CLASSES_TABLE, RANGES_TABLE)
    placeholders = ", ".join(["%s"] * len(names))
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    indexes = {
        (_text(row["TABLE_NAME"]), _text(row["INDEX_NAME"]))
        for row in db.query(
            "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    fks = {
        (_text(row["TABLE_NAME"]), _text(row["CONSTRAINT_NAME"]))
        for row in db.query(
            "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders}) "
            "AND REFERENCED_TABLE_NAME IS NOT NULL",
            names,
        )
    }
    return engines, columns, indexes, fks


def _check_groups(engines, columns, dry_run: bool = False) -> bool:
    """Confere `muscle_groups`; devolve se as colunas da 055 existem.

    Só em dry-run a ausência delas é aceita (ver docstring do módulo)."""
    if GROUPS_TABLE not in engines:
        _fail(f"tabela {GROUPS_TABLE} nao existe")
    if engines[GROUPS_TABLE].lower() != "innodb":
        _fail(f"{GROUPS_TABLE} usa ENGINE={engines[GROUPS_TABLE]}, esperado InnoDB")
    for name in ("id", "name"):
        if (GROUPS_TABLE, name) not in columns:
            _fail(f"{GROUPS_TABLE}.{name} nao existe")
    tipo = _normalize_type(columns[(GROUPS_TABLE, "id")]["COLUMN_TYPE"])
    if tipo != "bigint":
        _fail(f"{GROUPS_TABLE}.id e {tipo}, esperado bigint")
    tem_055 = True
    for name in (COL_SIZE_CLASS, COL_PARENT):
        if (GROUPS_TABLE, name) not in columns:
            # `size_class` e o parent vêm da 055: o seed lê o primeiro e o
            # ALTER posiciona a coluna nova depois do segundo.
            if not dry_run:
                _fail(f"{GROUPS_TABLE}.{name} nao existe (a 055 precisa estar aplicada)")
            tem_055 = False
    return tem_055


def _check_existing(table: str, expected, engines, columns) -> None:
    if engines[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in expected.items():
        info = columns.get((table, name))
        if info is None:
            _fail(f"{table}.{name} nao existe")
        tipo = _normalize_type(info["COLUMN_TYPE"])
        if tipo != expected_type:
            _fail(f"{table}.{name} e {tipo}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")


def _check_volume_class_column(columns) -> bool:
    info = columns.get((GROUPS_TABLE, COL_VOLUME_CLASS))
    if info is None:
        return False
    tipo = _normalize_type(info["COLUMN_TYPE"])
    if tipo != "bigint":
        _fail(f"{GROUPS_TABLE}.{COL_VOLUME_CLASS} e {tipo}, esperado bigint")
    if _text(info["IS_NULLABLE"]) != "YES":
        _fail(f"{GROUPS_TABLE}.{COL_VOLUME_CLASS} e NOT NULL, esperado NULL (NULL = sem macro-grupo)")
    return True


def _read_data(db, *, tem_classes: bool, tem_ranges: bool, tem_coluna: bool, tem_size_class: bool = True):
    """Lê só o que EXISTE: pedir coluna/tabela que ainda não nasceu seria erro
    de SQL. O que não existe tem estado conhecido sem ler - vazio."""
    groups = []
    if tem_size_class:
        colunas = ["id", "name", COL_SIZE_CLASS] + ([COL_VOLUME_CLASS] if tem_coluna else [])
        groups = list(db.query(f"SELECT {', '.join(colunas)} FROM {GROUPS_TABLE}", None))
    classes = (
        list(db.query(f"SELECT id, name, weekly_source FROM {CLASSES_TABLE}", None)) if tem_classes else []
    )
    ranges = (
        list(
            db.query(
                f"SELECT class_id, frequency_bucket, sets_source, exercises_source FROM {RANGES_TABLE}",
                None,
            )
        )
        if tem_ranges
        else []
    )
    return groups, classes, ranges


def _seed_pendente(groups, classes, ranges, *, tem_coluna: bool) -> bool:
    """População DE NASCIMENTO (mesma regra da 055): só semeia se ninguém
    tocou no cadastro. Tocado = algum grupo associado, algum valor com origem
    `admin` ou algum macro-grupo com nome fora do seed."""
    if not tem_coluna:
        return True
    if any(row[COL_VOLUME_CLASS] is not None for row in groups):
        return False
    if any(_text(row["weekly_source"]) == "admin" for row in classes):
        return False
    if any(_text(row["name"]) not in SEED_NAMES for row in classes):
        return False
    return not any(
        _text(row["sets_source"]) == "admin" or _text(row["exercises_source"]) == "admin" for row in ranges
    )


def _seed_statements(groups, classes, ranges):
    statements: list[tuple[str, tuple | None]] = []
    existentes = {_text(row["name"]): int(row["id"]) for row in classes}
    faixas_existentes = {(int(row["class_id"]), _text(row["frequency_bucket"])) for row in ranges}

    for name, description, sort_order, _size, weekly_min, weekly_max, _faixas in SEED_CLASSES:
        if name in existentes:
            continue
        statements.append(
            (
                INSERT_CLASS_SQL,
                (name, description, sort_order, weekly_min, weekly_max, "derived", WEEKLY_EVIDENCE),
            )
        )

    for name, _desc, _sort, _size, _wmin, _wmax, faixas in SEED_CLASSES:
        class_id = existentes.get(name)
        for bucket, ex_min, ex_max, s_min, s_max, ex_src, ex_evid, s_src, s_evid in faixas:
            if class_id is not None and (class_id, bucket) in faixas_existentes:
                continue
            statements.append(
                (
                    INSERT_RANGE_SQL,
                    (bucket, s_min, s_max, ex_min, ex_max, s_src, s_evid, ex_src, ex_evid, name),
                )
            )

    alvos = {size for _n, _d, _s, size, *_resto in SEED_CLASSES}
    # `groups is None`: dry-run com a 055 pendente - a associação é planejada.
    if groups is None or any(_text(row[COL_SIZE_CLASS]) in alvos for row in groups):
        params: list = []
        for name, _desc, _sort, size, *_resto in SEED_CLASSES:
            params.extend((size, name))
        statements.append((ASSOCIATE_SQL, tuple(params)))
    return statements


def plan(db) -> list[tuple[str, tuple | None]]:
    """Lê schema e dados; devolve [(sql, params)] na ordem. Sem escrita."""
    engines, columns, indexes, fks = _read_schema(db)
    tem_055 = _check_groups(engines, columns, bool(getattr(db, "dry_run", False)))

    tem_classes = CLASSES_TABLE in engines
    tem_ranges = RANGES_TABLE in engines
    if tem_classes:
        _check_existing(CLASSES_TABLE, EXPECTED_CLASSES_COLUMNS, engines, columns)
    if tem_ranges:
        if not tem_classes:
            _fail(f"{RANGES_TABLE} existe sem {CLASSES_TABLE} (filha sem a mae)")
        _check_existing(RANGES_TABLE, EXPECTED_RANGES_COLUMNS, engines, columns)
    tem_coluna = _check_volume_class_column(columns)

    statements: list[tuple[str, tuple | None]] = []
    if not tem_classes:
        statements.append((CREATE_CLASSES_SQL, None))
    if not tem_ranges:
        statements.append((CREATE_RANGES_SQL, None))
    if not tem_coluna:
        statements.append((ADD_VOLUME_CLASS_SQL, None))
    elif (GROUPS_TABLE, FK_VOLUME_CLASS) not in fks:
        # Coluna sem a FK (aplicação interrompida no meio): cada peça isolada.
        if (GROUPS_TABLE, IDX_VOLUME_CLASS) not in indexes:
            statements.append((ADD_VOLUME_CLASS_INDEX_SQL, None))
        statements.append((ADD_VOLUME_CLASS_FK_SQL, None))

    if not tem_055:
        # Dry-run com a 055 pendente: `size_class` não pode ser lida (seria
        # erro de SQL) e a coluna nova também não existe, então o seed é o de
        # nascimento completo, com a associação.
        _grupos, classes, ranges = _read_data(
            db, tem_classes=tem_classes, tem_ranges=tem_ranges, tem_coluna=False, tem_size_class=False
        )
        statements.extend(_seed_statements(None, classes, ranges))
        return statements

    groups, classes, ranges = _read_data(
        db, tem_classes=tem_classes, tem_ranges=tem_ranges, tem_coluna=tem_coluna
    )
    if _seed_pendente(groups, classes, ranges, tem_coluna=tem_coluna):
        statements.extend(_seed_statements(groups, classes, ranges))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: FK, coluna, e as tabelas (filha primeiro)."""
    engines, columns, indexes, fks = _read_schema(db)
    statements: list[tuple[str, tuple | None]] = []
    if GROUPS_TABLE in engines:
        if (GROUPS_TABLE, FK_VOLUME_CLASS) in fks:
            statements.append((DROP_FK_SQL, None))
        if (GROUPS_TABLE, COL_VOLUME_CLASS) in columns:
            # O índice cai junto com a coluna (é o único que a cobre).
            statements.append((DROP_COLUMN_SQL, None))
        elif (GROUPS_TABLE, IDX_VOLUME_CLASS) in indexes:
            statements.append((DROP_INDEX_SQL, None))
    if RANGES_TABLE in engines:
        statements.append((DROP_RANGES_SQL, None))
    if CLASSES_TABLE in engines:
        statements.append((DROP_CLASSES_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
