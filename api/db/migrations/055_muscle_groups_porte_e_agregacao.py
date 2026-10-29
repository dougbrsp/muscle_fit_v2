"""055 - porte e agregacao dos grupos musculares (Fase 4, PR B).

    muscle_groups
      + size_class                   ENUM('large','small','abs','unclassified')
                                     NOT NULL DEFAULT 'unclassified'
                                     AFTER secondary_muscles
      + methodology_parent_group_id  BIGINT NULL  -> muscle_groups(id)
                                                     ON DELETE SET NULL
      + KEY idx_mg_methodology_parent (methodology_parent_group_id)
      + CONSTRAINT fk_mg_methodology_parent

Por que: a Fase 4 avalia um Modelo pela metodologia de volume por grupo
(EXERCICIOS_GRUPOS / Leandro Twin), que prescreve faixa de exercicios por
sessao cruzando **porte do grupo** (grande / pequeno / abdomen) com
**frequencia semanal**. Duas coisas que o schema hoje nao sabe dizer:

1. **Qual o porte de cada grupo.** `muscle_groups` guarda nome e os musculos
   que o compoem, nada sobre tamanho. O porte e um atributo do GRUPO (nao do
   exercicio, nem do tipo de treino), entao e coluna aqui - e editavel por
   admin (decisao 5 da fase), porque a lista do documento nao cobre o
   catalogo inteiro do MuscleFit.

2. **Quais grupos sao FATIAS de um mesmo musculo.** O catalogo daqui e mais
   fino que o vocabulario da metodologia: `Peitoral` e `Peitoral superior`
   sao duas linhas, mas um so "peitoral" para o autor; o mesmo vale para as
   duas Costas, os dois Antebracos e as duas Panturrilhas. Comparar cada
   fatia isolada com a faixa do bloco inteiro acusaria subtreino em todas
   elas. `methodology_parent_group_id` aponta a fatia para o grupo que a
   REPRESENTA na metodologia; a avaliacao soma as fatias antes de comparar.
   NULL = o proprio grupo ja e o bloco (decisao 6 da fase).

**Nada le estas colunas ainda.** O motor da metodologia (PR C) e os endpoints
(PR E) vem depois; este PR entrega schema, seed e CRUD. Nenhuma leitura do
painel muda.

--- Decisoes do DDL -------------------------------------------------------

1. **ENUM, e nao VARCHAR ou tabela de dominio.** O dominio e FECHADO pela
   metodologia: `large`/`small`/`abs` sao as tres categorias que o documento
   define, e `unclassified` e a quarta afirmacao possivel ("nao sabemos"),
   que precisa existir para a coluna poder ser NOT NULL sem inventar porte
   para grupo nenhum. Valor novo aqui significaria metodologia nova - e uma
   metodologia nova muda a versao da metrica, nao so uma linha de dado. E o
   mesmo criterio que a 054 usou ao recusar ENUM para `volume_mode` (dominio
   ABERTO, valor novo de motor nao pode exigir DDL): aqui o dominio e o
   oposto, e por isso a escolha e a oposta.

2. **NOT NULL DEFAULT 'unclassified'** (e nao NULL). NULL e
   `'unclassified'` diriam a mesma coisa, e ter os dois criaria duas
   maneiras de escrever "nao classificado" que toda consulta teria de
   tratar. Com DEFAULT, grupo criado pelo CRUD ou pelo
   `_find_or_create_muscle_group` nasce explicitamente nao classificado, sem
   o INSERT precisar saber da coluna - que e o que faz a JANELA INVERSA
   (schema novo + codigo velho, entre a migracao e o deploy) funcionar sem
   nenhum cuidado extra.

3. **FK para `muscle_groups`** (auto-referencia), nao para `exercise_catalog`
   - a 028 proibe FK nova para aquela tabela. Esta e uma FK nova para uma
   tabela pequena, estavel e sem nada disso.

4. **ON DELETE SET NULL**, e nao o RESTRICT implicito. Apagar um grupo que e
   pai de fatias deve DESAGREGAR as fatias, nao ser recusado: a agregacao e
   metadado de leitura, nao integridade do negocio. E ha um caminho real de
   DELETE em `muscle_groups`: o proprio baseline `001_schema.sql` remove os
   grupos orfaos (`DELETE FROM muscle_groups WHERE id NOT IN (...)`); com
   RESTRICT, reaplicar a cadeia inteira sobre um banco ja migrado poderia
   falhar ali. Hoje a API nao expoe DELETE de grupo muscular (so
   GET/POST/PUT), entao a regra e uma garantia para o futuro, nao um
   comportamento em uso.

5. **`AFTER secondary_muscles`** posiciona as colunas junto das outras
   descritivas do grupo. Posicao de coluna e cosmetica e so pode ser
   cosmetica: `SELECT *` so aparece em `exercise_mapping_catalog.py`
   (`create_muscle_group`/`update_muscle_group`), que le o resultado por NOME
   de coluna (cursor de dicionario) e nao por indice - conferido antes de
   escolher `AFTER`. Todo o resto da aplicacao lista as colunas
   explicitamente.

6. **Indice proprio para a FK.** O InnoDB criaria um sozinho com o nome da
   constraint; nomea-lo aqui deixa o `plan_down` simetrico e o nome sob
   controle.

--- Seed ------------------------------------------------------------------

O seed e **por NOME**, `UPDATE ... WHERE name IN (...)`, e nome inexistente
simplesmente nao faz nada. Os nomes sao os que `002_reestruturacao.sql`
fixou ("tabela definitiva confirmada pelo usuario", secao 4 daquele arquivo,
UPDATE por id) - e NAO os nomes do backfill do `001_schema.sql`, que a 002
renomeou linha a linha. Semear pelos nomes antigos ('Costas medio',
'Peitoral medio/inferior', 'Ombros', ...) seria semear no vazio: eles nao
existem em nenhum banco que tenha passado pela 002, que e todo banco do
projeto. `test_migration_055_plano_aurora.py` amarra as duas pontas,
conferindo cada nome semeado contra o DDL da 002.

Porte, pela lista do documento (decisao 5):

  grandes : peitoral, dorsal (costas), deltoides (ombros), coxas
  pequenos: biceps, triceps, antebraco, trapezio, panturrilha, gluteo

`Abdomen` -> `abs`, que e a terceira categoria do documento.

Fica `unclassified` de proposito o que o documento **nao cobre**:
`Adutores`, `Lombar`, `Flexores do quadril`, `Manguito rotador` e `Core`.
Sobre `Core`: a decisao D4 da fase mandava classifica-lo como `abs` *se ele
fosse o abdomen do MuscleFit*. Ele nao e - `Abdomen` (id 1, 'Reto
abdominal') existe como grupo proprio desde a 002, e `Core` (id 55,
'Transverso do abdomen' + estabilizadores lombares) e o grupo de
ESTABILIZACAO. Classificar os dois como `abs` faria a agregacao somar como
abdomen series que sao de estabilizacao. Entao `Abdomen` -> `abs` e `Core`
-> `unclassified`, exatamente o ramo que a propria D4 previa.

Agregacao (decisao 6), fatia -> grupo que a representa:

  Costas - miolo/densidade  -> Costas - largura dorsal   (bloco "dorsal")
  Peitoral superior         -> Peitoral
  Antebraco posterior       -> Antebraco anterior
  Panturrilha anterior      -> Panturrilha posterior

Coxas ficam em DOIS blocos (decisao D3, recomendacao do plano): `Quadriceps`
e `Posterior de coxa / Isquiotibiais` sao prescritos separadamente na
pratica, entao nenhum dos dois aponta para o outro - os dois sao pai de si
mesmos (parent NULL). `Gluteos` tambem fica solto: o documento o lista como
grupo PEQUENO proprio, nao como fatia de coxa.

O pai de cada par e o grupo que carrega o musculo principal do bloco:
latissimo (largura dorsal) para as costas, peitoral maior para o peito,
flexores (anterior) para o antebraco, gastrocnemio/soleo (posterior) para a
panturrilha. A fatia herda o mesmo porte do pai, porque e o BLOCO que tem
porte.

**O seed respeita o que o admin editou**, e isso exigiu mais que um guard no
WHERE. Ele e uma populacao DE NASCIMENTO da coluna: so roda quando a coluna
ainda nao existe (deploy normal) ou quando ela existe e NINGUEM a tocou
ainda - nenhum grupo classificado e nenhum pai definido (aplicacao
interrompida entre o ALTER e o UPDATE). Nao e uma reconciliacao contra a
lista deste arquivo.

A razao e uma so, e e sobre o parent: `methodology_parent_group_id` NAO
consegue distinguir "nunca semeado" de "o admin DESAGREGOU esta fatia" -
`NULL` diz as duas coisas. Um `WHERE ... IS NULL`, sozinho, reimporia o pai a
cada reexecucao e desfaria a decisao do admin em silencio. Com `size_class`
o guard bastaria (ali "editado" tem valor proprio), mas duas regras
diferentes para as duas colunas da mesma migracao seriam piores que uma so.
Os guards do SQL continuam no lugar, como cinto e suspensorio.

--- Fluxo -----------------------------------------------------------------

Mesmo padrao da 047..054: `plan(db)` decide, `up(db)` executa o plano,
`plan_down`/`down` sao o inverso. Diferente da 052, o `plan` daqui le
tambem DADO (`SELECT id, name[, size_class][, parent] FROM muscle_groups`) -
tem de ler, porque o que resta semear depende do que ja foi semeado e do que
o admin mudou. Continua sem escrita nenhuma no plano.

- colunas ausentes       -> 1 ALTER + N UPDATEs de seed;
- estado convergido      -> 0 statements;
- estado parcial         -> so o que falta (coluna a coluna, seed a seed);
- coluna presente com outro tipo/nulabilidade, ENGINE != InnoDB ou
  `muscle_groups` ausente -> `MuscleGroupPorteError` ANTES do primeiro
  statement.

`plan_down` derruba FK, indice e as duas colunas. Reverter APAGA a
classificacao (o dado vive nas colunas); como nada le essas colunas ainda,
perde-se configuracao, nunca comportamento.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "muscle_groups"

COL_SIZE_CLASS = "size_class"
COL_PARENT = "methodology_parent_group_id"

FK_PARENT = "fk_mg_methodology_parent"
IDX_PARENT = "idx_mg_methodology_parent"

# O dominio fechado da metodologia. A ordem importa: e ela que aparece no
# `COLUMN_TYPE` do MySQL, e o teste compara a string inteira.
SIZE_CLASSES = ("large", "small", "abs", "unclassified")
SIZE_CLASS_DEFAULT = "unclassified"

SIZE_CLASS_SQL_TYPE = "ENUM(" + ", ".join(f"'{value}'" for value in SIZE_CLASSES) + ")"
SIZE_CLASS_COLUMN_TYPE = "enum(" + ",".join(f"'{value}'" for value in SIZE_CLASSES) + ")"

ADD_SIZE_CLASS_SQL = (
    f"ALTER TABLE {TABLE} ADD COLUMN {COL_SIZE_CLASS} {SIZE_CLASS_SQL_TYPE} "
    f"NOT NULL DEFAULT '{SIZE_CLASS_DEFAULT}' AFTER secondary_muscles"
)
ADD_PARENT_SQL = (
    f"ALTER TABLE {TABLE} ADD COLUMN {COL_PARENT} BIGINT NULL AFTER {COL_SIZE_CLASS}, "
    f"ADD KEY {IDX_PARENT} ({COL_PARENT}), "
    f"ADD CONSTRAINT {FK_PARENT} FOREIGN KEY ({COL_PARENT}) "
    f"REFERENCES {TABLE}(id) ON DELETE SET NULL"
)
ADD_COLUMNS_SQL = (
    f"ALTER TABLE {TABLE} "
    f"ADD COLUMN {COL_SIZE_CLASS} {SIZE_CLASS_SQL_TYPE} NOT NULL "
    f"DEFAULT '{SIZE_CLASS_DEFAULT}' AFTER secondary_muscles, "
    f"ADD COLUMN {COL_PARENT} BIGINT NULL AFTER {COL_SIZE_CLASS}, "
    f"ADD KEY {IDX_PARENT} ({COL_PARENT}), "
    f"ADD CONSTRAINT {FK_PARENT} FOREIGN KEY ({COL_PARENT}) "
    f"REFERENCES {TABLE}(id) ON DELETE SET NULL"
)

DROP_FK_SQL = f"ALTER TABLE {TABLE} DROP FOREIGN KEY {FK_PARENT}"
DROP_PARENT_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COL_PARENT}"
DROP_SIZE_CLASS_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COL_SIZE_CLASS}"
DROP_COLUMNS_SQL = (
    f"ALTER TABLE {TABLE} DROP COLUMN {COL_PARENT}, DROP COLUMN {COL_SIZE_CLASS}"
)

# --- Seed ------------------------------------------------------------------
#
# Nomes conforme `002_reestruturacao.sql` (secao 4). Nome que nao existir no
# banco nao faz nada - o `IN` simplesmente nao casa.

SEED_PORTE: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "large",
        (
            "Peitoral",
            "Peitoral superior",
            "Costas — largura dorsal",
            "Costas — miolo/densidade",
            "Ombros / Deltoides",
            "Quadríceps",
            "Posterior de coxa / Isquiotibiais",
        ),
    ),
    (
        "small",
        (
            "Bíceps",
            "Tríceps",
            "Antebraço anterior",
            "Antebraço posterior",
            "Panturrilha posterior",
            "Panturrilha anterior",
            "Glúteos",
        ),
    ),
    ("abs", ("Abdômen",)),
)

# (nome do pai, nomes das fatias que passam a somar nele).
SEED_AGREGACAO: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Costas — largura dorsal", ("Costas — miolo/densidade",)),
    ("Peitoral", ("Peitoral superior",)),
    ("Antebraço anterior", ("Antebraço posterior",)),
    ("Panturrilha posterior", ("Panturrilha anterior",)),
)

# Grupos que o documento NAO cobre e que ficam `unclassified` de proposito.
# Nao vira statement nenhum - o DEFAULT ja os deixa assim; a tupla existe
# para o teste afirmar a intencao e para quem ler o arquivo nao achar que
# foram esquecidos.
SEM_PORTE_NO_DOCUMENTO = (
    "Adutores",
    "Lombar",
    "Flexores do quadril",
    "Manguito rotador",
    "Core",
)


class MuscleGroupPorteError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise MuscleGroupPorteError(f"055: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # O MySQL devolve o ENUM sem espaco entre os valores; normalizar os dois
    # lados evita que uma diferenca de formatacao vire "tipo divergente".
    return "".join(_text(column_type).split()).lower()


def _read_schema(db):
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
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
    }
    indexes = set()
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
        (TABLE,),
    ):
        indexes.add(_text(row["INDEX_NAME"]))
    fks = set()
    for row in db.query(
        "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME "
        "FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s) "
        "AND REFERENCED_TABLE_NAME IS NOT NULL",
        (TABLE,),
    ):
        fks.add(_text(row["CONSTRAINT_NAME"]))
    return engines, columns, indexes, fks


def _check_table(engines, columns) -> None:
    if TABLE not in engines:
        _fail(f"tabela {TABLE} nao existe")
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")
    for name in ("id", "name", "secondary_muscles"):
        if name not in columns:
            _fail(f"{TABLE}.{name} nao existe")
    if _normalize_type(columns["id"]["COLUMN_TYPE"]) != "bigint":
        _fail(f"{TABLE}.id e {_text(columns['id']['COLUMN_TYPE'])}, esperado bigint")
    if _text(columns["id"]["COLUMN_KEY"]) != "PRI":
        _fail(f"{TABLE}.id nao e PRIMARY KEY")


def _check_size_class(columns) -> bool:
    info = columns.get(COL_SIZE_CLASS)
    if info is None:
        return False
    tipo = _normalize_type(info["COLUMN_TYPE"])
    if tipo != SIZE_CLASS_COLUMN_TYPE:
        _fail(f"{TABLE}.{COL_SIZE_CLASS} e {tipo}, esperado {SIZE_CLASS_COLUMN_TYPE}")
    if _text(info["IS_NULLABLE"]) != "NO":
        _fail(f"{TABLE}.{COL_SIZE_CLASS} aceita NULL, esperado NOT NULL")
    if _text(info["COLUMN_DEFAULT"]) != SIZE_CLASS_DEFAULT:
        _fail(
            f"{TABLE}.{COL_SIZE_CLASS} tem DEFAULT {_text(info['COLUMN_DEFAULT'])!r}, "
            f"esperado {SIZE_CLASS_DEFAULT!r}"
        )
    return True


def _check_parent(columns) -> bool:
    info = columns.get(COL_PARENT)
    if info is None:
        return False
    if _normalize_type(info["COLUMN_TYPE"]) != "bigint":
        _fail(f"{TABLE}.{COL_PARENT} e {_text(info['COLUMN_TYPE'])}, esperado bigint")
    if _text(info["IS_NULLABLE"]) != "YES":
        _fail(f"{TABLE}.{COL_PARENT} e NOT NULL, esperado NULL (NULL = o grupo ja e o bloco)")
    return True


def _rows(db, *, tem_size: bool, tem_parent: bool) -> list[dict]:
    """As linhas de `muscle_groups` que o seed precisa conhecer.

    Le so as colunas que EXISTEM: antes do ALTER, `size_class` e o parent nao
    estao la, e pedi-los seria erro de SQL. Quando nao existem, o estado deles
    e conhecido sem ler - acabaram de nascer no DEFAULT."""
    selecionadas = ["id", "name"]
    if tem_size:
        selecionadas.append(COL_SIZE_CLASS)
    if tem_parent:
        selecionadas.append(COL_PARENT)
    sql = f"SELECT {', '.join(selecionadas)} FROM {TABLE}"
    return list(db.query(sql, None))


def _seed_pendente(rows, *, tem_size: bool, tem_parent: bool) -> bool:
    """O seed e uma populacao DE NASCIMENTO da coluna, e nao uma reconciliacao
    contra a lista deste arquivo. So roda quando a coluna ainda nao existe
    (deploy normal) ou quando ela existe e NINGUEM a tocou ainda (aplicacao
    interrompida entre o ALTER e o UPDATE).

    Existe por uma razao concreta: `methodology_parent_group_id` nao consegue
    distinguir "nunca semeado" de "o admin DESAGREGOU esta fatia" - NULL diz
    as duas coisas. Um guard de `IS NULL` no SQL, sozinho, reimporia o pai a
    cada reexecucao e desfaria a decisao do admin. (Com `size_class` o guard
    basta, porque "editado" tem valor proprio; mas ter duas regras diferentes
    para as duas colunas da mesma migracao seria pior que uma so.) Tabela em
    que alguem ja mexeu nao e mais a tabela recem-migrada, e a migracao nao
    tem como saber mais que o admin sobre ela.

    Os guards do SQL continuam la, como cinto e suspensorio."""
    if not tem_size or not tem_parent:
        return True
    return all(
        _text(row[COL_SIZE_CLASS]) == SIZE_CLASS_DEFAULT and row[COL_PARENT] is None
        for row in rows
    )


def _seed_statements(rows, *, tem_size: bool, tem_parent: bool):
    por_nome = {_text(row["name"]): row for row in rows}
    statements: list[tuple[str, tuple | None]] = []

    for size_class, nomes in SEED_PORTE:
        alvos = [
            nome
            for nome in nomes
            if nome in por_nome
            and (not tem_size or _text(por_nome[nome][COL_SIZE_CLASS]) == SIZE_CLASS_DEFAULT)
        ]
        if not alvos:
            continue
        placeholders = ", ".join(["%s"] * len(alvos))
        statements.append(
            (
                f"UPDATE {TABLE} SET {COL_SIZE_CLASS} = %s "
                f"WHERE {COL_SIZE_CLASS} = %s AND name IN ({placeholders})",
                (size_class, SIZE_CLASS_DEFAULT, *alvos),
            )
        )

    for nome_pai, fatias in SEED_AGREGACAO:
        pai = por_nome.get(nome_pai)
        if pai is None:
            # Pai renomeado ou inexistente: as fatias ficam sem agregacao, que
            # e o estado honesto - e o admin resolve pela UI.
            continue
        pai_id = int(pai["id"])
        alvos = [
            nome
            for nome in fatias
            if nome in por_nome
            and int(por_nome[nome]["id"]) != pai_id
            and (not tem_parent or por_nome[nome][COL_PARENT] is None)
        ]
        if not alvos:
            continue
        placeholders = ", ".join(["%s"] * len(alvos))
        # `id <> %s` e cinto e suspensorio contra ciclo trivial (grupo pai de
        # si mesmo) caso dois nomes desta lista apontem para a mesma linha.
        statements.append(
            (
                f"UPDATE {TABLE} SET {COL_PARENT} = %s "
                f"WHERE {COL_PARENT} IS NULL AND id <> %s AND name IN ({placeholders})",
                (pai_id, pai_id, *alvos),
            )
        )

    return statements


def plan(db) -> list[tuple[str, tuple | None]]:
    """Le o schema e os nomes dos grupos; devolve [(sql, params)] na ordem."""
    engines, columns, indexes, fks = _read_schema(db)
    _check_table(engines, columns)

    tem_size = _check_size_class(columns)
    tem_parent = _check_parent(columns)

    statements: list[tuple[str, tuple | None]] = []
    if not tem_size and not tem_parent:
        # Caminho normal (producao hoje): um unico ALTER, uma unica
        # reconstrucao da tabela.
        statements.append((ADD_COLUMNS_SQL, None))
    else:
        if not tem_size:
            statements.append((ADD_SIZE_CLASS_SQL, None))
        if not tem_parent:
            statements.append((ADD_PARENT_SQL, None))
        elif FK_PARENT not in fks:
            # Coluna sem a FK (aplicacao interrompida no meio): o indice pode
            # ou nao existir, entao cada peca entra isolada.
            if IDX_PARENT not in indexes:
                statements.append((f"ALTER TABLE {TABLE} ADD KEY {IDX_PARENT} ({COL_PARENT})", None))
            statements.append(
                (
                    f"ALTER TABLE {TABLE} ADD CONSTRAINT {FK_PARENT} FOREIGN KEY ({COL_PARENT}) "
                    f"REFERENCES {TABLE}(id) ON DELETE SET NULL",
                    None,
                )
            )

    rows = _rows(db, tem_size=tem_size, tem_parent=tem_parent)
    if _seed_pendente(rows, tem_size=tem_size, tem_parent=tem_parent):
        statements.extend(_seed_statements(rows, tem_size=tem_size, tem_parent=tem_parent))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: derruba FK, indice e colunas. O seed nao e desfeito
    por statement nenhum - ele vive NAS colunas, e some com elas."""
    engines, columns, indexes, fks = _read_schema(db)
    if TABLE not in engines:
        return []

    tem_size = COL_SIZE_CLASS in columns
    tem_parent = COL_PARENT in columns

    statements: list[tuple[str, tuple | None]] = []
    if FK_PARENT in fks:
        statements.append((DROP_FK_SQL, None))
    if tem_parent and tem_size:
        # O indice cai junto com a coluna (e o unico que a cobre).
        statements.append((DROP_COLUMNS_SQL, None))
    elif tem_parent:
        statements.append((DROP_PARENT_SQL, None))
    elif tem_size:
        statements.append((DROP_SIZE_CLASS_SQL, None))
    elif IDX_PARENT in indexes:
        # Indice orfao (coluna ja removida a mao): limpa mesmo assim.
        statements.append((f"ALTER TABLE {TABLE} DROP INDEX {IDX_PARENT}", None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
