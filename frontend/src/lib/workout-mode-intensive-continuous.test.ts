import { describe, expect, it, vi } from 'vitest';

import { FALLBACK_ADVANCED_TECHNIQUES } from './advanced-techniques';
import { EXEC_SECONDS_PER_REP, repsExecutionSeconds } from './template-time-estimate';
import { createRestCountdown } from './workout-mode-rest-timer';
import {
  clampReducedReps,
  correctQueuedIntensiveRecord,
  INTENSIVE_SET_IN_FLIGHT_ERROR,
  createIntensiveBlock,
  intensivePhaseEndedAt,
  intensiveReducer,
  intensiveSeriesCompletion,
  normalizeIntensiveBlock,
  reconcileIntensiveBlock,
  reducibleRecord,
  resolveIntensiveProfile,
  restoreIntensiveBlocks,
  type IntensiveBlockState,
  type IntensiveEvent,
  type IntensiveProfile,
} from './workout-mode-intensive';

const T0 = Date.parse('2026-11-07T12:00:00Z');
const REST = 30;

function profile(overrides: Partial<Parameters<typeof resolveIntensiveProfile>[0]> = {}): IntensiveProfile {
  const p = resolveIntensiveProfile({
    techniqueCode: 'fst7',
    catalog: FALLBACK_ADVANCED_TECHNIQUES,
    parameters: { technique_code: 'fst7', target_rpe: 10, continuous_clock: true },
    plannedSetsCount: 3,
    itemRestSeconds: REST,
    repsTarget: '8-12',
    ...overrides,
  });
  if (!p) throw new Error('perfil');
  return p;
}

function run(state: IntensiveBlockState, ...events: IntensiveEvent[]) {
  return events.reduce(intensiveReducer, state);
}

function started(p = profile()): IntensiveBlockState {
  return run(createIntensiveBlock(p, 40), { type: 'START', now: T0 });
}

function finishSet(state: IntensiveBlockState, n: number, now: number): IntensiveBlockState {
  return run(state, {
    type: 'COMPLETE_SET',
    reps: state.continuous?.repsTop ?? null,
    rpe: 10,
    clientKey: `k${n}`,
    setNumber: n,
    now,
    restSeconds: REST,
  });
}

describe('FST-7 com relógio contínuo: perfil', () => {
  it('topo das reps e duração da série vêm do dono do tempo de treino', () => {
    const p = profile({ peakContractionSeconds: 1 });
    expect(p.continuous).toEqual({ repsTop: 12, setSeconds: repsExecutionSeconds(12, 1) });
    expect(profile().continuous?.setSeconds).toBe(12 * EXEC_SECONDS_PER_REP);
  });

  it('sem alvo de reps legível, ou em outra técnica, não há relógio contínuo', () => {
    expect(profile({ repsTarget: null }).continuous).toBeNull();
    expect(profile({ repsTarget: 'até a falha' }).continuous).toBeNull();
    // Sem o flag pronto do protocolo (dono: backend) o frontend não liga o relógio contínuo.
    expect(profile({ parameters: { technique_code: 'fst7', target_rpe: 10 } }).continuous).toBeNull();
    expect(profile({ parameters: { technique_code: 'fst7', continuous_clock: false } }).continuous).toBeNull();
    const gvt = resolveIntensiveProfile({
      techniqueCode: 'gvt',
      catalog: FALLBACK_ADVANCED_TECHNIQUES,
      parameters: { technique_code: 'gvt', continuous_clock: false },
      plannedSetsCount: 10,
      itemRestSeconds: 60,
      repsTarget: '10',
    });
    expect(gvt?.continuous).toBeNull();
  });
});

