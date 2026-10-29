import { describe, expect, it } from 'vitest';
import { EMPTY_DASHBOARD_FILTERS } from './dashboard-filters';
import {
  INSIGHT_SECTION_ORDER,
  formatComparison,
  formatEvidence,
  insightHref,
  insightSections,
  metricLabel,
  progressionBars,
  progressionLegend,
  reassessHoursLabel,
} from './dashboard-overview';
import type { Insight, ProgressionBlock } from './dashboard-overview-types';

const insight: Insight = {
  code: 'MUSCLE_BELOW_MIN_TWO_WEEKS',
  question: 'Algum grupo ficou abaixo do mínimo?',
  status: 'BELOW_MIN',
  severity: 'ATTENTION',
  answer: 'Costas ficou abaixo do mínimo nas duas últimas semanas.',
  evidence: [
    {
      metric: 'weekly_effective_sets',
      from_value: 5,
      to_value: 4,
      unit: 'séries/sem',
      nature: 'DERIVED',
      observed_at: '2026-09-22',
    },
    { metric: 'recovery_pct', from_value: null, to_value: 55.5, unit: '%', nature: 'INFERRED', observed_at: null },
  ],
  comparison: {
    basis: 'weekly_target_min_two_consecutive_weeks',
    window: '7d x 2',
    keys: { muscle_group_id: 6 },
  },
  data_quality: 'MEDIUM',
  limitations: ['Sem contribuição cadastrada em 2 exercícios.'],
  alternative_explanations: ['Deload planejado.'],
  suggested_action: 'Priorizar um exercício direto.',
  reassess_after: '2026-09-29',
  source_screen: '/dashboard/musculos',
  fingerprint: 'MUSCLE_BELOW_MIN_TWO_WEEKS:muscle_group_id=6',
  // F2.2: `null` = não houve decisão de cooldown (antes da migração 051 ou com
  // a flag desligada). Nenhuma seção do cartão depende dele — o selo é extra.
  cooldown: null,
};

describe('formatEvidence', () => {
  it('mostra de → para com unidade e data', () => {
    expect(formatEvidence(insight.evidence[0])).toBe(
      'Séries efetivas/semana: 5 → 4 séries/sem (22/09/2026)',
    );
  });

  it('sem valor anterior mostra só o atual, nunca zero', () => {
    expect(formatEvidence(insight.evidence[1])).toBe('Recuperação estimada: 55,5 %');
  });

  it('valor ausente vira travessão', () => {
    expect(
      formatEvidence({ metric: 'e1rm', from_value: null, to_value: null, unit: 'kg', nature: 'ESTIMATED', observed_at: null }),
    ).toBe('e1RM estimado: —');
  });
});

describe('metricLabel', () => {
  it('traduz códigos conhecidos e preserva sufixo indexado', () => {
    expect(metricLabel('target_achievement_pct[6]')).toBe('target_achievement_pct[6]');
    expect(metricLabel('direct_sets')).toBe('Séries diretas');
  });
});

describe('formatComparison', () => {
  it('descreve base, janela e chaves', () => {
    expect(formatComparison(insight.comparison)).toBe(
      'Base: mínimo semanal da meta, em duas semanas consecutivas; janela: 7d x 2; chaves: muscle_group_id=6',
    );
  });

  it('lista de ids vira valores separados por vírgula', () => {
    expect(formatComparison({ basis: 'x', window: '28d', keys: { exercise_ids: [1, 2] } })).toBe(
      'Base: x; janela: 28d; chaves: exercise_ids=1,2',
    );
  });

  it('sem comparação diz que não há', () => {
    expect(formatComparison(null)).toBe('Sem comparação declarada.');
  });
});

