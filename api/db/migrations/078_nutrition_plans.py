"""078 - plano nutricional por aluno (plano de nutrição, Lote L2b, 2026-10-17).

    nutrition_plans
      id                     BIGINT AUTO_INCREMENT PK
      user_id                BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      created_by             BIGINT NULL      quem criou (admin/PT); referência LÓGICA
      published_by           BIGINT NULL      quem publicou; referência LÓGICA
      status                 VARCHAR(16)      'draft' | 'published'
      strategy               VARCHAR(16)      códigos de `domain/nutrition/strategy.Strategy`
      advanced               TINYINT(1)       faixa de superávit do avançado (bulking)
      bmr_formula            VARCHAR(24)      `bmr.BmrFormula` ('auto' = seleção do motor)
      activity_level         VARCHAR(16) NULL override do nível do perfil (`ActivityLevel`)
      training_kcal_mode     VARCHAR(24)      `energy_expenditure.TdeeMode`
      goal_body_fat_percent  DECIMAL(4,1) NULL
      goal_weight_kg         DECIMAL(5,1) NULL
      weekly_rate_share      DECIMAL(6,4) NULL ritmo do cutting (fração do peso por semana)
      surplus_share          DECIMAL(6,4) NULL superávit do bulking (fração do TDEE)
      protein_g_per_kg       DECIMAL(4,2)
      fat_kcal_share         DECIMAL(6,4)
      carb_min_g_per_kg      DECIMAL(4,2) NULL piso de carbo do bulking
      meals_per_day          TINYINT NULL
      carb_cycling_json      JSON NULL        L8 (ciclo de carbo); o L2b grava NULL
      refeed_days_per_week   TINYINT NULL     L8; o L2b grava NULL
      diet_break_pattern     VARCHAR(16) NULL L8 (`phases.BreakProtocol`); o L2b grava NULL
      notes                  VARCHAR(1000) NULL
      engine_version         VARCHAR(32)      versão do motor na última gravação
      effective_from         DATE NULL        dia civil (fuso do aluno) em que passa a valer
      superseded_at          DATETIME NULL    instante (relógio do banco) em que foi substituído
      published_at           DATETIME NULL
      created_at / updated_at
      current_user_id        BIGINT GENERATED ALWAYS AS
                             (IF(status = 'published' AND superseded_at IS NULL, user_id, NULL)) VIRTUAL
      UNIQUE uq_np_current_user (current_user_id)   UM plano vigente por aluno
      KEY idx_np_user_effective (user_id, effective_from)

--- Decisões do DDL -------------------------------------------------------

1. **Vigência no banco** (padrão das 049/052/065): `current_user_id` vale o
   aluno enquanto o plano está publicado e não substituído, NULL no resto; o
   UNIQUE recusa um segundo vigente (tupla com NULL não colide). Publicar é
   UPDATE do vigente (`superseded_at`) + UPDATE do rascunho (`status`); duas
   publicações simultâneas para o mesmo aluno: a segunda bate no UNIQUE e a
   rota responde 409. O histórico guarda quantos planos substituídos quiser.
2. **`effective_from` é DATE** (dia do aluno), `superseded_at` é DATETIME
   (relógio do banco): o plano de um DIA é o publicado de maior
   `effective_from <= dia` (`domain/nutrition/plan.plan_in_force`), então o
   instante da troca não precisa virar dia.
3. **Códigos em VARCHAR**, vocabulário do motor `domain/nutrition` (mesma
   regra da 072/074): a migração não enumera o vocabulário, quem valida é a
   rota com o motor. Nomes das colunas de parâmetro seguem o motor
   (`weekly_rate_share`, não `weekly_rate_percent` do rascunho do plano §6.2:
   gravar percentual exigiria conversão fora do dono).
4. **FK só para `users`**; `created_by`/`published_by` são referências lógicas
   (um admin apagado não apaga o plano do aluno). Nenhuma FK para
   `exercise_catalog` (a 028 recusa).
5. **Colunas do L8 nascem agora** (DDL do plano §6.2) e ficam NULL até lá: o
   L8 só passa a escrevê-las, sem ALTER.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa; `down`/`plan_down`
fazem DROP TABLE (a tabela nasce vazia; `nutrition_daily_targets` só a
referencia logicamente).

- tabela ausente -> 1 `CREATE TABLE`;
- presente na forma esperada -> 0 statements (reexecução);
- coluna/tipo/nulabilidade diferente, expressão gerada diferente, UNIQUE
  ausente, ENGINE != InnoDB ou `users` incompatível -> `NutritionPlansError`
  ANTES do primeiro statement;
- `users` ausente: erro numa execução real; aceito em DRY-RUN.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; módulo
autocontido (não importa código do app).
"""

