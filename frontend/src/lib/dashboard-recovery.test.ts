import { describe, expect, it } from 'vitest';
import { EM_DASH } from './dashboard-format';
import {
  adjustmentLabel,
  bodyMapEntries,
  bucketSignals,
  collectLimitations,
  countByReadiness,
  countUnfavorable,
  factorLines,
  isAdjustmentStale,
  readyAtLabel,
  reassessLabel,
  recommendationLabel,
  RECOVERY_BODY_MAP_COLORS,
  SIGNAL_BUCKET_ORDER,
  sortGroupsByUrgency,
  trainingVsRestLabel,
} from './dashboard-recovery';
import type {
  RecoveryFactors,
  RecoveryGroup,
  RecoveryRecommendation,
  RecoverySignal,
  RecoverySignalsResponse,
  TrainingVsRestMetric,
} from './dashboard-recovery-types';
import { READINESS_ORDER, statusLegend, statusVisual, type ReadinessCode } from './dashboard-status';
import type { Metric } from './dashboard-v3-types';
import { resolveRegions } from '@/components/bodymap/bodymap';

const metric = (value: number | null): Metric => ({
  value,
  unit: 'h',
  nature: 'DERIVED',
  metric_version: 'recovery-signals-v1',
  data_quality: value == null ? 'INSUFFICIENT' : 'HIGH',
  limitations: [],
});

const signal = (over: Partial<RecoverySignal>): RecoverySignal => ({
  code: 'hrv',
  label: 'HRV',
  description: '',
  current: metric(32),
  baseline: metric(40),
  delta: metric(-8),
  direction: 'BELOW',
  reading: 'UNFAVORABLE',
  agreement: 'AGREES',
  observations: 7,
  baseline_observations: 28,
  min_observations: 14,
  source: 'intervals_wellness',
  limitations: [],
  ...over,
});

const group = (over: Partial<RecoveryGroup>): RecoveryGroup => ({
  muscle_group_id: 1,
  muscle_group: 'Peito',
  readiness_status: 'READY',
  guidance: 'ideal',
  recovery_pct: metric(100),
  estimated_recovery_hours: metric(24),
  remaining_hours: metric(0),
  hours_since_last_stimulus: metric(30),
  session_dose: metric(4),
  last_session_at: null,
  last_trained_local_date: null,
  ready_at: null,
  ready_label: 'Pronto',
  max_rpe: 10,
  allow_failure: true,
  volume_multiplier: 1,
  blocking_signal_codes: [],
  explanation: [],
  recommendation: null,
  factors: null,
  ...over,
});

const recommendation = (over: Partial<RecoveryRecommendation> = {}): RecoveryRecommendation => ({
  direct_sets_min: 2,
  direct_sets_max: 3,
  maximum_rpe: 10,
  allow_failure_on_last_set: true,
  allow_progression: true,
  allow_indirect_work: true,
  volume_multiplier: 1,
  ...over,
});

const factors = (over: Partial<RecoveryFactors> = {}): RecoveryFactors => ({
  session_dose: 4.2,
  dose_last_72h: 7.75,
  avg_rpe_used: 8.25,
  avg_rpe_window: 8.4,
  density_penalty: null,
  signal_penalty: null,
  performance_ratio: null,
  recent_sets: [],
  notes: [],
  ...over,
});

const vsRest = (over: Partial<TrainingVsRestMetric> = {}): TrainingVsRestMetric => ({
  code: 'hrv',
  label: 'Variabilidade da FC (HRV)',
  unit: 'ms',
  source: 'intervals_wellness',
  higher_is_better: true,
  training_days: metric(50),
  rest_days: metric(70),
  delta: metric(-20),
  direction: 'BELOW',
  reading: 'UNFAVORABLE',
  training_observations: 3,
  rest_observations: 4,
  min_days: 3,
  limitations: [],
  ...over,
});

