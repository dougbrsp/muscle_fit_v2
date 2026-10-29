// PR-14 — RPE seguro no cliente (PLANO_20260912.md §5.7).
//
// O que estes testes protegem: o modo treino NUNCA oferecer um RPE acima do
// teto seguro do par, e os rótulos de `intensity_status`/`safety_event` serem
// os mesmos no modo treino e nos dashboards (um único módulo).

import { describe, expect, it } from 'vitest';

import {
  buildSafetyEventBannerText,
  formatRecoverySignals,
  INTENSITY_STATUS_COLORS,
  INTENSITY_STATUS_LABELS,
  isSafetyAlertEvent,
  recoveryCeilingText,
  resolveSafetyBannerCeiling,
  SAFETY_EVENT_LABELS,
  SAFETY_EVENT_RPE_ABOVE_CEILING,
  SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET,
  safetyEventColor,
  type IntensityStatus,
} from './intensity-status';
import motorConstants from './motor-constants.generated.json';
import { clampRpeSuggestion } from './workout-mode-types';

describe('clampRpeSuggestion', () => {
  it('corta a sugestão acima do teto (o antigo default fixo de RPE 10)', () => {
    expect(clampRpeSuggestion(10, 9)).toBe(9);
    expect(clampRpeSuggestion(10, 8)).toBe(8);
    expect(clampRpeSuggestion(9.5, 9)).toBe(9);
  });

  it('não mexe no valor dentro do teto — nem para cima', () => {
    expect(clampRpeSuggestion(8, 9)).toBe(8);
    expect(clampRpeSuggestion(10, 10)).toBe(10);
    expect(clampRpeSuggestion(7, 10)).toBe(7);
  });

  it('sem teto conhecido devolve o valor como veio', () => {
    expect(clampRpeSuggestion(10, null)).toBe(10);
    expect(clampRpeSuggestion(10, undefined)).toBe(10);
  });

  it('sem valor não inventa nenhum', () => {
    expect(clampRpeSuggestion(null, 9)).toBeNull();
    expect(clampRpeSuggestion(null, null)).toBeNull();
  });
});

describe('rótulos de intensidade e segurança', () => {
  const STATUSES: IntensityStatus[] = [
    'REACHED',
    'BELOW_TARGET',
    'FAILURE_NOT_ALLOWED_REACHED_CEILING',
    'ABOVE_SAFE_CEILING',
  ];

  it('cobre os quatro status da §5.7, com rótulo e cor', () => {
    for (const status of STATUSES) {
      expect(INTENSITY_STATUS_LABELS[status]).toBeTruthy();
      expect(INTENSITY_STATUS_COLORS[status]).toBeTruthy();
    }
    expect(Object.keys(INTENSITY_STATUS_LABELS).sort()).toEqual([...STATUSES].sort());
  });

  it('descreve os dois safety_event (segurança do par e informativo da recuperação)', () => {
    expect(SAFETY_EVENT_RPE_ABOVE_CEILING).toBe('RPE_ABOVE_SAFE_CEILING');
    expect(SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET).toBe('RPE_ABOVE_RECOVERY_TARGET');
    expect(SAFETY_EVENT_LABELS[SAFETY_EVENT_RPE_ABOVE_CEILING]).toBe('RPE acima do teto seguro');
    expect(SAFETY_EVENT_LABELS[SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET]).toBe('RPE acima do alvo do dia');
  });

  it('paridade com o backend: códigos e rótulos vêm do JSON gerado pelo dono', () => {
    // Fixture gerada por api/tests/unit/test_m76_lote1b_espelhos_fe.py (--gravar) a
    // partir de api/app/domain/intensity_status_labels.py.
    const intensity = motorConstants.intensity;
    expect(INTENSITY_STATUS_LABELS).toEqual(intensity.intensity_status_labels);
    expect(SAFETY_EVENT_LABELS).toEqual(intensity.safety_event_labels);
    expect(Object.keys(INTENSITY_STATUS_COLORS).sort()).toEqual(Object.keys(intensity.intensity_status_labels).sort());
    expect(intensity.safety_alert_events).toEqual([SAFETY_EVENT_RPE_ABOVE_CEILING]);
  });

  it('laranja só para o alerta de segurança do par; o informativo não é laranja', () => {
    expect(isSafetyAlertEvent(SAFETY_EVENT_RPE_ABOVE_CEILING)).toBe(true);
    expect(isSafetyAlertEvent(SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET)).toBe(false);
    expect(isSafetyAlertEvent(null)).toBe(false);
    expect(safetyEventColor(SAFETY_EVENT_RPE_ABOVE_CEILING)).toBe('orange');
    expect(safetyEventColor(SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET)).not.toBe('orange');
  });
});

