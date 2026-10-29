import { describe, expect, it, vi } from 'vitest';

import {
  MAX_SAFE_RPE_OPTIONS,
  buildBatchSafetyPayload,
  buildEquipmentSafetyPayload,
  buildEquipmentTechniquesPayload,
  buildPairSafetyPayload,
  describeEquipmentMaxSafeRpe,
  describeInheritedTechnique,
  describePairMaxSafeRpe,
  equipmentOfType,
  equipmentSafetyToForm,
  formatLoadIncrement,
  hasPairOverride,
  isBlockedByFailure,
  pairSafetyToForm,
  pairTechniqueChoice,
  pairTechniqueCompatible,
  parseLoadIncrement,
  runBatch,
  selectValueToRpe,
  type EquipmentExerciseSafety,
  type EquipmentSafety,
} from './training-safety';

const CATALOG = [
  { code: 'rest_pause', is_active: true, failure_requirement: 'requires_failure' as const },
  { code: 'drop_set', is_active: true, failure_requirement: 'requires_failure' as const },
  { code: 'gvt', is_active: true, failure_requirement: 'failure_not_required' as const },
  { code: 'cheat_reps', is_active: false, failure_requirement: 'beyond_failure' as const },
];

const EFFECTIVE = {
  max_safe_rpe: 9,
  failure_allowed: false,
  safety_source: null,
  allowed_advanced_techniques: [],
  blocked_advanced_techniques: [],
};

function equipmentSafety(extra: Partial<EquipmentSafety> = {}): EquipmentSafety {
  return {
    equipment_id: 42,
    equipment_name: 'Supino articulado',
    equipment_type_id: 2,
    max_safe_rpe: null,
    load_increment_kg: null,
    safety_notes: null,
    techniques: {},
    effective: EFFECTIVE,
    ...extra,
  };
}

function pairSafety(extra: Partial<EquipmentExerciseSafety> = {}): EquipmentExerciseSafety {
  return {
    equipment_exercise_id: 118,
    equipment_id: 42,
    equipment_name: 'Supino articulado',
    exercise_id: 7,
    exercise_name: 'Supino reto',
    is_active: true,
    max_safe_rpe_override: null,
    safety_notes: null,
    techniques: {},
    inherited: { max_safe_rpe: null, techniques: {} },
    resolved: EFFECTIVE,
    ...extra,
  };
}

describe('RPE máximo seguro', () => {
  it('select vai de 6 a 10 e só o 10 fala de falha', () => {
    expect(MAX_SAFE_RPE_OPTIONS.map((o) => o.value)).toEqual(['6', '7', '8', '9', '10']);
    expect(MAX_SAFE_RPE_OPTIONS[4].label).toBe('10 = falha concêntrica permitida');
    expect(MAX_SAFE_RPE_OPTIONS.slice(0, 4).every((o) => o.label === o.value)).toBe(true);
  });

  it('vazio ou fora da faixa vira null (desconhecido)', () => {
    expect(selectValueToRpe(null)).toBeNull();
    expect(selectValueToRpe('')).toBeNull();
    expect(selectValueToRpe('5')).toBeNull();
    expect(selectValueToRpe('11')).toBeNull();
    expect(selectValueToRpe('9.5')).toBeNull();
    expect(selectValueToRpe('10')).toBe(10);
  });

  it('desconhecido é exibido como 9 (D3)', () => {
    expect(describeEquipmentMaxSafeRpe(null)).toBe('9 (padrão: não cadastrado)');
    expect(describeEquipmentMaxSafeRpe(undefined)).toBe('9 (padrão: não cadastrado)');
    expect(describeEquipmentMaxSafeRpe(10)).toBe('10');
  });

  it('par: sobrescrito > herdado > padrão 9', () => {
    expect(describePairMaxSafeRpe(8, 10)).toEqual({ value: 8, origin: 'override', label: '8 (sobrescrito neste par)' });
    expect(describePairMaxSafeRpe(null, 10)).toEqual({ value: 10, origin: 'inherited', label: '10 (herdado do equipamento)' });
    expect(describePairMaxSafeRpe(null, null)).toMatchObject({ value: 9, origin: 'default' });
  });
});

