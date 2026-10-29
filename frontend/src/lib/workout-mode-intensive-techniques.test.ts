// Modo Intensivo, PR 3: GVT, drop set, rest-pause e a família "além da falha".
// Só funções puras; nenhum número de regra escrito aqui (a carga do drop vem
// do espelho do dono `session_loads`, a pausa e o RPE-alvo da prescrição).
import { describe, expect, it } from 'vitest';

import { FALLBACK_ADVANCED_TECHNIQUES, getAdvancedTechniqueSetType } from './advanced-techniques';
import {
  adjustApplicationLoad,
  createIntensiveBlock,
  intensiveApplicationKey,
  intensiveApplicationLabel,
  intensiveApplicationPayload,
  intensiveApplicationPlannedLoad,
  intensiveExtraRepsEnabled,
  intensiveExtraRepsPayload,
  intensivePauseHoldsCompletion,
  intensiveReducer,
  intensiveRestSeconds,
  intensiveSetPayload,
  isIntensiveBlockProfile,
  nextIntensiveSetNumber,
  pauseRemainingSeconds,
  reconcileIntensiveBlock,
  resolveIntensiveProfile,
  undoApplicationLoad,
  type IntensiveBlockState,
  type IntensiveEvent,
  type IntensiveProfile,
} from './workout-mode-intensive';
import { quickAdjustPlannedKg } from './workout-mode-intensive';
import { techniqueApplicationLoad } from './workout-mode-load-suggestions';

const T0 = Date.parse('2026-10-24T12:00:00Z');

function profileOf(
  techniqueCode: string,
  overrides: Partial<Parameters<typeof resolveIntensiveProfile>[0]> = {},
): IntensiveProfile {
  const profile = resolveIntensiveProfile({
    techniqueCode,
    catalog: FALLBACK_ADVANCED_TECHNIQUES,
    parameters: { technique_code: techniqueCode, target_rpe: 9 },
    plannedSetsCount: 4,
    itemRestSeconds: 90,
    ...overrides,
  });
  if (!profile) throw new Error(`perfil esperado: ${techniqueCode}`);
  return profile;
}

function run(state: IntensiveBlockState, ...events: IntensiveEvent[]): IntensiveBlockState {
  return events.reduce(intensiveReducer, state);
}

function complete(index: number, reps: number | null, restSeconds: number, extra: Partial<IntensiveEvent> = {}): IntensiveEvent {
  return {
    type: 'COMPLETE_SET',
    reps,
    clientKey: `k${index}`,
    setNumber: index,
    now: T0 + index * 60_000,
    restSeconds,
    ...extra,
  } as IntensiveEvent;
}

describe('GVT: bloco de séries inteiras, igual ao FST-7', () => {
  it('10 séries da prescrição (sem meta, o default do catálogo) e descanso do item', () => {
    const gvt = profileOf('gvt', { plannedSetsCount: null, itemRestSeconds: 60 });
    expect(gvt).toMatchObject({ stageKind: 'working_block', restBetween: { kind: 'item_rest', seconds: 60 }, targetRpe: 9 });
    expect(gvt.totalSets).toBe(FALLBACK_ADVANCED_TECHNIQUES.find((t) => t.code === 'gvt')?.default_sets_count);
    expect(isIntensiveBlockProfile(gvt)).toBe(true);
    // Sem alongamento cadastrado para o GVT: a tela não inventa orientação.
    expect(gvt.stretchGuidance).toBeNull();
  });

  it('a meta do item vence o default; o bloco percorre as 10 séries com o descanso do item', () => {
    const gvt = profileOf('gvt', { plannedSetsCount: 10, itemRestSeconds: 60 });
    let s = run(createIntensiveBlock(gvt, 60), { type: 'START' });
    for (let i = 1; i < 10; i += 1) {
      s = run(s, complete(i, 10, intensiveRestSeconds(gvt)));
      expect(s.phase).toBe('DESCANSO');
      expect(s.rest?.totalSeconds).toBe(60);
      s = run(s, { type: 'REST_EXPIRED' }, { type: 'START_NEXT' });
    }
    s = run(s, complete(10, 10, 60), { type: 'SAVE_OK', clientKey: 'k1' });
    for (let i = 2; i <= 10; i += 1) s = run(s, { type: 'SAVE_OK', clientKey: `k${i}` });
    expect(s.records).toHaveLength(10);
    expect(s.phase).toBe('REVISAO_FINAL');
  });
});