describe('buildSafetyEventBannerText — evento informativo (Lote 1/P1)', () => {
  const event = SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET;

  it('texto neutro com o teto do dia atribuído à recuperação', () => {
    const prescription = {
      effective_rpe_ceiling: 9,
      clamps: [{ field: 'rpe_target', from: 10, to: 9, reason: 'RECOVERY' }],
    };
    const overlay = { effective_rpe_ceiling: 9, max_safe_rpe: 10, clamps: [] };
    expect(buildSafetyEventBannerText(event, prescription, overlay)).toBe(
      'RPE acima do alvo do dia (teto 9 pela recuperação) — a série vale como intensidade atingida.',
    );
  });

  it('sem teto conhecido: só o rótulo', () => {
    expect(buildSafetyEventBannerText(event, null, null)).toBe(
      'RPE acima do alvo do dia — a série vale como intensidade atingida.',
    );
  });
});

describe('buildSafetyEventBannerText', () => {
  const event = SAFETY_EVENT_RPE_ABOVE_CEILING;
  const suffix = ' — a série vale como intensidade atingida e não eleva o alvo dos próximos treinos.';

  it('teto da recuperação: lê o teto final do servidor e atribui à recuperação', () => {
    // Cenário sintético baseado em caso real: prescrição congelada 9 por
    // RECOVERY. Depois do M76 (C5) o overlay já vem composto (nunca acima do
    // congelado): os dois dizem 9 e a tela não recalcula o menor.
    const prescription = {
      effective_rpe_ceiling: 9,
      clamps: [{ field: 'rpe_target', from: 10, to: 9, reason: 'RECOVERY' }],
    };
    const overlay = { effective_rpe_ceiling: 9, max_safe_rpe: 10, clamps: [] };
    expect(resolveSafetyBannerCeiling(prescription, overlay)).toEqual({ ceiling: 9, origin: 'RECOVERY' });
    expect(buildSafetyEventBannerText(event, prescription, overlay)).toBe(
      `RPE acima do alvo da recuperação (teto 9 hoje)${suffix}`,
    );
  });

  it('usa o teto da prescrição exibida, não o do overlay', () => {
    // O servidor garante prescrição exibida <= overlay; se divergirem, vale a exibida.
    expect(resolveSafetyBannerCeiling({ effective_rpe_ceiling: 8 }, { effective_rpe_ceiling: 9, max_safe_rpe: 9 })).toEqual({
      ceiling: 8,
      origin: 'RECOVERY',
    });
  });

  it('teto do equipamento: mantém o texto atual', () => {
    // Overlay de hoje baixou o teto do par para 8 (a prescrição exibida já vem cortada).
    const prescription = {
      effective_rpe_ceiling: 8,
      clamps: [{ field: 'rpe_target', from: 10, to: 9, reason: 'RECOVERY' }],
    };
    const overlay = { effective_rpe_ceiling: 8, max_safe_rpe: 8, clamps: [] };
    expect(buildSafetyEventBannerText(event, prescription, overlay)).toBe(
      `RPE acima do teto seguro (teto 8 neste equipamento)${suffix}`,
    );
    // Empate com o teto do par é do equipamento (mesma regra do servidor).
    expect(
      buildSafetyEventBannerText(event, { effective_rpe_ceiling: 9 }, { effective_rpe_ceiling: 9, max_safe_rpe: 9 }),
    ).toBe(`RPE acima do teto seguro (teto 9 neste equipamento)${suffix}`);
    // Só o overlay (ficha sem snapshot).
    expect(buildSafetyEventBannerText(event, null, { effective_rpe_ceiling: 10, max_safe_rpe: 10 })).toBe(
      `RPE acima do teto seguro (teto 10 neste equipamento)${suffix}`,
    );
  });

  it('sem overlay: o corte RECOVERY da prescrição decide a atribuição', () => {
    const prescription = {
      effective_rpe_ceiling: 9,
      clamps: [{ field: 'rpe_target', from: 10, to: 9, reason: 'RECOVERY' }],
    };
    expect(resolveSafetyBannerCeiling(prescription, null)).toEqual({ ceiling: 9, origin: 'RECOVERY' });
    expect(resolveSafetyBannerCeiling({ effective_rpe_ceiling: 9, clamps: [] }, null)).toEqual({
      ceiling: 9,
      origin: 'EQUIPMENT',
    });
  });

  it('sem teto conhecido: rótulo sem parênteses', () => {
    expect(buildSafetyEventBannerText(event, null, null)).toBe(`RPE acima do teto seguro${suffix}`);
    expect(buildSafetyEventBannerText(event, { effective_rpe_ceiling: null }, undefined)).toBe(
      `RPE acima do teto seguro${suffix}`,
    );
  });
});