from __future__ import annotations

import re

TABLE = "nutrition_plans"
USERS_TABLE = "users"

COL_CURRENT = "current_user_id"
IDX_CURRENT = "uq_np_current_user"
IDX_USER_EFFECTIVE = "idx_np_user_effective"
FK_USER = "fk_np_user"

# Expressão da coluna gerada, como NÓS a escrevemos.
CURRENT_EXPRESSION = "IF(status = 'published' AND superseded_at IS NULL, user_id, NULL)"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  created_by BIGINT NULL,
  published_by BIGINT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'draft',
  strategy VARCHAR(16) NOT NULL,
  advanced TINYINT(1) NOT NULL DEFAULT 0,
  bmr_formula VARCHAR(24) NOT NULL,
  activity_level VARCHAR(16) NULL,
  training_kcal_mode VARCHAR(24) NOT NULL,
  goal_body_fat_percent DECIMAL(4,1) NULL,
  goal_weight_kg DECIMAL(5,1) NULL,
  weekly_rate_share DECIMAL(6,4) NULL,
  surplus_share DECIMAL(6,4) NULL,
  protein_g_per_kg DECIMAL(4,2) NOT NULL,
  fat_kcal_share DECIMAL(6,4) NOT NULL,
  carb_min_g_per_kg DECIMAL(4,2) NULL,
  meals_per_day TINYINT NULL,
  carb_cycling_json JSON NULL,
  refeed_days_per_week TINYINT NULL,
  diet_break_pattern VARCHAR(16) NULL,
  notes VARCHAR(1000) NULL,
  engine_version VARCHAR(32) NOT NULL,
  effective_from DATE NULL,
  superseded_at DATETIME NULL,
  published_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  {COL_CURRENT} BIGINT GENERATED ALWAYS AS ({CURRENT_EXPRESSION}) VIRTUAL,
  UNIQUE KEY {IDX_CURRENT} ({COL_CURRENT}),
  KEY {IDX_USER_EFFECTIVE} (user_id, effective_from),
  CONSTRAINT {FK_USER} FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "created_by": ("bigint", "YES"),
    "published_by": ("bigint", "YES"),
    "status": ("varchar(16)", "NO"),
    "strategy": ("varchar(16)", "NO"),
    "advanced": ("tinyint(1)", "NO"),
    "bmr_formula": ("varchar(24)", "NO"),
    "activity_level": ("varchar(16)", "YES"),
    "training_kcal_mode": ("varchar(24)", "NO"),
    "goal_body_fat_percent": ("decimal(4,1)", "YES"),
    "goal_weight_kg": ("decimal(5,1)", "YES"),
    "weekly_rate_share": ("decimal(6,4)", "YES"),
    "surplus_share": ("decimal(6,4)", "YES"),
    "protein_g_per_kg": ("decimal(4,2)", "NO"),
    "fat_kcal_share": ("decimal(6,4)", "NO"),
    "carb_min_g_per_kg": ("decimal(4,2)", "YES"),
    "meals_per_day": ("tinyint", "YES"),
    "carb_cycling_json": ("json", "YES"),
    "refeed_days_per_week": ("tinyint", "YES"),
    "diet_break_pattern": ("varchar(16)", "YES"),
    "notes": ("varchar(1000)", "YES"),
    "engine_version": ("varchar(32)", "NO"),
    "effective_from": ("date", "YES"),
    "superseded_at": ("datetime", "YES"),
    "published_at": ("datetime", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
    COL_CURRENT: ("bigint", "YES"),
}

