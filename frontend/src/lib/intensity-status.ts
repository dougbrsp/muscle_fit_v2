// `intensity_status` e `safety_event` (PLANO_20260912.md §5.7, PR-14) — os
// mesmos rótulos e cores no modo treino e nos dashboards, em um único lugar.
//
// Quem DECIDE o status é sempre o servidor (alvo efetivo gravado na série +
// evento de segurança); aqui só mora a apresentação.

import motorConstants from './motor-constants.generated.json';

const INTENSITY = motorConstants.intensity;

export type IntensityStatus =
  | 'REACHED'
  | 'BELOW_TARGET'
  | 'FAILURE_NOT_ALLOWED_REACHED_CEILING'
  | 'ABOVE_SAFE_CEILING';

// Rótulos: dono no backend (`api/app/domain/intensity_status_labels.py`),
// exportados para `motor-constants.generated.json` (paridade conferida por
// `api/tests/unit/test_m76_lote1b_espelhos_fe.py`). Aqui só a cor.
export const INTENSITY_STATUS_LABELS: Record<string, string> = INTENSITY.intensity_status_labels;

export const INTENSITY_STATUS_COLORS: Record<string, string> = {
  REACHED: 'teal',
  BELOW_TARGET: 'gray',
  FAILURE_NOT_ALLOWED_REACHED_CEILING: 'teal',
  ABOVE_SAFE_CEILING: 'orange',
};

// Evento de uma série já gravada (`exercise_sets.safety_event`). Os dois valem
// como intensidade atingida e NUNCA elevam o alvo futuro:
// - `RPE_ABOVE_SAFE_CEILING`: RPE 10 num par (exercício × equipamento) que
//   proíbe a falha — alerta de SEGURANÇA (laranja);
// - `RPE_ABOVE_RECOVERY_TARGET` (Lote 1/P1, 2026-10-11): RPE 10 num par que
//   permite a falha, quando só a recuperação do dia a negou — INFORMATIVO.
export const SAFETY_EVENT_RPE_ABOVE_CEILING: string = INTENSITY.safety_event_rpe_above_ceiling;
export const SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET: string = INTENSITY.safety_event_rpe_above_recovery_target;

export const SAFETY_EVENT_LABELS: Record<string, string> = INTENSITY.safety_event_labels;

// set-eval-v5 (2026-10-12): reps da série contra a faixa da ficha, calculadas
// pelo motor (`progression_engine.set_reps_position`) e devolvidas prontas no
// salvamento da série (`reps_position` + `reps_position_label`). Aqui só o
// rótulo gerado, como fallback.
export const REPS_POSITION_LABELS: Record<string, string> = INTENSITY.reps_position_labels;

const SAFETY_ALERT_EVENTS: ReadonlySet<string> = new Set(INTENSITY.safety_alert_events);

/** O evento gravado é alerta de segurança (laranja)? Os demais são informativos. */
export function isSafetyAlertEvent(safetyEvent: string | null | undefined): boolean {
  return safetyEvent != null && SAFETY_ALERT_EVENTS.has(safetyEvent);
}

/** Cor do banner/chip do evento: laranja só para o alerta de segurança do par. */
export function safetyEventColor(safetyEvent: string | null | undefined): 'orange' | 'blue' {
  return isSafetyAlertEvent(safetyEvent) ? 'orange' : 'blue';
}

// Banner do evento de segurança no Modo Treino.
//
// O teto EXIBIDO é o que o servidor já compôs (M76, C5): a prescrição exibida
// (snapshot congelado com a recuperação + overlay de hoje, que só restringe)
// e, sem snapshot, o overlay do par. A tela não recalcula o menor dos dois.
// Aqui só se decide a ATRIBUIÇÃO do texto: teto final abaixo do RPE máximo
// seguro do par (`overlay.max_safe_rpe`) é da recuperação; sem overlay, vale
// o corte `RECOVERY` registrado na prescrição; senão, é do equipamento. Só apresentação: não decide quando o banner aparece nem
// altera o evento.

type CeilingClamp = { field: string; reason: string };

// RPE 10 Lote 6/P9: sinal de recuperação que cortou o teto, congelado na ficha
// (`prescription_json.recovery_signals`, dono `domain/recovery_signal_summary.py`).
// O texto vem pronto do backend; aqui só a data vira DD/MM.
export type RecoverySignal = {
  code: string;
  text: string;
  source_session_date?: string | null;
};