describe('banner e briefing dizem o sinal da recuperação (RPE 10 Lote 6/P9)', () => {
  const signals = [
    {
      code: 'EXCESSIVE_REP_DROP',
      text: 'queda de repetições no exercício Abdominal crunch (12 → 8 reps)',
      source_session_date: '2026-10-03',
    },
    { code: 'PERFORMANCE_DROP', text: 'desempenho abaixo do esperado', source_session_date: '2026-10-05' },
  ];
  const prescription = {
    effective_rpe_ceiling: 9,
    clamps: [{ field: 'rpe_target', from: 10, to: 9, reason: 'RECOVERY' }],
    recovery_signals: signals,
  };
  const overlay = { effective_rpe_ceiling: 9, max_safe_rpe: 10, clamps: [] };

  it('o FE só formata a data (DD/MM) e junta os textos do backend', () => {
    expect(formatRecoverySignals(signals)).toBe(
      'queda de repetições no exercício Abdominal crunch (12 → 8 reps) em 03/10; desempenho abaixo do esperado em 05/10',
    );
    expect(formatRecoverySignals([{ code: 'JOINT_PAIN', text: 'dor articular', source_session_date: null }])).toBe(
      'dor articular',
    );
    expect(formatRecoverySignals([])).toBeNull();
    expect(formatRecoverySignals(undefined)).toBeNull();
  });

  it('banner informativo cita o sinal que cortou o teto', () => {
    expect(buildSafetyEventBannerText(SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET, prescription, overlay)).toBe(
      'RPE acima do alvo do dia (teto 9 pela recuperação: queda de repetições no exercício Abdominal crunch ' +
        '(12 → 8 reps) em 03/10; desempenho abaixo do esperado em 05/10) — a série vale como intensidade atingida.',
    );
  });

  it('banner de segurança com teto da recuperação também cita o sinal', () => {
    expect(
      buildSafetyEventBannerText(SAFETY_EVENT_RPE_ABOVE_CEILING, { ...prescription, recovery_signals: signals.slice(0, 1) }, overlay),
    ).toBe(
      'RPE acima do alvo da recuperação (teto 9 hoje: queda de repetições no exercício Abdominal crunch (12 → 8 reps) ' +
        'em 03/10) — a série vale como intensidade atingida e não eleva o alvo dos próximos treinos.',
    );
  });

  it('briefing: "Teto 9 hoje pela recuperação: …"', () => {
    expect(recoveryCeilingText(9, signals.slice(1))).toBe(
      'Teto 9 hoje pela recuperação: desempenho abaixo do esperado em 05/10',
    );
    expect(recoveryCeilingText(null, signals.slice(1))).toBe('Recuperação: desempenho abaixo do esperado em 05/10');
    expect(recoveryCeilingText(9, [])).toBeNull();
  });
});
