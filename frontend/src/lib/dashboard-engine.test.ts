/**
 * Unit: a apresentação de `/dashboard/engine` (F3.1).
 *
 * O que estes testes travam é a fronteira que a tela existe para manter:
 * - a coluna "planejado" do modo `percent_budget` NUNCA nomeia um exercício;
 * - um código de motivo desconhecido aparece CRU, nunca como "outro";
 * - item sem execução não vira "0 séries";
 * - a data da geração não é inventada para a ficha lida do `raw_payload`.
 */

import { describe, expect, it } from 'vitest';
import {
  AUDIT_SOURCE_META,
  collectEngineLimitations,
  executedSummary,
  generatedSummary,
  plannedSummary,
  reasonCodeLabel,
  reasonText,
  runTimestampLabel,
} from './dashboard-engine';
import type {
  EngineItem,
  EnginePlanResponse,
  EnginePlanned,
  EngineRun,
} from './dashboard-engine-types';

const PLANEJADO_VAZIO: EnginePlanned = {
  source: 'unavailable',
  item_kind: null,
  item_order: null,
  muscle_group_id: null,
  muscle_group_name: null,
  exercise_id: null,
  exercise_name: null,
  equipment_id: null,
  sets_count: null,
  reps_target: null,
  rpe_target: null,
  advanced_technique: null,
  prioritize: null,
  budget_min_sets: null,
  budget_max_sets: null,
  budget_weekly_sets: null,
};

function item(patch: Partial<EngineItem> = {}): EngineItem {
  return {
    plan_exercise_id: 1,
    plan_sequence: 1,
    status: 'COMPLETED',
    planned: PLANEJADO_VAZIO,
    generated_name: 'Supino reto',
    generated_exercise_id: 10,
    generated: {
      source: 'frozen_snapshot',
      sets_count: 3,
      reps_target: '8-10',
      load_target: '60',
      rpe_target: '8',
      rest_seconds: 90,
      engine_version: 'v2',
    },
    executed: null,
    missing_sets: null,
    substitution: null,
    reasons: [],
    progression: null,
    limitations: [],
    ...patch,
  };
}

function run(patch: Partial<EngineRun> = {}): EngineRun {
  return {
    run_id: 7,
    source: 'generation_runs',
    created_at: '2026-09-26T10:00:00',
    plan_date: '2026-09-26',
    workout_plan_id: 99,
    plan_name: 'Ficha A',
    template_id: 3,
    template_slot_sequence: 1,
    volume_mode: 'percent_budget',
    engine_version: 'v2',
    llm_used: false,
    job_id: null,
    selection_count: 4,
    safety_clamp_count: 1,
    substitution_count: 0,
    fallback_count: 0,
    limitations: [],
    ...patch,
  };
}

describe('plannedSummary', () => {
  it('nomeia o exercicio quando o Modelo o nomeou', () => {
    const texto = plannedSummary({
      ...PLANEJADO_VAZIO,
      source: 'template_item',
      exercise_name: 'Supino reto',
      sets_count: 3,
      reps_target: '8-10',
      rpe_target: '8',
    });
    expect(texto).toBe('Supino reto · 3x · 8-10 · RPE 8');
  });

  it('item livre do Modelo mostra o grupo e os alvos do item (M101d)', () => {
    const texto = plannedSummary({
      ...PLANEJADO_VAZIO,
      source: 'template_item',
      free_exercise: true,
      muscle_group_name: 'Costas',
      sets_count: 4,
      reps_target: '8-10',
      rpe_target: '8',
    });
    expect(texto).toBe('Costas (exercício escolhido pelo motor) · 4x · 8-10 · RPE 8');
  });

  it('no orcamento por grupo NAO nomeia exercicio nenhum', () => {
    const texto = plannedSummary({
      ...PLANEJADO_VAZIO,
      source: 'muscle_group_budget',
      muscle_group_name: 'Peitoral',
      exercise_name: 'Supino reto', // ainda que venha preenchido, nao pode aparecer
      budget_min_sets: 6,
      budget_max_sets: 9,
    });
    expect(texto).toContain('Peitoral');
    expect(texto).not.toContain('Supino');
  });

  it('sem Modelo devolve tracinho, nao um palpite', () => {
    expect(plannedSummary(PLANEJADO_VAZIO)).toBe('—');
  });
});

describe('reasonCodeLabel', () => {
  it('traduz os codigos que os motores emitem hoje', () => {
    expect(reasonCodeLabel('NO_DIRECT_WORK')).toBe('a recuperação do grupo não permitia trabalho direto');
  });

  it('codigo desconhecido aparece CRU - nunca "outro"', () => {
    expect(reasonCodeLabel('MOTOR_V3_MOTIVO_NOVO')).toBe('MOTOR_V3_MOTIVO_NOVO');
  });

  it('ausencia de codigo nao vira texto', () => {
    expect(reasonCodeLabel(null)).toBeNull();
  });
});

describe('reasonText', () => {
  it('junta tipo, codigo e detalhe do motor', () => {
    const texto = reasonText({
      kind: 'safety_clamp',
      code: 'RECOVERY',
      detail: 'RPE 9 -> 8',
      data: { field: 'rpe_target' },
    });
    expect(texto).toBe('Limite de segurança · teto imposto pela recuperação do grupo · RPE 9 -> 8');
  });
});

describe('executedSummary', () => {
  it('item sem execucao NAO vira "0 series"', () => {
    expect(executedSummary(item({ executed: null, status: 'SKIPPED' }))).toBe('—');
  });

  it('zero serie REGISTRADA continua sendo zero', () => {
    const texto = executedSummary(
      item({
        executed: {
          exercise_row_id: 5,
          exercise_id: 10,
          name: 'Supino reto',
          position_in_session: 1,
          working_sets: 0,
          total_sets: 2,
          total_reps: null,
          best_load_kg: null,
          avg_rpe: null,
          sets: [],
        },
      }),
    );
    expect(texto).toBe('0 série(s) de trabalho');
  });
});

describe('generatedSummary', () => {
  it('monta a prescricao da ficha', () => {
    expect(generatedSummary(item())).toBe('3x · 8-10 · 60 kg · RPE 8');
  });
});

describe('runTimestampLabel', () => {
  it('a ficha lida do raw_payload NAO ganha data de geracao inventada', () => {
    const texto = runTimestampLabel(run({ source: 'plan_raw_payload', created_at: '2026-01-02T03:04:05' }));
    expect(texto).toContain('data da geração não registrada');
  });

  it('a linha de generation_runs mostra o instante real', () => {
    expect(runTimestampLabel(run())).not.toContain('não registrada');
  });
});

describe('AUDIT_SOURCE_META', () => {
  it('as tres fontes tem frases DIFERENTES', () => {
    const frases = new Set(Object.values(AUDIT_SOURCE_META).map((meta) => meta.hint));
    expect(frases.size).toBe(3);
  });
});

describe('collectEngineLimitations', () => {
  it('nao repete a mesma frase vinda de varios itens', () => {
    const frase = 'Um ou mais itens não têm decisão de progressão vinculada.';
    const payload = {
      limitations: [frase],
      run: run({ limitations: [] }),
      items: [item({ limitations: [frase] }), item({ limitations: [frase] })],
    } as unknown as EnginePlanResponse;
    expect(collectEngineLimitations(payload)).toEqual([frase]);
  });
});
