"""052 - vigencia das Metas de Ativacao por data (M12).

    training_type_muscle_group_targets
      + effective_from            DATETIME NULL   inicio da vigencia (inclusivo);
                                                  NULL = VIGENTE DESDE SEMPRE
      + superseded_at             DATETIME NULL   fim da vigencia (exclusivo);
                                                  NULL = VIGENTE agora
      + current_training_type_id  BIGINT GENERATED ALWAYS AS
                                  (IF(superseded_at IS NULL, training_type_id, NULL)) VIRTUAL
      + KEY idx_ttmgt_type (training_type_id)
      + UNIQUE KEY uq_ttmgt_current (current_training_type_id, muscle_group_id)
      - UNIQUE KEY uq_ttmgt_type_group (training_type_id, muscle_group_id)

Por que: a tabela nasceu no baseline `010_training_types.sql` guardando SO a
meta de hoje - `UNIQUE (training_type_id, muscle_group_id)` e um unico
escritor (`training_types._replace_training_type_targets`) que faz DELETE +
INSERT. O passado da meta nao existe em lugar nenhum. Consequencia: o
historico do dashboard e medido contra a meta de HOJE. Quem mudou a meta em
setembro ve os ciclos de julho avaliados contra um objetivo que ainda nao
existia na epoca.

O M23 (migracao 049) ja resolveu exatamente este problema para o outro lado
do par: a ATRIBUICAO de Modelo passou a ter vigencia (`unassigned_at` +
coluna gerada com UNIQUE), e desatribuir virou UPDATE. Ficou incoerente - o
plano tem historia, a meta nao. Esta migracao aplica a MESMA forma a meta, de
proposito: uma so gramatica de vigencia no schema, e nao duas.

Duas metades, como na 049:

1. **Uma versao vigente por (tipo, grupo)** vira INVARIANTE DO BANCO.
   `current_training_type_id` vale `training_type_id` enquanto a linha esta
   vigente e NULL depois que ela e superada; o UNIQUE sobre
   `(current_training_type_id, muscle_group_id)` recusa uma segunda vigente
   para o mesmo par (em MySQL, uma tupla com NULL nao colide em indice
   UNIQUE - por isso o historico pode ter quantas linhas superadas quiser).
   Nenhum caminho de escrita, presente ou futuro, fura isso por esquecimento
   de validacao.
2. **Sem perder o passado** vira `superseded_at`: substituir a meta passa a
   ser UPDATE + INSERT, e cada linha carrega o intervalo SEMIABERTO
   `[effective_from, superseded_at)` em que aquele numero foi o alvo.
   Semiaberto pelo mesmo motivo da 049: o instante em que uma versao termina
   e o instante em que a proxima comeca (o escritor usa o MESMO `NOW()` nas
   duas escritas); fechado dos dois lados, esse instante pertenceria a duas
   versoes.

Por que o UNIQUE antigo SAI: com historico, o mesmo (tipo, grupo) aparece
tantas vezes quantas a meta foi editada - inclusive voltando a um valor
anterior. `(training_type_id, muscle_group_id)` proibiria a segunda linha. O
que ele protegia de fato - "duas metas vigentes para o mesmo grupo" - passa a
ser garantido pelo UNIQUE novo.

`idx_ttmgt_type` entra ANTES do DROP e nao e decoracao: `fk_ttmgt_type` (FK
em `training_type_id`, ON DELETE CASCADE) usa `uq_ttmgt_type_group` como
indice de apoio, e o InnoDB recusa o DROP do ultimo indice que atende a FK
(errno 150). Sem esse statement a migracao falharia no meio. O UNIQUE novo
NAO serve de apoio: ele comeca por `current_training_type_id`, que e outra
coluna. Mesma licao da 049, e por isso o mesmo remedio.

Nenhuma FK nova. (A 028 proibe FK nova para `exercise_catalog`; aqui nao
haveria nem motivo - as duas FKs de que a tabela precisa, para
`training_types` e `muscle_groups`, ja existem desde o baseline 010 e ficam
intocadas.)

--- Backfill --------------------------------------------------------------

NAO HA statement de backfill, e isso e uma decisao, nao um esquecimento.

As duas colunas nascem NULL, e NULL ja e a resposta certa para as linhas que
existem: `superseded_at IS NULL` = vigente (e elas sao, de fato, as metas de
hoje) e `effective_from IS NULL` = "vigente desde sempre".

A alternativa tentadora era datar o inicio com `training_types.updated_at`,
que na pratica e quando os targets foram reescritos pela ultima vez
(`_replace_training_type_targets` so roda em create/duplicate/update, e o
update tambem toca `updated_at`). Ela foi recusada: isso deixaria todo o
periodo ANTERIOR a essa data SEM meta nenhuma, e um ciclo antigo cairia no
fallback generico. Trocar "meta errada" por "nenhuma meta" e piorar o
historico, nao corrigi-lo. O banco simplesmente nao sabe o que a meta era
antes da primeira edicao registrada - o DELETE fisico apagou -, e `NULL`
("sempre foi essa, ate onde este banco sabe") e a unica afirmacao honesta
disponivel.

Consequencia PRATICA, medida e desejada: ligar a resolucao por data logo
depois desta migracao NAO muda numero nenhum ja exibido. A vigencia comeca a
valer a partir da PRIMEIRA edicao de meta feita depois do deploy - a partir
dali, a edicao deixa de apagar o passado.

Efeito medido no DEV em 2026-09-26 (`schema_migration_files` = 49): 38 linhas
de meta, 2 tipos de treino (`Hipertrofia Masculino`, 20 grupos;
`Hipertrofia Feminino`, 18), ambos com `updated_at` = 2026-09-03. Zero linhas
escritas pela migracao; as 38 ficam vigentes desde sempre, byte a byte os
mesmos valores. O unico usuario com Modelo vigente (user 1, tipo 2) tem 49
sessoes em 9 semanas civis, 48 delas anteriores a 2026-09-03 - isto e, 9
ciclos hoje medidos contra uma meta reescrita depois deles. Nenhum desses 9
muda com esta migracao (nao ha versao anterior para comparar); os proximos
param de correr esse risco.

--- `down` ----------------------------------------------------------------

Reversivel na FORMA, nao no DADO: desfaz os quatro statements na ordem
inversa (recria `uq_ttmgt_type_group`, dropa o UNIQUE novo, a coluna gerada e
as duas colunas de vigencia). O historico escrito depois da migracao some
junto com as colunas - e por isso `plan_down` PARA ANTES do primeiro
statement se a tabela ja contiver o mesmo par (tipo, grupo) duas vezes:
recriar `uq_ttmgt_type_group` falharia, e deve falhar, porque nao existe
estado antigo equivalente para aquele historico. A mensagem diz quais pares
duplicam.

--- Estados e idempotencia ------------------------------------------------

Cada statement tem guarda propria em `plan()`, que so LE `information_schema`:

- producao hoje (nenhuma coluna nova, UNIQUE antigo presente) -> 4 statements;
- estado convergido -> 0 statements (reexecucao e no-op);
- estado PARCIAL (aplicacao interrompida no meio) -> so o que falta, na mesma
  ordem;
- coluna existente com tipo/nulabilidade diferentes, `current_training_type_id`
  com outra expressao, ENGINE != InnoDB ou tabela ausente ->
  `TrainingTypeTargetVersioningError` ANTES do primeiro statement.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "training_type_muscle_group_targets"

COL_EFFECTIVE_FROM = "effective_from"
COL_SUPERSEDED_AT = "superseded_at"
COL_CURRENT_TYPE = "current_training_type_id"

IDX_TYPE = "idx_ttmgt_type"
IDX_CURRENT = "uq_ttmgt_current"
IDX_LEGACY_UNIQUE = "uq_ttmgt_type_group"

# Expressao da coluna gerada, como NOS a escrevemos.
CURRENT_TYPE_EXPRESSION = f"IF({COL_SUPERSEDED_AT} IS NULL, training_type_id, NULL)"

# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

ADD_COLUMNS_SQL = (
    f"ALTER TABLE {TABLE} "
    f"ADD COLUMN {COL_EFFECTIVE_FROM} DATETIME NULL, "
    f"ADD COLUMN {COL_SUPERSEDED_AT} DATETIME NULL, "
    f"ADD KEY {IDX_TYPE} (training_type_id)"
)

DROP_LEGACY_UNIQUE_SQL = f"ALTER TABLE {TABLE} DROP INDEX {IDX_LEGACY_UNIQUE}"

ADD_CURRENT_TYPE_SQL = (
    f"ALTER TABLE {TABLE} ADD COLUMN {COL_CURRENT_TYPE} BIGINT "
    f"GENERATED ALWAYS AS ({CURRENT_TYPE_EXPRESSION}) VIRTUAL"
)

ADD_CURRENT_UNIQUE_SQL = (
    f"ALTER TABLE {TABLE} ADD UNIQUE KEY {IDX_CURRENT} ({COL_CURRENT_TYPE}, muscle_group_id)"
)

# Inverso, na ordem contraria.
DROP_CURRENT_UNIQUE_SQL = f"ALTER TABLE {TABLE} DROP INDEX {IDX_CURRENT}"
DROP_CURRENT_TYPE_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COL_CURRENT_TYPE}"
RESTORE_LEGACY_UNIQUE_SQL = (
    f"ALTER TABLE {TABLE} ADD UNIQUE KEY {IDX_LEGACY_UNIQUE} (training_type_id, muscle_group_id)"
)
DROP_COLUMNS_SQL = (
    f"ALTER TABLE {TABLE} "
    f"DROP COLUMN {COL_SUPERSEDED_AT}, "
    f"DROP COLUMN {COL_EFFECTIVE_FROM}, "
    f"DROP INDEX {IDX_TYPE}"
)

DUPLICADAS_SQL = f"""
SELECT training_type_id, muscle_group_id, COUNT(*) AS n
FROM {TABLE}
GROUP BY training_type_id, muscle_group_id
HAVING COUNT(*) > 1
"""

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_NEW_COLUMNS: dict[str, tuple[str, str]] = {
    COL_EFFECTIVE_FROM: ("datetime", "YES"),
    COL_SUPERSEDED_AT: ("datetime", "YES"),
    COL_CURRENT_TYPE: ("bigint", "YES"),
}

# Colunas que a 052 EXIGE encontrar (a coluna gerada e o UNIQUE novo dependem
# delas). Nao sao alteradas.
REQUIRED_COLUMNS: dict[str, str] = {
    "id": "bigint",
    "training_type_id": "bigint",
    "muscle_group_id": "bigint",
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")
_NAO_PALAVRA_RE = re.compile(r"[^a-z0-9_]+")


class TrainingTypeTargetVersioningError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise TrainingTypeTargetVersioningError(f"052: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _normalize_expression(expression) -> str:
    """Compara a expressao da coluna gerada IGNORANDO o que o MySQL reescreve:
    crases, aspas, espacos e caixa. O que sobra ainda distingue uma expressao
    diferente de uma formatada diferente."""
    return _NAO_PALAVRA_RE.sub("", _text(expression).strip().lower())


EXPECTED_CURRENT_TYPE_EXPRESSION = _normalize_expression(CURRENT_TYPE_EXPRESSION)


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
    if name == COL_CURRENT_TYPE:
        encontrada = _normalize_expression(info.get("GENERATION_EXPRESSION"))
        if not encontrada:
            _fail(f"{TABLE}.{name} existe mas NAO e coluna gerada")
        if encontrada != EXPECTED_CURRENT_TYPE_EXPRESSION:
            _fail(
                f"{TABLE}.{name} e gerada por {_text(info.get('GENERATION_EXPRESSION'))!r}, "
                f"esperado {CURRENT_TYPE_EXPRESSION!r}"
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

    tem_effective_from = _check_new_column(columns, COL_EFFECTIVE_FROM)
    tem_superseded_at = _check_new_column(columns, COL_SUPERSEDED_AT)
    tem_current_type = _check_new_column(columns, COL_CURRENT_TYPE)
    tem_idx_type = _check_index(indexes, IDX_TYPE, unique=False, columns=("training_type_id",))
    tem_idx_current = _check_index(
        indexes, IDX_CURRENT, unique=True, columns=(COL_CURRENT_TYPE, "muscle_group_id")
    )
    tem_unique_antigo = _check_index(
        indexes, IDX_LEGACY_UNIQUE, unique=True, columns=("training_type_id", "muscle_group_id")
    )

    if tem_current_type and not tem_superseded_at:
        # A coluna gerada nao pode existir sem a coluna de que ela deriva - se
        # isso aparecer, o banco nao e o que este arquivo descreve.
        _fail(f"{TABLE}.{COL_CURRENT_TYPE} existe sem {COL_SUPERSEDED_AT}")

    statements: list[tuple[str, tuple | None]] = []

    # 1) colunas de vigencia + indice que vai apoiar a FK depois do DROP.
    #    Num estado parcial (por exemplo so `idx_ttmgt_type` presente) o ALTER
    #    inteiro falharia, entao cada parte vira seu proprio statement.
    faltando = [
        sql
        for presente, sql in (
            (tem_effective_from, f"ALTER TABLE {TABLE} ADD COLUMN {COL_EFFECTIVE_FROM} DATETIME NULL"),
            (tem_superseded_at, f"ALTER TABLE {TABLE} ADD COLUMN {COL_SUPERSEDED_AT} DATETIME NULL"),
            (tem_idx_type, f"ALTER TABLE {TABLE} ADD KEY {IDX_TYPE} (training_type_id)"),
        )
        if not presente
    ]
    if len(faltando) == 3:
        # Caminho normal (producao hoje): um unico ALTER, uma unica
        # reconstrucao da tabela.
        statements.append((ADD_COLUMNS_SQL, None))
    else:
        statements.extend((sql, None) for sql in faltando)

    # Sem statement de backfill: NULL ja e a resposta certa para as linhas que
    # existem (vigentes, desde sempre). Ver o cabecalho.

    # 2) o UNIQUE antigo sai (ele proibiria a segunda versao do mesmo par).
    if tem_unique_antigo:
        statements.append((DROP_LEGACY_UNIQUE_SQL, None))

    # 3) e 4) a invariante "uma versao vigente por (tipo, grupo)".
    if not tem_current_type:
        statements.append((ADD_CURRENT_TYPE_SQL, None))
    if not tem_idx_current:
        statements.append((ADD_CURRENT_UNIQUE_SQL, None))

    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`, na ordem contraria. Para ANTES do primeiro statement
    se a tabela ja contiver o mesmo par (tipo, grupo) duas vezes - recriar
    `uq_ttmgt_type_group` seria impossivel."""
    engines, columns, indexes = _read(db)
    if TABLE not in engines:
        return []

    tem_effective_from = COL_EFFECTIVE_FROM in columns
    tem_superseded_at = COL_SUPERSEDED_AT in columns
    tem_current_type = COL_CURRENT_TYPE in columns
    tem_idx_type = IDX_TYPE in indexes
    tem_idx_current = IDX_CURRENT in indexes
    tem_unique_antigo = IDX_LEGACY_UNIQUE in indexes

    if not tem_unique_antigo:
        duplicadas = [
            f"(tipo {_text(row['training_type_id'])}, grupo {_text(row['muscle_group_id'])}): {_text(row['n'])}x"
            for row in db.query(DUPLICADAS_SQL, None)
        ]
        if duplicadas:
            _fail(
                "o historico ja tem o mesmo par (tipo, grupo) mais de uma vez, entao "
                f"{IDX_LEGACY_UNIQUE} nao pode ser recriado: {', '.join(duplicadas)}"
            )

    statements: list[tuple[str, tuple | None]] = []
    if tem_idx_current:
        statements.append((DROP_CURRENT_UNIQUE_SQL, None))
    if tem_current_type:
        statements.append((DROP_CURRENT_TYPE_SQL, None))
    if not tem_unique_antigo:
        statements.append((RESTORE_LEGACY_UNIQUE_SQL, None))

    faltando = [
        sql
        for presente, sql in (
            (tem_superseded_at, f"ALTER TABLE {TABLE} DROP COLUMN {COL_SUPERSEDED_AT}"),
            (tem_effective_from, f"ALTER TABLE {TABLE} DROP COLUMN {COL_EFFECTIVE_FROM}"),
            (tem_idx_type, f"ALTER TABLE {TABLE} DROP INDEX {IDX_TYPE}"),
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
