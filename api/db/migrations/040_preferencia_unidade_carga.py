"""040 - unidade de carga preferida do usuario (melhoria M3 do backlog).

    users.preferred_weight_unit  ENUM('kg','lb') NOT NULL DEFAULT 'kg'
                                 AFTER is_advanced_user

A preferencia e do USUARIO (nao do equipamento nem da academia): ele pode
anotar a carga em quilos ou em libras no Modo Treino, e a tela converte antes
de enviar. O BANCO NAO MUDA DE SEMANTICA: `exercise_sets.weight_kg` continua
em QUILOS e continua sendo carga TOTAL (decisao D12). Esta coluna e so de
APRESENTACAO/ENTRADA - nenhum motor (progressao, volume, relatorios, IA) a le.

DEFAULT 'kg': toda linha existente passa a significar exatamente o que ja
significava hoje (todo mundo usa quilos), entao nenhum usuario muda de
comportamento com o deploy desta migracao. E a unica coluna NOT NULL do lote
justamente porque o default cobre 100% das linhas ja gravadas.

ENUM e nao VARCHAR porque o dominio e FECHADO por definicao fisica: existem
exatamente duas unidades de massa em uso no treinamento de forca, e uma
terceira nao apareceria sem mudanca de codigo de qualquer forma (o fator de
conversao e uma constante do modulo `app/weight_units.py` / `lib/weight-units.ts`).
Um valor novo aqui exigiria DDL - e correto que exija.

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements;
`up(db)` executa o plano.

- coluna ausente -> exatamente 1 `ALTER TABLE users ADD COLUMN ...`;
- coluna presente no formato esperado -> nada (estado convergido);
- tabela `users` ausente, ENGINE != InnoDB, ou coluna presente com tipo/
  nulabilidade/default diferente -> `PreferenciaUnidadeCargaError` ANTES do
  primeiro statement.

Numeracao: esta migracao nasceu como 039 e virou 040 porque a 039
(`039_descricao_execucao_exercicio.py`, colunas aditivas em `exercise_catalog`)
foi entregue em paralelo e entra antes. As duas sao INDEPENDENTES - tabelas
diferentes, sem ordem obrigatoria entre elas; o numero so precisa ser unico
(o runner recusa versao duplicada, ver `discover_migrations`).

Aurora hoje (recorte real, somente leitura, 2026-09-19,
tests/unit/data/aurora_schema_2026_09_19_038.json - `users` vai ate
`updated_at` e NAO tem `preferred_weight_unit`) -> 1 statement. O recorte e de
antes da 039, mas isso nao muda nada aqui: a 039 nao toca `users`, a unica
tabela que esta migracao le ou escreve.
Reexecucao: 0 statements.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "users"
COLUMN = "preferred_weight_unit"
AFTER_COLUMN = "is_advanced_user"

COLUMN_DDL = "ENUM('kg','lb') NOT NULL DEFAULT 'kg'"
ALTER_SQL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_DDL} AFTER {AFTER_COLUMN}"

EXPECTED_TYPE = "enum('kg','lb')"
EXPECTED_NULLABLE = "NO"
EXPECTED_DEFAULT = "kg"


class PreferenciaUnidadeCargaError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise PreferenciaUnidadeCargaError(f"040: {message}. Nenhum statement executado.")


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
            (TABLE,),
        )
    }
    if TABLE not in engines:
        _fail(f"tabela {TABLE} nao existe")
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")

    columns = {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
        if _text(row["TABLE_NAME"]) == TABLE
    }

    # A coluna e posicionada com AFTER; se a ancora sumir (schema divergente),
    # o ALTER falharia no meio do deploy - melhor falhar antes de executar.
    if AFTER_COLUMN not in columns:
        _fail(f"{TABLE}.{AFTER_COLUMN} nao existe (ancora do AFTER)")

    info = columns.get(COLUMN)
    if info is None:
        return [(ALTER_SQL, None)]

    actual_type = _text(info["COLUMN_TYPE"]).strip().lower().replace(", ", ",")
    if actual_type != EXPECTED_TYPE:
        _fail(f"{TABLE}.{COLUMN} e {_text(info['COLUMN_TYPE'])}, esperado {EXPECTED_TYPE}")
    if _text(info["IS_NULLABLE"]) != EXPECTED_NULLABLE:
        _fail(f"{TABLE}.{COLUMN} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    if _text(info["COLUMN_DEFAULT"]) != EXPECTED_DEFAULT:
        _fail(f"{TABLE}.{COLUMN} tem DEFAULT={info['COLUMN_DEFAULT']!r}, esperado {EXPECTED_DEFAULT!r}")
    return []


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)
