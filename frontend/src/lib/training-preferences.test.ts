import { describe, expect, it } from 'vitest';
import {
  buildTrainingPreferencesPath,
  displayedPairStars,
  groupTrainingPreferences,
  UNGROUPED_LABEL,
  type TrainingPreferenceItem,
} from './equipment-recommend';
import { isAdminRole, adminRouteState } from './admin-route';

function item(overrides: Partial<TrainingPreferenceItem>): TrainingPreferenceItem {
  return {
    equipment_exercise_id: 1,
    exercise_id: 10,
    exercise_name: 'Supino reto',
    muscle_group_id: 1,
    primary_group: 'Peitoral',
    equipment_id: 100,
    equipment_name: 'Barra livre',
    photo_url: null,
    stars: null,
    ...overrides,
  };
}

const ITEMS: TrainingPreferenceItem[] = [
  item({ equipment_exercise_id: 3, equipment_id: 102, equipment_name: 'Supino articulado', stars: 4 }),
  item({ equipment_exercise_id: 1 }),
  item({ equipment_exercise_id: 5, exercise_id: 20, exercise_name: 'Agachamento', primary_group: 'Quadríceps', muscle_group_id: 2 }),
  item({ equipment_exercise_id: 7, exercise_id: 30, exercise_name: 'Prancha', primary_group: null, muscle_group_id: null }),
  item({ equipment_exercise_id: 9, exercise_id: 11, exercise_name: 'Crucifixo', equipment_name: 'Crossover' }),
];

describe('groupTrainingPreferences', () => {
  it('agrupa grupo -> exercício -> equipamentos em ordem alfabética, "sem grupo" por último', () => {
    const groups = groupTrainingPreferences(ITEMS);
    expect(groups.map((g) => g.group)).toEqual(['Peitoral', 'Quadríceps', UNGROUPED_LABEL]);
    expect(groups[0].exercises.map((e) => e.exerciseName)).toEqual(['Crucifixo', 'Supino reto']);
    expect(groups[0].exercises[1].pairs.map((p) => p.equipment_name)).toEqual(['Barra livre', 'Supino articulado']);
  });

  it('a nota não reordena a lista', () => {
    const avaliado = ITEMS.map((i) => (i.equipment_exercise_id === 1 ? { ...i, stars: 1 } : i));
    const ordem = (list: TrainingPreferenceItem[]) =>
      groupTrainingPreferences(list).flatMap((g) => g.exercises.flatMap((e) => e.pairs.map((p) => p.equipment_exercise_id)));
    expect(ordem(avaliado)).toEqual(ordem(ITEMS));
  });

  it('busca sem acento, em qualquer ordem, por exercício, equipamento ou grupo', () => {
    expect(groupTrainingPreferences(ITEMS, 'quadriceps').map((g) => g.group)).toEqual(['Quadríceps']);
    const porEquipamento = groupTrainingPreferences(ITEMS, 'articulado SUPINO');
    expect(porEquipamento[0].exercises[0].pairs.map((p) => p.equipment_exercise_id)).toEqual([3]);
    expect(groupTrainingPreferences(ITEMS, 'inexistente')).toEqual([]);
  });

  it('lista vazia ou ausente', () => {
    expect(groupTrainingPreferences(undefined)).toEqual([]);
    expect(groupTrainingPreferences([])).toEqual([]);
  });
});

describe('displayedPairStars', () => {
  it('usa o cache de preferências quando carregado (inclui o otimista e a remoção)', () => {
    const avaliado = item({ equipment_exercise_id: 3, stars: 4 });
    expect(displayedPairStars(avaliado, new Map([[3, 2]]))).toBe(2);
    expect(displayedPairStars(avaliado, new Map())).toBeNull();
  });

  it('antes do cache, usa a nota da própria lista; valor sujo vira sem avaliação', () => {
    expect(displayedPairStars(item({ stars: 5 }), null)).toBe(5);
    expect(displayedPairStars(item({ stars: null }), null)).toBeNull();
    expect(displayedPairStars(item({ stars: 9 }), null)).toBeNull();
  });
});

describe('buildTrainingPreferencesPath', () => {
  it('só põe gym_id quando há academia', () => {
    expect(buildTrainingPreferencesPath(4)).toBe('/me/training-preferences?gym_id=4');
    expect(buildTrainingPreferencesPath(null)).toBe('/me/training-preferences');
  });
});

describe('adminRouteState', () => {
  it('carregando enquanto /auth/me não resolveu; libera só admin; redireciona o resto', () => {
    expect(adminRouteState({ isSuccess: false, role: undefined })).toBe('loading');
    expect(adminRouteState({ isSuccess: true, role: 'admin' })).toBe('allow');
    expect(adminRouteState({ isSuccess: true, role: 'personal_trainer' })).toBe('redirect');
    expect(adminRouteState({ isSuccess: true, role: 'regular' })).toBe('redirect');
    expect(adminRouteState({ isSuccess: false, isError: true, role: undefined })).toBe('redirect');
    expect(isAdminRole('admin')).toBe(true);
    expect(isAdminRole(undefined)).toBe(false);
  });
});
