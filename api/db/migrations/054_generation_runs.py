"""054 - `generation_runs`: a auditoria de COMO uma ficha foi gerada passa a
ter casa propria (M16; pre-requisito da F3.1, a tela
`/dashboard/engine`: planejado x gerado x executado).

    generation_runs
      id                     BIGINT AUTO_INCREMENT PK
      user_id                BIGINT NOT NULL -> users(id) ON DELETE CASCADE
      workout_plan_id        BIGINT NULL -> workout_plans(id) ON DELETE SET NULL
                                             ficha que NASCEU deste rascunho;
                                             NULL = rascunho gerado e nao salvo
      job_id                 VARCHAR(36) NULL   job do DynamoDB que produziu a
                                                geracao (expira em 6h)
      template_id            BIGINT NULL        FK LOGICA (indice, sem constraint)
      template_slot_sequence INT NULL
      volume_mode            VARCHAR(40) NULL   'percent_budget'|'equipment_series'|...
      engine_version         VARCHAR(40) NULL
      llm_used               TINYINT(1) NOT NULL DEFAULT 0
      plan_date              DATE NULL          dia a que o rascunho se refere
      audit_hash             CHAR(64) NOT NULL  sha256 do JSON canonico do
                                                `generation_audit` - e o que
                                                liga o rascunho a ficha salva
      audit_json             JSON NOT NULL      o `generation_audit` INTEIRO,
                                                verbatim
      request_json           JSON NULL          payload que originou a geracao
                                                (o `request` do job do DynamoDB)
      created_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      KEY (user_id, created_at)      historico do usuario (leitura da F3.1)
      KEY (workout_plan_id)          "como esta ficha foi gerada?"
      KEY (user_id, audit_hash)      a ligacao rascunho -> ficha, no save
      KEY (template_id, created_at)  "o que este Modelo vem gerando?"

O problema que a tabela resolve: hoje o registro de como uma ficha foi gerada
vive em DOIS lugares efemeros. (a) `workout_plans.raw_payload.generation_audit`
- que so existe se o cliente devolver o `raw_payload` no POST de save, e so
para o rascunho que o usuario resolveu salvar; (b) o item do job no DynamoDB
(`app/async_jobs.py`, `ttl = agora + 6h`). Rascunho gerado e descartado nao
deixa rastro nenhum depois de seis horas, e rascunho salvo por um cliente que
nao ecoe o `raw_payload` tambem nao. A F3.1 quer justamente a serie historica
disso.

Decisoes do DDL, nenhuma obvia:

1. **JSON inteiro + um punhado de colunas promovidas** (e nao "uma coluna por
   campo da auditoria"). O `generation_audit` NAO tem forma unica: o Modo A
   grava `window`, `activation`, `selection`, `safety_clamps`, `substitutions`,
   `fallbacks`; o Modo B grava um subconjunto. Normalizar isso em colunas
   seria congelar, em DDL, uma forma que os motores ainda mudam - e cada
   mudanca de motor viraria uma migracao. Pior: seria uma SEGUNDA verdade
   sobre o mesmo conteudo, que pode divergir do bloco que continua indo para o
   `raw_payload`. Entao `audit_json` guarda o bloco VERBATIM (a fonte), e so
   sobem para coluna os campos pelos quais a F3.1 vai FILTRAR ou JUNTAR:
   `user_id`, `workout_plan_id`, `template_id`, `template_slot_sequence`,
   `volume_mode`, `engine_version`, `llm_used`, `plan_date`, `created_at`.
   Contagens derivadas (quantos fallbacks, quantas substituicoes) NAO viram
   coluna de proposito: sao deriváveis do JSON na leitura e, promovidas,
   seriam mais um numero que pode divergir do JSON ao lado.

2. **`audit_hash` e a ligacao, e ela existe porque o formato do
   `generation_audit` nao muda neste PR.** A geracao acontece na Lambda e
   devolve um rascunho; a ficha so nasce depois, se o usuario salvar (`POST
   /workout-plans`). Carimbar um `generation_run_id` DENTRO do bloco de
   auditoria seria mudar o formato do bloco - exatamente o que o M16 proibe
   (mudar forma e destino no mesmo PR torna impossivel saber o que quebrou).
   O sha256 do JSON canonico do bloco identifica a mesma auditoria dos dois
   lados sem tocar em um byte dela: a geracao grava o hash, o save recalcula o
   hash do `raw_payload.generation_audit` e marca a linha. Nao e UNIQUE - duas
   geracoes podem, em tese, produzir auditorias identicas, e um UNIQUE ai
   faria a segunda geracao FALHAR; a ligacao pega a linha mais antiga ainda
   sem ficha.

3. **`workout_plan_id` com FK fisica ON DELETE SET NULL** (mesmo alvo e mesma
   regra que a 025 ja usa em `cardio_recommendations` - a FK para
   `workout_plans(id) ON DELETE SET NULL` esta provada em producao). Apagar a
   ficha nao apaga o fato de que a geracao aconteceu: a linha sobrevive,
   desligada.

4. **`template_id` com FK LOGICA** (indice proprio, sem constraint fisica),
   pela mesma razao que a 038 usou em `workout_plan_templates.generation_log_id`:
   este e um registro HISTORICO. Com `ON DELETE SET NULL`, apagar um Modelo
   apagaria o agrupamento "o que este Modelo gerou" de todo o historico; com
   `ON DELETE CASCADE`, apagaria o historico inteiro. Guardar o id mesmo orfao
   e o que preserva a serie. O mesmo NAO vale para `user_id`: sem usuario nao
   ha o que auditar, entao ali a FK e fisica e CASCADE.

5. **`llm_used` TINYINT(1) NOT NULL DEFAULT 0** e a unica flag da auditoria
   com dominio fechado em TODOS os motores, e e a pergunta que a F3.1 faz
   primeiro ("isto saiu do motor deterministico ou da IA?"). `volume_mode` e
   `engine_version` ficam VARCHAR, nao ENUM, pela mesma razao do `status` da
   038: valor novo de motor nao pode exigir DDL.

6. **Nao existe coluna `status`.** A linha so nasce quando um rascunho FOI
   produzido - geracao que falhou nao tem `generation_audit` para persistir, e
   a falha ja esta no `error` do job e no log estruturado. Uma coluna que hoje
   so teria um valor seria uma promessa que o codigo nao cumpre. Se um dia as
   falhas forem persistidas, ela entra aditiva.

7. **`request_json`** e a UNICA coisa do item do DynamoDB que nao esta em
   nenhum outro lugar e que morre em 6h: o payload que originou a geracao
   (modelo, slot, academia, data, faixa de descanso). E o "pedido" do
   par pedido/resposta - sem ele, a F3.1 tem o gerado mas nao o pedido.

Fronteira com `template_generation_logs` (038): aquela tabela audita a
geracao de um **MODELO** (`workout_plan_templates`) pela IA de periodizacao -
prompt, tokens, custo, validador. Esta audita a geracao de uma **FICHA**
(`workout_plans`) a partir de um modelo ja existente, pelos motores
deterministicos. Objetos diferentes, motores diferentes, ciclos de vida
diferentes; as duas se encontram em `template_id`. Nenhuma coluna da 038 e
repetida aqui. E `progression_decisions` responde uma terceira pergunta -
por que a CARGA/REPS de um exercicio mudou - por exercicio e por sessao, nao
por geracao.

Retencao / duplicacao declarada: `raw_payload.generation_audit` CONTINUA
sendo gravado como hoje. Nao ha "apontar so para a tabela" neste PR por duas
razoes: o congelamento ja le `raw_payload.generation_audit.substitutions[]`
(`app/generation/freeze_mode_b.py`), e remover o bloco do payload ao mesmo
tempo em que ele passa a ser gravado em outro lugar e a mudanca dupla que o
M16 proibe. A duplicacao e DELIBERADA e dura ate a F3.1 estar em producao e
validada lendo `generation_runs`; so entao um PR posterior pode avaliar
enxugar o `raw_payload`, com os dois lados comparados em dados reais.

Sem DML: a tabela nasce vazia e nao ha backfill possivel. O historico que se
perderia ja se perdeu (os jobs expiraram); o que existe em
`raw_payload.generation_audit` de fichas antigas continua la e e legivel -
mas um backfill inventaria `created_at` de geracao (so temos a data da
FICHA, que nao e a da geracao) e nao teria `request_json`, produzindo linhas
que parecem historico e nao sao.

Fluxo (mesmo padrao da 047/048/050/051): `plan(db)` so LE
`information_schema`; `up(db)` executa o plano; `down(db)`/`plan_down(db)`
sao o inverso (DROP TABLE se existir - reversivel de verdade: nada aponta
para esta tabela e perde-la so devolve o sistema ao comportamento pre-M16).

- tabela ausente -> exatamente 1 `CREATE TABLE`;
- tabela presente no formato esperado -> nada (reexecucao: 0 statements);
- tabela presente com coluna/tipo/nulabilidade diferente, ENGINE != InnoDB,
  ou `users`/`workout_plans` incompativeis -> `GenerationRunsError` ANTES do
  primeiro statement.

De `workout_plans` so se confere EXISTENCIA e ENGINE, nao o tipo da coluna
`id`: o recorte real da Aurora
(tests/unit/data/aurora_schema_2026_09_17_035.json) traz a lista de tabelas
mas so as colunas de `users` e `equipment_exercises`. O tipo ja esta provado
em producao por outro caminho - a 025 criou `cardio_recommendations` com FK
para `workout_plans(id) ON DELETE SET NULL`, que e exatamente a FK desta
migracao. De `users` se confere o mesmo que a 050/051 conferem.

Aurora (mesmo recorte, nao tem `generation_runs`) -> 1 statement.
Reexecucao: 0.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "generation_runs"
USERS_TABLE = "users"
PLANS_TABLE = "workout_plans"

# sha256 em hexadecimal.
AUDIT_HASH_LENGTH = 64

USER_INDEX = "idx_gr_user_created"
PLAN_INDEX = "idx_gr_plan"
LINK_INDEX = "idx_gr_user_audit"
TEMPLATE_INDEX = "idx_gr_template_created"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  workout_plan_id BIGINT NULL,
  job_id VARCHAR(36) NULL,
  template_id BIGINT NULL,
  template_slot_sequence INT NULL,
  volume_mode VARCHAR(40) NULL,
  engine_version VARCHAR(40) NULL,
  llm_used TINYINT(1) NOT NULL DEFAULT 0,
  plan_date DATE NULL,
  audit_hash CHAR({AUDIT_HASH_LENGTH}) NOT NULL,
  audit_json JSON NOT NULL,
  request_json JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY {USER_INDEX} (user_id, created_at),
  KEY {PLAN_INDEX} (workout_plan_id),
  KEY {LINK_INDEX} (user_id, audit_hash),
  KEY {TEMPLATE_INDEX} (template_id, created_at),
  CONSTRAINT fk_gr_user FOREIGN KEY (user_id) REFERENCES {USERS_TABLE}(id) ON DELETE CASCADE,
  CONSTRAINT fk_gr_plan FOREIGN KEY (workout_plan_id) REFERENCES {PLANS_TABLE}(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "user_id": ("bigint", "NO"),
    "workout_plan_id": ("bigint", "YES"),
    "job_id": ("varchar(36)", "YES"),
    "template_id": ("bigint", "YES"),
    "template_slot_sequence": ("int", "YES"),
    "volume_mode": ("varchar(40)", "YES"),
    "engine_version": ("varchar(40)", "YES"),
    "llm_used": ("tinyint", "NO"),
    "plan_date": ("date", "YES"),
    "audit_hash": (f"char({AUDIT_HASH_LENGTH})", "NO"),
    "audit_json": ("json", "NO"),
    "request_json": ("json", "YES"),
    "created_at": ("datetime", "NO"),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class GenerationRunsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise GenerationRunsError(f"054: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `tinyint(1)` -> `tinyint` (MySQL 8 ainda mostra a largura do tinyint).
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db) -> tuple[dict[str, str], dict[tuple[str, str], dict]]:
    names = (TABLE, USERS_TABLE, PLANS_TABLE)
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


def _check_users(engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
    if USERS_TABLE not in engines:
        _fail(f"tabela {USERS_TABLE} nao existe")
    if engines[USERS_TABLE].lower() != "innodb":
        _fail(f"{USERS_TABLE} usa ENGINE={engines[USERS_TABLE]}, esperado InnoDB")
    info = columns.get((USERS_TABLE, "id"))
    if info is None:
        _fail(f"{USERS_TABLE}.id nao existe")
    if _normalize_type(info["COLUMN_TYPE"]) != "bigint":
        _fail(f"{USERS_TABLE}.id e {_text(info['COLUMN_TYPE'])}, esperado bigint")
    if _text(info["COLUMN_KEY"]) != "PRI":
        _fail(f"{USERS_TABLE}.id nao e PRIMARY KEY")


def _check_plans(engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
    """`workout_plans` e do baseline (001). O recorte real da Aurora lista a
    tabela mas nao as colunas dela, entao aqui se confere o que o recorte
    responde - existencia e ENGINE. O tipo de `workout_plans.id` ja esta
    provado em producao pela FK identica da 025 (`cardio_recommendations`).
    Se ela estiver de outro jeito, o proprio `CREATE TABLE` falha, sem meio
    estado: a FK e criada junto com a tabela."""
    if PLANS_TABLE not in engines:
        _fail(f"tabela {PLANS_TABLE} nao existe")
    if engines[PLANS_TABLE].lower() != "innodb":
        _fail(f"{PLANS_TABLE} usa ENGINE={engines[PLANS_TABLE]}, esperado InnoDB")
    info = columns.get((PLANS_TABLE, "id"))
    # Coluna ausente do RECORTE nao e coluna ausente do banco - so se confere
    # o tipo quando ele veio.
    if info is not None and _normalize_type(info["COLUMN_TYPE"]) != "bigint":
        _fail(f"{PLANS_TABLE}.id e {_text(info['COLUMN_TYPE'])}, esperado bigint")


def _check_existing(engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS.items():
        info = columns.get((TABLE, name))
        if info is None:
            _fail(f"{TABLE}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns = _read(db)
    _check_users(engines, columns)
    _check_plans(engines, columns)
    if TABLE in engines:
        _check_existing(engines, columns)
        return []
    return [(CREATE_SQL, None)]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP TABLE` se a tabela existe, senao nada."""
    engines, _columns = _read(db)
    if TABLE not in engines:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