describe('insightSections', () => {
  it('sempre devolve as sete seções do contrato, na ordem', () => {
    const sections = insightSections(insight);
    expect(sections.map((s) => s.key)).toEqual([...INSIGHT_SECTION_ORDER]);
    expect(sections[0].lines).toEqual([insight.answer]);
    expect(sections[3].lines).toEqual(['Média — limitações:', 'Sem contribuição cadastrada em 2 exercícios.']);
    expect(sections[5].lines).toEqual(['Priorizar um exercício direto.']);
    expect(sections[6].lines).toEqual(['29/09/2026']);
  });

  it('seções vazias mostram travessão em vez de sumir', () => {
    const sections = insightSections({
      ...insight,
      evidence: [],
      alternative_explanations: [],
      suggested_action: null,
      reassess_after: null,
      limitations: [],
    });
    expect(sections.find((s) => s.key === 'evidence')?.lines).toEqual(['—']);
    expect(sections.find((s) => s.key === 'alternatives')?.lines).toEqual(['—']);
    expect(sections.find((s) => s.key === 'action')?.lines).toEqual(['Nenhuma ação: apenas informação.']);
    expect(sections.find((s) => s.key === 'reassess')?.lines).toEqual(['—']);
    expect(sections.find((s) => s.key === 'confidence')?.lines).toEqual(['Média']);
  });
});

describe('insightHref', () => {
  it('preserva o filtro do hub para destinos do dashboard', () => {
    const href = insightHref(insight, { ...EMPTY_DASHBOARD_FILTERS, window: '90d' });
    expect(href.startsWith('/dashboard/musculos')).toBe(true);
    expect(href).toContain('window=90d');
  });

  it('destino fora do hub vai limpo', () => {
    expect(insightHref({ ...insight, source_screen: '/dominio-exercicios' }, { ...EMPTY_DASHBOARD_FILTERS, window: '90d' })).toBe(
      '/dominio-exercicios',
    );
  });
});

describe('progressionBars', () => {
  const block: ProgressionBlock = {
    question: 'Estou evoluindo?',
    answer: '2 de 3 comparáveis progredindo.',
    drilldown_href: '/dashboard/progressao',
    data_quality: 'HIGH',
    limitations: [],
    exercises_total: 4,
    comparable_total: 3,
    exposures_per_exercise: 8,
    truncated: false,
    counts: [
      { status: 'PROGRESSING', label: 'Progredindo', count: 2, exercises: [] },
      { status: 'STABLE', label: 'Estável', count: 0, exercises: [] },
      { status: 'PLATEAU_SUSPECTED', label: 'Suspeita de platô', count: 1, exercises: [] },
      { status: 'REGRESSING', label: 'Regredindo', count: 0, exercises: [] },
      { status: 'INSUFFICIENT_DATA', label: 'Dados insuficientes', count: 1, exercises: [] },
      { status: 'NOT_COMPARABLE', label: 'Não comparável', count: 0, exercises: [] },
    ],
  };

  it('só status com contagem, com parcela do total e cor do registro único', () => {
    const bars = progressionBars(block);
    expect(bars.map((b) => b.status)).toEqual(['PROGRESSING', 'PLATEAU_SUSPECTED', 'INSUFFICIENT_DATA']);
    expect(bars[0].share).toBeCloseTo(0.5);
    expect(bars[0].color).toBe('green');
    expect(bars[1].color).toBe('yellow');
  });

  it('sem exercício a parcela é zero, não NaN', () => {
    const bars = progressionBars({ ...block, exercises_total: 0, counts: block.counts.map((c) => ({ ...c, count: 0 })) });
    expect(bars).toEqual([]);
  });

  it('legenda lista status e contagem', () => {
    expect(progressionLegend(progressionBars(block))).toBe('Progredindo 2 · Suspeita de platô 1 · Dados insuficientes 1');
  });
});

describe('reassessHoursLabel', () => {
  it('converte horas em dias quando >= 24h', () => {
    expect(reassessHoursLabel(36)).toBe('≈ 2 dias');
    expect(reassessHoursLabel(24)).toBe('≈ 1 dia');
    expect(reassessHoursLabel(8)).toBe('≈ 8 h');
  });

  it('sem prazo devolve null', () => {
    expect(reassessHoursLabel(null)).toBeNull();
    expect(reassessHoursLabel(-1)).toBeNull();
  });
});
