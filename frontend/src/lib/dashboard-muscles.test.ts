import { describe, expect, it } from 'vitest';
import {
  ESCALA_ALEM_DO_MAXIMO,
  contributionsByExercise,
  countByStatus,
  groupsOutsideTemplatePlan,
  groupsWithGenericTarget,
  humanizeHours,
  isTemplatePlanItem,
  minTargetMaxScale,
  plannedVsRealizedScale,
  equipmentLabel,
  pointsByOffset,
  sessionDelta,
  sessionOffsetLabel,
  sessionsForExercise,
  sortMuscleItems,
} from './dashboard-muscles';
import type { Metric, MuscleItem, MuscleSession } from './dashboard-v3-types';

function metric(value: number | null): Metric {
  return {
    value,
    unit: 'séries',
    nature: 'DERIVED',
    metric_version: 'effective-sets-v1',
    data_quality: 'HIGH',
    limitations: [],
  };
}

function item(overrides: Partial<MuscleItem> & Pick<MuscleItem, 'muscle_group_id'>): MuscleItem {
  const base: MuscleItem = {
    muscle_group_id: overrides.muscle_group_id,
    muscle_group: `Grupo ${overrides.muscle_group_id}`,
    targets: {
      min: metric(6),
      target: metric(9),
      max: metric(27),
      source: 'assigned_template',
      training_type_id: 1,
    },
    realized: {
      physical_sets: metric(0),
      effective_sets_stimulus: metric(0),
      effective_sets_fatigue: metric(0),
      direct_sets: metric(0),
      indirect_sets: metric(0),
      stabilizer_sets: metric(0),
      weekly_effective_sets: metric(0),
      avg_rpe: metric(null),
    },
    planned: { effective_sets: metric(null), weekly_effective_sets: metric(null), plans_count: 0 },
    projected: { remaining_sets: metric(null), pending_slots: 0, cycle_length_days: null },
    frequency: {
      direct_sessions: metric(0),
      participation_sessions: metric(0),
      median_interval_hours: metric(null),
    },
    readiness: null,
    last_stimulus_local_date: null,
    last_direct_local_date: null,
    hours_since_last_stimulus: metric(null),
    status: 'BELOW_MIN',
    status_basis: 'realized_only',
    top_exercises: [],
    data_quality: 'HIGH',
    limitations: [],
  };
  return { ...base, ...overrides };
}

// ---------------------------------------------------------------------------
// Barra mínimo → alvo → máximo
// ---------------------------------------------------------------------------