describe('FST-7 com relógio contínuo: reducer', () => {
  const setMs = () => (profile().continuous?.setSeconds ?? 0) * 1000;

  it('START abre a série com relógio rodando (sem estado "pronta")', () => {
    const s = started();
    expect(s.phase).toBe('SERIE_EM_EXECUCAO');
    expect(s.setClock).toMatchObject({ endsAt: T0 + setMs() });
  });

  it('série grava no topo com o RPE-alvo e abre o descanso do item no fim exato da série', () => {
    const end = T0 + setMs();
    const s = finishSet(started(), 1, end);
    expect(s.records[0]).toMatchObject({ reps: 12, rpe: 10, weightKg: 40, sync: 'saving' });
    expect(s.phase).toBe('DESCANSO');
    expect(s.setClock).toBeNull();
    expect(s.rest).toMatchObject({ endsAt: end + REST * 1000, totalSeconds: REST });
  });

  it('o descanso vencido inicia a próxima série sozinho, sem lacuna no relógio', () => {
    const end = T0 + setMs();
    const s = run(finishSet(started(), 1, end), { type: 'REST_EXPIRED' });
    expect(s.phase).toBe('SERIE_EM_EXECUCAO');
    expect(s.currentSet).toBe(2);
    expect(s.setClock?.endsAt).toBe(end + REST * 1000 + setMs());
    expect(s.rest).toBeNull();
  });

  it('avanço manual: pular o descanso e concluir a série agora', () => {
    const resting = finishSet(started(), 1, T0 + 10_000);
    const skipped = run(resting, { type: 'SKIP_REST', now: T0 + 15_000 });
    expect(skipped.phase).toBe('SERIE_EM_EXECUCAO');
    expect(skipped.setClock?.endsAt).toBe(T0 + 15_000 + setMs());
    const early = finishSet(skipped, 2, T0 + 20_000);
    expect(early.records).toHaveLength(2);
    expect(early.rest?.endsAt).toBe(T0 + 20_000 + REST * 1000);
  });

  it('ajustar o relógio da série (±15 s) não pausa nem muda de fase', () => {
    const s = run(started(), { type: 'SET_CLOCK_ADJUSTED', endsAt: T0 + 51_000, totalSeconds: 51 });
    expect(s.phase).toBe('SERIE_EM_EXECUCAO');
    expect(s.setClock).toMatchObject({ endsAt: T0 + 51_000, totalSeconds: 51 });
  });

  it('percorre todas as séries e chega à REVISAO_FINAL após a gravação de todas', () => {
    let s = started();
    let now = T0;
    for (let n = 1; n <= 3; n += 1) {
      now += setMs();
      s = finishSet(s, n, now);
      if (n < 3) {
        expect(s.phase).toBe('DESCANSO');
        s = run(s, { type: 'REST_EXPIRED' });
        now += REST * 1000;
      }
    }
    expect(s.records).toHaveLength(3);
    expect(s.phase).toBe('REGISTRO_PENDENTE');
    expect(s.setClock).toBeNull();
    const saved = s.records.reduce((acc, r) => run(acc, { type: 'SAVE_OK', clientKey: r.clientKey }), s);
    expect(saved.phase).toBe('REVISAO_FINAL');
  });

  it('o relógio nunca para: normalizar descanso vencido já começa a série', () => {
    const end = T0 + setMs();
    const after = end + REST * 1000;
    const s = normalizeIntensiveBlock(finishSet(started(), 1, end), after + 5_000);
    expect(s.phase).toBe('SERIE_EM_EXECUCAO');
    expect(s.setClock?.endsAt).toBe(after + setMs());
  });

  it('snapshot de antes do relógio contínuo ("pronta") vira série em andamento', () => {
    const legacy: IntensiveBlockState = { ...started(), phase: 'PROXIMA_SERIE_PRONTA', setClock: null, rest: null };
    const s = normalizeIntensiveBlock(legacy, T0 + 99_000);
    expect(s.phase).toBe('SERIE_EM_EXECUCAO');
    expect(s.setClock?.endsAt).toBe(T0 + 99_000 + setMs());
  });

  it('o snapshot restaurado preserva o relógio contínuo (catch-up, nada reinicia a série)', () => {
    const restored = restoreIntensiveBlocks({ 1: JSON.parse(JSON.stringify(started())) })[1];
    expect(restored.continuous).toEqual(profile().continuous);
    expect(restored.setClock?.endsAt).toBe(T0 + setMs());
    const reconciled = reconcileIntensiveBlock({
      stored: restored,
      profile: profile(),
      serverSets: [],
      initialPlannedKg: 40,
      now: T0 + 3_600_000,
    });
    // Série vencida fora da tela: o relógio NÃO para; o fim real fica no setClock (a tela grava com ele).
    expect(reconciled.phase).toBe('SERIE_EM_EXECUCAO');
    expect(reconciled.setClock?.endsAt).toBe(T0 + setMs());
  });

  it('conclusão automática usa o fim real da série (catch-up), o agora quando adiantada e nunca o futuro', () => {
    const s = started();
    const end = T0 + setMs();
    expect(intensiveSeriesCompletion(s, 10, end + 500_000)).toEqual({ reps: 12, rpe: 10, now: end });
    expect(intensiveSeriesCompletion(s, 10, T0 + 5_000)).toEqual({ reps: 12, rpe: 10, now: T0 + 5_000 });
    expect(intensiveSeriesCompletion(s, null, end)?.rpe).toBeNull();
    expect(intensiveSeriesCompletion(run(s, { type: 'END_TECHNIQUE' }), 10, end)).toBeNull();
    expect(intensivePhaseEndedAt(100, 50)).toBe(50);
    expect(intensivePhaseEndedAt(100, 150)).toBe(100);
  });

  it('retomada longe do fim: DESCANSO vencido há mais de rest+série cascateia série a série até o fim, com a hora real de cada uma', () => {
    const end1 = T0 + setMs();
    let s: IntensiveBlockState = finishSet(started(), 1, end1);
    // O usuário volta muito depois: nenhuma série é pulada nem repetida.
    const comeback = end1 + (REST + 36 + REST + 36 + REST) * 1000 + 99_000;
    const completedAt: string[] = [];
    for (let guard = 0; guard < 10; guard += 1) {
      s = normalizeIntensiveBlock(s, comeback);
      const completion = intensiveSeriesCompletion(s, 10, comeback);
      if (!completion) break;
      completedAt.push(new Date(completion.now).toISOString());
      s = run(s, {
        type: 'COMPLETE_SET',
        reps: completion.reps,
        rpe: completion.rpe,
        clientKey: `k${s.currentSet}`,
        setNumber: s.currentSet,
        now: completion.now,
        restSeconds: REST,
      });
    }
    expect(s.records.map((r) => r.index)).toEqual([1, 2, 3]);
    expect(s.phase).toBe('REGISTRO_PENDENTE');
    // 2ª série terminou exatamente rest + série depois da 1ª; a 3ª, o mesmo depois da 2ª.
    expect(completedAt).toEqual([
      new Date(end1 + (REST + 36) * 1000).toISOString(),
      new Date(end1 + 2 * (REST + 36) * 1000).toISOString(),
    ]);
    expect(s.records.map((r) => r.completedAt)).toEqual([
      new Date(end1).toISOString(),
      ...completedAt,
    ]);
  });

  it('reconcile segue o perfil atual (relógio contínuo ligado/desligado)', () => {
    const p = profile();
    const stored = createIntensiveBlock(profile({ repsTarget: null }), 40);
    const s = reconcileIntensiveBlock({ stored, profile: p, serverSets: [], initialPlannedKg: 40, now: T0 });
    expect(s.continuous).toEqual(p.continuous);
    const off = reconcileIntensiveBlock({
      stored: s,
      profile: profile({ repsTarget: null }),
      serverSets: [],
      initialPlannedKg: 40,
      now: T0,
    });
    expect(off.continuous).toBeNull();
  });
});

