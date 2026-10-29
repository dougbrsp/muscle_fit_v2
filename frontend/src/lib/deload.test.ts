import { describe, expect, it } from 'vitest';
import parity from './training-goal.parity.json';
import {
  deloadBannerTitle,
  deloadLoadOrigin,
  deloadPrescriptionText,
  deloadRuleText,
  periodRangeLabel,
  shortDate,
  startPeriodLabel,
  PAIR_SUGGESTION_QUERY_ROOT,
  WORKOUT_MODE_PLAN_QUERY_ROOT,
  pairSuggestionPath,
  workoutModePlanPath,
  workoutModePlanQueryKey,
  type DeloadConstants,
  type DeloadPrescription,
} from './deload';
import { goalDefaultRestSeconds, techniqueForGoal, type TrainingGoalsResponse } from './training-goals';

// Números REAIS do dono (gerados por api/tests/unit/test_m97_f3_objetivo_telas.py --gravar).
const constants = parity.deload_constants as DeloadConstants;
const prescription = parity.deload_prescription as DeloadPrescription;

describe('textos do deload interpolam os números do dono', () => {
  it('regra', () => {
    const text = deloadRuleText(constants);
    expect(text).toContain(`${Math.round(constants.load_factor * 100)}%`);
    expect(text).toContain(`RPE-alvo ${constants.target_rpe}`);
    expect(text).toContain(`${Math.round(constants.sets_reduction_fraction * 100)}%`);
    expect(deloadRuleText(null)).not.toMatch(/\d/);
  });
  it('botão do período usa a duração default', () => {
    expect(startPeriodLabel(constants)).toContain(`${constants.period_default_days} dias`);
    expect(startPeriodLabel(null)).toBe('Iniciar semana de deload');
  });
  it('prescrição do item', () => {
    const text = deloadPrescriptionText(prescription)!;
    expect(text).toContain(`${prescription.sets_count} séries × ${prescription.reps_target} reps`);
    expect(text).toContain(`RPE ${prescription.target_rpe}`);
    expect(text).toContain(String(prescription.load_kg).replace('.', ','));
    expect(deloadLoadOrigin(prescription)).toContain(`${Math.round(prescription.load_factor * 100)}% de`);
    expect(deloadPrescriptionText(null)).toBeNull();
    expect(deloadPrescriptionText({ ...prescription, load_kg: null, sets_count: null })).toBe(
      `${prescription.reps_target} reps · RPE ${prescription.target_rpe}`,
    );
  });
});

describe('banner e datas', () => {
  const period = { id: 1, starts_on: '2026-10-20', ends_on: '2026-10-26' };
  it('título só com deload ativo', () => {
    expect(deloadBannerTitle({ active: false, source: null, period: null })).toBeNull();
    expect(deloadBannerTitle({ active: true, source: 'manual', period: null })).toBe('Sessão de deload');
    expect(deloadBannerTitle({ active: true, source: 'period', period })).toBe('Semana de deload (20/10 a 26/10)');
  });
  it('datas curtas sem fuso', () => {
    expect(shortDate('2026-10-05')).toBe('05/10');
    expect(periodRangeLabel(null)).toBe('');
  });
});

describe('Modo Treino por objetivo (dados da API)', () => {
  const goals = parity.payload as TrainingGoalsResponse;
  const strength = goals.items.find((item) => item.disallowed_techniques.length > 0)!;
  const hypertrophy = goals.items.find((item) => item.is_default)!;
  it('técnica proibida pelo objetivo não é oferecida; as demais seguem', () => {
    const blocked = strength.disallowed_techniques[0];
    expect(techniqueForGoal(goals, strength.value, blocked)).toBeNull();
    expect(techniqueForGoal(goals, hypertrophy.value, blocked)).toBe(blocked);
    expect(techniqueForGoal(goals, null, blocked)).toBe(blocked);
    expect(techniqueForGoal(null, strength.value, blocked)).toBe(blocked);
    expect(techniqueForGoal(goals, strength.value, null)).toBeNull();
  });
  it('linha sem descanso usa o descanso do perfil do objetivo', () => {
    expect(goalDefaultRestSeconds(goals, strength.value, -1)).toBe(strength.defaults.rest_seconds);
    expect(goalDefaultRestSeconds(goals, null, -1)).toBe(hypertrophy.defaults.rest_seconds);
    expect(goalDefaultRestSeconds(null, strength.value, -1)).toBe(-1);
  });
});

describe('ficha do Modo Treino pela sessão (fix M97)', () => {
  it('pede a ficha com session_id para o servidor aplicar o deload', () => {
    expect(workoutModePlanPath(3, 9)).toBe('/workout-plans/3?session_id=9');
    expect(workoutModePlanPath(3, null)).toBe('/workout-plans/3');
  });

  it('a chave começa pela raiz invalidada ao ligar/desligar o deload', () => {
    const key = workoutModePlanQueryKey(3, 9);
    expect(key[0]).toBe(WORKOUT_MODE_PLAN_QUERY_ROOT);
    expect(key).toEqual([WORKOUT_MODE_PLAN_QUERY_ROOT, 3, 9]);
    expect(workoutModePlanQueryKey(null, 9)).toEqual([WORKOUT_MODE_PLAN_QUERY_ROOT, null, 9]);
  });
});

describe('sugestão do par trocado pela sessão (M97-D2)', () => {
  it('manda a sessão, o item e o objetivo dele para o servidor', () => {
    expect(
      pairSuggestionPath({
        mappingId: 5,
        equipmentLinkId: 33,
        sessionId: 9,
        workoutPlanExerciseId: 7,
        trainingGoal: 'strength',
      }),
    ).toBe(
      '/exercises/suggested-progression?mapping_id=5&equipment_id=33&session_id=9&workout_plan_exercise_id=7&training_goal=strength',
    );
  });

  it('sem sessão, equipamento ou objetivo: o pedido de sempre', () => {
    expect(pairSuggestionPath({ mappingId: 5 })).toBe('/exercises/suggested-progression?mapping_id=5');
    expect(pairSuggestionPath({ mappingId: 5, equipmentLinkId: 33, sessionId: null, trainingGoal: null })).toBe(
      '/exercises/suggested-progression?mapping_id=5&equipment_id=33',
    );
  });

  it('a raiz é a mesma da chave da consulta invalidada ao ligar/desligar o deload', () => {
    expect(PAIR_SUGGESTION_QUERY_ROOT).toBe('suggested-progression');
  });
});
