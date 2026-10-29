import { describe, expect, it } from 'vitest';
import { groupSessionsBySlot, itemStatusEntries, ratePercent, totalItems } from './dashboard-workouts';
import type { ItemStatusCounts, WorkoutSession, WorkoutSessionGroup } from './dashboard-workouts-types';

const METRICA = {
  value: 1,
  unit: null,
  nature: 'OBSERVED' as const,
  metric_version: 'sets-v1',
  data_quality: 'HIGH' as const,
  limitations: [],
};

const CONTAGEM: ItemStatusCounts = {
  completed: 0,
  interrupted: 0,
  skipped: 0,
  substituted: 0,
  extra: 0,
};

function sessao(overrides: Partial<WorkoutSession> & { activity_id: number }): WorkoutSession {
  return {
    local_date: '2026-09-20',
    started_at: null,
    name: 'Treino',
    session_status: 'DONE',
    duration_seconds: 3600,
    exercises_count: 5,
    physical_sets: METRICA,
    effective_sets_stimulus: METRICA,
    effective_sets_fatigue: METRICA,
    avg_rpe: METRICA,
    records: METRICA,
    progressions: METRICA,
    item_status_counts: { ...CONTAGEM },
    plan_link: null,
    group: grupo({}),
    data_quality: 'HIGH',
    limitations: [],
    ...overrides,
  };
}

function grupo(overrides: Partial<WorkoutSessionGroup>): WorkoutSessionGroup {
  return {
    key: 'unlinked',
    kind: 'unlinked',
    label: 'Treinos sem ficha',
    template_id: null,
    template_name: null,
    lineage_template_id: null,
    slot_sequence: null,
    ...overrides,
  };
}

function slot(
  templateId: number,
  sequence: number,
  label: string,
  templateName = 'Modelo',
  lineageId = templateId,
): WorkoutSessionGroup {
  // Fixture: a chave é a que o dono manda pronta (linhagem + posição).
  return grupo({
    key: ['slot', lineageId, sequence].join(':'),
    kind: 'slot',
    label,
    template_id: templateId,
    template_name: templateName,
    lineage_template_id: lineageId,
    slot_sequence: sequence,
  });
}

describe('agrupamento pelo grupo pronto do backend', () => {
  it('renomear o slot e o Modelo não parte o grupo; o rótulo é o da sessão mais recente', () => {
    const grupos = groupSessionsBySlot([
      sessao({ activity_id: 2, local_date: '2026-10-23', group: slot(36, 2, 'Pull - SEX', 'PPL-D-UL-D') }),
      sessao({ activity_id: 1, local_date: '2026-10-16', group: slot(36, 2, 'Pull', 'PPL antigo') }),
    ]);
    expect(grupos).toHaveLength(1);
    expect(grupos[0].label).toBe('Pull - SEX');
    expect(grupos[0].templateName).toBe('PPL-D-UL-D');
    expect(grupos[0].sessions.map((s) => s.activity_id)).toEqual([1, 2]);
  });

  it('Modelo duplicado (mesma linhagem) junta com o de origem; rótulo e Modelo da sessão mais recente', () => {
    const grupos = groupSessionsBySlot([
      sessao({ activity_id: 3, local_date: '2026-10-23', group: slot(36, 2, 'Pull - SEX', 'PPL Ponderado', 34) }),
      sessao({ activity_id: 4, local_date: '2026-10-22', group: slot(36, 1, 'Push', 'PPL Ponderado', 34) }),
      sessao({ activity_id: 1, local_date: '2026-10-16', group: slot(34, 2, 'Pull', 'PPL', 34) }),
      sessao({ activity_id: 9, local_date: '2026-10-20', group: slot(28, 1, 'A', 'ABC') }),
    ]);
    expect(grupos.map((g) => g.key)).toEqual(['slot:34:1', 'slot:34:2', 'slot:28:1']);
    const pull = grupos[1];
    expect(pull.sessions.map((s) => s.activity_id)).toEqual([1, 3]);
    expect(pull.label).toBe('Pull - SEX');
    expect(pull.templateName).toBe('PPL Ponderado');
    expect(pull.templateId).toBe(36);
  });

  it('mesmo nome em Modelos ou posições diferentes não funde', () => {
    const grupos = groupSessionsBySlot([
      sessao({ activity_id: 1, group: slot(34, 2, 'Pull') }),
      sessao({ activity_id: 2, group: slot(36, 2, 'Pull') }),
      sessao({ activity_id: 3, group: slot(36, 3, 'Pull') }),
    ]);
    expect(grupos.map((g) => g.key).sort()).toEqual(['slot:34:2', 'slot:36:2', 'slot:36:3']);
  });

  it('ordena por Modelo mais recente e, dentro dele, pela sequência do ciclo', () => {
    const grupos = groupSessionsBySlot([
      sessao({ activity_id: 1, local_date: '2026-10-10', group: slot(28, 1, 'A') }),
      sessao({ activity_id: 2, local_date: '2026-10-22', group: slot(36, 3, 'Legs') }),
      sessao({ activity_id: 3, local_date: '2026-10-21', group: slot(36, 1, 'Push') }),
      sessao({ activity_id: 4, local_date: '2026-10-09', group: slot(28, 2, 'B') }),
    ]);
    expect(grupos.map((g) => g.key)).toEqual(['slot:36:1', 'slot:36:3', 'slot:28:1', 'slot:28:2']);
  });

  it('fichas sem Modelo e treinos sem ficha ficam em grupos únicos, por último', () => {
    const semModelo = grupo({ key: 'no_template', kind: 'no_template', label: 'Fichas sem Modelo' });
    const grupos = groupSessionsBySlot([
      sessao({ activity_id: 1, name: 'Treino A', group: semModelo }),
      sessao({ activity_id: 2, name: 'Treino A', group: semModelo }),
      sessao({ activity_id: 3, group: grupo({}) }),
      sessao({ activity_id: 4, group: slot(9, 1, 'Upper 1') }),
    ]);
    expect(grupos.map((g) => g.kind)).toEqual(['slot', 'no_template', 'unlinked']);
    expect(grupos[1].sessions).toHaveLength(2);
  });
});

describe('contagem de itens por status', () => {
  it('omite os zeros e respeita a ordem canônica', () => {
    const entradas = itemStatusEntries({
      ...CONTAGEM,
      extra: 1,
      completed: 3,
      skipped: 2,
    });
    expect(entradas).toEqual([
      { code: 'COMPLETED', count: 3 },
      { code: 'SKIPPED', count: 2 },
      { code: 'EXTRA', count: 1 },
    ]);
  });

  it('sessão sem item classificado devolve lista vazia', () => {
    expect(itemStatusEntries({ ...CONTAGEM })).toEqual([]);
    expect(totalItems({ ...CONTAGEM })).toBe(0);
  });

  it('total soma os cinco status', () => {
    expect(
      totalItems({ completed: 1, interrupted: 2, skipped: 3, substituted: 4, extra: 5 }),
    ).toBe(15);
  });
});

describe('taxa de aderência', () => {
  it('converte para pontos percentuais', () => {
    expect(ratePercent(0.75)).toBe(75);
    expect(ratePercent(0)).toBe(0);
  });

  it('ausência continua ausência — nunca 0%', () => {
    expect(ratePercent(null)).toBeNull();
    expect(ratePercent(undefined)).toBeNull();
  });
});
