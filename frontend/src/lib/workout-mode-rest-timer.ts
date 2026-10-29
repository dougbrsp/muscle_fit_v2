// Relógio do descanso do Modo Treino (novo visual, PR3/3): lógica pura,
// testável sem DOM, usada pelo `RestTimerHero` (e pelo `RestTimer` do treino
// livre) através de `useRestCountdown`.
//
// Só RELÓGIO: a duração do descanso chega pronta (`resolveRestSeconds`), e
// os ±15 s são os mesmos passos que a tela já oferecia. Nada aqui decide
// carga, série ou duração.
//
// A fonte de verdade é um timestamp ABSOLUTO (`endsAt`, epoch ms), nunca um
// contador decrementado: o Safari do iPhone pausa o `setInterval` com a tela
// bloqueada, e ao voltar o restante é recalculado pelo relógio de parede.
// Esse mesmo timestamp é persistido na sessão local (`rest_timer`), o que
// permite reconstruir o descanso depois de um reload.

import type { AdvancedLocalStageKind } from './workout-mode-advanced-stage';
import type { SavedSetIntensity } from './set-engine-status';

/** Passo dos botões −15 s / +15 s (o mesmo de antes do novo visual). */
export const REST_ADJUST_STEP_SECONDS = 15;

const TICK_MS = 1000;

/** Segundos que faltam até `endsAt` (nunca negativo). Mesmo arredondamento de antes. */
export function remainingRestSeconds(endsAt: number, now: number): number {
  return Math.max(0, Math.round((endsAt - now) / 1000));
}

/**
 * Novo fim do descanso depois de −15 s / +15 s. Nunca antes de "agora": o
 * restante fica em zero, não negativo.
 */
export function adjustRestEndsAt(endsAt: number, deltaSeconds: number, now: number): number {
  return Math.max(now, endsAt + deltaSeconds * 1000);
}

/** Fração decorrida (0–100) para a barra de tempo. Sem total (descanso zero): cheia. */
export function restElapsedPercent(totalSeconds: number, remainingSeconds: number): number {
  if (totalSeconds <= 0) return 100;
  const elapsed = Math.min(totalSeconds, Math.max(0, totalSeconds - remainingSeconds));
  return (elapsed / totalSeconds) * 100;
}

/** "MM:SS" (minutos podem passar de 59: descanso longo vira "75:00"). */
export function formatRestClock(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
}

/** Texto acessível do relógio ("1 minuto e 5 segundos restantes"). */
export function restClockAriaLabel(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  const parts: string[] = [];
  if (minutes > 0) parts.push(`${minutes} ${minutes === 1 ? 'minuto' : 'minutos'}`);
  if (rest > 0 || minutes === 0) parts.push(`${rest} ${rest === 1 ? 'segundo' : 'segundos'}`);
  return `${parts.join(' e ')} restantes de descanso`;
}

// ── Persistência na sessão local ───────────────────────────────────────────

/** Etapa do modo avançado que abre o descanso: a mesma sub-etapa local do fluxo. */
export type RestAfterStageKind = AdvancedLocalStageKind;

/**
 * Descanso em andamento gravado em `StoredWorkoutModeSession.rest_timer`.
 * Campo opcional: sessão salva antes dele carrega sem descanso, como antes.
 */
export type StoredRestTimer = {
  /** Identidade do descanso: exercício + série/etapa que o abriu. */
  rest_key: string;
  /** Fim absoluto do descanso (epoch ms). */
  rest_ends_at: number;
  /** Duração total exibida na barra (já com os ±15 s aplicados). */
  total_seconds: number;
  /** Descanso depois da ÚLTIMA série do exercício (transição para o próximo). */
  exercise_transition: boolean;
  /** Exercício recém-encerrado na transição (`exerciseKey`), senão null. */
  transition_from_key: string | null;
  /** Etapa do modo avançado que abriu o descanso; null = série de trabalho. */
  after_stage_kind: RestAfterStageKind | null;
  /** A série foi de fato gravada (o chip "SÉRIE SALVA" depende disso). */
  set_saved: boolean;
  /** Resposta do motor ao salvar a série (status e carga da próxima série). */
  saved_set: { for_key: string | null; saved: SavedSetIntensity } | null;
};

// Etapas válidas do modo avançado (sub-etapa do fluxo, não `set_type`: a
// classificação de série é de `set-types.ts`). Mapa exaustivo sobre o tipo:
// uma etapa nova no fluxo obriga a entrada aqui, sem lista paralela.
const STAGE_KINDS: Readonly<Record<RestAfterStageKind, true>> = { warmup: true, feeler: true, technique: true };

function isRestAfterStageKind(value: unknown): value is RestAfterStageKind {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(STAGE_KINDS, value);
}

/**
 * `rest_timer` da sessão salva -> descanso válido, ou null. Tolerante a
 * sessão antiga (sem o campo) e a lixo no localStorage.
 */
