"""066 - versao do Modelo na ficha e na auditoria; saneamento dos slots sem
versao (PR2 do versionamento de Modelo).

    workout_plans
      + template_version_id  BIGINT NULL   versao do Modelo de que a ficha nasceu
      + KEY idx_wp_template_version (template_version_id)
    generation_runs
      + template_version_id  BIGINT NULL   versao lida na geracao do rascunho
      + KEY idx_gr_template_version (template_version_id)
    workout_plan_template_slots
      (dado) slots sem `version_id` viram uma versao nova - ver abaixo

Sem FK fisica nas duas colunas novas, de proposito: sao HISTORICO. A versao
pertence ao Modelo e o Modelo nunca e apagado fisicamente pela aplicacao
(exclusao logica, 065); uma FK so acrescentaria uma regra de DELETE para um
caminho que nao existe e uma verificacao a mais em todo save de ficha. A
coerencia "a versao e DESTE Modelo" e validada no SERVIDOR, no save
(`services._validated_template_version_id`), e nao confiada ao cliente.

Fichas antigas ficam com `template_version_id NULL`, SEM backfill: o
congelamento e os leitores resolvem NULL pela versao vigente (como hoje).
Inventar a versao de uma ficha antiga seria afirmar algo que o banco nao sabe.

--- Slots orfaos da janela de deploy da 065 ----------------------------------

Entre a 065 e o update das Lambdas, o codigo ANTIGO ainda fazia
`DELETE FROM slots WHERE template_id = %s` + `INSERT` sem `version_id`. Um
Modelo editado nessa janela ficou com:
- a versao vigente SEM slots (o DELETE levou os slots dela, inclusive os da
  v1 - o DELETE era por `template_id`), e
- os slots de verdade com `version_id NULL`, invisiveis para o codigo novo.
Um Modelo CRIADO na janela ficou sem versao nenhuma e com slots orfaos.

REGRA (a ultima escrita vence, sem perder nada): os orfaos de cada Modelo
viram uma versao NOVA e vigente.
1. a vigente do Modelo com orfaos e encerrada em `NOW()` (lido UMA vez pelo
   plano: o mesmo instante fecha uma e abre a outra);
2. entra a versao n+1 (v1 se nao havia nenhuma) com o cabecalho e a intencao
   de HOJE (`workout_plan_templates` + 056 - foi o codigo antigo que os
   gravou por ultimo), `effective_from` = o mesmo instante e
   `change_summary` explicando a origem;
3. os orfaos sao pendurados nela (`UPDATE ... JOIN ... current_template_id`).

Por que nao "pendurar os orfaos na v1": a v1 pode ainda ter slots (Modelo
nao tocado pelo DELETE nao tem orfao, mas um INSERT antigo sem DELETE teria)
e a `sequence` colidiria no `UNIQUE (version_id, sequence)`. Uma versao so dos
orfaos nunca colide com outra versao. Dentro dos proprios orfaos, uma
`sequence` repetida no mesmo Modelo nao tem resolucao segura (qual dos dois
e o slot de verdade?) - nesse caso o plano PARA antes do primeiro statement
e lista os Modelos, para a correcao ser decidida por gente, nao pela
migracao. O codigo antigo sempre apagava antes de inserir, entao isso nao
deve aparecer.

Os tres statements sao conjuntos (sem um por Modelo) e reexecutaveis; o
plano so os inclui quando HA orfao (uma contagem), entao o schema convergido
continua dando 0 statements.

--- Por que `version_id` NAO vira NOT NULL aqui -------------------------------

O plano do versionamento previa `version_id NOT NULL` nesta migracao. Ele foi
deixado de fora DE PROPOSITO: a 065 (ja aplicada em DEV, arquivo imutavel
pelo checksum do runner) confere `version_id` como `bigint NULL` e PARA com
erro quando encontra outra forma. Com o NOT NULL, a 065 deixaria de ser
reexecutavel sobre o schema convergido - e o runner e a suite reexecutam
toda migracao sobre schemas ja migrados (`migrate` idempotente,
`test_migrate_dataapi`). CHECK nao serve (o MySQL recusa CHECK em coluna com
acao referencial - `fk_wpts_version` e ON DELETE CASCADE) e trigger fica fora
do padrao do projeto. A invariante "todo slot tem versao" fica garantida por:
- um UNICO escritor de slots no app (`template_versions.insert_version_slots`
  e a copia por `INSERT ... SELECT`), sempre com `version_id`;
- o teste-guarda `test_guarda_sql_slots_por_versao.py` (todo SQL de slots em
  `api/app` cita `version_id`);
- este saneamento, que tambem e o conserto se um orfao aparecer.

--- Estados e idempotencia ------------------------------------------------

- producao hoje (065 aplicada) -> 2 ALTER das colunas novas (+ 3 statements
  de orfaos SE houver orfao);
- convergido -> 0 statements;
- parcial -> so o que falta (cada ALTER tem guarda propria);
- `migrate --dry-run` de banco ATRASADO (065/054 so planejadas, nao
  aplicadas): tabela/coluna que migracao anterior ainda cria pode faltar no
  plano - tolerado (o runner aplica em ordem). Sem `version_id` no plano nao
  da para contar orfaos: os 3 statements entram (sao no-op sem orfao). Tipo
  errado continua erro.

`down`: tira as duas colunas novas. Nao desfaz as versoes criadas para
orfaos (sao historico verdadeiro).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

PLANS = "workout_plans"
RUNS = "generation_runs"
SLOTS = "workout_plan_template_slots"
VERSIONS = "workout_plan_template_versions"
TEMPLATES = "workout_plan_templates"
INTENT = "workout_plan_template_methodology_intent"
INTENT_TARGETS = "workout_plan_template_methodology_intent_targets"

COL = "template_version_id"
IDX_PLANS = "idx_wp_template_version"
IDX_RUNS = "idx_gr_template_version"

ORPHAN_SUMMARY = "slots orfaos da janela de deploy da 065 (066)"

ADD_PLANS_SQL = f"ALTER TABLE {PLANS} ADD COLUMN {COL} BIGINT NULL, ADD KEY {IDX_PLANS} ({COL})"
ADD_RUNS_SQL = f"ALTER TABLE {RUNS} ADD COLUMN {COL} BIGINT NULL, ADD KEY {IDX_RUNS} ({COL})"

_ORPHAN_TEMPLATES = f"SELECT DISTINCT template_id FROM {SLOTS} WHERE version_id IS NULL"

ORPHAN_CLOSE_SQL = f"""
UPDATE {VERSIONS} v
JOIN ({_ORPHAN_TEMPLATES}) o ON o.template_id = v.template_id
SET v.superseded_at = %s
WHERE v.superseded_at IS NULL
"""

ORPHAN_OPEN_SQL = f"""
INSERT INTO {VERSIONS} (
  template_id, version_number, training_type_id, volume_mode, template_type,
  grid_rows, grid_cols, methodology_intent, methodology_target_group_ids,
  effective_from, superseded_at, created_by_user_id, change_summary
)
SELECT
  t.id,
  COALESCE((SELECT MAX(x.version_number) FROM {VERSIONS} x WHERE x.template_id = t.id), 0) + 1,
  t.training_type_id, t.volume_mode, t.template_type, t.grid_rows, t.grid_cols,
  COALESCE(mi.intent, 'standard'),
  COALESCE(
    (SELECT JSON_ARRAYAGG(mit.muscle_group_id) FROM {INTENT_TARGETS} mit WHERE mit.template_id = t.id),
    JSON_ARRAY()
  ),
  %s, NULL, NULL, '{ORPHAN_SUMMARY}'
