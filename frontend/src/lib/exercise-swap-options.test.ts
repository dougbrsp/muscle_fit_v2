import { describe, expect, it } from 'vitest';
import {
  applyDraftEquipmentChoice,
  draftEquipmentOptionsBody,
  draftEquipmentOptionsKey,
  draftSwapOptionsState,
  filterBySwapEligibility,
  generatedMappingIds,
  indexSwapOptions,
  rowSwapEligibility,
  stampGeneratedRows,
  swapEligibilityFor,
  pickedExercisePatch,
  swapExerciseResetPatch,
  typedExercisePatch,
  type DraftSwapOptionsResponse,
  type ExerciseSwapOptionsResponse,
} from './exercise-swap-options';
import { toWorkoutPlanExerciseInput } from './workout-plan-payload';

const response: ExerciseSwapOptionsResponse = {
  plan_id: 10,
  editable: true,
  rows: [
    { workout_plan_exercise_id: 1, primary_group_id: 12, exercise_ids: [5, 6] },
    { workout_plan_exercise_id: 2, primary_group_id: null, exercise_ids: null },
  ],
};

const rowForPayload = () => stampGeneratedRows([{ sequence: 1, exercise_name: 'Supino reto', mapping_id: 5 }])[0];

const catalog = [
  { mappingId: 5, label: 'Supino reto' },
  { mappingId: 6, label: 'Supino inclinado' },
  { mappingId: 7, label: 'Puxada alta' },
  { mappingId: null, label: 'Sem vínculo' },
];

describe('swapEligibilityFor', () => {
  const index = indexSwapOptions(response);

  it('linha nova ou tabela sem linhas salvas fica livre', () => {
    expect(swapEligibilityFor(null, index).kind).toBe('free');
    expect(swapEligibilityFor(1, undefined).kind).toBe('free');
  });

  it('linha salva oferece só os ids que o servidor devolveu', () => {
    const labels = filterBySwapEligibility(catalog, swapEligibilityFor(1, index)).map((item) => item.label);
    expect(labels).toEqual(['Supino reto', 'Supino inclinado']);
  });

  it('linha sem restrição no servidor oferece o catálogo inteiro', () => {
    expect(filterBySwapEligibility(catalog, swapEligibilityFor(2, index))).toHaveLength(catalog.length);
  });

  it('carregando ou linha ausente da resposta não oferece nada', () => {
    expect(filterBySwapEligibility(catalog, swapEligibilityFor(1, 'loading'))).toEqual([]);
    expect(filterBySwapEligibility(catalog, swapEligibilityFor(99, index))).toEqual([]);
  });
});

