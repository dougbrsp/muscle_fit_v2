"""Seeds de Modelo escritos direto no banco, no formato das migracoes 065/066.

Desde a 065 os leitores so enxergam slots da versao VIGENTE: um seed que
insere slot cru precisa dizer de qual versao ele e (slot sem versao e o
"orfao" que a 066 saneia - nenhum caminho do app grava um). `versao_v1(cur,
template_id)` devolve o `id` da versao VIGENTE do Modelo, criando a v1 (como
o backfill da 065: `effective_from NULL` = desde sempre) quando ele ainda nao
tem nenhuma. Reexecutavel - chamar uma vez por slot nao cria versoes a mais.

Uso (o argumento e avaliado ANTES do INSERT do slot, entao o `lastrowid`
lido depois continua sendo o do slot):

    cur.execute(
        "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name) "
        "VALUES (%s, %s, 1, 'A')",
        (versao_v1(cur, template_id), template_id),
    )
"""

from __future__ import annotations

from typing import Any

_V1_SQL = """
INSERT INTO workout_plan_template_versions (
  template_id, version_number, training_type_id, volume_mode, template_type,
  grid_rows, grid_cols, methodology_intent, methodology_target_group_ids,
  effective_from, superseded_at, created_by_user_id
)
SELECT t.id, 1, t.training_type_id, t.volume_mode, t.template_type,
       t.grid_rows, t.grid_cols, 'standard', JSON_ARRAY(), NULL, NULL, t.created_by_user_id
FROM workout_plan_templates t
WHERE t.id = %s
  AND NOT EXISTS (SELECT 1 FROM workout_plan_template_versions v WHERE v.template_id = t.id)
"""

_CURRENT_SQL = "SELECT id FROM workout_plan_template_versions WHERE current_template_id = %s"


def versao_v1(cur: Any, template_id: int) -> int:
    cur.execute(_V1_SQL, (template_id,))
    cur.execute(_CURRENT_SQL, (template_id,))
    row = cur.fetchone()
    return int(row["id"] if isinstance(row, dict) else row[0])