describe('incremento de carga total', () => {
  it('aceita vírgula e ponto; vazio = não cadastrado', () => {
    expect(parseLoadIncrement('')).toEqual({ value: null, error: null });
    expect(parseLoadIncrement('  ')).toEqual({ value: null, error: null });
    expect(parseLoadIncrement('2,5')).toEqual({ value: 2.5, error: null });
    expect(parseLoadIncrement('2.5')).toEqual({ value: 2.5, error: null });
    expect(parseLoadIncrement('2')).toEqual({ value: 2, error: null });
    expect(parseLoadIncrement('999,99').value).toBe(999.99);
  });

  it.each(['0', '0,00', '-1', '1,255', 'abc', '1000', '2,5kg', '1e3'])('rejeita %s', (text) => {
    const result = parseLoadIncrement(text);
    expect(result.value).toBeNull();
    expect(result.error).toBeTruthy();
  });

  it('formata com vírgula', () => {
    expect(formatLoadIncrement(2.5)).toBe('2,5');
    expect(formatLoadIncrement(null)).toBe('');
  });
});

describe('formulário do equipamento', () => {
  it('carrega só as técnicas marcadas compatíveis', () => {
    const form = equipmentSafetyToForm(
      equipmentSafety({ max_safe_rpe: 10, load_increment_kg: 2.5, safety_notes: 'ok', techniques: { rest_pause: true, gvt: false } }),
    );
    expect(form.maxSafeRpe).toBe('10');
    expect(form.loadIncrement).toBe('2,5');
    expect([...form.checkedTechniques]).toEqual(['rest_pause']);
  });

  it('salvar marca TODAS as técnicas ativas: marcada = true, desmarcada = false; inativa não é tocada', () => {
    expect(buildEquipmentTechniquesPayload(CATALOG, new Set(['rest_pause', 'cheat_reps']))).toEqual({
      rest_pause: true,
      drop_set: false,
      gvt: false,
    });
  });

  it('payload completo; vazio vira null', () => {
    const result = buildEquipmentSafetyPayload(
      { maxSafeRpe: null, loadIncrement: '', safetyNotes: '   ', checkedTechniques: new Set() },
      CATALOG,
    );
    expect(result.payload).toEqual({
      max_safe_rpe: null,
      load_increment_kg: null,
      safety_notes: null,
      techniques: { rest_pause: false, drop_set: false, gvt: false },
    });
  });

  it('erro de incremento ou de notas bloqueia o envio', () => {
    const base = { maxSafeRpe: '9', loadIncrement: '2', safetyNotes: '', checkedTechniques: new Set<string>() };
    expect(buildEquipmentSafetyPayload({ ...base, loadIncrement: '0' }, CATALOG).payload).toBeNull();
    expect(buildEquipmentSafetyPayload({ ...base, safetyNotes: 'x'.repeat(2001) }, CATALOG).error).toMatch(/2000/);
    expect(buildEquipmentSafetyPayload(base, CATALOG).error).toBeNull();
  });

  it('avisa técnica que exige falha com RPE efetivo abaixo de 10', () => {
    expect(isBlockedByFailure(CATALOG[0], 9)).toBe(true);
    expect(isBlockedByFailure(CATALOG[0], 10)).toBe(false);
    expect(isBlockedByFailure(CATALOG[2], 6)).toBe(false);
  });
});