FROM {TEMPLATES} t
JOIN ({_ORPHAN_TEMPLATES}) o ON o.template_id = t.id
LEFT JOIN {INTENT} mi ON mi.template_id = t.id
WHERE NOT EXISTS (SELECT 1 FROM {VERSIONS} c WHERE c.current_template_id = t.id)
"""

ORPHAN_ATTACH_SQL = f"""
UPDATE {SLOTS} s
JOIN {VERSIONS} v ON v.current_template_id = s.template_id
SET s.version_id = v.id
WHERE s.version_id IS NULL
"""

ORPHAN_COUNT_SQL = f"SELECT COUNT(*) AS n FROM {SLOTS} WHERE version_id IS NULL"

# Inverso.
DROP_PLANS_SQL = f"ALTER TABLE {PLANS} DROP INDEX {IDX_PLANS}, DROP COLUMN {COL}"
DROP_RUNS_SQL = f"ALTER TABLE {RUNS} DROP INDEX {IDX_RUNS}, DROP COLUMN {COL}"

ORPHAN_COLLISIONS_SQL = f"""
SELECT template_id, sequence, COUNT(*) AS n
FROM {SLOTS}
WHERE version_id IS NULL
GROUP BY template_id, sequence
HAVING COUNT(*) > 1
"""

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class TemplateVersionOnPlansError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise TemplateVersionOnPlansError(f"066: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


_TABLES = (PLANS, RUNS, SLOTS, VERSIONS)


def _read(db):
    placeholders = ", ".join(["%s"] * len(_TABLES))
    engines = {
        _text(r["TABLE_NAME"]): _text(r["ENGINE"])
        for r in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            _TABLES,
        )
    }
    columns = {
        (_text(r["TABLE_NAME"]), _text(r["COLUMN_NAME"])): r
        for r in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            _TABLES,
        )
    }
    indexes: dict[tuple[str, str], list[dict]] = {}
    for r in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE "
        f"FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
        _TABLES,
    ):
        indexes.setdefault((_text(r["TABLE_NAME"]), _text(r["INDEX_NAME"])), []).append(r)
    return engines, columns, indexes


def _column(columns, table: str, name: str, *, expected_type: str, expected_nullable: str | None) -> bool:
    info = columns.get((table, name))
    if info is None:
        return False
    if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
        _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
    if expected_nullable is not None and _text(info["IS_NULLABLE"]) != expected_nullable:
        _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    return True


def _index(indexes, table: str, name: str, column: str) -> bool:
    rows = indexes.get((table, name))
    if not rows:
        return False
    cols = tuple(_text(r["COLUMN_NAME"]) for r in sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"])))
    if cols != (column,) or int(rows[0]["NON_UNIQUE"]) != 1:
        _fail(f"{table}.{name} existe com outra forma ({cols})")
    return True


def _add_column_statements(columns, indexes, table: str, idx: str, combined: str) -> list[tuple[str, tuple | None]]:
    tem_col = _column(columns, table, COL, expected_type="bigint", expected_nullable="YES")
    tem_idx = _index(indexes, table, idx, COL)
    if tem_col and tem_idx:
        return []
    if not tem_col and not tem_idx:
        return [(combined, None)]
    if not tem_col:
        return [(f"ALTER TABLE {table} ADD COLUMN {COL} BIGINT NULL", None)]
    return [(f"ALTER TABLE {table} ADD KEY {idx} ({COL})", None)]


def plan(db) -> list[tuple[str, tuple | None]]:
    """Le `information_schema`, quantos slots estao sem versao e - so quando
    ha o que sanear - as `sequence` repetidas entre eles e o relogio do banco
    (uma vez)."""
    engines, columns, indexes = _read(db)
    for table in (PLANS, SLOTS):
        if table not in engines:
            _fail(f"tabela {table} nao existe")
    for table, engine in engines.items():
        if engine.lower() != "innodb":
            _fail(f"{table} usa ENGINE={engine}, esperado InnoDB")

    statements: list[tuple[str, tuple | None]] = []
    statements += _add_column_statements(columns, indexes, PLANS, IDX_PLANS, ADD_PLANS_SQL)
    # `generation_runs` nasce na 054: num dry-run de banco atrasado ela pode
    # ainda nao existir no plano - o ALTER entra do mesmo jeito.
    statements += _add_column_statements(columns, indexes, RUNS, IDX_RUNS, ADD_RUNS_SQL)

    tem_version_id = _column(columns, SLOTS, "version_id", expected_type="bigint", expected_nullable=None)
    if tem_version_id:
        orfaos = db.query(ORPHAN_COUNT_SQL, None)
        if not orfaos or not int(orfaos[0]["n"] or 0):
            return statements
        colisoes = [
            f"modelo {_text(r['template_id'])} sequence {_text(r['sequence'])} ({_text(r['n'])}x)"
            for r in db.query(ORPHAN_COLLISIONS_SQL, None)
        ]
        if colisoes:
            _fail(
                "slots sem versao com a mesma sequence no mesmo Modelo; nao ha como escolher o "
                f"slot certo automaticamente: {', '.join(colisoes)}"
            )

    row = db.query("SELECT NOW() AS agora", None)
    agora = row[0]["agora"] if row else None
    statements += [
        (ORPHAN_CLOSE_SQL, (agora,)),
        (ORPHAN_OPEN_SQL, (agora,)),
        (ORPHAN_ATTACH_SQL, None),
    ]
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    engines, columns, indexes = _read(db)
    statements: list[tuple[str, tuple | None]] = []
    for table, idx, combined in ((RUNS, IDX_RUNS, DROP_RUNS_SQL), (PLANS, IDX_PLANS, DROP_PLANS_SQL)):
        tem_col = (table, COL) in columns
        tem_idx = (table, idx) in indexes
        if tem_col and tem_idx:
            statements.append((combined, None))
        elif tem_idx:
            statements.append((f"ALTER TABLE {table} DROP INDEX {idx}", None))
        elif tem_col:
            statements.append((f"ALTER TABLE {table} DROP COLUMN {COL}", None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