describe('FST-7 com relógio contínuo: redução de reps no descanso', () => {
  const resting = () => finishSet(started(), 1, T0 + 36_000);

  it('só a série recém-gravada, só no descanso', () => {
    expect(reducibleRecord(resting())?.clientKey).toBe('k1');
    expect(reducibleRecord(started())).toBeNull();
    expect(reducibleRecord(run(resting(), { type: 'REST_EXPIRED' }))).toBeNull();
  });

  it('aceita de 1 até o topo e recusa acima do topo ou abaixo de 1', () => {
    const s = resting();
    expect(clampReducedReps(s, 10)).toBe(10);
    expect(clampReducedReps(s, 12)).toBe(12);
    expect(clampReducedReps(s, 13)).toBeNull();
    expect(clampReducedReps(s, 0)).toBeNull();
    expect(clampReducedReps(s, Number.NaN)).toBeNull();
    expect(clampReducedReps({ continuous: null }, 5)).toBeNull();
  });

  it('a edição da série não mexe nos relógios nem na fase', () => {
    const s = resting();
    const edited = run(s, { type: 'UPDATE_RECORD', clientKey: 'k1', reps: 9 });
    expect(edited.records[0].reps).toBe(9);
    expect(edited.rest).toEqual(s.rest);
    expect(edited.phase).toBe('DESCANSO');
  });

  it('o ajuste rápido de carga segue disponível no descanso, sem parar o relógio', () => {
    const s = resting();
    const adjusted = run(s, { type: 'ADJUST_LOAD', kg: 35, source: 'manual' });
    expect(adjusted.planned.kg).toBe(35);
    expect(adjusted.rest).toEqual(s.rest);
  });
});

