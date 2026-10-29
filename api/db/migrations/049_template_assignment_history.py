"""049 - UMA atribuicao de Modelo de Treino vigente por usuario, com historico
de vigencia (M23; absorve o M15 "historico de assignments", registrado como
fora do MVP em §17 do plano do Novo Dashboard).

    workout_plan_template_assignments
      + unassigned_at         DATETIME NULL    fim da vigencia (exclusivo);
                                               NULL = VIGENTE agora
      + unassigned_by_user_id BIGINT   NULL    quem encerrou (auditoria,
                                               simetrica a assigned_by_user_id)
      + active_user_id        BIGINT GENERATED ALWAYS AS
                              (IF(unassigned_at IS NULL, user_id, NULL)) VIRTUAL
      + KEY idx_wpta_template (template_id)
      + UNIQUE KEY uq_wpta_active_user (active_user_id)
      - UNIQUE KEY uq_wpta_template_user (template_id, user_id)

Por que: hoje a tabela e um "vinculo vivo" sem historico
(`007_workout_plan_templates.sql`, secao 5). O UNIQUE e `(template_id,
user_id)`, isto e, ele impede o MESMO modelo duas vezes - e permite N
MODELOS DIFERENTES ao mesmo tempo. Quem le (`analytics.
_resolve_latest_assigned_template_id`, `training_types.
resolve_active_training_type_for_user`, `muscle_summary_service`,
`adherence_service`) resolve isso EM SILENCIO pegando o maior `assigned_at`,
e desatribuir e DELETE fisico - o passado some. Pedido do usuario: "o usuario
so deve ter um modelo associado por vez, sem perder as evolucoes de trabalho
em grupos musculares realizadas com o modelo anterior".

As duas metades do pedido viram duas coisas distintas no schema:

1. **Uma por vez** vira uma INVARIANTE DO BANCO, nao uma regra de aplicacao.
   `active_user_id` e uma coluna gerada que vale `user_id` enquanto a
   atribuicao esta vigente e NULL depois que ela e encerrada; o UNIQUE sobre
   ela recusa uma segunda vigente para o mesmo usuario (em MySQL, NULLs nao
   colidem em indice UNIQUE - por isso o historico pode ter quantas linhas
   encerradas quiser). Nenhum caminho de escrita, presente ou futuro,
   consegue furar isso por esquecimento de validacao.
2. **Sem perder o passado** vira `unassigned_at`: desatribuir passa a ser um
   UPDATE, e cada linha carrega o intervalo `[assigned_at, unassigned_at)`
   em que aquele modelo foi o vigente. E isso que permite resolver "qual era
   o modelo em 12/08?" - a fronteira de versao do plano (§4 do plano).

Por que o UNIQUE antigo SAI: com historico, o mesmo modelo pode ser
atribuido, encerrado e atribuido de novo (o caso real: trocar de ficha e
voltar). `(template_id, user_id)` proibiria a volta. O que ele protegia de
fato - "o mesmo modelo duas vezes ao mesmo tempo" - passa a ser garantido,
com folga, pelo novo UNIQUE de uma vigente por usuario.

`idx_wpta_template` entra ANTES do DROP e nao e decoracao: `fk_wpta_template`
(FK em `template_id`) usa `uq_wpta_template_user` como indice de apoio, e o
InnoDB recusa o DROP do ultimo indice que atende a FK (errno 150). Sem esse
statement a migracao falharia no meio - exatamente o que o resto do arquivo
existe para evitar.

Sem FK em `unassigned_by_user_id` (ao contrario de `assigned_by_user_id`, que
tem ON DELETE RESTRICT): o campo e auditoria de um fato ja ocorrido, e uma FK
RESTRICT ali passaria a travar a remocao de um usuario por causa de uma
desatribuicao antiga. NULL e o estado honesto para as linhas que a migracao
encerra (ninguem as encerrou - o backfill as encerrou).

--- Backfill (statement 2) ------------------------------------------------

Aditivo e determinista: toda atribuicao que tem outra DEPOIS dela (mesmo
usuario) e encerrada com `unassigned_at` = `assigned_at` DA SEGUINTE; a
ultima de cada usuario fica com `unassigned_at` NULL, isto e, VIGENTE. Nao ha
buraco nem sobreposicao na linha do tempo, e quem tinha uma atribuicao so
continua exatamente como estava.

Empate em `assigned_at` e desempatado por `id` (a ordem fica TOTAL: `id` e
unico). Sem isso, duas atribuicoes no mesmo segundo poderiam ficar as duas
vigentes e o UNIQUE do statement 5 quebraria a migracao no ultimo passo.

O UPDATE roda ANTES de `active_user_id` existir, e o UNIQUE entra so DEPOIS
dele: a ordem dos 5 statements e a propria prova de que o indice nunca ve o
estado intermediario em que todo mundo esta vigente.

Efeito medido no DEV em 2026-09-24 (`schema_migration_files` = 48, a mesma
versao de producao): 4 atribuicoes, 2 usuarios; o usuario 1 tem 3 (modelos 9,
18 e 19) e passa a ter 1 vigente (19, o de maior `assigned_at`) + 2
encerradas; o usuario 4 tem 1 e nao muda. Nenhuma linha e apagada.

--- `down` -----------------------------------------------------------------

Reversivel na FORMA, nao no DADO: `plan_down` desfaz os cinco statements na
ordem inversa (recria `uq_wpta_template_user`, dropa o UNIQUE novo, a coluna
gerada e as duas colunas de vigencia). O que ele NAO desfaz e o `unassigned_at`
escrito pelo backfill - porque ele some junto com a coluna. Um `down` so e
possivel enquanto NENHUMA linha nova tiver sido criada depois de uma
desatribuicao: se o mesmo par (template, user) aparecer duas vezes, recriar
`uq_wpta_template_user` falha - e deve falhar, porque nao existe estado
antigo equivalente para aquele historico. `plan_down` detecta esse caso e
para ANTES do primeiro statement, com a mensagem dizendo quais pares
duplicam.

--- Estados e idempotencia -------------------------------------------------

Cada um dos 5 statements tem guarda propria em `plan()`, que so LE
`information_schema`:

- producao hoje (nenhuma coluna nova, UNIQUE antigo presente) -> 5 statements;
- estado convergido (tudo aplicado) -> 0 statements (reexecucao e no-op);
- estado PARCIAL (uma aplicacao interrompida no meio) -> so o que falta, na
  mesma ordem;
- coluna existente com tipo/nulabilidade diferentes, `active_user_id` com
  outra expressao, ENGINE != InnoDB ou tabela ausente -> `TemplateAssignmentHistoryError`
  ANTES do primeiro statement.

O backfill (statement 2) so entra enquanto `unassigned_at` NAO existe, isto
e, uma unica vez na vida do banco: depois que a coluna existe, "encerrada" e
"nunca encerrada" sao dados legitimos do usuario e reescreve-los seria
destruir historico - nao convergir schema.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "workout_plan_template_assignments"

COL_UNASSIGNED_AT = "unassigned_at"
COL_UNASSIGNED_BY = "unassigned_by_user_id"
COL_ACTIVE_USER = "active_user_id"

IDX_TEMPLATE = "idx_wpta_template"
IDX_ACTIVE_USER = "uq_wpta_active_user"
IDX_LEGACY_UNIQUE = "uq_wpta_template_user"

# Expressao da coluna gerada, como NOS a escrevemos.
ACTIVE_USER_EXPRESSION = f"IF({COL_UNASSIGNED_AT} IS NULL, user_id, NULL)"

# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

ADD_COLUMNS_SQL = (
    f"ALTER TABLE {TABLE} "
    f"ADD COLUMN {COL_UNASSIGNED_AT} DATETIME NULL, "
    f"ADD COLUMN {COL_UNASSIGNED_BY} BIGINT NULL, "
    f"ADD KEY {IDX_TEMPLATE} (template_id)"
)

# Encerra toda atribuicao que tem outra DEPOIS dela (mesmo usuario) com o
# `assigned_at` da seguinte. `(assigned_at, id)` da ordem TOTAL: sem o
# desempate por `id`, duas do mesmo segundo ficariam as duas vigentes.
BACKFILL_SQL = f"""
UPDATE {TABLE} a
JOIN (
  SELECT atual.id AS id, MIN(seguinte.assigned_at) AS next_assigned_at
  FROM {TABLE} atual
  JOIN {TABLE} seguinte
    ON seguinte.user_id = atual.user_id
   AND (seguinte.assigned_at > atual.assigned_at
        OR (seguinte.assigned_at = atual.assigned_at AND seguinte.id > atual.id))
  GROUP BY atual.id
) proxima ON proxima.id = a.id
SET a.{COL_UNASSIGNED_AT} = proxima.next_assigned_at
WHERE a.{COL_UNASSIGNED_AT} IS NULL
"""

DROP_LEGACY_UNIQUE_SQL = f"ALTER TABLE {TABLE} DROP INDEX {IDX_LEGACY_UNIQUE}"

ADD_ACTIVE_USER_SQL = (
    f"ALTER TABLE {TABLE} ADD COLUMN {COL_ACTIVE_USER} BIGINT "
    f"GENERATED ALWAYS AS ({ACTIVE_USER_EXPRESSION}) VIRTUAL"
)

ADD_ACTIVE_UNIQUE_SQL = f"ALTER TABLE {TABLE} ADD UNIQUE KEY {IDX_ACTIVE_USER} ({COL_ACTIVE_USER})"

# Inverso, na ordem contraria.
DROP_ACTIVE_UNIQUE_SQL = f"ALTER TABLE {TABLE} DROP INDEX {IDX_ACTIVE_USER}"
DROP_ACTIVE_USER_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COL_ACTIVE_USER}"
RESTORE_LEGACY_UNIQUE_SQL = f"ALTER TABLE {TABLE} ADD UNIQUE KEY {IDX_LEGACY_UNIQUE} (template_id, user_id)"
DROP_COLUMNS_SQL = (
    f"ALTER TABLE {TABLE} "
    f"DROP COLUMN {COL_UNASSIGNED_BY}, "
    f"DROP COLUMN {COL_UNASSIGNED_AT}, "
    f"DROP INDEX {IDX_TEMPLATE}"
)

DUPLICADAS_SQL = f"""
SELECT template_id, user_id, COUNT(*) AS n
FROM {TABLE}
GROUP BY template_id, user_id
HAVING COUNT(*) > 1
"""

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_NEW_COLUMNS: dict[str, tuple[str, str]] = {
    COL_UNASSIGNED_AT: ("datetime", "YES"),
    COL_UNASSIGNED_BY: ("bigint", "YES"),
    COL_ACTIVE_USER: ("bigint", "YES"),
}

# Colunas que a 049 EXIGE encontrar (o backfill e a coluna gerada dependem
# delas). Nao sao alteradas.
REQUIRED_COLUMNS: dict[str, str] = {
    "id": "bigint",
    "template_id": "bigint",
    "user_id": "bigint",
    "assigned_at": "timestamp",
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")
_NAO_PALAVRA_RE = re.compile(r"[^a-z0-9_]+")


class TemplateAssignmentHistoryError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise TemplateAssignmentHistoryError(f"049: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _normalize_expression(expression) -> str:
    """Compara a expressao da coluna gerada IGNORANDO o que o MySQL reescreve:
    crases, aspas, espacos e caixa. O que sobra ("ifunassigned_atisnull...")
    ainda distingue uma expressao diferente de uma formatada diferente."""
    return _NAO_PALAVRA_RE.sub("", _text(expression).strip().lower())


EXPECTED_ACTIVE_USER_EXPRESSION = _normalize_expression(ACTIVE_USER_EXPRESSION)


def _read(db) -> tuple[dict[str, str], dict[str, dict], dict[str, list[dict]]]:
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
    }
    columns = {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, EXTRA, GENERATION_EXPRESSION "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
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


def _check_table(engines: dict[str, str], columns: dict[str, dict]) -> None:
    if TABLE not in engines:
        _fail(f"tabela {TABLE} nao existe")
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")
    for name, expected_type in REQUIRED_COLUMNS.items():
        info = columns.get(name)
        if info is None:
            _fail(f"{TABLE}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")


def _check_new_column(columns: dict[str, dict], name: str) -> bool:
    """`True` quando a coluna ja existe NO FORMATO esperado (nada a fazer),
    `False` quando ela ainda nao existe. Formato divergente -> erro."""
    info = columns.get(name)
    if info is None:
        return False
    expected_type, expected_nullable = EXPECTED_NEW_COLUMNS[name]
    if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
        _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
    if _text(info["IS_NULLABLE"]) != expected_nullable:
        _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    if name == COL_ACTIVE_USER:
        encontrada = _normalize_expression(info.get("GENERATION_EXPRESSION"))
        if not encontrada:
            _fail(f"{TABLE}.{name} existe mas NAO e coluna gerada")
        if encontrada != EXPECTED_ACTIVE_USER_EXPRESSION:
            _fail(
                f"{TABLE}.{name} e gerada por {_text(info.get('GENERATION_EXPRESSION'))!r}, "
                f"esperado {ACTIVE_USER_EXPRESSION!r}"
            )
    return True


def _check_index(indexes: dict[str, list[dict]], name: str, *, unique: bool, columns: tuple[str, ...]) -> bool:
    """`True` quando o indice ja existe COM a forma esperada, `False` quando
    ele nao existe. Forma divergente -> erro."""
    rows = indexes.get(name)
    if not rows:
        return False
    encontradas = tuple(
        _text(row["COLUMN_NAME"]) for row in sorted(rows, key=lambda row: int(row["SEQ_IN_INDEX"]))
    )
    if encontradas != columns:
        _fail(f"{TABLE}.{name} cobre {encontradas}, esperado {columns}")
    non_unique = int(rows[0]["NON_UNIQUE"])
    if unique and non_unique != 0:
        _fail(f"{TABLE}.{name} existe mas NAO e UNIQUE")
    if not unique and non_unique != 1:
        _fail(f"{TABLE}.{name} existe como UNIQUE, esperado indice comum")
    return True


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes = _read(db)
    _check_table(engines, columns)

    tem_unassigned_at = _check_new_column(columns, COL_UNASSIGNED_AT)
    tem_unassigned_by = _check_new_column(columns, COL_UNASSIGNED_BY)
    tem_active_user = _check_new_column(columns, COL_ACTIVE_USER)
    tem_idx_template = _check_index(indexes, IDX_TEMPLATE, unique=False, columns=("template_id",))
    tem_idx_active = _check_index(indexes, IDX_ACTIVE_USER, unique=True, columns=(COL_ACTIVE_USER,))
    tem_unique_antigo = _check_index(
        indexes, IDX_LEGACY_UNIQUE, unique=True, columns=("template_id", "user_id")
    )

    if tem_active_user and not tem_unassigned_at:
        # `active_user_id` nao pode existir sem a coluna de que ela deriva -
        # se isso aparecer, o banco nao e o que este arquivo descreve.
        _fail(f"{TABLE}.{COL_ACTIVE_USER} existe sem {COL_UNASSIGNED_AT}")

    statements: list[tuple[str, tuple | None]] = []

    # 1) colunas de vigencia + indice que vai apoiar a FK depois do DROP.
    #    As tres partes entram juntas ou nenhuma: num estado parcial (por
    #    exemplo so `idx_wpta_template` presente) o ALTER inteiro falharia,
    #    entao cada parte vira seu proprio statement.
    faltando = [
        sql
        for presente, sql in (
            (tem_unassigned_at, f"ALTER TABLE {TABLE} ADD COLUMN {COL_UNASSIGNED_AT} DATETIME NULL"),
            (tem_unassigned_by, f"ALTER TABLE {TABLE} ADD COLUMN {COL_UNASSIGNED_BY} BIGINT NULL"),
            (tem_idx_template, f"ALTER TABLE {TABLE} ADD KEY {IDX_TEMPLATE} (template_id)"),
        )
        if not presente
    ]
    if len(faltando) == 3:
        # Caminho normal (producao hoje): um unico ALTER, uma unica
        # reconstrucao da tabela.
        statements.append((ADD_COLUMNS_SQL, None))
    else:
        statements.extend((sql, None) for sql in faltando)

    # 2) backfill: SO na primeira vez, isto e, enquanto `unassigned_at` ainda
    #    nao existia. Depois disso "encerrada"/"vigente" e dado do usuario.
    if not tem_unassigned_at:
        statements.append((BACKFILL_SQL, None))

    # 3) o UNIQUE antigo sai (ele proibiria reatribuir o mesmo modelo).
    if tem_unique_antigo:
        statements.append((DROP_LEGACY_UNIQUE_SQL, None))

    # 4) e 5) a invariante "uma vigente por usuario".
    if not tem_active_user:
        statements.append((ADD_ACTIVE_USER_SQL, None))
    if not tem_idx_active:
        statements.append((ADD_ACTIVE_UNIQUE_SQL, None))

    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`, na ordem contraria. Para ANTES do primeiro statement
    se o historico ja contiver o mesmo par (template, user) duas vezes -
    recriar `uq_wpta_template_user` seria impossivel."""
    engines, columns, indexes = _read(db)
    if TABLE not in engines:
        return []

    tem_unassigned_at = COL_UNASSIGNED_AT in columns
    tem_unassigned_by = COL_UNASSIGNED_BY in columns
    tem_active_user = COL_ACTIVE_USER in columns
    tem_idx_template = IDX_TEMPLATE in indexes
    tem_idx_active = IDX_ACTIVE_USER in indexes
    tem_unique_antigo = IDX_LEGACY_UNIQUE in indexes

    if not tem_unique_antigo:
        duplicadas = [
            f"(template {_text(row['template_id'])}, user {_text(row['user_id'])}): {_text(row['n'])}x"
            for row in db.query(DUPLICADAS_SQL, None)
        ]
        if duplicadas:
            _fail(
                "o historico ja tem o mesmo par (template, user) mais de uma vez, entao "
                f"{IDX_LEGACY_UNIQUE} nao pode ser recriado: {', '.join(duplicadas)}"
            )

    statements: list[tuple[str, tuple | None]] = []
    if tem_idx_active:
        statements.append((DROP_ACTIVE_UNIQUE_SQL, None))
    if tem_active_user:
        statements.append((DROP_ACTIVE_USER_SQL, None))
    if not tem_unique_antigo:
        statements.append((RESTORE_LEGACY_UNIQUE_SQL, None))

    faltando = [
        sql
        for presente, sql in (
            (tem_unassigned_by, f"ALTER TABLE {TABLE} DROP COLUMN {COL_UNASSIGNED_BY}"),
            (tem_unassigned_at, f"ALTER TABLE {TABLE} DROP COLUMN {COL_UNASSIGNED_AT}"),
            (tem_idx_template, f"ALTER TABLE {TABLE} DROP INDEX {IDX_TEMPLATE}"),
        )
        if presente
    ]
    if len(faltando) == 3:
        statements.append((DROP_COLUMNS_SQL, None))
    else:
        statements.extend((sql, None) for sql in faltando)

    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
