"""117 - corrige o equipamento de dois treinos de agosto do usuario 1 (issue #137).

Os treinos de 12 e 24/08 do usuario 1 (Crucifixo/Voador) foram feitos no
"Pec Deck / Voador" mas ficaram vinculados ao "HumanSport Shoulder Chest"
(equipment 53). O usuario confirmou na conversa que ambos foram no Pec Deck.
O erro estava nos DADOS (a tela mostrou o que o banco tem): treino anterior,
melhor carga, e1RM e alvo de 31,5 kg do HumanSport vinham desses dois treinos.

Esta migracao NAO calcula carga, trilha nem recorde. Ela so (1) reatribui o
vinculo de equipamento de duas linhas e (2) descarta as duas trilhas
derivadas calibradas com elas; o motor (`progression_state` /
`progression_evaluator.track_for`) recria o estado pelo caminho normal.

Estado ANTERIOR (medido em producao; usar para desfazer a mao):

    exercises.id = 267 (activity 146, 2026-08-12, user 1, gym 1, mapping 4,
                        original_name 'Voador (Pec Deck)')            equipment_id = 416
    exercises.id = 355 (activity 174, 2026-08-24, user 1, gym 1, mapping 4,
                        original_name 'Crucifixo (HumanSport Shoulder Chest)')
                                                                     equipment_id = 488
    gym_equipment 416 = (gym 1, mapping 3, equipment 53, inativo); 488 = (gym 1,
    mapping 87, equipment 53, inativo); destino 77 = (gym 1, mapping 4,
    equipment 30 'Pec Deck / Voador', ativo).

    progression_tracks (user 1, exercise_id 4, equipment_id 53, working_load_kg 31.50):
      id 1547  fatigue_context 'heavily_fatigued'  calibrated_at e last_executed_at 2026-08-24
      id 1548  fatigue_context 'pre_fatigued'      calibrated_at 2026-08-12, last_executed_at 2026-11-12
    (as demais colunas das trilhas nao foram gravadas aqui: `down` NAO as recria.)

Trilhas: o DELETE de `progression_tracks` apaga em CASCATA as linhas de
`progression_decisions` com `track_id` nessas trilhas (FK `fk_pd_track`, 036):
sao historico derivado das trilhas descartadas. Depois, o par
(exercicio 4, equipamento 53) fica sem trilha: a prescricao parte de trilha
nova em calibracao (`deterministic_prescription`), a ultima execucao vem dos
logs (a de 12/11, 12 x 12 kg) e a trilha reaparece quando o proximo treino do
par for finalizado.

Guardas (cada statement so age se TUDO valer; senao 0 linhas, sem erro):
- exercises: id, activity_id, mapping_id e equipment_id atuais exatos, a
  activity e do user 1 E o vinculo 77 existe com gym 1, mapping 4, equipment 30;
- progression_tracks: id, user_id, exercise_id, equipment_id e working_load_kg.
Idempotente: depois de aplicada, o equipment_id atual deixa de bater (0 linhas).

Data API: um statement por chamada, parametros `%s`, nenhuma literal com ':';
modulo autocontido.
"""

from __future__ import annotations

USER_ID = 1
GYM_ID = 1
MAPPING_ID = 4
TARGET_LINK_ID = 77
TARGET_LINK_EQUIPMENT_ID = 30
# (exercises.id, activity_id, equipment_id errado)
EXERCISE_FIXES = ((267, 146, 416), (355, 174, 488))
# (progression_tracks.id)
TRACK_IDS = (1547, 1548)
TRACK_EXERCISE_ID = 4
TRACK_EQUIPMENT_ID = 53
TRACK_WORKING_LOAD = "31.50"

RELINK_SQL = (
    "UPDATE exercises e JOIN activities a ON a.id = e.activity_id "
    "SET e.equipment_id = %s "
    "WHERE e.id = %s AND e.activity_id = %s AND e.mapping_id = %s AND e.equipment_id = %s "
    "AND a.user_id = %s "
    "AND EXISTS (SELECT 1 FROM gym_equipment g WHERE g.id = %s AND g.gym_id = %s "
    "AND g.mapping_id = %s AND g.equipment_id = %s)"
)
UNRELINK_SQL = (
    "UPDATE exercises SET equipment_id = %s "
    "WHERE id = %s AND activity_id = %s AND mapping_id = %s AND equipment_id = %s"
)
DELETE_TRACK_SQL = (
    "DELETE FROM progression_tracks "
    "WHERE id = %s AND user_id = %s AND exercise_id = %s AND equipment_id = %s AND working_load_kg = %s"
)


def plan(db=None) -> list[tuple[str, tuple]]:
    """Os guardas estao no proprio SQL; o plano e fixo."""
    statements: list[tuple[str, tuple]] = [
        (
            RELINK_SQL,
            (TARGET_LINK_ID, exercise_id, activity_id, MAPPING_ID, wrong_link, USER_ID,
             TARGET_LINK_ID, GYM_ID, MAPPING_ID, TARGET_LINK_EQUIPMENT_ID),
        )
        for exercise_id, activity_id, wrong_link in EXERCISE_FIXES
    ]
    statements += [
        (DELETE_TRACK_SQL, (track_id, USER_ID, TRACK_EXERCISE_ID, TRACK_EQUIPMENT_ID, TRACK_WORKING_LOAD))
        for track_id in TRACK_IDS
    ]
    return statements


def plan_down(db=None) -> list[tuple[str, tuple]]:
    """Devolve SO o vinculo dos exercicios (as trilhas apagadas nao sao recriadas)."""
    return [
        (UNRELINK_SQL, (wrong_link, exercise_id, activity_id, MAPPING_ID, TARGET_LINK_ID))
        for exercise_id, activity_id, wrong_link in EXERCISE_FIXES
    ]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