describe('FST-7 com relógio contínuo: limites e bordas', () => {
  const resting = () => finishSet(started(), 1, T0 + 36_000);

  it('UPDATE_RECORD do bloco contínuo só aceita 1..topo (recusa 13, 0 e negativo)', () => {
    for (const bad of [13, 0, -2]) {
      expect(run(resting(), { type: 'UPDATE_RECORD', clientKey: 'k1', reps: bad }).records[0].reps).toBe(12);
    }
    expect(run(resting(), { type: 'UPDATE_RECORD', clientKey: 'k1', reps: 1 }).records[0].reps).toBe(1);
    // Outros campos do mesmo evento seguem valendo.
    expect(run(resting(), { type: 'UPDATE_RECORD', clientKey: 'k1', reps: 99, weightKg: 35 }).records[0]).toMatchObject({ reps: 12, weightKg: 35 });
  });

  it('A2: a SÉRIE que expira dispara exatamente 1 COMPLETE_SET {topo, RPE-alvo}, sem duplicar em re-render', () => {
    vi.useFakeTimers();
    vi.setSystemTime(T0);
    try {
      let state = started();
      const dispatched: IntensiveEvent[] = [];
      const dispatch = (event: IntensiveEvent) => {
        dispatched.push(event);
        state = intensiveReducer(state, event);
      };
      const clock = state.setClock!;
      const countdown = createRestCountdown({
        endsAt: clock.endsAt,
        totalSeconds: clock.totalSeconds,
        autoAdvanceOnZero: () => true,
        onChange: () => {},
        onZero: () => {},
        onFinish: () => {
          const completion = intensiveSeriesCompletion(state, 10);
          if (completion) {
            dispatch({ type: 'COMPLETE_SET', reps: completion.reps, rpe: completion.rpe, clientKey: 'k1', setNumber: 1, now: completion.now, restSeconds: REST });
          }
        },
      });
      countdown.start();
      vi.advanceTimersByTime(clock.totalSeconds * 1000 + 5_000);
      countdown.recalc();
      countdown.recalc();
      countdown.dispose();
      expect(dispatched).toHaveLength(1);
      expect(dispatched[0]).toMatchObject({ type: 'COMPLETE_SET', reps: 12, rpe: 10 });
      // Re-render com a mesma série: o reducer ignora o segundo COMPLETE_SET (fase já é DESCANSO).
      const again = intensiveReducer(state, dispatched[0]);
      expect(again.records).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('M1: RPE-alvo nulo grava rpe null e o bloco chega à REVISAO_FINAL', () => {
    const p = profile({ parameters: { technique_code: 'fst7', continuous_clock: true } });
    let s = run(createIntensiveBlock(p, 40), { type: 'START', now: T0 });
    for (let n = 1; n <= 3; n += 1) {
      const completion = intensiveSeriesCompletion(s, p.targetRpe, T0 + n * 1_000_000)!;
      s = run(s, { type: 'COMPLETE_SET', reps: completion.reps, rpe: completion.rpe, clientKey: `k${n}`, setNumber: n, now: completion.now, restSeconds: REST });
      if (n < 3) s = run(s, { type: 'REST_EXPIRED' });
    }
    expect(s.records.every((r) => r.rpe === null)).toBe(true);
    const saved = s.records.reduce((acc, r) => run(acc, { type: 'SAVE_OK', clientKey: r.clientKey }), s);
    expect(saved.phase).toBe('REVISAO_FINAL');
  });

  it('M3: FST-7 fora de working_block (família diferente) não liga o relógio contínuo', () => {
    const p = profile({ supportedStageKinds: new Set(['working_block', 'application', 'beyond_failure']) });
    expect(p.stageKind).toBe('working_block');
    const base = createIntensiveBlock({ ...p, stageKind: 'beyond_failure', continuous: null }, 40);
    expect(base.continuous).toBeUndefined();
    expect(base.phase).toBe('SERIE_EM_EXECUCAO');
  });

  it('M4: restSeconds=0 segue direto para a próxima série; SKIP_SET e END_TECHNIQUE no contínuo', () => {
    const zero = run(started(), { type: 'COMPLETE_SET', reps: 12, rpe: 10, clientKey: 'k1', setNumber: 1, now: T0 + 36_000, restSeconds: 0 });
    expect(zero.phase).toBe('SERIE_EM_EXECUCAO');
    expect(zero.currentSet).toBe(2);
    expect(zero.setClock?.endsAt).toBe(T0 + 36_000 + 36_000);

    const skipped = run(started(), { type: 'SKIP_SET', now: T0 + 7_000 });
    expect(skipped.currentSet).toBe(2);
    expect(skipped.skipped).toEqual([1]);
    expect(skipped.setClock?.endsAt).toBe(T0 + 7_000 + 36_000);

    const ended = run(resting(), { type: 'END_TECHNIQUE' });
    expect(ended.phase).toBe('REGISTRO_PENDENTE');
    expect(ended.setClock).toBeNull();
    expect(ended.rest).toBeNull();
    expect(run(started(), { type: 'END_TECHNIQUE' }).phase).toBe('TECNICA_CONCLUIDA');
  });

  it('M5: SET_CLOCK_ADJUSTED sem setClock é ignorado e repetir o mesmo valor é idempotente', () => {
    const noClock = createIntensiveBlock(profile(), 40);
    expect(run(noClock, { type: 'SET_CLOCK_ADJUSTED', endsAt: T0, totalSeconds: 5 })).toBe(noClock);
    const s = started();
    const same = run(s, { type: 'SET_CLOCK_ADJUSTED', endsAt: s.setClock!.endsAt, totalSeconds: s.setClock!.totalSeconds });
    expect(same).toBe(s);
  });

  it('B3: round-trip JSON do snapshot com continuous/setClock e reconcile', () => {
    const s = finishSet(started(), 1, T0 + 36_000);
    const back = JSON.parse(JSON.stringify(s)) as IntensiveBlockState;
    expect(back).toEqual(s);
    const reconciled = reconcileIntensiveBlock({ stored: back, profile: profile(), serverSets: [], initialPlannedKg: 40, now: T0 + 40_000 });
    expect(reconciled.phase).toBe('DESCANSO');
    expect(reconciled.continuous).toEqual(profile().continuous);
    expect(reconciled.rest).toEqual(s.rest);
    const midBlock = reconcileIntensiveBlock({ stored: started(), profile: profile(), serverSets: [], initialPlannedKg: 40, now: T0 + 10_000 });
    expect(midBlock.setClock).toEqual(started().setClock);
  });
});

describe.each(['gvt', 'drop_set', 'rest_pause'])('M2: %s segue o fluxo com toque (sem relógio contínuo)', (code) => {
  const p = resolveIntensiveProfile({
    techniqueCode: code,
    catalog: FALLBACK_ADVANCED_TECHNIQUES,
    parameters: { technique_code: code, continuous_clock: false },
    plannedSetsCount: 4,
    itemRestSeconds: 30,
    repsTarget: '8-12',
  });

  it('continuous nulo, START sem setClock, REST_EXPIRED -> PROXIMA_SERIE_PRONTA', () => {
    if (!p) throw new Error('perfil');
    expect(p.continuous).toBeNull();
    const s0 = createIntensiveBlock(p, 40);
    expect(s0.continuous).toBeUndefined();
    const s1 = run(s0, { type: 'START', now: T0 });
    expect(s1.setClock ?? null).toBeNull();
    const s2 = run(s1, { type: 'COMPLETE_SET', reps: 10, rpe: 9, clientKey: 'k1', setNumber: 1, now: T0, restSeconds: 30 });
    expect(s2.phase).toBe('DESCANSO');
    expect(run(s2, { type: 'REST_EXPIRED' }).phase).toBe('PROXIMA_SERIE_PRONTA');
  });
});

describe('correção de série ainda na fila offline (P1-2)', () => {
  it("'edited': envia o corpo à fila e aplica a correção ao registro", async () => {
    const bodies: Record<string, number>[] = [];
    const applied: unknown[] = [];
    await correctQueuedIntensiveRecord({
      correction: { reps: 9, weightKg: 35 },
      editPending: async (body) => {
        bodies.push(body);
        return 'edited';
      },
      apply: (patch) => applied.push(patch),
    });
    expect(bodies).toEqual([{ reps: 9, weight_kg: 35 }]);
    expect(applied).toEqual([{ reps: 9, weightKg: 35 }]);
  });

  it.each(['in_flight', 'none'] as const)("'%s': recusa com a mensagem de sempre e não altera o registro", async (outcome) => {
    const applied: unknown[] = [];
    await expect(
      correctQueuedIntensiveRecord({
        correction: { reps: 8 },
        editPending: async () => outcome,
        apply: (patch) => applied.push(patch),
      }),
    ).rejects.toThrow(INTENSIVE_SET_IN_FLIGHT_ERROR);
    expect(applied).toEqual([]);
  });
});
