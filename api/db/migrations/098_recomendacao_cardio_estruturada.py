"""098 - Recomendação de cardio estruturada (M98 C3a; `cardio-agent-v3`).

    cardio_recommendations
      + recommendation_json  JSON NULL          saída validada do agente v3
                                                (sessões, prescrição do dono,
                                                envelope, vínculo com o Modelo)
      + prompt_version       VARCHAR(40) NULL   versão do prompt que gerou a linha

Por que: o agente v3 devolve JSON estruturado (contrato em
`app/cardio_recommendation_v3.py`), validado por `domain/cardio_prescription.py`
e filtrado pelo envelope de `domain/cardio_envelope.py`. A C3b ("aplicar no
Modelo") lê `recommendation_json.sessions[].plan_link` para gravar a sessão num
RASCUNHO do Modelo. `recommendation_markdown` (NOT NULL) continua sendo gravado
(gerado do JSON): a tela atual e o histórico seguem legíveis.
Plano: `docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md` §C3.

--- Decisões do DDL -------------------------------------------------------

1. **NULL, sem default.** As 27 linhas de produção (v1/v2, só markdown) ficam
   com as duas colunas NULL = "recomendação antiga" (lida como markdown).
2. **Um `ALTER TABLE ... ADD COLUMN ..., ADD COLUMN ..., ALGORITHM=INSTANT`.**
   Só metadado (MySQL 8.0 / Aurora MySQL 3, colunas no fim), uma só versão de
   linha (`INNODB_TABLES.TOTAL_ROW_VERSIONS`, limite 64; em produção = 0). O
   ALGORITHM explícito falha em vez de copiar a tabela em silêncio.
3. **Nenhuma FK, nenhum índice, nenhum backfill.** Nenhuma leitura filtra pelas
   colunas; a listagem só lê `prompt_version` (pequena), o detalhe lê o JSON.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` são o inverso (o runner de produção não tem ação
`down`: em produção, reverter é uma migração nova; `down` serve ao DEV e aos
testes).

- colunas ausentes -> 1 ALTER com as que faltam;
- colunas presentes com o tipo/nulabilidade esperados -> 0 (idempotente);
- coluna presente com outra forma -> `RecomendacaoCardioError` antes de qualquer
  statement;
- tabela ausente: erro numa execução real; aceito em DRY-RUN (lista o ALTER).

`down`: `ALTER TABLE cardio_recommendations DROP COLUMN ...` das que existirem
(perde o JSON; o markdown fica).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':' (viraria parâmetro no shim); módulo autocontido (não
importa código do app).
"""

from __future__ import annotations

TABLE = "cardio_recommendations"
# (nome, DDL, tipo normalizado esperado), na ordem do ALTER.
COLUMNS = (
    ("recommendation_json", "JSON NULL DEFAULT NULL", "json"),
    ("prompt_version", "VARCHAR(40) NULL DEFAULT NULL", "varchar(40)"),
)


class RecomendacaoCardioError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise RecomendacaoCardioError(f"098: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return "".join(_text(column_type).split()).lower()


def add_sql(names) -> str:
    ddl = {name: column_ddl for name, column_ddl, _ in COLUMNS}
    parts = ", ".join(f"ADD COLUMN {name} {ddl[name]}" for name in names)
    return f"ALTER TABLE {TABLE} {parts}, ALGORITHM=INSTANT"


def drop_sql(names) -> str:
    return f"ALTER TABLE {TABLE} " + ", ".join(f"DROP COLUMN {name}" for name in names)


def _read(db):
    """(tabela existe?, {coluna: linha do information_schema})."""
    exists = bool(
        list(
            db.query(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (TABLE,),
            )
        )
    )
    rows = list(
        db.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (TABLE,),
        )
    )
    wanted = {name for name, _, _ in COLUMNS}
    return exists, {_text(row["COLUMN_NAME"]): row for row in rows if _text(row["COLUMN_NAME"]) in wanted}


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, found = _read(db)
    if not exists:
        if not bool(getattr(db, "dry_run", False)):
            _fail(f"tabela {TABLE} nao existe")
        return [(add_sql([name for name, _, _ in COLUMNS]), None)]
    missing = []
    for name, _, expected in COLUMNS:
        info = found.get(name)
        if info is None:
            missing.append(name)
            continue
        actual = _normalize_type(info["COLUMN_TYPE"])
        if actual != expected:
            _fail(f"{TABLE}.{name} e {actual}, esperado {expected}")
        if _text(info["IS_NULLABLE"]) != "YES":
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado YES")
    return [(add_sql(missing), None)] if missing else []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta as colunas que existirem."""
    exists, found = _read(db)
    present = [name for name, _, _ in COLUMNS if name in found]
    if not exists or not present:
        return []
    return [(drop_sql(present), None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