describe('bucketSignals', () => {
  it('separa divergente, concordante, neutro e sem baseline', () => {
    const buckets = bucketSignals([
      signal({ code: 'hrv', agreement: 'DIVERGES', reading: 'FAVORABLE' }),
      signal({ code: 'resting_hr', agreement: 'AGREES' }),
      signal({ code: 'sleep_duration', agreement: 'NEUTRAL', reading: 'NEUTRAL' }),
      signal({ code: 'form_tsb', agreement: 'NO_BASELINE', reading: 'UNKNOWN', baseline: metric(null) }),
    ]);

    expect(buckets.diverging.map((s) => s.code)).toEqual(['hrv']);
    expect(buckets.agreeing.map((s) => s.code)).toEqual(['resting_hr']);
    expect(buckets.neutral.map((s) => s.code)).toEqual(['sleep_duration']);
    expect(buckets.unavailable.map((s) => s.code)).toEqual(['form_tsb']);
  });

  it('mostra o divergente ANTES do concordante — é ele que não pode sumir', () => {
    expect(SIGNAL_BUCKET_ORDER[0]).toBe('diverging');
  });

  it('nenhum sinal se perde no caminho', () => {
    const entrada = [
      signal({ code: 'a', agreement: 'AGREES' }),
      signal({ code: 'b', agreement: 'DIVERGES' }),
      signal({ code: 'c', agreement: 'NO_BASELINE' }),
      signal({ code: 'd', agreement: 'NEUTRAL' }),
    ];
    const buckets = bucketSignals(entrada);
    const total = SIGNAL_BUCKET_ORDER.reduce((soma, key) => soma + buckets[key].length, 0);
    expect(total).toBe(entrada.length);
  });
});

describe('countUnfavorable', () => {
  it('só conta sinal desfavorável COM comparação possível', () => {
    expect(
      countUnfavorable([
        signal({ reading: 'UNFAVORABLE', agreement: 'AGREES' }),
        signal({ reading: 'UNFAVORABLE', agreement: 'NO_BASELINE' }),
        signal({ reading: 'FAVORABLE', agreement: 'DIVERGES' }),
      ]),
    ).toBe(1);
  });
});

describe('sortGroupsByUrgency', () => {
  it('põe o mais restritivo primeiro e desempata por horas restantes', () => {
    const ordenados = sortGroupsByUrgency([
      group({ muscle_group: 'Peito', readiness_status: 'READY' }),
      group({ muscle_group: 'Costas', readiness_status: 'NO_DIRECT_WORK', remaining_hours: metric(10) }),
      group({ muscle_group: 'Ombros', readiness_status: 'REDUCED_DOSE', remaining_hours: metric(4) }),
      group({ muscle_group: 'Biceps', readiness_status: 'REDUCED_DOSE', remaining_hours: metric(9) }),
    ]);
    expect(ordenados.map((g) => g.muscle_group)).toEqual(['Costas', 'Biceps', 'Ombros', 'Peito']);
  });

  it('não muda o array recebido', () => {
    const original = [group({ muscle_group: 'A' }), group({ muscle_group: 'B' })];
    sortGroupsByUrgency(original);
    expect(original.map((g) => g.muscle_group)).toEqual(['A', 'B']);
  });
});

describe('countByReadiness', () => {
  it('conta por estado e ignora código fora do contrato', () => {
    expect(
      countByReadiness([
        group({ readiness_status: 'READY' }),
        group({ readiness_status: 'READY' }),
        group({ readiness_status: 'NO_DIRECT_WORK' }),
        group({ readiness_status: 'INVENTADO' }),
      ]),
    ).toEqual({ READY: 2, REDUCED_DOSE: 0, NO_DIRECT_WORK: 1 });
  });
});

describe('reassessLabel', () => {
  it('sem nada pendente NÃO vira "0 h"', () => {
    expect(reassessLabel(null)).toContain('Nada pendente');
    expect(reassessLabel(0)).toContain('Nada pendente');
  });

  it('formata horas e dias', () => {
    expect(reassessLabel(0.5)).toContain('menos de 1 h');
    expect(reassessLabel(10)).toContain('10 h');
    expect(reassessLabel(36)).toContain('dia');
  });
});

describe('collectLimitations', () => {
  it('junta envelope, sinais e recorte por dia de treino, sem repetir', () => {
    const payload = {
      limitations: ['Sem wearable', 'Sem contexto'],
      signals: [
        signal({ limitations: ['Sem wearable', 'HRV sem baseline'] }),
        signal({ code: 'x', limitations: ['HRV sem baseline'] }),
      ],
      training_vs_rest: {
        training_days: 3,
        rest_days: 4,
        min_days: 3,
        metrics: [vsRest({ limitations: ['HRV: 2 dia(s) com treino'] })],
        limitations: ['Sem wearable', 'Associação, nunca causa'],
      },
    } as unknown as RecoverySignalsResponse;

    expect(collectLimitations(payload)).toEqual([
      'Sem wearable',
      'Sem contexto',
      'HRV sem baseline',
      'Associação, nunca causa',
      'HRV: 2 dia(s) com treino',
    ]);
  });
});

// ---------------------------------------------------------------------------
// "Bom para" (2026-10-17): instante e rótulo prontos do backend
// ---------------------------------------------------------------------------

