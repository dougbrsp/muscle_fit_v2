"""Guards de schema legado ("ALTER TABLE ADD COLUMN" lazy) compartilhados.

Varios modulos do app expoem uma funcao `_ensure_schema()` que roda
ALTER TABLE idempotente (via `information_schema` + `PREPARE`/`EXECUTE`) para
colunas que, em producao, foram adicionadas ao longo do tempo por fora das
migrations numeradas em `api/db/migrations/` - so passam a existir depois que
algum endpoint do modulo correspondente e chamado pela primeira vez.

Em producao (Aurora, rodando ha tempo) essas colunas ja existem havia muito.
Mas num banco local recem-criado do zero (`docker compose up`, sem seed de
producao) nenhuma delas existe ainda, e isso quebra endpoints de LEITURA que
dependem dessas colunas sem nunca ter passado por um endpoint de escrita do
mesmo modulo (ex.: `GET /activities` falha com "Unknown column
'a.workout_plan_id'" antes de qualquer chamada a `app.workout_sessions`).

Este modulo centraliza a lista de modulos com guard e a logica de rodar todos
eles em best-effort, para ser reaproveitado tanto por
`api/scripts/bootstrap_local_db.py` (seed opcional com dump de producao)
quanto pelo hook de startup em `api/app/main.py` (banco local vazio, sem
seed).

Os modulos que saem desta lista passam a ter o schema numa migracao
versionada (`python -m app.migrate`), a mesma que roda na Aurora.
"""

from __future__ import annotations

import importlib
import logging

logger = logging.getLogger("fitness.schema_guards")

# Sairam desta lista, com schema em migracao versionada (`python -m app.migrate`)
# e sem DDL em runtime:
# - `cardio_recommendations`: 025;
# - `workout_sessions` (junto com os guards de `services.py` para series e
#   fichas): 026;
# - `devices`, `exercise_mapping_catalog` (guard ja era no-op), `gyms` (DROP
#   de `color_palette`, coluna que ja nao existe na Aurora nem em dev),
#   `intervals_integration`, `manual_effort_metrics` (historico; leitura saiu no M99), `reports` e
#   `sleep_metrics`: 027.
# Lista vazia: nenhum guard de runtime restante. Modulo novo com guard deve
# preferir uma migracao versionada.
SCHEMA_GUARD_MODULES: list[str] = []


def run_all_schema_guards() -> None:
    """Importa cada modulo em `SCHEMA_GUARD_MODULES` e chama `_ensure_schema()`.

    Best-effort: se um modulo especifico falhar ao ser importado ou ao rodar
    seu guard, apenas loga um aviso e segue para os demais - nunca deve
    derrubar o processo (chamador) por causa de um guard que falhou."""
    for module_name in SCHEMA_GUARD_MODULES:
        try:
            module = importlib.import_module(f"app.{module_name}")
            module._ensure_schema()
        except Exception:  # noqa: BLE001 - best-effort, ver docstring do modulo
            logger.warning(
                "Guard de schema de 'app.%s' nao pode ser aplicado; seguindo mesmo assim.",
                module_name,
                exc_info=True,
            )