export type SafetyBannerCeilingSource = {
  effective_rpe_ceiling?: number | null;
  clamps?: CeilingClamp[] | null;
  recovery_signals?: RecoverySignal[] | null;
};

export type SafetyBannerOverlaySource = SafetyBannerCeilingSource & {
  max_safe_rpe?: number | null;
};

export type SafetyBannerCeiling = {
  ceiling: number | null;
  origin: 'RECOVERY' | 'EQUIPMENT' | null;
};

const SAFETY_BANNER_SUFFIX = 'a série vale como intensidade atingida e não eleva o alvo dos próximos treinos.';
const INFORMATIVE_BANNER_SUFFIX = 'a série vale como intensidade atingida.';

/** `2026-10-03` -> `03/10` (data de parede, sem fuso). */
function formatDayMonth(isoDate: string | null | undefined): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate ?? '');
  return match ? `${match[3]}/${match[2]}` : null;
}

/** "queda de repetições no exercício Abdominal crunch (12 → 8 reps) em 03/10; …" */
export function formatRecoverySignals(signals: RecoverySignal[] | null | undefined): string | null {
  const parts = (signals ?? [])
    .filter((signal) => signal?.text)
    .map((signal) => {
      const day = formatDayMonth(signal.source_session_date);
      return day ? `${signal.text} em ${day}` : signal.text;
    });
  return parts.length ? parts.join('; ') : null;
}

/** "Teto 9 hoje pela recuperação: queda de repetições … em 03/10" (briefing). */
export function recoveryCeilingText(
  ceiling: number | null | undefined,
  signals: RecoverySignal[] | null | undefined,
): string | null {
  const reasons = formatRecoverySignals(signals);
  if (!reasons) return null;
  return ceiling != null ? `Teto ${ceiling} hoje pela recuperação: ${reasons}` : `Recuperação: ${reasons}`;
}

export function resolveSafetyBannerCeiling(
  prescription: SafetyBannerCeilingSource | null | undefined,
  overlay: SafetyBannerOverlaySource | null | undefined,
): SafetyBannerCeiling {
  const ceiling = prescription?.effective_rpe_ceiling ?? overlay?.effective_rpe_ceiling ?? null;
  if (ceiling == null) return { ceiling: null, origin: null };
  // Empate com o teto do par -> equipamento (mesma regra de `training_safety.compose`).
  const pairCeiling = overlay?.max_safe_rpe ?? null;
  if (pairCeiling != null) return { ceiling, origin: ceiling < pairCeiling ? 'RECOVERY' : 'EQUIPMENT' };
  const recoveryClamp = (prescription?.clamps ?? []).some(
    (clamp) => clamp.reason === 'RECOVERY' && clamp.field === 'rpe_target',
  );
  return { ceiling, origin: recoveryClamp ? 'RECOVERY' : 'EQUIPMENT' };
}

export function buildSafetyEventBannerText(
  safetyEvent: string,
  prescription: SafetyBannerCeilingSource | null | undefined,
  overlay: SafetyBannerOverlaySource | null | undefined,
): string {
  const { ceiling, origin } = resolveSafetyBannerCeiling(prescription, overlay);
  // Lote 6/P9: o sinal que cortou o teto (texto do backend), quando a ficha o congelou.
  const reasons = formatRecoverySignals(prescription?.recovery_signals);
  const reasonsPart = reasons ? `: ${reasons}` : '';
  if (!isSafetyAlertEvent(safetyEvent)) {
    // Informativo: o par permite a falha e só a recuperação baixou o teto do dia.
    const label = SAFETY_EVENT_LABELS[safetyEvent] || safetyEvent;
    const ceilingPart = ceiling != null ? ` (teto ${ceiling} pela recuperação${reasonsPart})` : '';
    return `${label}${ceilingPart} — ${INFORMATIVE_BANNER_SUFFIX}`;
  }
  if (ceiling != null && origin === 'RECOVERY') {
    return `RPE acima do alvo da recuperação (teto ${ceiling} hoje${reasonsPart}) — ${SAFETY_BANNER_SUFFIX}`;
  }
  const label = SAFETY_EVENT_LABELS[safetyEvent] || safetyEvent;
  const ceilingPart = ceiling != null ? ` (teto ${ceiling} neste equipamento)` : '';
  return `${label}${ceilingPart} — ${SAFETY_BANNER_SUFFIX}`;
}
