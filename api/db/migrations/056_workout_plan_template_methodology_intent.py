"""056 - intencao declarada da metodologia de volume, persistida por Modelo
(Fase 4, PR H).

    workout_plan_template_methodology_intent
      template_id  BIGINT NOT NULL PRIMARY KEY
                   -> workout_plan_templates(id) ON DELETE CASCADE
      intent       ENUM('standard','specialization','maintenance','cutting')
                   NOT NULL DEFAULT 'standard'
      updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                   ON UPDATE CURRENT_TIMESTAMP

    workout_plan_template_methodology_intent_targets
      template_id      BIGINT NOT NULL
                       -> workout_plan_template_methodology_intent(template_id)
                          ON DELETE CASCADE
      muscle_group_id  BIGINT NOT NULL -> muscle_groups(id) ON DELETE CASCADE
      PRIMARY KEY (template_id, muscle_group_id)
      KEY idx_wptmit_group (muscle_group_id)

Por que: desde o PR E a avaliacao pela metodologia (`intelligence/
methodology_volume.py`) aceita uma INTENCAO (`standard`, `specialization`,
`maintenance`, `cutting`, com grupos-alvo) que troca a SEVERIDADE de um
desvio esperado para `informational` - nunca o status. Ate aqui a intencao
so viajava como parametro do preview e do `GET /{id}/methodology-volume`; o
gancho aditivo do POST/PUT do Modelo avaliava sempre com `standard`, entao um
Modelo de cutting deliberado acusava "abaixo da faixa" a cada salvamento. O
plano da fase e explicito: falso alarme em escolha deliberada e o pior
desfecho, porque ensina o usuario a ignorar o painel. Persistir a intencao
POR MODELO e o que faz o gancho respeitar a escolha.

--- Decisoes do DDL -------------------------------------------------------

1. **`template_id` e a PRIMARY KEY** da tabela de intencao: UMA intencao por
   Modelo, garantida pelo banco e nao por convencao. Sem `id` proprio - nada
   precisa apontar para "a intencao" por um numero seu; a chave natural e o
   Modelo.

2. **Sem linha = `standard`.** A tabela nasce vazia e o codigo trata ausencia
   como o default de hoje; e por isso que a migracao nao precisa de seed e o
   comportamento de producao nao muda um byte ate alguem declarar uma
   intencao. `DEFAULT 'standard'` na coluna e coerencia com essa leitura, nao
   um segundo caminho.

3. **`intent` e ENUM, nao VARCHAR** - ao contrario do `status` da 038 e do
   `volume_mode` da 054. Ali, valor novo de motor nao podia exigir DDL; aqui o
   dominio e o do MOTOR PURO (`INTENT_KINDS`), que so muda com PR e teste, e um
   valor fora dele faria `MethodologyIntent.__post_init__` levantar. O ENUM
   recusa no banco o que o motor recusaria depois.

4. **Grupos-alvo em tabela filha, com FK para `muscle_groups`** (e NAO para
   `exercise_catalog`: a migracao 028 enumera as FKs validas do catalogo e
   recusa novas; aqui o alvo e um GRUPO, mesmo tipo da 055). A FK com CASCADE
   faz o alvo sumir junto com o grupo apagado, em vez de deixar uma
   especializacao apontando para um id que nao existe mais - o motor
   ignoraria o alvo em silencio (`target_blocks` so resolve o que encontra) e
   a intencao passaria a valer "para ninguem" sem aviso.

5. **A filha aponta para a tabela de intencao, nao direto para o Modelo.**
   Assim nao existe alvo sem intencao declarada: apagar o Modelo apaga a
   intencao (CASCADE) e a intencao apagada leva os alvos (CASCADE). Uma unica
   cadeia, sem linha orfa possivel em nenhum dos dois sentidos.

6. **`updated_at`, sem `created_at`.** A linha e um estado (a intencao ATUAL
   do Modelo), nao um historico; o que interessa e quando ela mudou pela
   ultima vez. Historico de intencoes, se um dia importar, e outra tabela.

**Nada e escrito aqui.** `PUT /workout-plan-templates/{id}/methodology-intent`
grava; `_get_template` le e anexa `methodology_intent` (aditivo) ao dict do
Modelo; o gancho e o `GET /{id}/methodology-volume` sem `intent` na query
usam o que estiver gravado. Precedencia: query explicita > persistida >
`standard`.

Fluxo (mesmo padrao da 054): `plan(db)` so LE `information_schema`; `up(db)`
executa o plano; `down(db)`/`plan_down(db)` sao o inverso (DROP das duas
tabelas, filha primeiro - reversivel de verdade: nada mais aponta para elas e
perde-las devolve todo Modelo a `standard`, o comportamento pre-PR H).

- nenhuma das duas tabelas -> exatamente 2 `CREATE TABLE` (mae, depois filha);
- so a mae presente no formato esperado -> 1 `CREATE TABLE` (a filha);
- as duas presentes no formato esperado -> nada (reexecucao: 0 statements);
- tabela presente com coluna/tipo/nulabilidade diferente, ENGINE != InnoDB,
  filha sem a mae, ou `workout_plan_templates`/`muscle_groups`
  incompativeis -> `MethodologyIntentError` ANTES do primeiro statement.

Das duas tabelas-pai so se confere EXISTENCIA e ENGINE (e o tipo de `id`
quando o recorte o traz): o recorte real da Aurora
(tests/unit/data/aurora_schema_2026_09_17_035.json) lista as tabelas mas so
traz colunas de `users` e `equipment_exercises`. Os tipos ja estao provados
em producao por outro caminho: `workout_plan_templates.id BIGINT` recebe a FK
de `workout_plan_template_slots` desde a 007, e `muscle_groups.id BIGINT`
recebe a self-FK da 055.

Aurora (mesmo recorte, sem as duas tabelas) -> 2 statements. Reexecucao: 0.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

INTENT_TABLE = "workout_plan_template_methodology_intent"
TARGETS_TABLE = "workout_plan_template_methodology_intent_targets"
TEMPLATES_TABLE = "workout_plan_templates"
GROUPS_TABLE = "muscle_groups"

# Espelho de `intelligence.methodology_volume.INTENT_KINDS` (a migracao nao
# importa codigo do app; o teste unitario amarra os dois).
INTENT_KINDS = ("standard", "specialization", "maintenance", "cutting")
INTENT_COLUMN_TYPE = "enum(" + ",".join(f"'{k}'" for k in INTENT_KINDS) + ")"

TARGETS_GROUP_INDEX = "idx_wptmit_group"

CREATE_INTENT_SQL = f"""
CREATE TABLE IF NOT EXISTS {INTENT_TABLE} (
  template_id BIGINT NOT NULL PRIMARY KEY,
  intent ENUM({", ".join(f"'{k}'" for k in INTENT_KINDS)}) NOT NULL DEFAULT 'standard',
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_wptmi_template FOREIGN KEY (template_id) REFERENCES {TEMPLATES_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

CREATE_TARGETS_SQL = f"""
CREATE TABLE IF NOT EXISTS {TARGETS_TABLE} (
  template_id BIGINT NOT NULL,
  muscle_group_id BIGINT NOT NULL,
  PRIMARY KEY (template_id, muscle_group_id),
  KEY {TARGETS_GROUP_INDEX} (muscle_group_id),
  CONSTRAINT fk_wptmit_intent FOREIGN KEY (template_id) REFERENCES {INTENT_TABLE}(template_id) ON DELETE CASCADE,
  CONSTRAINT fk_wptmit_group FOREIGN KEY (muscle_group_id) REFERENCES {GROUPS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_TARGETS_SQL = f"DROP TABLE IF EXISTS {TARGETS_TABLE}"
DROP_INTENT_SQL = f"DROP TABLE IF EXISTS {INTENT_TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_INTENT_COLUMNS: dict[str, tuple[str, str]] = {
    "template_id": ("bigint", "NO"),
    "intent": (INTENT_COLUMN_TYPE, "NO"),
    "updated_at": ("datetime", "NO"),
}
EXPECTED_TARGETS_COLUMNS: dict[str, tuple[str, str]] = {
    "template_id": ("bigint", "NO"),
    "muscle_group_id": ("bigint", "NO"),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class MethodologyIntentError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise MethodologyIntentError(f"056: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `bigint(20)` -> `bigint`; `enum('a', 'b')` -> `enum('a','b')` (o MySQL
    # devolve sem espaco, mas normalizar aqui deixa o teste independente disso).
    text = _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())
    return text.replace(", ", ",")


def _read(db) -> tuple[dict[str, str], dict[tuple[str, str], dict]]:
    names = (INTENT_TABLE, TARGETS_TABLE, TEMPLATES_TABLE, GROUPS_TABLE)
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
    return engines, columns


def _check_parent(table: str, engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
    """Tabela-pai do baseline/007: o recorte da Aurora lista a tabela mas nao
    as colunas dela, entao se confere o que o recorte responde - existencia e
    ENGINE - e o tipo de `id` so quando ele veio. Se estiver de outro jeito, o
    proprio `CREATE TABLE` falha, sem meio estado: a FK nasce com a tabela."""
    if table not in engines:
        _fail(f"tabela {table} nao existe")
    if engines[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    info = columns.get((table, "id"))
    if info is not None and _normalize_type(info["COLUMN_TYPE"]) != "bigint":
        _fail(f"{table}.id e {_text(info['COLUMN_TYPE'])}, esperado bigint")


def _check_existing(
    table: str,
    expected: dict[str, tuple[str, str]],
    engines: dict[str, str],
    columns: dict[tuple[str, str], dict],
) -> None:
    if engines[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in expected.items():
        info = columns.get((table, name))
        if info is None:
            _fail(f"{table}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns = _read(db)
    _check_parent(TEMPLATES_TABLE, engines, columns)
    _check_parent(GROUPS_TABLE, engines, columns)

    statements: list[tuple[str, tuple | None]] = []
    if INTENT_TABLE in engines:
        _check_existing(INTENT_TABLE, EXPECTED_INTENT_COLUMNS, engines, columns)
    else:
        statements.append((CREATE_INTENT_SQL, None))

    if TARGETS_TABLE in engines:
        if INTENT_TABLE not in engines:
            _fail(f"{TARGETS_TABLE} existe sem {INTENT_TABLE} (filha sem a mae)")
        _check_existing(TARGETS_TABLE, EXPECTED_TARGETS_COLUMNS, engines, columns)
    else:
        statements.append((CREATE_TARGETS_SQL, None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: DROP da filha e depois da mae, so das que existem."""
    engines, _columns = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    if TARGETS_TABLE in engines:
        statements.append((DROP_TARGETS_SQL, None))
    if INTENT_TABLE in engines:
        statements.append((DROP_INTENT_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