describe('drop set: etapas sem descanso artificial', () => {
  const drop = profileOf('drop_set', {
    parameters: { technique_code: 'drop_set', applications: 3, pause_seconds: null, target_rpe: 10 },
    techniqueDurationSeconds: null,
  });

  it('perfil de aplicação, sem pausa (nem o descanso do item) e com redução de carga', () => {
    expect(drop).toMatchObject({ stageKind: 'application', restBetween: { kind: 'none' }, loadConstant: false, targetRpe: 10 });
    expect(intensiveRestSeconds(drop)).toBe(0);
    expect(isIntensiveBlockProfile(drop)).toBe(false);
    // Snapshot antigo sem o fator: o catálogo ainda diz que o drop reduz a carga.
    expect(profileOf('drop_set', { parameters: null }).loadConstant).toBe(false);
  });

  it('etapa em destaque: "Drop 2 de 3"', () => {
    expect(intensiveApplicationLabel('Drop', 2, 3)).toBe('Drop 2 de 3');
  });

  it('a redução prevista vem do dono e é gravada como protocol; a extra pelo ajuste rápido como fatigue', () => {
    const protocolKg = techniqueApplicationLoad('drop_set', 50, 2, { step: 2.5 });
    const key = intensiveApplicationKey('item', 5, 2);
    const planned = intensiveApplicationPlannedLoad({
      profile: drop,
      protocolKg,
      previousApplicationKg: 40,
      adjustment: null,
      key,
    });
    expect(planned).toEqual({ kg: protocolKg, source: 'protocol' });

    const fatigueKg = quickAdjustPlannedKg({ plannedKg: protocolKg, unit: 'kg', loadIncrementKg: 2.5, action: 'minus_step' });
    const adjusted = adjustApplicationLoad(null, key, planned, fatigueKg, 'fatigue');
    const afterAdjust = intensiveApplicationPlannedLoad({ profile: drop, protocolKg, previousApplicationKg: 40, adjustment: adjusted, key });
    expect(afterAdjust).toEqual({ kg: fatigueKg, source: 'fatigue' });

    // Desfazer volta à redução do protocolo; outra etapa não herda o ajuste.
    const undone = undoApplicationLoad(adjusted, key);
    expect(intensiveApplicationPlannedLoad({ profile: drop, protocolKg, previousApplicationKg: 40, adjustment: undone, key })).toEqual(
      planned,
    );
    const nextKey = intensiveApplicationKey('item', 5, 3);
    expect(
      intensiveApplicationPlannedLoad({ profile: drop, protocolKg, previousApplicationKg: fatigueKg, adjustment: adjusted, key: nextKey }).source,
    ).toBe('protocol');

    const protocolBody = intensiveApplicationPayload({
      setNumber: 6,
      clientKey: 'c6',
      setType: getAdvancedTechniqueSetType('drop_set'),
      techniqueCode: 'drop_set',
      anchorSetNumber: 5,
      reps: 8,
      planned,
      completedAt: '2026-10-24T12:00:00.000Z',
      restSeconds: intensiveRestSeconds(drop),
    });
    expect(protocolBody).toMatchObject({
      set_type: 'drop_set',
      technique_code: 'drop_set',
      anchor_set_number: 5,
      reps: 8,
      reps_pending: false,
      weight_kg: protocolKg,
      planned_weight_kg: protocolKg,
      load_adjust_source: 'protocol',
      rpe_target_source: 'protocol',
      rpe: null,
      rest_seconds: 0,
    });
    const fatigueBody = intensiveApplicationPayload({
      setNumber: 7,
      clientKey: 'c7',
      setType: 'drop_set',
      techniqueCode: 'drop_set',
      anchorSetNumber: 5,
      reps: 6,
      planned: afterAdjust,
      completedAt: '2026-10-24T12:00:30.000Z',
      restSeconds: 0,
    });
    expect(fatigueBody).toMatchObject({ load_adjust_source: 'fatigue', weight_kg: fatigueKg, planned_weight_kg: fatigueKg });
  });

  it('sem reps = etapa pendente (reps nulas, nunca 0)', () => {
    const body = intensiveApplicationPayload({
      setNumber: 6,
      clientKey: 'c6',
      setType: 'drop_set',
      techniqueCode: 'drop_set',
      anchorSetNumber: 5,
      reps: 0,
      planned: { kg: 40, source: 'protocol' },
      completedAt: '2026-10-24T12:00:00.000Z',
      restSeconds: 0,
    });
    expect(body).toMatchObject({ reps: null, reps_pending: true, rpe: null });
  });

  it('ajuste igual à carga atual, negativo ou com origem desconhecida não muda nada', () => {
    const key = intensiveApplicationKey('item', 5, 1);
    const base = { kg: 40, source: 'protocol' as const };
    expect(adjustApplicationLoad(null, key, base, 40, 'fatigue')).toBeNull();
    expect(adjustApplicationLoad(null, key, base, -1, 'fatigue')).toBeNull();
    expect(adjustApplicationLoad(null, key, base, 35, 'xyz' as never)).toBeNull();
    expect(undoApplicationLoad(null, key)).toBeNull();
  });
});

