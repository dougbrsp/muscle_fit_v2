// M97 F5: objetivo do Modelo e objetivo por dia no pedido de periodização e no
// relatório do validador. Os valores de objetivo são opacos aqui: a tela só
// repassa o que `GET /training-goals` devolveu (nenhum literal de objetivo).

import { describe, expect, it } from 'vitest';
import {
  buildPeriodizationRequest,
  describePeriodizationEntry,
  newPeriodizationForm,
  periodizationDayGoals,
  periodizationJobResultToSeed,
} from './periodization';
import { PERIODIZATION_REPORT_CODE_LABEL } from './periodization-types';
import type { PeriodizationFormState, PeriodizationJobResult } from './periodization-types';

function form(overrides: Partial<PeriodizationFormState> = {}): PeriodizationFormState {
  return { ...newPeriodizationForm(), ...overrides };
}

function jobResult(trainingGoal?: string): PeriodizationJobResult {
  return {
    draft: {
      name: 'Híbrido',
      description: null,
      training_type_id: null,
      volume_mode: 'equipment_series',
      ...(trainingGoal ? { training_goal: trainingGoal } : {}),
      is_active: true,
      generation_log_id: 1,
      slots: [],
    },
    validation_report: { errors: [], warnings: [], auto_fixes: [] },
    rationale: '',
    source_citations: [],
    generation_log_id: 1,
    model: 'm',
    prompt_version: 'periodization-v12',
  } as PeriodizationJobResult;
}

describe('objetivo por dia no pedido', () => {
  it('sem escolha, o corpo é o de sempre (sem training_goal nem day_goals)', () => {
    const request = buildPeriodizationRequest(form(), 'obj-default');
    expect(request).not.toHaveProperty('training_goal');
    expect(request).not.toHaveProperty('day_goals');
  });

  it('acompanha o número de dias sem perder as escolhas', () => {
    expect(periodizationDayGoals({ daysPerCycle: 3, dayGoals: ['obj-b'] })).toEqual(['obj-b', null, null]);
    expect(periodizationDayGoals({ daysPerCycle: 2, dayGoals: ['obj-b', null, 'obj-b'] })).toEqual(['obj-b', null]);
  });

  it('dia que herda vira o objetivo do Modelo; sem Modelo, o default do dono', () => {
    const hibrido = form({ daysPerCycle: 5, dayGoals: ['obj-b', null, 'obj-b', null, null] });
    expect(buildPeriodizationRequest(hibrido, 'obj-default').day_goals).toEqual([
      'obj-b',
      'obj-default',
      'obj-b',
      'obj-default',
      'obj-default',
    ]);
    const comModelo = form({ daysPerCycle: 2, trainingGoal: 'obj-b', dayGoals: [null, 'obj-default'] });
    expect(buildPeriodizationRequest(comModelo, 'obj-default')).toMatchObject({
      training_goal: 'obj-b',
      day_goals: ['obj-b', 'obj-default'],
    });
  });

  it('sem o default carregado não manda lista incompleta', () => {
    const request = buildPeriodizationRequest(form({ daysPerCycle: 2, dayGoals: ['obj-b', null] }), null);
    expect(request).not.toHaveProperty('day_goals');
  });

  it('o rascunho leva o objetivo do Modelo para o builder', () => {
    expect(periodizationJobResultToSeed(jobResult()).header.trainingGoal).toBeUndefined();
    expect(periodizationJobResultToSeed(jobResult('obj-b')).header.trainingGoal).toBe('obj-b');
  });
});

describe('relatório do objetivo', () => {
  it('descreve os auto-fixes e o aviso com os rótulos vindos do backend', () => {
    expect(
      describePeriodizationEntry({
        code: 'ISOLATION_GOAL_OVERRIDE',
        slot: 1,
        item: 3,
        day_goal_label: 'Objetivo B',
        training_goal_label: 'Objetivo A',
      }),
    ).toBe('Isolador (dia 1, item 3) num dia de Objetivo B ficou no objetivo Objetivo A (faixa moderada, não carga máxima).');
    expect(
      describePeriodizationEntry({
        code: 'GOAL_REPS_FIXED',
        slot: 2,
        item: 1,
        from: '8-12',
        to: '3-5',
        training_goal_label: 'Objetivo B',
      }),
    ).toBe('Repetições (dia 2, item 1) ajustadas de 8-12 para 3-5: a faixa do objetivo Objetivo B.');
    expect(
      describePeriodizationEntry({
        code: 'DAY_HEAVY_COMPOUNDS_OUT_OF_RANGE',
        slot: 3,
        slot_name: 'Inferiores',
        training_goal_label: 'Objetivo B',
        compound_count: 1,
        min: 2,
        max: 4,
      }),
    ).toBe('Dia 3 (Inferiores) de Objetivo B tem 1 composto(s) pesado(s); o objetivo pede de 2 a 4.');
    expect(
      describePeriodizationEntry({
        code: 'RPE_CLAMPED',
        slot: 1,
        item: 1,
        from: '10',
        to: '9',
        training_goal_label: 'Objetivo B',
      }),
    ).toBe('RPE ajustado de 10 para 9 (dia 1, item 1) pelo teto do objetivo Objetivo B.');
    expect(
      describePeriodizationEntry({
        code: 'TECHNIQUE_REMOVED',
        slot: 1,
        item: 2,
        technique: 'drop_set',
        training_goal_label: 'Objetivo B',
      }),
    ).toBe('Técnica "drop_set" removida (dia 1, item 2): fora da política do objetivo Objetivo B.');
  });

  it('o RPE cortado pelo equipamento continua com o texto de sempre', () => {
    expect(describePeriodizationEntry({ code: 'RPE_CLAMPED', slot: 1, item: 1, from: '10', to: '9' })).toBe(
      'RPE ajustado de 10 para 9 (dia 1, item 1) pelo teto seguro do equipamento.',
    );
  });

  it('os códigos novos têm rótulo em português', () => {
    for (const code of ['ISOLATION_GOAL_OVERRIDE', 'GOAL_REPS_FIXED', 'DAY_HEAVY_COMPOUNDS_OUT_OF_RANGE']) {
      expect(PERIODIZATION_REPORT_CODE_LABEL[code]).toBeTruthy();
    }
  });
});