# Largura de exibição dos inteiros some no MySQL 8, MENOS a de `tinyint(1)`
# (que o MySQL mantém por ser o booleano). `tinyint(4)` -> `tinyint`.
_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")
_CHARSET_INTRODUCER_RE = re.compile(r"_(?:utf8mb4|utf8mb3|utf8|latin1|binary)(?=\s*\\?')")
_NAO_PALAVRA_RE = re.compile(r"[^a-z0-9_]+")


class NutritionPlansError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise NutritionPlansError(f"078: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _normalize_expression(expression) -> str:
    """Compara a expressão IGNORANDO o que o MySQL reescreve: crases, aspas,
    parênteses, espaços, caixa e o introdutor de charset da literal
    (`_utf8mb4'published'`)."""
    texto = _CHARSET_INTRODUCER_RE.sub("", _text(expression).strip().lower())
    return _NAO_PALAVRA_RE.sub("", texto)


EXPECTED_CURRENT_EXPRESSION = _normalize_expression(CURRENT_EXPRESSION)


def _read(db):
    names = (TABLE, USERS_TABLE)
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
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, GENERATION_EXPRESSION "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    indexes: dict[str, list[dict]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
        (TABLE,),
    ):
        indexes.setdefault(_text(row["INDEX_NAME"]), []).append(row)
    return engines, columns, indexes


def _check_users(engines, columns) -> None:
    if engines[USERS_TABLE].lower() != "innodb":
        _fail(f"{USERS_TABLE} usa ENGINE={engines[USERS_TABLE]}, esperado InnoDB")
    info = columns.get((USERS_TABLE, "id"))
    if info is None:
        _fail(f"{USERS_TABLE}.id nao existe")
    if _normalize_type(info["COLUMN_TYPE"]) != "bigint":
        _fail(f"{USERS_TABLE}.id e {_text(info['COLUMN_TYPE'])}, esperado bigint")
    if _text(info["COLUMN_KEY"]) != "PRI":
        _fail(f"{USERS_TABLE}.id nao e PRIMARY KEY")


def _check_index(indexes, name: str, *, unique: bool, cols: tuple[str, ...]) -> None:
    rows = indexes.get(name)
    if not rows:
        _fail(f"{TABLE}.{name} nao existe")
    encontradas = tuple(_text(r["COLUMN_NAME"]) for r in sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"])))
    if encontradas != cols:
        _fail(f"{TABLE}.{name} cobre {encontradas}, esperado {cols}")
    if (int(rows[0]["NON_UNIQUE"]) == 0) != unique:
        _fail(f"{TABLE}.{name} {'NAO e' if unique else 'e'} UNIQUE")


def _check_existing(engines, columns, indexes) -> None:
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS.items():
        info = columns.get((TABLE, name))
        if info is None:
            _fail(f"{TABLE}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    encontrada = _normalize_expression(columns[(TABLE, COL_CURRENT)].get("GENERATION_EXPRESSION"))
    if not encontrada:
        _fail(f"{TABLE}.{COL_CURRENT} existe mas NAO e coluna gerada")
    if encontrada != EXPECTED_CURRENT_EXPRESSION:
        _fail(
            f"{TABLE}.{COL_CURRENT} e gerada por "
            f"{_text(columns[(TABLE, COL_CURRENT)].get('GENERATION_EXPRESSION'))!r}, esperado {CURRENT_EXPRESSION!r}"
        )
    _check_index(indexes, IDX_CURRENT, unique=True, cols=(COL_CURRENT,))
    _check_index(indexes, IDX_USER_EFFECTIVE, unique=False, cols=("user_id", "effective_from"))


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes = _read(db)
    if USERS_TABLE not in engines:
        if bool(getattr(db, "dry_run", False)):
            return [(CREATE_SQL, None)]
        _fail(f"tabela {USERS_TABLE} nao existe")
    _check_users(engines, columns)
    if TABLE in engines:
        _check_existing(engines, columns, indexes)
        return []
    return [(CREATE_SQL, None)]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP TABLE` se a tabela existe, senão nada."""
    engines, _columns, _indexes = _read(db)
    if TABLE not in engines:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