describe('rest-pause: pausa da prescrição e mini-blocos separados', () => {
  it('pausa congelada (`pause_seconds`) ou, sem ela, a da ficha; carga constante', () => {
    const frozen = profileOf('rest_pause', {
      parameters: { technique_code: 'rest_pause', applications: 3, pause_seconds: 15, target_rpe: 9 },
      techniqueDurationSeconds: 20,
    });
    expect(frozen).toMatchObject({ stageKind: 'application', restBetween: { kind: 'technique_pause', seconds: 15 }, loadConstant: true });
    const fromItem = profileOf('rest_pause', {
      parameters: { technique_code: 'rest_pause', applications: 3, pause_seconds: null },
      techniqueDurationSeconds: 20,
    });
    expect(intensiveRestSeconds(fromItem)).toBe(20);
    // A pausa NUNCA é o descanso do item.
    expect(intensiveRestSeconds(fromItem)).not.toBe(90);
  });

  it('cada mini-bloco é uma linha própria ligada à série principal, sem RPE individual', () => {
    const rp = profileOf('rest_pause', {
      parameters: { technique_code: 'rest_pause', applications: 3, pause_seconds: 15 },
    });
    const bodies = [1, 2, 3].map((index) =>
      intensiveApplicationPayload({
        setNumber: 4 + index,
        clientKey: `mb${index}`,
        setType: getAdvancedTechniqueSetType('rest_pause'),
        techniqueCode: 'rest_pause',
        anchorSetNumber: 4,
        reps: 4,
        planned: intensiveApplicationPlannedLoad({
          profile: rp,
          protocolKg: 60,
          previousApplicationKg: index > 1 ? 60 : null,
          adjustment: null,
          key: intensiveApplicationKey('item', 4, index),
        }),
        completedAt: '2026-10-24T12:00:00.000Z',
        restSeconds: intensiveRestSeconds(rp),
      }),
    );
    expect(new Set(bodies.map((b) => b.set_number)).size).toBe(3);
    for (const body of bodies) {
      expect(body).toMatchObject({ set_type: 'rest_pause', anchor_set_number: 4, rpe: null, rest_seconds: 15, weight_kg: 60 });
      expect(body).not.toHaveProperty('load_adjust_source');
    }
  });

  it('carga constante: o mini-bloco seguinte repete a carga executada (ajuste por fadiga continua)', () => {
    const rp = profileOf('rest_pause', { parameters: { technique_code: 'rest_pause', pause_seconds: 15 } });
    const next = intensiveApplicationPlannedLoad({
      profile: rp,
      protocolKg: 60,
      previousApplicationKg: 55,
      adjustment: null,
      key: intensiveApplicationKey('item', 4, 3),
    });
    expect(next).toEqual({ kg: 55, source: null });
  });

  it('cronômetro da pausa: segundos restantes pelo fim absoluto', () => {
    expect(pauseRemainingSeconds(T0 + 15_000, T0)).toBe(15);
    expect(pauseRemainingSeconds(T0 + 15_000, T0 + 14_100)).toBe(1);
    expect(pauseRemainingSeconds(T0 + 15_000, T0 + 20_000)).toBe(0);
  });
});

