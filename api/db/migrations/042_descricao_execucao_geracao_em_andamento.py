"""042 - marca de "geracao da descricao de execucao EM ANDAMENTO" no catalogo
(M4: geracao assincrona no primeiro uso do briefing).

UMA coluna nova em `exercise_catalog`, aditiva e NULLABLE (nenhuma coluna
existente e tocada, nenhuma linha muda de comportamento):

    execution_description_generation_started_at  DATETIME NULL
        quando a geracao automatica desta descricao foi ENFILEIRADA (a
        api-core disparou a Lambda). NULL = ninguem esta gerando agora.

Por que uma coluna e nao um lock na aplicacao: o briefing roda em
`musclefit-api-core`, uma Lambda com N instancias concorrentes e sem memoria
compartilhada. Dois usuarios abrindo o mesmo exercicio no mesmo segundo caem
em dois processos diferentes. O unico ponto que os dois enxergam e o banco,
e a reserva e feita com UM `UPDATE ... WHERE
execution_description_generation_started_at IS NULL OR < <cutoff>`: o MySQL
serializa o UPDATE na linha, entao exatamente UM dos dois ve `rowcount = 1` e
dispara a geracao. O outro ve 0 e nao faz nada. Ver
`app/exercise_description_service.claim_generation`.

Por que DATETIME e nao BOOLEAN/TINYINT: um booleano que vaza (Lambda morta por
timeout, deploy no meio, erro nao previsto) prende o exercicio PARA SEMPRE -
nunca mais haveria geracao automatica para ele, e ninguem perceberia. Com o
carimbo de tempo a reserva expira sozinha (`GENERATION_LOCK_MINUTES`), e a
proxima abertura do briefing tenta de novo. A mesma razao pela qual o lock do
proprio runner de migracoes e um lease com expiracao, e nao `GET_LOCK()`.

Sem indice novo: a coluna so e lida/escrita por `id` (a reserva de UMA linha)
e na varredura da geracao em lote, que ja e um scan de algumas centenas de
linhas do catalogo - o mesmo racional da 039.

DATETIME e nao TIMESTAMP, como todas as colunas de auditoria da 039: e um
marco, nao "ultima alteracao da linha" (`updated_at` ja cobre isso), e
TIMESTAMP em MySQL 8 carrega default/ON UPDATE implicitos indesejados aqui.

Depende da 039 (as colunas `execution_description*` precisam existir para a
reserva fazer sentido): numa execucao REAL, se `execution_description` nao
estiver la, esta migracao FALHA antes de executar qualquer statement, em vez
de criar uma coluna solta.

`execution_description` ausente em DRY-RUN e outra historia. Num banco novo o
runner planeja o LOTE inteiro de uma vez sem executar nada, entao a 039, ainda
pendente, nao chegou a criar a coluna quando o `plan()` da 042 roda: exigi-la
ali abortaria o plano do lote inteiro por um estado que nao e o real. Em
dry-run, portanto, a ausencia e aceita e o `ALTER` aparece no plano; o formato
e conferido de novo na execucao real, que e quando a 039 ja rodou. Mesma regra
que a 033 usa com `advanced_techniques` (criada pela 031) e a 041 com
`intervals_activities` (criada pela 027).

A tolerancia vale SO para a coluna da 039. `exercise_catalog` vem do baseline
(002/028): se a TABELA nao existe, o banco nao e o que esta migracao espera, e
ela falha inclusive em dry-run.

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements; `up(db)`
executa o plano.

- coluna ausente -> exatamente 1 `ALTER TABLE exercise_catalog ADD COLUMN`;
- ja presente no formato esperado -> 0 statements (estado convergido);
- tabela ausente, ENGINE != InnoDB, 039 nao aplicada (fora do dry-run), ou
  coluna presente com tipo/nulabilidade/default diferente ->
  `GeracaoEmAndamentoError` ANTES do primeiro statement.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app nem usa classes de dados).
"""

from __future__ import annotations

CATALOG = "exercise_catalog"

COLUMN = "execution_description_generation_started_at"
COLUMN_DDL = "DATETIME NULL"
EXPECTED_TYPE = "datetime"
EXPECTED_NULLABLE = "YES"

# Pre-requisito vindo da 039: sem o texto, nao ha o que reservar. Nao vem do
# baseline, entao pode estar pendente no MESMO lote - ver docstring.
REQUIRED_COLUMN = "execution_description"


ALTER_SQL = f"ALTER TABLE {CATALOG} ADD COLUMN {COLUMN} {COLUMN_DDL}"


class GeracaoEmAndamentoError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise GeracaoEmAndamentoError(f"042: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (CATALOG,),
        )
    }
    if CATALOG not in engines:
        _fail(f"tabela {CATALOG} nao existe")
    if engines[CATALOG].lower() != "innodb":
        _fail(f"{CATALOG} usa ENGINE={engines[CATALOG]}, esperado InnoDB")

    existing = {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (CATALOG,),
        )
    }

    if REQUIRED_COLUMN not in existing and not bool(getattr(db, "dry_run", False)):
        _fail(f"{CATALOG}.{REQUIRED_COLUMN} nao existe (migracao 039 nao aplicada)")

    info = existing.get(COLUMN)
    if info is None:
        return [(ALTER_SQL, None)]

    label = f"{CATALOG}.{COLUMN}"
    if _text(info["COLUMN_TYPE"]).strip().lower() != EXPECTED_TYPE:
        _fail(f"{label} e {_text(info['COLUMN_TYPE'])}, esperado {EXPECTED_TYPE}")
    if _text(info["IS_NULLABLE"]) != EXPECTED_NULLABLE:
        _fail(f"{label} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    if info["COLUMN_DEFAULT"] is not None:
        _fail(f"{label} tem DEFAULT {_text(info['COLUMN_DEFAULT'])!r}, esperado NULL")
    return []


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)
