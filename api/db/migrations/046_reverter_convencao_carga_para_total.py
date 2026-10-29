"""046 - reverte `equipment_exercises.load_convention` de 'per_side'/'per_dumbbell'
para 'total' (correcao de CADASTRO, nao de dado de execucao).

    UPDATE equipment_exercises SET load_convention = 'total'
     WHERE load_convention IN ('per_side','per_dumbbell')

Por que: a coluna criada pela 045 descreve COMO O VALOR FOI REGISTRADO, e nao
como o aparelho funciona fisicamente. Todo o historico de
`exercise_sets.weight_kg` foi - e continua sendo - gravado em carga TOTAL em
quilos (decisao D12, documentada em `app/weight_units.py`). Os pares marcados
a mao em producao como 'per_side' (plate-loaded) e 'per_dumbbell' (halteres)
descreviam o aparelho, nao o registro: para esses pares a convencao correta e
'total'.

O que quebraria se ficassem como estao: 'per_side' e 'per_dumbbell' NAO
convertem valor nenhum (ver `app/intelligence/dashboard/comparability.py`),
mas entram na `comparability_key` e no rotulo da tela de exercicio. O rotulo
("Por lado" / "Por halter") passaria a mentir sobre um numero que e total; e
se algum dia o registro mudasse de fato, o sistema leria uma regressao de 50%
que nunca existiu.

Alternativa avaliada e DESCARTADA: dividir a carga historica por 2. Afetaria o
motor de progressao, seria desfeita pela proxima reimportacao, e falsa em
exercicios unilaterais (um halter de cada vez) e, acima de tudo, reescreveria
dado OBSERVADO para acomodar um erro de cadastro. Por isso esta migracao NAO
toca em `exercise_sets` - nenhuma linha de execucao muda.

Idempotencia: em duas camadas. O runner (`app.migrate`) registra a versao em
`schema_migration_files` e nunca reexecuta o arquivo; e mesmo que alguem rode
o UPDATE de novo a mao, o `WHERE` ja nao encontra linha nenhuma (os valores
viraram 'total'). Rodar duas vezes e inofensivo.

Coluna ausente: se `equipment_exercises.load_convention` nao existir (banco
anterior a 045), a migracao e um NO-OP com aviso, e nao um erro. Na pratica o
runner sempre aplica a 045 antes da 046; a guarda existe para deixar o
comportamento explicito em banco restaurado de backup antigo.

`down`: IRREVERSIVEL POR NATUREZA. Depois do UPDATE nao ha como saber quais
pares eram 'per_side' e quais eram 'per_dumbbell' - a informacao foi fundida
em 'total'. `down`/`plan_down` sao um no-op explicito (0 statements): fingir
uma reversao, escolhendo um dos dois valores, produziria cadastro errado. O
caminho de volta, se um dia for desejado, e recadastrar pela tela
`/equipamentos-exercicios`.

Fluxo (mesmo padrao da 045): `plan(db)` so LE `information_schema` e devolve
os statements; `up(db)` executa o plano e loga o `rowcount`; erro tipado
(`ConvencaoCargaError`) ANTES do primeiro statement.

- coluna presente com o ENUM esperado -> exatamente 1 `UPDATE`;
- coluna ausente -> 0 statements (aviso no log);
- tabela ausente, ENGINE != InnoDB, ou coluna presente sem 'total'/'per_side'/
  'per_dumbbell' no ENUM -> `ConvencaoCargaError` antes de qualquer statement.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("fitness.migrate")

TABLE = "equipment_exercises"
COLUMN = "load_convention"

# Convencoes que descreviam o APARELHO e nao o REGISTRO: viram 'total'.
CONVENCOES_REVERTIDAS = ("per_side", "per_dumbbell")
CONVENCAO_ALVO = "total"

_VALORES_SQL = ",".join(f"'{value}'" for value in CONVENCOES_REVERTIDAS)
UPDATE_SQL = (
    f"UPDATE {TABLE} SET {COLUMN} = '{CONVENCAO_ALVO}' WHERE {COLUMN} IN ({_VALORES_SQL})"
)

# Valores que o ENUM da coluna precisa conhecer para o UPDATE fazer sentido.
VALORES_EXIGIDOS = (CONVENCAO_ALVO,) + CONVENCOES_REVERTIDAS


class ConvencaoCargaError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise ConvencaoCargaError(f"046: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read_columns(db) -> dict:
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
    }
    if TABLE not in engines:
        _fail(f"tabela {TABLE} nao existe")
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")

    return {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
        if _text(row["TABLE_NAME"]) == TABLE
    }


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    columns = _read_columns(db)
    info = columns.get(COLUMN)
    if info is None:
        # Banco anterior a 045: nao ha convencao cadastrada para corrigir.
        logger.warning(
            "046: %s.%s nao existe (banco anterior a 045); nada a corrigir.", TABLE, COLUMN
        )
        return []

    tipo = _text(info["COLUMN_TYPE"]).strip().lower()
    faltando = [valor for valor in VALORES_EXIGIDOS if f"'{valor}'" not in tipo]
    if faltando:
        _fail(
            f"{TABLE}.{COLUMN} e {_text(info['COLUMN_TYPE'])}, "
            f"sem o(s) valor(es) {', '.join(faltando)} exigido(s) pelo UPDATE"
        )
    return [(UPDATE_SQL, None)]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """NO-OP deliberado: a 046 e IRREVERSIVEL.

    Depois do UPDATE, 'per_side' e 'per_dumbbell' estao fundidos em 'total' e
    nao ha como distinguir um do outro. Nao existe reversao automatica
    honesta; recadastrar pela tela e o unico caminho de volta.
    """
    return []


def up(db):
    for sql, params in plan(db):
        afetadas = db.execute(sql, params)
        logger.info("046: %s par(es) equipamento<->exercicio revertido(s) para '%s'.", afetadas, CONVENCAO_ALVO)


def down(db):
    # Intencionalmente vazio: ver `plan_down`.
    for sql, params in plan_down(db):
        db.execute(sql, params)
