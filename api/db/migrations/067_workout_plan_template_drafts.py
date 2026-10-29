"""067 - rascunho do Modelo de Treino ("Salvar rascunho" x "Publicar modelo").

    workout_plan_template_drafts                      (NOVA)
      template_id         BIGINT NOT NULL PK -> workout_plan_templates(id) ON DELETE CASCADE
      payload             JSON NOT NULL        o MESMO corpo que o PUT do Modelo aceita
                                               (nome, descricao, cabecalho, grade, slots, itens)
      updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      updated_by_user_id  BIGINT NULL          quem salvou por ultimo (ref. LOGICA a users, SEM FK)

Por que: o profissional quer ir salvando o Modelo enquanto ajusta, sem criar
uma versao a cada salvamento. "Salvar rascunho" grava AQUI; "Publicar modelo"
e o PUT de sempre (validacao completa + versao nova quando ha mudanca
estrutural) e apaga o rascunho na mesma transacao.

--- Decisoes do DDL -------------------------------------------------------

1. **Um rascunho por Modelo** (`template_id` e a PK), compartilhado por quem
   pode editar o Modelo. Sobrescrever e um `INSERT ... ON DUPLICATE KEY
   UPDATE` - sem historico de rascunhos (o historico oficial sao as versoes).

2. **`payload` JSON, opaco para o banco.** O rascunho aceita estado
   incompleto (item sem exercicio, slot vazio, nome em branco) - nada dele e
   normalizado em tabelas, e NENHUM leitor (motor de geracao, dashboards,
   ficha, relatorios) le esta tabela: a versao vigente continua sendo a unica
   oficial. `tests/unit/test_template_drafts.py` prova que so o modulo do
   rascunho e as rotas do Modelo citam a tabela.

3. **FK so para `workout_plan_templates`, com CASCADE.** Nenhuma FK para
   `exercise_catalog` (a 028 enumera as FKs validas daquela tabela e recusa
   novas): os ids de exercicio vivem dentro do JSON, sem constraint.
   `updated_by_user_id` tambem sem FK, mesmo motivo da 064 - apagar um
   usuario nao pode apagar nem bloquear o rascunho de um Modelo.

4. **Sem DML, sem backfill.** A tabela nasce vazia: nenhum Modelo tem
   rascunho. Codigo antigo nao conhece a tabela e segue igual (aditiva).

Dependencias: `workout_plan_templates` (007). Nenhuma migracao recente e
pre-requisito.

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- tabela ausente -> 1 `CREATE TABLE`;
- tabela presente no formato esperado -> nada (reexecucao: 0 statements);
- `workout_plan_templates` ausente ou nao InnoDB, `id` que nao e BIGINT, ou
  tabela presente com coluna/tipo/nulabilidade diferente ->
  `TemplateDraftsError` ANTES do primeiro statement.

Aurora (sem a tabela) -> 1 statement. Reexecucao: 0. `down` -> `DROP TABLE`
(reversivel: sem a tabela, o codigo anterior a esta migracao nao tem o que ler;
o codigo novo trata a ausencia como "sem rascunho").

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "workout_plan_template_drafts"
TEMPLATES_TABLE = "workout_plan_templates"
FK_TEMPLATE = "fk_wptd_template"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  template_id BIGINT NOT NULL PRIMARY KEY,
  payload JSON NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_by_user_id BIGINT NULL,
  CONSTRAINT {FK_TEMPLATE} FOREIGN KEY (template_id) REFERENCES {TEMPLATES_TABLE}(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_TABLE_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "template_id": ("bigint", "NO"),
    "payload": ("json", "NO"),
    "updated_at": ("datetime", "NO"),
    "updated_by_user_id": ("bigint", "YES"),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class TemplateDraftsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise TemplateDraftsError(f"067: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `bigint(20)` -> `bigint`
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db) -> tuple[dict[str, str], dict[tuple[str, str], dict]]:
    names = (TABLE, TEMPLATES_TABLE)
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


def _check_parent(engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
    """`workout_plan_templates` existe, e InnoDB e (quando o recorte traz a
    coluna) tem `id` BIGINT - o tipo que `template_id` copia para a FK."""
    if TEMPLATES_TABLE not in engines:
        _fail(f"tabela {TEMPLATES_TABLE} nao existe")
    if engines[TEMPLATES_TABLE].lower() != "innodb":
        _fail(f"{TEMPLATES_TABLE} usa ENGINE={engines[TEMPLATES_TABLE]}, esperado InnoDB")
    info = columns.get((TEMPLATES_TABLE, "id"))
    if info is not None and _normalize_type(info["COLUMN_TYPE"]) != "bigint":
        _fail(f"{TEMPLATES_TABLE}.id e {_text(info['COLUMN_TYPE'])}, esperado bigint")


def _check_existing(engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS.items():
        info = columns.get((TABLE, name))
        if info is None:
            _fail(f"{TABLE}.{name} nao existe")
        # MariaDB devolve JSON como `longtext`; o projeto so roda MySQL 8
        # (local e Aurora 3), onde o tipo e `json`.
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns = _read(db)
    _check_parent(engines, columns)
    if TABLE in engines:
        _check_existing(engines, columns)
        return []
    return [(CREATE_SQL, None)]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: DROP TABLE se a tabela existe."""
    engines, _columns = _read(db)
    return [(DROP_TABLE_SQL, None)] if TABLE in engines else []


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
