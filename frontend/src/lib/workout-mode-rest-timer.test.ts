import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  REST_ADJUST_STEP_SECONDS,
  adjustRestEndsAt,
  createRestCountdown,
  formatRestClock,
  remainingRestSeconds,
  resolveRestStart,
  restElapsedPercent,
  restPrimaryLabel,
  restoreRestTimer,
  type RestCountdownSnapshot,
  type StoredRestTimer,
} from './workout-mode-rest-timer';
import { loadStoredSession, saveStoredSession } from './workout-mode-storage';

const T0 = new Date('2026-10-23T10:00:00Z').getTime();

function countdown(seconds: number, options: { autoAdvance?: boolean } = {}) {
  const onFinish = vi.fn();
  const onZero = vi.fn();
  const onEndsAtChange = vi.fn();
  const snapshots: RestCountdownSnapshot[] = [];
  const start = resolveRestStart({ restKey: 'wpe-1#s1', initialSeconds: seconds, stored: null, now: Date.now() });
  const timer = createRestCountdown({
    endsAt: start.endsAt,
    totalSeconds: start.totalSeconds,
    autoAdvanceOnZero: () => options.autoAdvance ?? true,
    onChange: (snap) => snapshots.push(snap),
    onZero,
    onFinish,
    onEndsAtChange,
  });
  return { timer, onFinish, onZero, onEndsAtChange, snapshots, last: () => snapshots[snapshots.length - 1] };
}