describe('readyAtLabel', () => {
  it('formata o instante do backend no fuso do usuário', () => {
    const grupo = group({ readiness_status: 'REDUCED_DOSE', ready_at: '2026-10-18T14:00:00-03:00', ready_label: null });
    expect(readyAtLabel(grupo, 'America/Sao_Paulo')).toBe('dom 18/10 14:00');
  });

  it('o dia é o da parede do usuário, não o de UTC (virada de fuso)', () => {
    // 22:30 em São Paulo já é segunda 01:30 em UTC.
    const grupo = group({ ready_at: '2026-10-18T22:30:00-03:00', ready_label: null });
    expect(readyAtLabel(grupo, 'America/Sao_Paulo')).toBe('dom 18/10 22:30');
    expect(readyAtLabel(grupo, 'UTC')).toBe('seg 19/10 01:30');
  });

  it('já pronto: mostra o rótulo do backend', () => {
    expect(readyAtLabel(group({ ready_at: null, ready_label: 'Pronto' }), 'America/Sao_Paulo')).toBe('Pronto');
  });

  it('sem previsão: traço, nunca uma data inventada', () => {
    const grupo = group({ readiness_status: 'NO_DIRECT_WORK', ready_at: null, ready_label: null });
    expect(readyAtLabel(grupo, 'America/Sao_Paulo')).toBe(EM_DASH);
  });
});

// ---------------------------------------------------------------------------
// F2.5 — M33: recomendação de séries, fatores e ajuste global
// ---------------------------------------------------------------------------

describe('recommendationLabel', () => {
  it('escreve a faixa de séries e as restrições do modelo', () => {
    expect(recommendationLabel(group({ recommendation: recommendation() }))).toBe(
      '2-3 série(s) diretas · RPE máx 10',
    );
  });

  it('faixa de um valor só não vira "2-2"', () => {
    const texto = recommendationLabel(
      group({ recommendation: recommendation({ direct_sets_min: 1, direct_sets_max: 1 }) }),
    );
    expect(texto).toContain('1 série(s) diretas');
  });

  it('dose reduzida escreve a REGRA do modelo (M76 B2), "sem falha" e "sem progressão"', () => {
    const regra = '75% das séries planejadas de cada exercício, arredondado para cima (mínimo 1)';
    const texto = recommendationLabel(
      group({
        recommendation: recommendation({
          direct_sets_min: null,
          direct_sets_max: null,
          maximum_rpe: 9,
          allow_failure_on_last_set: false,
          allow_progression: false,
          volume_multiplier: 0.75,
          minimum_sets: 1,
          sets_rule: regra,
        }),
      }),
    );
    expect(texto).toBe(`Séries: ${regra} · RPE máx 9 · sem falha · sem progressão`);
    expect(texto).not.toContain('1-2');
  });

  it('bloqueio NÃO é escrito como "0 séries" — é outro estado', () => {
    const texto = recommendationLabel(
      group({
        recommendation: recommendation({
          direct_sets_min: 0,
          direct_sets_max: 0,
          maximum_rpe: 8,
          allow_indirect_work: true,
        }),
      }),
    );
    expect(texto).toContain('Sem trabalho direto');
    expect(texto).toContain('indireto permitido');
    expect(texto).not.toContain('0 série');
  });

  it('sem recomendação no payload não inventa recomendação', () => {
    expect(recommendationLabel(group({ recommendation: null }))).toBe('—');
  });
});