describe('edição em lote por tipo', () => {
  const form = {
    applyMaxSafeRpe: false,
    maxSafeRpe: '10',
    applyLoadIncrement: false,
    loadIncrement: '5',
    applyTechniques: false,
    checkedTechniques: new Set(['gvt']),
  };

  it('só os campos ligados entram no payload (o resto fica intocado no PUT)', () => {
    expect(buildBatchSafetyPayload({ ...form, applyMaxSafeRpe: true }, CATALOG).payload).toEqual({ max_safe_rpe: 10 });
    expect(buildBatchSafetyPayload({ ...form, applyLoadIncrement: true }, CATALOG).payload).toEqual({ load_increment_kg: 5 });
    expect(buildBatchSafetyPayload({ ...form, applyTechniques: true }, CATALOG).payload).toEqual({
      techniques: { rest_pause: false, drop_set: false, gvt: true },
    });
    expect(buildBatchSafetyPayload({ ...form, applyMaxSafeRpe: true, maxSafeRpe: null }, CATALOG).payload).toEqual({ max_safe_rpe: null });
  });

  it('nunca inclui notas e exige ao menos um campo', () => {
    const all = buildBatchSafetyPayload({ ...form, applyMaxSafeRpe: true, applyLoadIncrement: true, applyTechniques: true }, CATALOG);
    expect(Object.keys(all.payload ?? {})).toEqual(['max_safe_rpe', 'load_increment_kg', 'techniques']);
    expect(buildBatchSafetyPayload(form, CATALOG).error).toMatch(/ao menos um campo/);
    expect(buildBatchSafetyPayload({ ...form, applyLoadIncrement: true, loadIncrement: 'x' }, CATALOG).payload).toBeNull();
  });

  it('filtra equipamentos pelo tipo', () => {
    const equipment = [
      { id: 1, equipment_type_id: 2 },
      { id: 2, equipment_type_id: 3 },
      { id: 3, equipment_type_id: null },
      { id: 4 },
      { id: 5, equipment_type_id: 2 },
    ];
    expect(equipmentOfType(equipment, 2).map((e) => e.id)).toEqual([1, 5]);
    expect(equipmentOfType(equipment, null)).toEqual([]);
  });

  it('runBatch segue após falha e reporta progresso', async () => {
    const save = vi.fn(async (id: number) => {
      if (id === 2) throw new Error('422 faixa');
    });
    const progress: Array<[number, number]> = [];
    const outcomes = await runBatch([1, 2, 3], save, (done, total) => progress.push([done, total]));
    expect(save).toHaveBeenCalledTimes(3);
    expect(outcomes).toEqual([
      { id: 1, ok: true },
      { id: 2, ok: false, error: '422 faixa' },
      { id: 3, ok: true },
    ]);
    expect(progress).toEqual([[1, 3], [2, 3], [3, 3]]);
  });
});

describe('override por par', () => {
  it('escolha: herdar × compatível × incompatível', () => {
    const overrides = { drop_set: false, gvt: true };
    expect(pairTechniqueChoice('rest_pause', overrides)).toBe('inherit');
    expect(pairTechniqueChoice('drop_set', overrides)).toBe('incompatible');
    expect(pairTechniqueChoice('gvt', overrides)).toBe('compatible');
  });

  it('herdado: sem marcação no equipamento = incompatível (D3)', () => {
    const inherited = { rest_pause: true, gvt: false };
    expect(describeInheritedTechnique('rest_pause', inherited)).toBe('Compatível (equipamento)');
    expect(describeInheritedTechnique('gvt', inherited)).toBe('Incompatível (equipamento)');
    expect(describeInheritedTechnique('drop_set', inherited)).toBe('Incompatível (equipamento sem marcação)');
    expect(pairTechniqueCompatible('inherit', 'rest_pause', inherited)).toBe(true);
    expect(pairTechniqueCompatible('inherit', 'drop_set', inherited)).toBe(false);
    expect(pairTechniqueCompatible('incompatible', 'rest_pause', inherited)).toBe(false);
    expect(pairTechniqueCompatible('compatible', 'drop_set', inherited)).toBe(true);
  });

  it('payload só leva a técnica que mudou; herdar = null (apaga o override)', () => {
    const saved = pairSafety({ max_safe_rpe_override: 8, techniques: { drop_set: false, gvt: true } });
    const form = pairSafetyToForm(saved, CATALOG);
    expect(form.choices).toEqual({ rest_pause: 'inherit', drop_set: 'incompatible', gvt: 'compatible', cheat_reps: 'inherit' });

    expect(buildPairSafetyPayload(form, saved).payload).toEqual({ max_safe_rpe_override: 8, safety_notes: null });

    const changed = { ...form, maxSafeRpeOverride: null, choices: { ...form.choices, drop_set: 'inherit' as const, rest_pause: 'compatible' as const } };
    expect(buildPairSafetyPayload(changed, saved).payload).toEqual({
      max_safe_rpe_override: null,
      safety_notes: null,
      techniques: { drop_set: null, rest_pause: true },
    });
  });

  it('detecta se o par tem algo sobrescrito', () => {
    expect(hasPairOverride(pairSafety())).toBe(false);
    expect(hasPairOverride(pairSafety({ max_safe_rpe_override: 9 }))).toBe(true);
    expect(hasPairOverride(pairSafety({ techniques: { gvt: false } }))).toBe(true);
  });
});
