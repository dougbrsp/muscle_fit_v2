"""091 - junta o Modelo 36 a linhagem do 34 (escrita de dado AUTORIZADA pelo
usuario em 2026-10-24: "Pode implementar, junta o 34 com o 36." So este par).

    UPDATE workout_plan_templates SET source_template_id = 34 WHERE id = 36

Por que: o 36 ("PPL-D-UL-D - Ponderado") foi criado em 17/10 duplicando o 34
("PPL (Descanso QUA) UL (Descanso DOM)"), antes de a 090 existir; a copia nao
gravou a origem. Medido em producao (2026-10-24, SELECT): a v1 do 36 e
identica a uma versao do 34 (mesmos 7 slots, sequencias 1..7, mesmos itens), os
dois sao do usuario 1, e o 34 tem 2 sessoes (slots 2 e 3, em 16 e 17/10) que
hoje ficam fora dos grupos do 36.

--- Guardas (todas precisam valer; senao, nada e escrito) ------------------

1. os dois Modelos existem (exclusao logica nao conta: a linha existe) e sao
   OS MESMOS medidos em producao: dia de criacao (`created_at`, imutavel)
   2026-10-12 para o 34 e 2026-10-17 para o 36 (o nome nao serve: o usuario
   pode renomear);
2. sao do mesmo usuario (`created_by_user_id`);
3. o 36 ainda esta com origem NULL (ninguem gravou outra linhagem antes);
4. o 34 nao tem origem (raiz; fecha qualquer ciclo, direto ou longo);
5. as grades sao compativeis: a versao vigente (065, `superseded_at IS NULL`)
   de cada um tem o MESMO conjunto de `sequence` de slots.

No DEV, num banco novo e no schema de teste os ids podem nao existir ou ser
outros Modelos: as guardas tornam a migracao inofensiva (0 statements). As
guardas 2-4 tambem estao no proprio UPDATE (`JOIN` + `WHERE`), entao uma
corrida entre o plano e a execucao nao escreve fora delas.

DRY-RUN antes da 090 aplicar (coluna ausente): o plano lista o UPDATE sem
avaliar as guardas (nao ha coluna para ler); a prova real e o relatorio do
migrate e o SELECT de `source_template_id` do 36 depois do deploy.

Idempotente: depois de aplicada, a guarda 3 falha e o plano fica vazio.
`down`: devolve a origem a NULL SOMENTE se ela ainda for a gravada aqui
(`source_template_id = 34`).

Data API: um statement por chamada, parametros `%s`, nenhuma literal com ':';
modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "workout_plan_templates"
COLUMN = "source_template_id"
COPY_TEMPLATE_ID = 36
SOURCE_TEMPLATE_ID = 34
# Dia de criacao medido em producao (2026-10-24): identidade dos dois Modelos.
EXPECTED_CREATED_DAY = {SOURCE_TEMPLATE_ID: "2026-10-12", COPY_TEMPLATE_ID: "2026-10-17"}

LINK_SQL = (
    "UPDATE workout_plan_templates c "
    "JOIN workout_plan_templates o ON o.id = %s "
    "SET c.source_template_id = o.id "
    "WHERE c.id = %s "
    "AND c.source_template_id IS NULL "
    "AND c.created_by_user_id = o.created_by_user_id "
    "AND o.source_template_id IS NULL"
)
LINK_PARAMS = (SOURCE_TEMPLATE_ID, COPY_TEMPLATE_ID)

UNLINK_SQL = "UPDATE workout_plan_templates SET source_template_id = NULL WHERE id = %s AND source_template_id = %s"
UNLINK_PARAMS = (COPY_TEMPLATE_ID, SOURCE_TEMPLATE_ID)

TEMPLATES_SQL = (
    "SELECT id, created_by_user_id, source_template_id, created_at FROM workout_plan_templates WHERE id IN (%s, %s)"
)
CURRENT_SEQUENCES_SQL = (
    "SELECT v.template_id, s.sequence "
    "FROM workout_plan_template_versions v "
    "JOIN workout_plan_template_slots s ON s.version_id = v.id "
    "WHERE v.template_id IN (%s, %s) AND v.superseded_at IS NULL"
)


def _column_exists(db) -> bool:
    return bool(
        db.query(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (TABLE, COLUMN),
        )
    )


def _int(value):
    return None if value is None else int(value)


def _day(value) -> str:
    """`YYYY-MM-DD` de um DATETIME/TIMESTAMP (objeto no PyMySQL, texto na Data API)."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        value = value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    return str(value)[:10]


def link_is_safe(db) -> bool:
    """Guardas 1-5, so leitura."""
    templates = {
        int(row["id"]): row for row in db.query(TEMPLATES_SQL, (COPY_TEMPLATE_ID, SOURCE_TEMPLATE_ID))
    }
    copy = templates.get(COPY_TEMPLATE_ID)
    source = templates.get(SOURCE_TEMPLATE_ID)
    if copy is None or source is None:
        return False
    for template_id, row in ((COPY_TEMPLATE_ID, copy), (SOURCE_TEMPLATE_ID, source)):
        if _day(row.get("created_at")) != EXPECTED_CREATED_DAY[template_id]:
            return False
    if _int(copy.get("created_by_user_id")) != _int(source.get("created_by_user_id")):
        return False
    if copy.get("source_template_id") is not None:
        return False
    if source.get("source_template_id") is not None:
        return False
    sequences: dict[int, set[int]] = {COPY_TEMPLATE_ID: set(), SOURCE_TEMPLATE_ID: set()}
    for row in db.query(CURRENT_SEQUENCES_SQL, (COPY_TEMPLATE_ID, SOURCE_TEMPLATE_ID)):
        sequences[int(row["template_id"])].add(int(row["sequence"]))
    copy_grid = sequences[COPY_TEMPLATE_ID]
    return bool(copy_grid) and copy_grid == sequences[SOURCE_TEMPLATE_ID]


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Sem a coluna (DRY-RUN antes da 090 aplicar): planeja o
    UPDATE guardado; numa execucao real a 090 ja rodou."""
    if not _column_exists(db):
        if bool(getattr(db, "dry_run", False)):
            return [(LINK_SQL, LINK_PARAMS)]
        raise RuntimeError("091: coluna workout_plan_templates.source_template_id ausente (090 nao aplicada)")
    return [(LINK_SQL, LINK_PARAMS)] if link_is_safe(db) else []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    if not _column_exists(db):
        return []
    return [(UNLINK_SQL, UNLINK_PARAMS)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