describe('factorLines', () => {
  it('mostra a explicação do modelo antes dos fatores', () => {
    const linhas = factorLines(
      group({ explanation: ['A última sessão gerou dose 4.20.'], factors: factors() }),
    );
    expect(linhas[0]).toBe('A última sessão gerou dose 4.20.');
    expect(linhas.some((linha) => linha.includes('Dose da última sessão'))).toBe(true);
    expect(linhas.some((linha) => linha.includes('RPE médio usado no cálculo'))).toBe(true);
  });

  it('fator ausente não vira linha com zero', () => {
    const linhas = factorLines(
      group({
        factors: factors({ avg_rpe_used: null, avg_rpe_window: null, density_penalty: null }),
      }),
    );
    expect(linhas.some((linha) => linha.includes('RPE médio'))).toBe(false);
    expect(linhas.some((linha) => linha.includes('densidade'))).toBe(false);
  });

  it('os dois RPE aparecem com rótulos distintos (F2.4: paridade com /recuperacao-muscular)', () => {
    // A coluna "RPE médio" da tela legada era o da JANELA (`avg_rpe_window`),
    // não o que entrou no cálculo da dose. Rotular os dois de "RPE médio"
    // deixaria a paridade só aparente.
    const linhas = factorLines(group({ factors: factors() }));
    expect(linhas.some((linha) => linha.startsWith('RPE médio usado no cálculo: '))).toBe(true);
    expect(linhas).toContain('RPE médio da janela: 8,4');
  });

  it('RPE da janela ausente não apaga o RPE do cálculo', () => {
    const linhas = factorLines(group({ factors: factors({ avg_rpe_window: null }) }));
    expect(linhas.some((linha) => linha.includes('RPE médio usado no cálculo'))).toBe(true);
    expect(linhas.some((linha) => linha.includes('RPE médio da janela'))).toBe(false);
  });

  it('penalidade zero também não vira linha (ela não aconteceu)', () => {
    const linhas = factorLines(group({ factors: factors({ density_penalty: 0 }) }));
    expect(linhas.some((linha) => linha.includes('densidade'))).toBe(false);
  });

  it('nota do RPE 10 isolado é repassada como está', () => {
    const linhas = factorLines(group({ factors: factors({ notes: ['RPE 10 isolado.'] }) }));
    expect(linhas).toContain('RPE 10 isolado.');
  });

  it('sem fatores, restam apenas as linhas do modelo', () => {
    expect(factorLines(group({ explanation: ['Nenhuma sessão recente.'], factors: null }))).toEqual([
      'Nenhuma sessão recente.',
    ]);
  });
});

describe('bodyMapEntries', () => {
  it('bloqueado é a intensidade mais forte e pronto a mais fraca', () => {
    const entradas = bodyMapEntries([
      group({ muscle_group: 'Peito', readiness_status: 'NO_DIRECT_WORK' }),
      group({ muscle_group: 'Costas', readiness_status: 'REDUCED_DOSE' }),
      group({ muscle_group: 'Ombros', readiness_status: 'READY' }),
    ]);
    expect(entradas.map((e) => e.intensity)).toEqual([3, 2, 1]);
  });

  it('grupo com status fora do contrato NÃO some do mapa (entra no degrau mais brando)', () => {
    const entradas = bodyMapEntries([group({ readiness_status: 'INVENTADO' })]);
    expect(entradas).toHaveLength(1);
    expect(entradas[0].intensity).toBe(1);
  });

  it('o tooltip traz o grupo e a prontidão', () => {
    const [entrada] = bodyMapEntries([
      group({ muscle_group: 'Peito', recovery_pct: metric(42) }),
    ]);
    expect(entrada.tooltip).toContain('Peito');
    expect(entrada.tooltip).toContain('42%');
  });
});

/**
 * Mapa corporal do recovery com a regra REAL do componente (`resolveRegions`):
 * quando grupos da mesma região divergem, o PIOR estado vence
 * (Evitar trabalho direto > Dose reduzida > Pronto) — decisão do usuário em
 * 2026-10-03. "Peitoral superior" e "Peitoral médio/inferior" caem os dois em
 * `chest` no banco (migração 013).
 */
describe('mapa corporal do recovery: o pior estado vence', () => {
  const GRUPO_PARA_SLUGS = {
    'Peitoral superior': ['chest'],
    'Peitoral médio/inferior': ['chest'],
    Bíceps: ['biceps'],
  };
  const hex = (code: ReadinessCode) => statusVisual('readiness', code).hex;

  function regiaoDoPeitoral(a: ReadinessCode, b: ReadinessCode) {
    const entradas = bodyMapEntries([
      group({ muscle_group: 'Peitoral superior', readiness_status: a }),
      group({ muscle_group: 'Peitoral médio/inferior', readiness_status: b }),
    ]);
    const regioes = resolveRegions(entradas, GRUPO_PARA_SLUGS, RECOVERY_BODY_MAP_COLORS);
    expect(regioes).toHaveLength(1);
    return regioes[0];
  }

  it.each<[ReadinessCode, ReadinessCode, ReadinessCode]>([
    ['READY', 'NO_DIRECT_WORK', 'NO_DIRECT_WORK'],
    ['NO_DIRECT_WORK', 'READY', 'NO_DIRECT_WORK'],
    ['REDUCED_DOSE', 'NO_DIRECT_WORK', 'NO_DIRECT_WORK'],
    ['NO_DIRECT_WORK', 'REDUCED_DOSE', 'NO_DIRECT_WORK'],
    ['READY', 'REDUCED_DOSE', 'REDUCED_DOSE'],
    ['REDUCED_DOSE', 'READY', 'REDUCED_DOSE'],
  ])('%s + %s -> cor de %s', (a, b, pior) => {
    expect(regiaoDoPeitoral(a, b).color).toBe(hex(pior));
  });

  it('o toque na região lista os dois grupos, o pior primeiro', () => {
    const chest = regiaoDoPeitoral('READY', 'NO_DIRECT_WORK');
    expect(chest.entries.map((e) => e.muscle_group)).toEqual(['Peitoral médio/inferior', 'Peitoral superior']);
  });

  it.each([...READINESS_ORDER])('grupo sozinho mantém a cor do próprio estado (%s)', (code) => {
    const regioes = resolveRegions(
      bodyMapEntries([group({ muscle_group: 'Bíceps', readiness_status: code })]),
      GRUPO_PARA_SLUGS,
      RECOVERY_BODY_MAP_COLORS,
    );
    expect(regioes.map((r) => [r.slug, r.color])).toEqual([['biceps', hex(code)]]);
  });

  it('cor de cada estado no mapa = cor do mesmo estado na legenda', () => {
    const legenda = statusLegend('readiness');
    for (const code of READINESS_ORDER) {
      const [entrada] = bodyMapEntries([group({ readiness_status: code })]);
      const rotulo = statusVisual('readiness', code).label;
      expect(RECOVERY_BODY_MAP_COLORS[entrada.intensity - 1]).toBe(
        legenda.find((item) => item.label === rotulo)?.color,
      );
    }
    expect(new Set(RECOVERY_BODY_MAP_COLORS).size).toBe(READINESS_ORDER.length);
  });
});

