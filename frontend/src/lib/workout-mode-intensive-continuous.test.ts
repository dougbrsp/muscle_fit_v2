import { describe, expect, it } from 'vitest';

import { FALLBACK_ADVANCED_TECHNIQUES } from './advanced-techniques';
import { EXEC_SECONDS_PER_REP, repsExecutionSeconds } from './template-time-estimate';
import {
  clampReducedReps,
  createIntensiveBlock,
  intensiveReducer,
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
    parameters: { technique_code: 'fst7', target_rpe: 10 },
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
    const gvt = resolveIntensiveProfile({
      techniqueCode: 'gvt',
      catalog: FALLBACK_ADVANCED_TECHNIQUES,
      parameters: { technique_code: 'gvt' },
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

  it('reabrir a tela com a série vencida recomeça o relógio em vez de gravar séries não vistas', () => {
    const restored = restoreIntensiveBlocks({ 1: started() }, T0 + 3_600_000)[1];
    expect(restored.records).toHaveLength(0);
    expect(restored.setClock?.endsAt).toBe(T0 + 3_600_000 + setMs());
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
