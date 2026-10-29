import { describe, expect, it } from 'vitest';
import {
  applyPreferenceChange,
  buildRecommendPath,
  exclusionByLinkId,
  exclusionReasonLabel,
  isValidStars,
  pairKey,
  sortExercisesByPairStars,
  starsByPairId,
  starsByPairKey,
  starsLabel,
  type EquipmentExercisePreferencesResponse,
  type EquipmentRecommendExcluded,
} from './equipment-recommend';
import type { RecommendItem } from './gyms-types';
import type { GymEquipmentRecommendItem } from './workout-mode-types';

// Barra livre (equipamento 7) tem DOIS pares: a nota é de cada exercício nela.
const PREFERENCES: EquipmentExercisePreferencesResponse = {
  ok: true,
  default_stars: 3,
  items: [
    {
      equipment_exercise_id: 501,
      equipment_id: 42,
      equipment_name: 'Supino articulado',
      exercise_id: 118,
      exercise_name: 'Supino reto',
      stars: 5,
      updated_at: '2026-09-17T10:00:00',
    },
    {
      equipment_exercise_id: 502,
      equipment_id: 7,
      equipment_name: 'Barra livre',
      exercise_id: 118,
      exercise_name: 'Supino reto',
      stars: 2,
      updated_at: null,
    },
    {
      equipment_exercise_id: 503,
      equipment_id: 7,
      equipment_name: 'Barra livre',
      exercise_id: 230,
      exercise_name: 'Remada curvada',
      stars: 5,
      updated_at: null,
    },
  ],
};

describe('buildRecommendPath', () => {
  it('monta a URL de troca durante o treino, com exclusão do equipamento atual', () => {
    expect(buildRecommendPath({ gymId: 3, mappingId: 118, excludeEquipmentLinkId: 311 })).toBe(
      '/gym-equipment/recommend?gym_id=3&mapping_id=118&exclude_equipment_id=311',
    );
  });

  it('só envia required_technique quando há técnica prescrita', () => {
    expect(buildRecommendPath({ gymId: 3, mappingId: 118, requiredTechnique: 'drop_set' })).toBe(
      '/gym-equipment/recommend?gym_id=3&mapping_id=118&required_technique=drop_set',
    );
    for (const empty of [null, undefined, '', '   ']) {
      expect(buildRecommendPath({ gymId: 3, mappingId: 118, requiredTechnique: empty })).toBe(
        '/gym-equipment/recommend?gym_id=3&mapping_id=118',
      );
    }
  });

  it('cai para primary_group (codificado) quando não há mapping_id', () => {
    expect(buildRecommendPath({ gymId: 3, primaryGroup: 'Tríceps & cia' })).toBe(
      '/gym-equipment/recommend?gym_id=3&primary_group=Tr%C3%ADceps+%26+cia',
    );
  });
});

describe('estrelas', () => {
  it('aceita só inteiros de 1 a 5', () => {
    expect([1, 3, 5].every(isValidStars)).toBe(true);
    expect([0, 6, 2.5, NaN, '4', null, undefined].some(isValidStars)).toBe(false);
  });

  it('indexa pelo id do par (Domínio de exercícios) e ignora nota suja', () => {
    const dirty = { ...PREFERENCES.items[0], equipment_exercise_id: 9, stars: 9 };
    expect([...starsByPairId([...PREFERENCES.items, dirty]).entries()]).toEqual([
      [501, 5],
      [502, 2],
      [503, 5],
    ]);
    expect(starsByPairId(undefined).size).toBe(0);
  });

  it('indexa por equipamento+exercício (Modo Treino): o MESMO equipamento tem notas diferentes por exercício', () => {
    const map = starsByPairKey(PREFERENCES.items);
    expect(map.get(pairKey(7, 118))).toBe(2);
    expect(map.get(pairKey(7, 230))).toBe(5);
    expect(map.get(pairKey(7, 999))).toBeUndefined(); // par sem nota não herda a do equipamento
    expect(map.get(pairKey(42, 230))).toBeUndefined();
    expect(starsByPairKey(null).size).toBe(0);
  });

  it('rótulo', () => {
    expect(starsLabel(null)).toBe('Sem avaliação (conta como 3 estrelas)');
    expect(starsLabel(1)).toBe('1 estrela');
    expect(starsLabel(4)).toBe('4 estrelas');
  });
});