describe('elegibilidade do rascunho gerado', () => {
  const draftResponse: DraftSwapOptionsResponse = {
    items: [
      { mapping_id: 5, primary_group_id: 12, exercise_ids: [5, 6] },
      { mapping_id: 9, primary_group_id: null, exercise_ids: null },
    ],
  };
  const labels = (eligibility: ReturnType<typeof rowSwapEligibility>) =>
    filterBySwapEligibility(catalog, eligibility).map((item) => item.label);
  const state = (hasData = true) => draftSwapOptionsState({ data: hasData ? draftResponse : undefined, isError: false }, true);
  const rows = () =>
    stampGeneratedRows([
      { sequence: 1, exercise_name: 'Supino reto', mapping_id: 5 },
      { sequence: 2, exercise_name: 'Livre', mapping_id: 9 },
      { sequence: 3, exercise_name: 'Sem vínculo', mapping_id: null },
    ]);

  it('linha gerada fica restrita aos ids do mapping_id original; null é livre', () => {
    const [restricted, free] = rows();
    expect(labels(rowSwapEligibility(restricted, undefined, state()))).toEqual(['Supino reto', 'Supino inclinado']);
    expect(labels(rowSwapEligibility(free, undefined, state()))).toHaveLength(catalog.length);
  });

  it('carregando ou erro não oferecem nada nas linhas geradas', () => {
    const [row] = rows();
    expect(labels(rowSwapEligibility(row, undefined, state(false)))).toEqual([]);
    expect(labels(rowSwapEligibility(row, undefined, draftSwapOptionsState({ isError: true }, true)))).toEqual([]);
  });

  it('linha nova à mão e linha gerada sem mapping_id ficam livres', () => {
    const [, , noMapping] = rows();
    expect(rowSwapEligibility({ sequence: 4 } as never, undefined, 'loading').kind).toBe('free');
    expect(rowSwapEligibility(noMapping, undefined, state()).kind).toBe('free');
    expect(rowSwapEligibility(rows()[0], undefined, undefined).kind).toBe('free');
  });

  it('a restrição sobrevive a trocar o exercício, reordenar e remover', () => {
    const swapped = { ...rows()[0], exercise_name: 'Puxada alta', mapping_id: 7 };
    expect(labels(rowSwapEligibility(swapped, undefined, state()))).toEqual(['Supino reto', 'Supino inclinado']);
    const reordered = [rows()[2], swapped, rows()[1]].map((row, i) => ({ ...row, sequence: i + 1 }));
    expect(labels(rowSwapEligibility(reordered[1], undefined, state()))).toEqual(['Supino reto', 'Supino inclinado']);
    expect(rowSwapEligibility(reordered[2], undefined, state()).kind).toBe('free');
    const removed = reordered.filter((row) => row.exercise_name !== 'Sem vínculo');
    expect(labels(rowSwapEligibility(removed[0], undefined, state()))).toEqual(['Supino reto', 'Supino inclinado']);
  });

  it('linha salva continua pelo workout_plan_exercise_id', () => {
    const saved = { workout_plan_exercise_id: 1, generated_mapping_id: 9 };
    expect(labels(rowSwapEligibility(saved, indexSwapOptions(response), state()))).toEqual(['Supino reto', 'Supino inclinado']);
  });

  it('a chave local não vai no payload de save', () => {
    const [row] = rows();
    expect(JSON.stringify(toWorkoutPlanExerciseInput(row, 1))).not.toContain('generated_mapping_id');
  });

  it('ids únicos, ordenados e sem estado quando não há linha mapeada', () => {
    expect(generatedMappingIds([{ mapping_id: 9 }, { mapping_id: 5 }, { mapping_id: 9 }, { mapping_id: null }, {}])).toEqual([5, 9]);
    expect(draftSwapOptionsState({ isError: false }, false)).toBeUndefined();
  });

  it('trocar para outro exercício limpa o equipamento; reescolher o mesmo não', () => {
    expect(swapExerciseResetPatch(5, 6)).toEqual({ preferred_equipment_id: null, equipment_name: null, suggested_load_kg: null });
    expect(swapExerciseResetPatch(5, 5)).toEqual({});
  });

  describe('comparação com o vínculo anterior à digitação', () => {
    const CLEARED = { preferred_equipment_id: null, equipment_name: null, suggested_load_kg: null };
    const linked = { mapping_id: 5 as number | null, last_linked_mapping_id: undefined as number | null | undefined };
    // Aplica o patch na linha como o updateExercise faz (spread).
    const typed = (row: typeof linked, text: string) => ({ ...row, ...typedExercisePatch(row, text, false) });

    it('digitar e reescolher o mesmo exercício não limpa', () => {
      const row = typed(typed(linked, 'Sup'), 'Supi');
      expect(row.mapping_id).toBeNull();
      expect(row.last_linked_mapping_id).toBe(5);
      const patch = pickedExercisePatch(row, 'Supino reto', 5);
      expect(patch).toEqual({ exercise_name: 'Supino reto', mapping_id: 5, last_linked_mapping_id: 5 });
    });

    it('digitar e escolher outro limpa', () => {
      expect(pickedExercisePatch(typed(linked, 'Pux'), 'Puxada alta', 7)).toMatchObject({ mapping_id: 7, ...CLEARED });
    });

    it('escolher outro sem digitar limpa; reescolher o mesmo sem digitar não', () => {
      expect(pickedExercisePatch(linked, 'Puxada alta', 7)).toMatchObject(CLEARED);
      expect(pickedExercisePatch(linked, 'Supino reto', 5)).not.toHaveProperty('equipment_name');
    });

    it('linha sem vínculo anterior que recebe exercício: aplica a limpeza (em linha nova é no-op)', () => {
      expect(pickedExercisePatch({ mapping_id: null }, 'Supino reto', 5)).toMatchObject({ mapping_id: 5, ...CLEARED });
    });

    it('nome livre sem escolha mantém o vínculo anterior guardado e não limpa nada', () => {
      const row = typed(linked, 'Meu exercício');
      expect(row).not.toHaveProperty('equipment_name');
      expect(row.last_linked_mapping_id).toBe(5);
    });

    it('o campo local não vai no payload de save', () => {
      const row = { ...rowForPayload(), ...typedExercisePatch({ mapping_id: 5 }, 'x', false) };
      expect(JSON.stringify(toWorkoutPlanExerciseInput(row, 1))).not.toContain('last_linked_mapping_id');
    });
  });
});

describe('equipamento no rascunho', () => {
  const row = { mapping_id: 5, preferred_equipment_id: 11, advanced_technique: 'rest_pause', equipment_name: 'A', suggested_load_kg: 40 };

  it('monta o corpo do POST a partir da linha', () => {
    expect(draftEquipmentOptionsBody(row, 3)).toEqual({
      gym_id: 3,
      mapping_id: 5,
      current_gym_equipment_id: 11,
      advanced_technique: 'rest_pause',
    });
    expect(draftEquipmentOptionsBody({ mapping_id: 5 }, 3)).toMatchObject({
      current_gym_equipment_id: null,
      advanced_technique: null,
    });
    expect(draftEquipmentOptionsBody({ mapping_id: null }, 3)).toBeNull();
  });

  it('aplicar a escolha muda só equipamento e nome', () => {
    expect(applyDraftEquipmentChoice(row, { gym_equipment_id: 12, equipment_name: 'B' })).toEqual({
      ...row,
      preferred_equipment_id: 12,
      equipment_name: 'B',
    });
  });

  it('trocar o exercício limpa o equipamento e muda a chave da consulta', () => {
    const next = { ...row, mapping_id: 6, ...swapExerciseResetPatch(5, 6) };
    expect(next.preferred_equipment_id).toBeNull();
    expect(draftEquipmentOptionsKey(draftEquipmentOptionsBody(next, 3))).not.toEqual(
      draftEquipmentOptionsKey(draftEquipmentOptionsBody(row, 3)),
    );
  });
});