export function restoreRestTimer(raw: unknown): StoredRestTimer | null {
  if (!raw || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  const key = value.rest_key;
  const endsAt = value.rest_ends_at;
  const total = value.total_seconds;
  if (typeof key !== 'string' || key.length === 0) return null;
  if (typeof endsAt !== 'number' || !Number.isFinite(endsAt)) return null;
  const totalSeconds = typeof total === 'number' && Number.isFinite(total) && total >= 0 ? total : 0;
  const stage = isRestAfterStageKind(value.after_stage_kind) ? value.after_stage_kind : null;
  const savedSetRaw = value.saved_set as Record<string, unknown> | null | undefined;
  const savedSet =
    savedSetRaw && typeof savedSetRaw === 'object' && savedSetRaw.saved && typeof savedSetRaw.saved === 'object'
      ? {
          for_key: typeof savedSetRaw.for_key === 'string' ? savedSetRaw.for_key : null,
          saved: savedSetRaw.saved as SavedSetIntensity,
        }
      : null;
  return {
    rest_key: key,
    rest_ends_at: endsAt,
    total_seconds: totalSeconds,
    exercise_transition: value.exercise_transition === true,
    transition_from_key: typeof value.transition_from_key === 'string' ? value.transition_from_key : null,
    after_stage_kind: stage,
    set_saved: value.set_saved === true,
    saved_set: savedSet,
  };
}

/**
 * Início do relógio de um descanso: o persistido, se for o MESMO descanso
 * (mesma `rest_key`), senão um novo a partir de agora.
 */
export function resolveRestStart({
  restKey,
  initialSeconds,
  stored,
  now,
}: {
  restKey: string;
  initialSeconds: number;
  stored: Pick<StoredRestTimer, 'rest_key' | 'rest_ends_at' | 'total_seconds'> | null | undefined;
  now: number;
}): { endsAt: number; totalSeconds: number; restored: boolean } {
  if (stored && stored.rest_key === restKey) {
    return { endsAt: stored.rest_ends_at, totalSeconds: Math.max(0, stored.total_seconds), restored: true };
  }
  const seconds = Math.max(0, initialSeconds);
  return { endsAt: now + seconds * 1000, totalSeconds: seconds, restored: false };
}

// ── Contagem com transição única ───────────────────────────────────────────

export type RestCountdownSnapshot = { remaining: number; total: number; expired: boolean };

export type RestCountdownOptions = {
  endsAt: number;
  totalSeconds: number;
  /**
   * Zerar encerra o descanso sozinho? Entre séries sim; na transição de
   * exercício não (só "Continuar" avança). Função: lida a cada zerar.
   */
  autoAdvanceOnZero: () => boolean;
  onChange: (snapshot: RestCountdownSnapshot) => void;
  /** Aviso de fim (bipe/vibração). Uma vez por descanso. */
  onZero: () => void;
  /** Fim do descanso: `skipped` = pulado/continuado pelo usuário. Uma vez. */
  onFinish: (skipped: boolean) => void;
  /** Fim recalculado (±15 s), para persistir. */
  onEndsAtChange?: (endsAt: number, totalSeconds: number) => void;
  now?: () => number;
};

export type RestCountdown = {
  start: () => void;
  recalc: () => void;
  adjust: (deltaSeconds: number) => void;
  /** Pular/continuar. `false` se o descanso já tinha terminado. */
  finish: () => boolean;
  dispose: () => void;
  snapshot: () => RestCountdownSnapshot;
};

/**
 * Contagem de um descanso. Garante transição ÚNICA: o aviso de fim toca uma
 * vez, e `onFinish` dispara no máximo uma vez (zerar, pular ou continuar,
 * o que vier primeiro). `dispose` derruba o intervalo e silencia tudo: um
 * descanso encerrado não deixa timer zumbi.
 */
export function createRestCountdown(options: RestCountdownOptions): RestCountdown {
  const now = options.now ?? (() => Date.now());
  let endsAt = options.endsAt;
  let total = Math.max(0, options.totalSeconds);
  let zeroSignaled = false;
  let finished = false;
  let disposed = false;
  let interval: ReturnType<typeof setInterval> | null = null;

  const stop = () => {
    if (interval != null) clearInterval(interval);
    interval = null;
  };

  const snapshot = (): RestCountdownSnapshot => {
    const remaining = remainingRestSeconds(endsAt, now());
    return { remaining, total, expired: remaining <= 0 };
  };

  const close = (skipped: boolean): boolean => {
    if (finished || disposed) return false;
    finished = true;
    stop();
    options.onFinish(skipped);
    return true;
  };

  const recalc = () => {
    if (disposed) return;
    const snap = snapshot();
    options.onChange(snap);
    if (snap.expired && !zeroSignaled && !finished) {
      zeroSignaled = true;
      options.onZero();
      if (options.autoAdvanceOnZero()) close(false);
    }
  };

  return {
    start: () => {
      if (disposed || finished || interval != null) return;
      interval = setInterval(recalc, TICK_MS);
      recalc();
    },
    recalc,
    adjust: (deltaSeconds: number) => {
      if (disposed || finished) return;
      const at = now();
      endsAt = adjustRestEndsAt(endsAt, deltaSeconds, at);
      total = Math.max(total, remainingRestSeconds(endsAt, at));
      options.onEndsAtChange?.(endsAt, total);
      recalc();
    },
    finish: () => close(true),
    dispose: () => {
      disposed = true;
      stop();
    },
    snapshot,
  };
}

// ── CTA do rodapé ──────────────────────────────────────────────────────────

/**
 * Rótulo da ação principal do descanso. Enquanto conta: "Pular descanso".
 * Na transição de exercício o relógio zerado não avança sozinho; o CTA vira
 * "Continuar" (a mesma ação de antes no card do próximo exercício).
 */
export function restPrimaryLabel({
  expired,
  isExerciseTransition,
  hasNextExercise,
}: {
  expired: boolean;
  isExerciseTransition: boolean;
  hasNextExercise: boolean;
}): string {
  if (!expired || !isExerciseTransition) return 'Pular descanso';
  return hasNextExercise ? 'Continuar para o próximo exercício' : 'Continuar';
}
