// Bipe e vibração dos cronômetros do Modo Treino (descanso e execução da
// série por tempo/máximo de reps, Lote T5). Extraído do `RestTimer.tsx` para
// os dois usarem o MESMO aviso de fim. Só navegador: fica fora do núcleo
// `workout-mode-core` (o relógio tem o próprio aviso).

/** Padrão de vibração do fim de um cronômetro (ms ligado/desligado). */
export const TIMER_FINISH_VIBRATION: number[] = [200, 100, 200];

// Beep curto via Web Audio API (oscilador, sem depender de um asset de
// áudio embutido) — mais confiável no Safari do iPhone do que um data-uri de
// áudio, e sem custo de bundle.
export function playBeep() {
  try {
    const AudioCtx =
      window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.value = 880;
    gain.gain.setValueAtTime(0.2, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.start();
    oscillator.stop(ctx.currentTime + 0.35);
    oscillator.onended = () => ctx.close();
  } catch {
    // Ambiente sem suporte a Web Audio (raro) — silencioso, não trava o treino.
  }
}

export function vibrate(pattern: number | number[]) {
  if (typeof navigator !== 'undefined' && 'vibrate' in navigator) {
    try {
      navigator.vibrate(pattern);
    } catch {
      // Safari iOS não suporta a Vibration API — no-op esperado.
    }
  }
}

/** Aviso de fim de cronômetro: vibração + bipe. */
export function signalTimerFinished() {
  vibrate(TIMER_FINISH_VIBRATION);
  playBeep();
}