describe('applyPreferenceChange (atualização otimista)', () => {
  const change = (equipmentExerciseId: number, stars: number | null) => ({
    equipmentExerciseId,
    equipmentId: 7,
    exerciseId: 118,
    stars,
  });

  it('troca a nota de um par já avaliado, sem mexer no outro par do mesmo equipamento nem mutar a entrada', () => {
    const next = applyPreferenceChange(PREFERENCES, change(502, 4));
    expect(next).not.toBe(PREFERENCES);
    expect(starsByPairId(next.items).get(502)).toBe(4);
    expect(starsByPairId(next.items).get(503)).toBe(5);
    expect(next.items.find((i) => i.equipment_exercise_id === 502)?.exercise_name).toBe('Supino reto');
    expect(starsByPairId(PREFERENCES.items).get(502)).toBe(2);
  });

  it('inclui par nunca avaliado, com os nomes informados', () => {
    const next = applyPreferenceChange(PREFERENCES, {
      equipmentExerciseId: 99,
      equipmentId: 8,
      exerciseId: 300,
      equipmentName: 'Crossover',
      exerciseName: 'Crucifixo',
      stars: 1,
    });
    expect(next.items.at(-1)).toEqual({
      equipment_exercise_id: 99,
      equipment_id: 8,
      equipment_name: 'Crossover',
      exercise_id: 300,
      mapping_id: 300,
      exercise_name: 'Crucifixo',
      stars: 1,
      updated_at: null,
    });
    expect(starsByPairKey(next.items).get(pairKey(8, 300))).toBe(1);
  });

  it('stars = null remove só aquele par; remover o que não existe não muda nada', () => {
    expect(applyPreferenceChange(PREFERENCES, change(502, null)).items.map((i) => i.equipment_exercise_id)).toEqual([501, 503]);
    expect(applyPreferenceChange(PREFERENCES, change(1234, null)).items).toHaveLength(3);
  });

  it('parte do zero quando o cache ainda está vazio', () => {
    const next = applyPreferenceChange(undefined, change(502, 5));
    expect(next.ok).toBe(true);
    expect(next.default_stars).toBe(3);
    expect(next.items.map((i) => [i.equipment_exercise_id, i.stars])).toEqual([[502, 5]]);
  });

  it('nota inválida é ignorada', () => {
    expect(applyPreferenceChange(PREFERENCES, change(502, 6))).toBe(PREFERENCES);
  });

  it('rollback = restaurar o snapshot devolve exatamente o estado anterior', () => {
    const snapshot = PREFERENCES;
    const optimistic = applyPreferenceChange(snapshot, change(502, 1));
    expect(starsByPairId(optimistic.items).get(502)).toBe(1);
    expect(starsByPairId(snapshot.items).get(502)).toBe(2);
  });
});

describe('sortExercisesByPairStars', () => {
  const options = [
    { mapping_id: 1, exercise_name: 'Zeta' },
    { mapping_id: 118, exercise_name: 'Supino reto' },
    { mapping_id: 230, exercise_name: 'Remada curvada' },
    { mapping_id: 3, exercise_name: 'Ábaco' },
  ];

  it('dentro do equipamento: mais estrelas primeiro; sem avaliação vale 3 e fica entre 5 e 2; empate por nome', () => {
    const sorted = sortExercisesByPairStars(options, 7, starsByPairKey(PREFERENCES.items));
    expect(sorted.map((o) => o.mapping_id)).toEqual([230, 3, 1, 118]);
    expect(options.map((o) => o.mapping_id)).toEqual([1, 118, 230, 3]); // não muta a entrada
  });

  it('a nota de OUTRO equipamento não conta', () => {
    // no equipamento 42 só o Supino reto (118) tem nota (5)
    const sorted = sortExercisesByPairStars(options, 42, starsByPairKey(PREFERENCES.items));
    expect(sorted.map((o) => o.mapping_id)).toEqual([118, 3, 230, 1]);
  });

  it('sem nenhuma avaliação é a ordem alfabética de antes', () => {
    expect(sortExercisesByPairStars(options, 7, new Map()).map((o) => o.exercise_name)).toEqual([
      'Ábaco',
      'Remada curvada',
      'Supino reto',
      'Zeta',
    ]);
  });
});

describe('excluídos por required_technique', () => {
  const excluded: EquipmentRecommendExcluded[] = [
    {
      gym_equipment_id: 305,
      id: 305,
      equipment_id: 7,
      equipment_name: 'Barra livre',
      mapping_id: 118,
      mapping_exercise_name: 'Supino reto',
      reason: 'FAILURE_REQUIRED_NOT_ALLOWED',
      max_safe_rpe: 9,
    },
  ];

  it('explica o motivo com o nome da técnica', () => {
    expect(exclusionReasonLabel('FAILURE_REQUIRED_NOT_ALLOWED', 'Drop set')).toBe(
      '"Drop set" exige chegar à falha, e a falha não é permitida neste equipamento.',
    );
    expect(exclusionReasonLabel('TECHNIQUE_INCOMPATIBLE', 'Rest-pause')).toBe(
      'Equipamento não marcado como compatível com "Rest-pause".',
    );
    expect(exclusionReasonLabel('MOTIVO_FUTURO', null)).toBe('Incompatível com a técnica prescrita.');
  });

  it('indexa pela associação (gym_equipment.id)', () => {
    expect(exclusionByLinkId(excluded).get(305)?.reason).toBe('FAILURE_REQUIRED_NOT_ALLOWED');
    expect(exclusionByLinkId(undefined).size).toBe(0);
  });
});

describe('tipo único (G20)', () => {
  it('RecommendItem e GymEquipmentRecommendItem são o mesmo tipo, com os campos novos', () => {
    const item: RecommendItem = {
      id: 311,
      mapping_id: 118,
      equipment_id: 42,
      equipment_name: 'Supino articulado',
      photo_url: null,
      mapping_exercise_name: 'Supino reto',
      mapping_amazfit_name: null,
      primary_group: 'Peitoral',
      user_preference_stars: 5,
      last_used_by_user_at: '2026-09-02T10:00:00',
      max_safe_rpe: 10,
      failure_allowed: true,
      allowed_advanced_techniques: [{ code: 'rest_pause', source: 'equipment_default' }],
      safety_source: 'equipment_default',
    };
    const same: GymEquipmentRecommendItem = item;
    expect(same.user_preference_stars).toBe(5);
  });
});