describe('minTargetMaxScale', () => {
  it('posiciona as três metas proporcionalmente à escala', () => {
    const escala = minTargetMaxScale({ min: 6, target: 9, max: 20, actual: 9 });
    expect(escala.scaleMax).toBe(20 * ESCALA_ALEM_DO_MAXIMO);
    expect(escala.targetPct).toBeCloseTo((9 / 25) * 100, 5);
    expect(escala.actualPct).toBeCloseTo((9 / 25) * 100, 5);
    expect(escala.overflow).toBe(false);
  });

  it('mantém visível um realizado acima do máximo, em vez de encostar na borda', () => {
    const escala = minTargetMaxScale({ min: 6, target: 9, max: 20, actual: 22 });
    expect(escala.overflow).toBe(true);
    expect(escala.actualPct).toBeGreaterThan(escala.maxPct);
    expect(escala.actualPct).toBeLessThan(100);
  });

  it('cresce a escala quando o realizado estoura até a folga', () => {
    const escala = minTargetMaxScale({ min: 6, target: 9, max: 20, actual: 100 });
    expect(escala.scaleMax).toBe(100);
    expect(escala.actualPct).toBe(100);
  });

  it('realizado nulo não vira zero na barra', () => {
    const escala = minTargetMaxScale({ min: 6, target: 9, max: 20, actual: null });
    expect(escala.actualPct).toBeNull();
  });

  it('projetado ganha marca PRÓPRIA, somada só na posição, nunca no realizado', () => {
    const escala = minTargetMaxScale({ min: 6, target: 9, max: 20, actual: 4, projected: 6 });
    // a marca do projetado fica em realizado + projetado...
    expect(escala.projectedPct).toBeCloseTo((10 / 25) * 100, 5);
    // ...e a do realizado continua onde estava.
    expect(escala.actualPct).toBeCloseTo((4 / 25) * 100, 5);
  });

  it('projeção zerada ou ausente não desenha marca', () => {
    expect(minTargetMaxScale({ min: 6, target: 9, max: 20, actual: 4 }).projectedPct).toBeNull();
    expect(
      minTargetMaxScale({ min: 6, target: 9, max: 20, actual: 4, projected: 0 }).projectedPct,
    ).toBeNull();
  });

  it('meta toda zerada não divide por zero', () => {
    const escala = minTargetMaxScale({ min: 0, target: 0, max: 0, actual: 0 });
    expect(escala.scaleMax).toBe(1);
    expect(escala.actualPct).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Ordenação
// ---------------------------------------------------------------------------

describe('sortMuscleItems', () => {
  const abaixo = item({ muscle_group_id: 1, muscle_group: 'Costas', status: 'BELOW_MIN' });
  const noAlvo = item({
    muscle_group_id: 2,
    muscle_group: 'Abdomen',
    status: 'ON_TARGET',
    realized: { ...abaixo.realized, effective_sets_stimulus: metric(30) },
  });
  const acima = item({ muscle_group_id: 3, muscle_group: 'Biceps', status: 'ABOVE_MAX' });

  it('por atenção coloca abaixo do mínimo antes de acima do máximo e do resto', () => {
    const ordenado = sortMuscleItems([noAlvo, acima, abaixo], 'status');
    expect(ordenado.map((i) => i.status)).toEqual(['BELOW_MIN', 'ABOVE_MAX', 'ON_TARGET']);
  });

  it('os três estados do planejado entram na mesma ordem de atenção', () => {
    const abaixoDoPlano = item({ muscle_group_id: 6, status: 'BELOW_TARGET' });
    const acimaDoPlano = item({ muscle_group_id: 7, status: 'ABOVE_TARGET' });
    const ordenado = sortMuscleItems([noAlvo, acimaDoPlano, abaixoDoPlano], 'status');
    expect(ordenado.map((i) => i.status)).toEqual(['BELOW_TARGET', 'ABOVE_TARGET', 'ON_TARGET']);
  });

  it('por volume usa as séries efetivas realizadas', () => {
    const ordenado = sortMuscleItems([abaixo, noAlvo], 'volume');
    expect(ordenado[0].muscle_group_id).toBe(2);
  });

  it('por nome respeita a ordem alfabética pt-BR', () => {
    const ordenado = sortMuscleItems([abaixo, noAlvo, acima], 'nome');
    expect(ordenado.map((i) => i.muscle_group)).toEqual(['Abdomen', 'Biceps', 'Costas']);
  });

  it('por último estímulo põe NUNCA treinado primeiro (a maior lacuna)', () => {
    const treinado = item({ muscle_group_id: 4, last_stimulus_local_date: '2026-09-20' });
    const nunca = item({ muscle_group_id: 5, last_stimulus_local_date: null });
    const ordenado = sortMuscleItems([treinado, nunca], 'ultimo_estimulo');
    expect(ordenado[0].muscle_group_id).toBe(5);
  });

  it('não muta a lista recebida', () => {
    const entrada = [noAlvo, abaixo];
    sortMuscleItems(entrada, 'nome');
    expect(entrada.map((i) => i.muscle_group_id)).toEqual([2, 1]);
  });
});

describe('countByStatus', () => {
  it('conta por estado sem inventar chaves', () => {
    const contagem = countByStatus([
      item({ muscle_group_id: 1, status: 'BELOW_MIN' }),
      item({ muscle_group_id: 2, status: 'BELOW_MIN' }),
      item({ muscle_group_id: 3, status: 'ON_TARGET' }),
    ]);
    expect(contagem).toEqual({ BELOW_MIN: 2, ON_TARGET: 1 });
  });
});

describe('plannedVsRealizedScale', () => {
  it('posiciona o planejado com a mesma folga da barra de três marcas', () => {
    const escala = plannedVsRealizedScale({ planned: 8, actual: 4 });
    expect(escala.scaleMax).toBe(8 * ESCALA_ALEM_DO_MAXIMO);
    expect(escala.plannedPct).toBeCloseTo(80);
    expect(escala.actualPct).toBeCloseTo(40);
    expect(escala.outsideModel).toBe(false);
    expect(escala.overflow).toBe(false);
  });

  it('realizado acima do planejado continua visível e é marcado', () => {
    const escala = plannedVsRealizedScale({ planned: 8, actual: 20 });
    expect(escala.scaleMax).toBe(20);
    expect(escala.actualPct).toBe(100);
    expect(escala.overflow).toBe(true);
  });

  it('grupo fora do Modelo não recebe marca de planejado e não divide por zero', () => {
    const escala = plannedVsRealizedScale({ planned: 0, actual: 3 });
    expect(escala.outsideModel).toBe(true);
    expect(escala.plannedPct).toBeNull();
    expect(escala.actualPct).toBe(100);
  });

  it('realizado nulo não vira zero na barra', () => {
    expect(plannedVsRealizedScale({ planned: 8, actual: null }).actualPct).toBeNull();
  });

  it('planejado nulo é tratado como fora do Modelo, nunca como zero de meta', () => {
    const escala = plannedVsRealizedScale({ planned: null, actual: null });
    expect(escala.outsideModel).toBe(true);
    expect(escala.scaleMax).toBe(1);
  });
});

describe('meta do próprio Modelo por equipamento', () => {
  const doModelo = (id: number, target: number | null, nome: string) =>
    item({
      muscle_group_id: id,
      muscle_group: nome,
      targets: { min: metric(null), target: metric(target), max: metric(null), source: 'template_plan', training_type_id: null },
    });

  it('reconhece a origem template_plan', () => {
    expect(isTemplatePlanItem(doModelo(1, 4, 'Peito'))).toBe(true);
    expect(isTemplatePlanItem(item({ muscle_group_id: 2 }))).toBe(false);
  });

  it('lista só os grupos que o Modelo não trabalha (alvo zero)', () => {
    const fora = groupsOutsideTemplatePlan([
      doModelo(1, 4, 'Peito'),
      doModelo(2, 0, 'Panturrilha'),
      doModelo(3, null, 'Abdomen'),
      item({ muscle_group_id: 4, muscle_group: 'Costas' }),
    ]);
    expect(fora).toEqual(['Panturrilha', 'Abdomen']);
  });
});

describe('groupsWithGenericTarget', () => {
  it('lista só os grupos cuja meta caiu no fallback genérico', () => {
    const generico = item({ muscle_group_id: 1, muscle_group: 'Panturrilha' });
    generico.targets = { ...generico.targets, source: 'fallback_generic', training_type_id: null };
    const comMeta = item({ muscle_group_id: 2, muscle_group: 'Peitoral' });
    expect(groupsWithGenericTarget([generico, comMeta])).toEqual(['Panturrilha']);
  });
});

// ---------------------------------------------------------------------------
// Drill-down
// ---------------------------------------------------------------------------

function session(overrides: Partial<MuscleSession> & Pick<MuscleSession, 'activity_id'>): MuscleSession {
  return {
    activity_name: 'Treino',
    local_date: '2026-09-20',
    started_at: '2026-09-20T19:00:00',
    physical_sets: 0,
    effective_sets_stimulus: 0,
    has_direct_work: true,
    exposures: [],
    ...overrides,
  };
}

const supino = {
  exercise_row_id: 1,
  exercise_id: 10,
  name: 'Supino',
  position_in_session: 1,
  role: 'PRIMARY' as const,
  effective_sets_stimulus: 3,
  sets: [
    { set_id: 1, set_number: 1, set_type: 'working', reps: 10, weight_kg: 60, rpe: 8, effective_sets_stimulus: 1, effective_sets_fatigue: 1, role: 'PRIMARY' as const },
    { set_id: 2, set_number: 2, set_type: 'working', reps: 10, weight_kg: 60, rpe: 8, effective_sets_stimulus: 1, effective_sets_fatigue: 1, role: 'PRIMARY' as const },
    { set_id: 3, set_number: 3, set_type: 'working', reps: 10, weight_kg: 60, rpe: 9, effective_sets_stimulus: 1, effective_sets_fatigue: 1, role: 'PRIMARY' as const },
  ],
};

const crucifixo = {
  ...supino,
  exercise_row_id: 2,
  exercise_id: 11,
  name: 'Crucifixo',
  effective_sets_stimulus: 1,
  sets: [supino.sets[0]],
};

describe('contributionsByExercise', () => {
  it('agrega por exercício e calcula a fração do estímulo do grupo', () => {
    const contribuicoes = contributionsByExercise([
      session({ activity_id: 1, exposures: [supino, crucifixo] }),
    ]);
    expect(contribuicoes.map((c) => c.name)).toEqual(['Supino', 'Crucifixo']);
    expect(contribuicoes[0].share).toBe(0.75);
    expect(contribuicoes[1].share).toBe(0.25);
    expect(contribuicoes[0].sets).toBe(3);
  });

  it('conta SESSÕES distintas, não exposições', () => {
    const contribuicoes = contributionsByExercise([
      session({ activity_id: 1, exposures: [supino, { ...supino, exercise_row_id: 9 }] }),
      session({ activity_id: 2, exposures: [supino] }),
    ]);
    expect(contribuicoes[0].sessions).toBe(2);
  });

  it('mantém os papéis separados quando o mesmo exercício contribui em dois', () => {
    const contribuicoes = contributionsByExercise([
      session({
        activity_id: 1,
        exposures: [supino, { ...supino, exercise_row_id: 3, role: 'STABILIZER' as const }],
      }),
    ]);
    expect(contribuicoes[0].roles).toEqual(['PRIMARY', 'STABILIZER']);
  });

  it('exercício sem id canônico é agrupado pelo nome, não descartado', () => {
    const semId = { ...supino, exercise_id: null, name: 'Exercício sem cadastro' };
    const contribuicoes = contributionsByExercise([session({ activity_id: 1, exposures: [semId] })]);
    expect(contribuicoes).toHaveLength(1);
    expect(contribuicoes[0].exerciseId).toBeNull();
  });

  it('sem estímulo nenhum, a fração é nula em vez de NaN', () => {
    const zerado = { ...supino, effective_sets_stimulus: 0, sets: [] };
    const contribuicoes = contributionsByExercise([session({ activity_id: 1, exposures: [zerado] })]);
    expect(contribuicoes[0].share).toBeNull();
  });
});

describe('sessionsForExercise', () => {
  it('filtra as sessões e as exposições do exercício pedido', () => {
    const sessoes = sessionsForExercise(
      [
        session({ activity_id: 1, exposures: [supino, crucifixo] }),
        session({ activity_id: 2, exposures: [crucifixo] }),
      ],
      10,
      'Supino',
    );
    expect(sessoes.map((s) => s.activity_id)).toEqual([1]);
    expect(sessoes[0].exposures).toHaveLength(1);
  });

  it('casa por nome quando não há id canônico', () => {
    const semId = { ...supino, exercise_id: null, name: 'Sem cadastro' };
    const sessoes = sessionsForExercise([session({ activity_id: 1, exposures: [semId] })], null, 'Sem cadastro');
    expect(sessoes).toHaveLength(1);
  });
});

describe('humanizeHours', () => {
  it('traduz horas em texto curto', () => {
    expect(humanizeHours(0.5)).toBe('menos de 1 h');
    expect(humanizeHours(30)).toBe('30 h');
    expect(humanizeHours(72)).toBe('3 dias');
  });

  it('ausência continua ausência — nunca "0 h"', () => {
    expect(humanizeHours(null)).toBeNull();
    expect(humanizeHours(undefined)).toBeNull();
  });
});


// ---------------------------------------------------------------------------
// Comparativos POR SESSÃO (F2.5 / M34)
// ---------------------------------------------------------------------------

describe('comparativo por sessão', () => {
  it('a coluna de um ponto ausente fica vazia em vez de deslizar o seguinte', () => {
    // O equipamento só apareceu em T-0 e T-3. Deslizar o T-3 para a coluna
    // T-1 sugeriria uma comparação que não aconteceu — foi o que a tabela
    // por equipamento legada fazia ao reindexar cada equipamento sozinho.
    const slots = pointsByOffset([{ offset: 0, id: 'a' }, { offset: 3, id: 'b' }], 4);
    expect(slots.map((slot) => slot?.id ?? null)).toEqual(['a', null, null, 'b']);
  });

  it('offset fora da faixa de colunas é descartado, não estoura o array', () => {
    expect(pointsByOffset([{ offset: 9 }], 4)).toEqual([null, null, null, null]);
  });

  it('T-0 contra T-1: sem T-1 não há variação (e não vira zero)', () => {
    const delta = sessionDelta([5, null]);
    expect(delta.delta).toBeNull();
    expect(delta.direction).toBe('none');
  });

  it('variação menor que o epsilon é estável — séries efetivas são fracionárias', () => {
    expect(sessionDelta([3.001, 3]).direction).toBe('stable');
    expect(sessionDelta([3.5, 3]).direction).toBe('up');
    expect(sessionDelta([2.5, 3]).direction).toBe('down');
  });

  it('o rótulo da coluna é T-n', () => {
    expect([0, 1, 2, 3].map(sessionOffsetLabel)).toEqual(['T-0', 'T-1', 'T-2', 'T-3']);
  });

  it('equipamento não registrado é dito, não inventado', () => {
    expect(equipmentLabel(null)).toBe('Equipamento não registrado');
    expect(equipmentLabel('   ')).toBe('Equipamento não registrado');
    expect(equipmentLabel('Supino reto')).toBe('Supino reto');
  });
});