describe('família "além da falha": bloco leve, reps extras com um toque', () => {
  const forced = profileOf('forced_reps', { plannedSetsCount: 3, itemRestSeconds: 90 });

  it('perfil sem briefing, descanso do item e RPE-alvo do backend', () => {
    expect(forced).toMatchObject({ stageKind: 'beyond_failure', restBetween: { kind: 'item_rest', seconds: 90 }, targetRpe: 9 });
    expect(isIntensiveBlockProfile(forced)).toBe(true);
    expect(createIntensiveBlock(forced, 50).phase).toBe('SERIE_EM_EXECUCAO');
    for (const code of ['negative_reps', 'partial_reps', 'cheat_reps']) {
      expect(profileOf(code).stageKind).toBe('beyond_failure');
    }
  });

  it('reps extras só na última série', () => {
    expect(intensiveExtraRepsEnabled(forced, 2, 3)).toBe(false);
    expect(intensiveExtraRepsEnabled(forced, 3, 3)).toBe(true);
    expect(intensiveExtraRepsEnabled(profileOf('fst7', { plannedSetsCount: 7 }), 7, 7)).toBe(false);
  });

  it('sem RPE obrigatório: série pendente sem RPE e reps extras numa linha ligada a ela', () => {
    let s = createIntensiveBlock(forced, 50);
    s = run(s, complete(3, 10, 90), { type: 'START_NEXT' });
    s = run({ ...s, phase: 'SERIE_EM_EXECUCAO', rest: null }, complete(4, 10, 90));
    s = run({ ...s, phase: 'SERIE_EM_EXECUCAO', rest: null }, complete(5, null, 90, { extraReps: 3, extraClientKey: 'x5' }));
    const last = s.records[2];
    expect(last).toMatchObject({ reps: null, rpe: null, extraReps: 3, extraClientKey: 'x5' });
    expect(intensiveSetPayload(last, 90)).toMatchObject({ reps: null, reps_pending: true, rpe: null, rpe_target_source: 'protocol' });
    expect(intensiveExtraRepsPayload(last, { techniqueCode: 'forced_reps', setType: getAdvancedTechniqueSetType('forced_reps') })).toEqual({
      set_number: 6,
      client_key: 'x5',
      set_type: 'forced_rep',
      reps: 3,
      reps_pending: false,
      weight_kg: 50,
      // Mesmo contrato das etapas de drop/rest-pause: carga planejada e alvo do protocolo.
      planned_weight_kg: 50,
      rpe_target_source: 'protocol',
      rpe: null,
      technique_code: 'forced_reps',
      anchor_set_number: 5,
      completed_at: last.completedAt,
    });
    // A linha extra ocupa o número seguinte.
    expect(nextIntensiveSetNumber(s, [])).toBe(7);
    // Série sem extras não gera linha.
    expect(intensiveExtraRepsPayload(s.records[0], { techniqueCode: 'forced_reps', setType: 'forced_rep' })).toBeNull();
  });

  it('extras sem chave de idempotência são ignorados (nunca grava sem `client_key`)', () => {
    const s = run(createIntensiveBlock(forced, 50), complete(3, 8, 90, { extraReps: 2 }));
    expect(s.records[0].extraReps).toBeUndefined();
  });

  it('retomada sem snapshot ignora a linha das reps extras (não é série do bloco)', () => {
    const s = reconcileIntensiveBlock({
      stored: undefined,
      profile: forced,
      serverSets: [
        { set_number: 3, set_type: 'working', reps: 10, weight_kg: 50 },
        { set_number: 4, set_type: 'forced_rep', reps: 2, weight_kg: 50 },
      ],
      initialPlannedKg: 50,
      now: T0,
    });
    expect(s.records).toHaveLength(1);
    expect(s.currentSet).toBe(2);
  });
});

describe('reps extras vindas só do servidor', () => {
  const forced = profileOf('forced_reps', { plannedSetsCount: 3, itemRestSeconds: 90 });

  it('a retomada sem snapshot devolve as extras ao registro da série (a revisão lista)', () => {
    const s = reconcileIntensiveBlock({
      stored: undefined,
      profile: forced,
      serverSets: [
        { set_number: 3, set_type: 'working', reps: 10, weight_kg: 50 },
        { set_number: 4, set_type: 'forced_rep', reps: 2, weight_kg: 50, anchor_set_number: 3, technique_code: 'forced_reps', client_key: 'k4' },
      ],
      initialPlannedKg: 50,
      now: T0,
    });
    expect(s.records).toHaveLength(1);
    expect(s.records[0]).toMatchObject({ setNumber: 3, extraReps: 2, extraClientKey: 'k4' });
    expect(nextIntensiveSetNumber(s, [])).toBe(5);
  });
});

describe('pausa do rest-pause segura o "Concluir"', () => {
  it('só libera no fim da pausa ou com "Pular pausa"', () => {
    const pause = { endsAt: T0 + 15_000 };
    expect(intensivePauseHoldsCompletion(pause, T0, false)).toBe(true);
    expect(intensivePauseHoldsCompletion(pause, T0, true)).toBe(false);
    expect(intensivePauseHoldsCompletion(pause, T0 + 15_000, false)).toBe(false);
    expect(intensivePauseHoldsCompletion(null, T0, false)).toBe(false);
  });
});

describe('regressão do FST-7', () => {
  it('continua com briefing, 7 séries e o descanso do item', () => {
    const fst7 = profileOf('fst7', { plannedSetsCount: 7, itemRestSeconds: 30 });
    expect(fst7).toMatchObject({ stageKind: 'working_block', totalSets: 7, restBetween: { kind: 'item_rest', seconds: 30 } });
    expect(createIntensiveBlock(fst7, 40).phase).toBe('BRIEFING');
    const s = run(createIntensiveBlock(fst7, 40), { type: 'START' }, complete(1, 12, 30));
    expect(s.phase).toBe('DESCANSO');
    expect(s.records[0].extraReps).toBeUndefined();
    expect(intensiveExtraRepsEnabled(fst7, 7, 7)).toBe(false);
  });
});