describe('relógio do descanso — restante por timestamp', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(T0);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it.each([15, 60, 90])('descanso de %i s: conta pelo relógio e zera uma vez só', (seconds) => {
    const { timer, onFinish, onZero, last } = countdown(seconds);
    timer.start();
    expect(last().remaining).toBe(seconds);
    vi.advanceTimersByTime(10_000);
    expect(last().remaining).toBe(seconds - 10);
    vi.advanceTimersByTime((seconds - 10) * 1000);
    expect(last()).toEqual({ remaining: 0, total: seconds, expired: true });
    vi.advanceTimersByTime(30_000);
    expect(onZero).toHaveBeenCalledTimes(1);
    expect(onFinish).toHaveBeenCalledTimes(1);
    expect(onFinish).toHaveBeenCalledWith(false);
  });

  it('sair e voltar (intervalo pausado pelo navegador) não acumula drift', () => {
    const { timer, last } = countdown(90);
    timer.start();
    // Aba em segundo plano: o relógio de parede anda 47 s sem nenhum tick.
    vi.setSystemTime(T0 + 47_000);
    timer.recalc(); // visibilitychange -> visible
    expect(last().remaining).toBe(43);
    expect(remainingRestSeconds(T0 + 90_000, T0 + 47_000)).toBe(43);
  });

  it('±15 s mudam o fim de verdade; o −15 s nunca deixa o restante negativo', () => {
    expect(REST_ADJUST_STEP_SECONDS).toBe(15);
    expect(adjustRestEndsAt(T0 + 10_000, -15, T0)).toBe(T0);
    expect(adjustRestEndsAt(T0 + 10_000, 15, T0)).toBe(T0 + 25_000);

    const { timer, last, onEndsAtChange, onFinish } = countdown(60);
    timer.start();
    timer.adjust(15);
    expect(last().remaining).toBe(75);
    expect(last().total).toBe(75);
    expect(onEndsAtChange).toHaveBeenLastCalledWith(T0 + 75_000, 75);
    timer.adjust(-15);
    timer.adjust(-15);
    expect(last().remaining).toBe(45);
    for (let i = 0; i < 5; i += 1) timer.adjust(-15);
    expect(last().remaining).toBe(0);
    expect(onFinish).toHaveBeenCalledTimes(1);
  });

  it('pular duas vezes, ou pular depois de zerar, transiciona uma vez só', () => {
    const skipTwice = countdown(60);
    skipTwice.timer.start();
    expect(skipTwice.timer.finish()).toBe(true);
    expect(skipTwice.timer.finish()).toBe(false);
    expect(skipTwice.onFinish).toHaveBeenCalledTimes(1);
    expect(skipTwice.onFinish).toHaveBeenCalledWith(true);

    const zeroThenSkip = countdown(15);
    zeroThenSkip.timer.start();
    vi.advanceTimersByTime(15_000);
    expect(zeroThenSkip.timer.finish()).toBe(false);
    expect(zeroThenSkip.onFinish).toHaveBeenCalledTimes(1);
  });

  it('pular derruba o intervalo (sem timer zumbi) e dispose silencia tudo', () => {
    const { timer, snapshots } = countdown(60);
    timer.start();
    timer.finish();
    const count = snapshots.length;
    vi.advanceTimersByTime(120_000);
    expect(snapshots.length).toBe(count);
    expect(vi.getTimerCount()).toBe(0);

    const other = countdown(30);
    other.timer.start();
    other.timer.dispose();
    vi.advanceTimersByTime(60_000);
    expect(other.onFinish).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('transição de exercício: zerar avisa mas não avança; "Continuar" avança uma vez', () => {
    const { timer, onFinish, onZero, last } = countdown(15, { autoAdvance: false });
    timer.start();
    vi.advanceTimersByTime(20_000);
    expect(last().expired).toBe(true);
    expect(onZero).toHaveBeenCalledTimes(1);
    expect(onFinish).not.toHaveBeenCalled();
    expect(timer.finish()).toBe(true);
    expect(timer.finish()).toBe(false);
    expect(onFinish).toHaveBeenCalledTimes(1);
    expect(onFinish).toHaveBeenCalledWith(true);
  });

  it('descanso zero: termina na hora, barra cheia', () => {
    const { timer, onFinish } = countdown(0);
    timer.start();
    expect(onFinish).toHaveBeenCalledWith(false);
    expect(restElapsedPercent(0, 0)).toBe(100);
  });
});

describe('reconstrução a partir de rest_ends_at', () => {
  const stored: StoredRestTimer = {
    rest_key: 'wpe-7#s2@1',
    rest_ends_at: T0 + 60_000,
    total_seconds: 90,
    exercise_transition: false,
    transition_from_key: null,
    after_stage_kind: null,
    set_saved: true,
    saved_set: { for_key: 'wpe-7', saved: { intensity_status: 'REACHED' } },
  };

  it('mesmo descanso: continua do fim salvo, não reinicia', () => {
    const start = resolveRestStart({ restKey: stored.rest_key, initialSeconds: 90, stored, now: T0 + 20_000 });
    expect(start).toEqual({ endsAt: T0 + 60_000, totalSeconds: 90, restored: true });
    expect(remainingRestSeconds(start.endsAt, T0 + 20_000)).toBe(40);
  });

  it('outro descanso (outra chave): começa do zero', () => {
    const start = resolveRestStart({ restKey: 'wpe-7#s3@2', initialSeconds: 90, stored, now: T0 });
    expect(start).toEqual({ endsAt: T0 + 90_000, totalSeconds: 90, restored: false });
  });

  it('já expirado ao voltar: o fim de descanso dispara uma única vez', () => {
    vi.useFakeTimers();
    vi.setSystemTime(T0 + 5 * 60_000);
    try {
      const start = resolveRestStart({ restKey: stored.rest_key, initialSeconds: 90, stored, now: Date.now() });
      const onFinish = vi.fn();
      const timer = createRestCountdown({
        endsAt: start.endsAt,
        totalSeconds: start.totalSeconds,
        autoAdvanceOnZero: () => true,
        onChange: () => {},
        onZero: () => {},
        onFinish,
      });
      timer.start();
      timer.recalc();
      vi.advanceTimersByTime(5_000);
      expect(onFinish).toHaveBeenCalledTimes(1);
      expect(onFinish).toHaveBeenCalledWith(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('restoreRestTimer: válido passa; sessão antiga e lixo viram null', () => {
    expect(restoreRestTimer(JSON.parse(JSON.stringify(stored)))).toEqual(stored);
    expect(restoreRestTimer(undefined)).toBeNull();
    expect(restoreRestTimer(null)).toBeNull();
    expect(restoreRestTimer({ rest_key: 'x' })).toBeNull();
    expect(restoreRestTimer({ rest_key: '', rest_ends_at: 1 })).toBeNull();
    expect(restoreRestTimer({ rest_key: 'k', rest_ends_at: 'amanhã' })).toBeNull();
    expect(restoreRestTimer({ rest_key: 'k', rest_ends_at: 5, after_stage_kind: 'qualquer' })).toMatchObject({
      after_stage_kind: null,
      total_seconds: 0,
      set_saved: false,
      saved_set: null,
    });
  });
});

describe('sessão local — compatibilidade do campo rest_timer', () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    (globalThis as unknown as { window: unknown }).window = {
      localStorage: {
        getItem: (key: string) => (store.has(key) ? (store.get(key) as string) : null),
        setItem: (key: string, value: string) => void store.set(key, String(value)),
        removeItem: (key: string) => void store.delete(key),
      },
    };
  });
  afterEach(() => {
    delete (globalThis as unknown as { window?: unknown }).window;
  });

  it('sessão antiga sem rest_timer continua carregando, sem descanso', () => {
    (globalThis as unknown as { window: { localStorage: Storage } }).window.localStorage.setItem(
      'musclefit.workout-mode-session',
      JSON.stringify({ session_id: 230, source: 'plan', workout_plan_id: 119, briefed_keys: ['wpe-1'] }),
    );
    const session = loadStoredSession();
    expect(session?.session_id).toBe(230);
    expect(restoreRestTimer(session?.rest_timer)).toBeNull();
  });

  it('ida e volta do localStorage preserva o descanso', () => {
    const rest: StoredRestTimer = {
      rest_key: 'wpe-1#s1@9',
      rest_ends_at: T0,
      total_seconds: 60,
      exercise_transition: true,
      transition_from_key: 'wpe-1',
      after_stage_kind: null,
      set_saved: true,
      saved_set: null,
    };
    saveStoredSession({ session_id: 1, source: 'plan', rest_timer: rest });
    expect(restoreRestTimer(loadStoredSession()?.rest_timer)).toEqual(rest);
  });
});

describe('apresentação', () => {
  it('MM:SS com dois dígitos', () => {
    expect(formatRestClock(59)).toBe('00:59');
    expect(formatRestClock(90)).toBe('01:30');
    expect(formatRestClock(-3)).toBe('00:00');
  });

  it('barra de tempo decorrido', () => {
    expect(restElapsedPercent(60, 60)).toBe(0);
    expect(restElapsedPercent(60, 30)).toBe(50);
    expect(restElapsedPercent(60, 0)).toBe(100);
  });

  it('CTA: "Pular descanso" enquanto conta; "Continuar" só na transição zerada', () => {
    expect(restPrimaryLabel({ expired: false, isExerciseTransition: true, hasNextExercise: true })).toBe('Pular descanso');
    expect(restPrimaryLabel({ expired: true, isExerciseTransition: false, hasNextExercise: true })).toBe('Pular descanso');
    expect(restPrimaryLabel({ expired: true, isExerciseTransition: true, hasNextExercise: true })).toBe(
      'Continuar para o próximo exercício',
    );
    expect(restPrimaryLabel({ expired: true, isExerciseTransition: true, hasNextExercise: false })).toBe('Continuar');
  });
});