describe('adjustmentLabel', () => {
  it('sem Intervals.icu a frase é "não houve ajuste", não "fator neutro"', () => {
    expect(adjustmentLabel(null)).toContain('Sem dado do Intervals.icu');
  });

  it('fator acima de 1 alonga o tempo de recuperação', () => {
    const texto = adjustmentLabel({
      factor: 1.12,
      form_tsb: -12.5,
      wellness_date: '2026-09-21',
      description: '',
    });
    expect(texto).toContain('12% a mais');
    expect(texto).toContain('TSB -12,5');
  });

  it('fator 1,0 é "sem alteração", e não 0%', () => {
    expect(
      adjustmentLabel({ factor: 1, form_tsb: 0, wellness_date: null, description: '' }),
    ).toContain('sem alteração');
  });

  it('M52: dado velho avisa a data do último wellness e que o ajuste foi ignorado', () => {
    const ajuste = {
      factor: 1,
      form_tsb: -25,
      wellness_date: '2026-09-03',
      stale: true,
      max_age_days: 3,
      description: '',
    };
    expect(isAdjustmentStale(ajuste)).toBe(true);
    const texto = adjustmentLabel(ajuste);
    expect(texto).toContain('desatualizado');
    expect(texto).toContain('03/09/2026');
    expect(texto).toContain('mais de 3 dias');
    // O TSB ignorado não aparece como se tivesse sido aplicado.
    expect(texto).not.toContain('TSB');
  });

  it('M52: sem a flag (ou fresco) não é aviso', () => {
    expect(isAdjustmentStale(null)).toBe(false);
    expect(
      isAdjustmentStale({ factor: 1.1, form_tsb: -15, wellness_date: null, description: '' }),
    ).toBe(false);
    expect(
      isAdjustmentStale({
        factor: 1.1,
        form_tsb: -15,
        wellness_date: null,
        stale: false,
        max_age_days: 3,
        description: '',
      }),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// F2.5 — M36: dia com treino × dia sem treino
// ---------------------------------------------------------------------------

describe('trainingVsRestLabel', () => {
  it('HRV menor em dia de treino lê desfavorável', () => {
    const texto = trainingVsRestLabel(vsRest());
    expect(texto).toContain('menor em dia de treino');
    expect(texto).toContain('desfavorável');
  });

  it('FC de repouso maior em dia de treino também lê desfavorável', () => {
    const texto = trainingVsRestLabel(
      vsRest({ code: 'resting_hr', higher_is_better: false, delta: metric(7), direction: 'ABOVE' }),
    );
    expect(texto).toContain('maior em dia de treino');
    expect(texto).toContain('desfavorável');
  });

  it('sem os dois lados não inventa diferença', () => {
    const texto = trainingVsRestLabel(
      vsRest({ training_days: metric(null), delta: metric(null), direction: 'UNKNOWN', reading: 'UNKNOWN' }),
    );
    expect(texto).toContain('Sem comparação possível');
  });

  it('dentro da tolerância diz que é igual, em vez de mostrar um ruído', () => {
    const texto = trainingVsRestLabel(
      vsRest({ delta: metric(0.4), direction: 'IN_LINE', reading: 'NEUTRAL' }),
    );
    expect(texto).toContain('Praticamente igual');
  });
});
