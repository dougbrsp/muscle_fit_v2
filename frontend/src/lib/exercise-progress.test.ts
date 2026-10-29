import { describe, expect, it } from 'vitest';
import {
  EXERCISE_PROGRESS_BASE_PATH,
  STATUS_REASON_TEXTS,
  COMPARABILITY_MISMATCH_HINTS,
  COMPARABILITY_MISMATCH_LABELS,
  fatigueContextLabel,
  statusWithContextLabel,
  exerciseProgressHref,
  buildExposurePair,
  E1RM_NOT_DECISIVE_NOTE,
  PAIRED_REPS_EFFORT_LABEL,
  buildRpeSeries,
  buildTrendSeries,
  comparabilityMismatch,
  decisionsForKey,
  e1rmReliabilityNote,
  isComparableWithCurrent,
  loadBetterWhen,
  progressionDecisionLabel,
  sessionEvolutionView,
  statusReasonsText,
} from './exercise-progress';
import { EMPTY_DASHBOARD_FILTERS } from './dashboard-filters';
import type { ComparabilityKey, Exposure, ProgressionDecision } from './dashboard-v3-types';
import type { LoadConvention } from './equipment-exercises-types';

const VERSIONS = { exposure: 'exposure-v1', e1rm: 'epley-rir-v1', tonnage: 'tonnage-v1' };

function key(
  equipmentId: number | null,
  convention: LoadConvention = 'total',
  equipmentAgnostic = false,
): ComparabilityKey {
  return {
    canonical_exercise_id: 10,
    equipment_id: equipmentId,
    load_convention: convention,
    equipment_agnostic: equipmentAgnostic,
  };
}

function exposure(overrides: Partial<Exposure> & { comparability_key: ComparabilityKey }): Exposure {
  return {
    activity_id: 1,
    local_date: '2026-09-01',
    started_at: null,
    equipment_id: overrides.comparability_key.equipment_id,
    equipment_name: 'Supino máquina',
    load_convention: overrides.comparability_key.load_convention,
    load_convention_source: 'equipment_exercises',
    position_in_session: 1,
    sets: [],
    total_reps: 24,
    best_load_kg: 60,
    avg_rpe: 8,
    e1rm: 75,
    tonnage: 1440,
    ...overrides,
  };
}

describe('exerciseProgressHref', () => {
  it('a janela padrão não polui a URL', () => {
    expect(exerciseProgressHref(42, EMPTY_DASHBOARD_FILTERS)).toBe(`${EXERCISE_PROGRESS_BASE_PATH}/42`);
  });

  it('preserva o filtro global do hub (janela personalizada)', () => {
    const href = exerciseProgressHref(42, {
      ...EMPTY_DASHBOARD_FILTERS,
      window: 'custom',
      dateFrom: '2026-01-01',
      dateTo: '2026-03-31',
    });
    expect(href).toContain('window=custom');
    expect(href).toContain('date_from=2026-01-01');
    expect(href).toContain('date_to=2026-03-31');
  });

  it('preserva o grupo muscular selecionado no drill-down de músculos', () => {
    expect(exerciseProgressHref(7, { ...EMPTY_DASHBOARD_FILTERS, window: '90d', muscleGroupId: 3 })).toBe(
      `${EXERCISE_PROGRESS_BASE_PATH}/7?window=90d&muscleGroupId=3`,
    );
  });
});

describe('statusReasonsText', () => {
  it('traduz cada código de comparability.REASON_* preservando a ordem', () => {
    const texts = statusReasonsText(['comparability_key_changed', 'equipment_not_resolved']);
    expect(texts.map((item) => item.code)).toEqual([
      'comparability_key_changed',
      'equipment_not_resolved',
    ]);
    expect(texts.every((item) => item.known)).toBe(true);
    expect(texts[0].text).toBe(STATUS_REASON_TEXTS.comparability_key_changed);
  });

  it('exibe o próprio código quando o backend emite uma razão que este frontend não conhece', () => {
    const [item] = statusReasonsText(['razao_do_futuro']);
    expect(item).toEqual({ code: 'razao_do_futuro', text: 'razao_do_futuro', known: false });
  });

  it('lista vazia para null/undefined (nunca quebra a tela)', () => {
    expect(statusReasonsText(null)).toEqual([]);
    expect(statusReasonsText(undefined)).toEqual([]);
  });

  it('a razão de dados insuficientes diz o que falta: duas exposições comparáveis', () => {
    expect(STATUS_REASON_TEXTS.not_enough_comparable_exposures).toContain('duas exposições comparáveis');
  });

  it('menos séries é aviso informativo, nunca regressão (progression-status-v2)', () => {
    const texts = statusReasonsText(['same_load_more_reps', 'fewer_working_sets']);
    expect(texts.every((item) => item.known)).toBe(true);
    expect(texts[1].text).toContain('menos séries');
    expect(texts[1].text).toContain('não conta como regressão');
    expect(texts[0].text).toContain('por série');
  });
});

describe('comparabilidade com a chave atual (pronta do backend, M76 Lote 3c)', () => {
  it('sem motivo do backend a exposição é comparável', () => {
    expect(comparabilityMismatch(exposure({ comparability_key: key(7) }))).toBeNull();
    expect(isComparableWithCurrent(exposure({ comparability_key: key(7) }))).toBe(true);
    expect(isComparableWithCurrent(exposure({ comparability_key: key(7), mismatch_with_current: null }))).toBe(true);
  });

  it('repassa o motivo calculado pelo backend, sem refazer keys_match', () => {
    for (const motivo of ['equipment', 'load_convention', 'equipment_unknown'] as const) {
      // A chave da exposição é IGUAL à atual: quem decide é o campo do backend.
      const item = exposure({ comparability_key: key(7), mismatch_with_current: motivo });
      expect(comparabilityMismatch(item)).toBe(motivo);
      expect(isComparableWithCurrent(item)).toBe(false);
    }
  });
});

describe('buildExposurePair', () => {
  it('devolve null sem exposições', () => {
    expect(buildExposurePair({ exposures: [], metric_versions: VERSIONS, data_quality: 'HIGH' })).toBeNull();
  });

  it('com uma única exposição não há par: variações ficam null, nunca 0', () => {
    const pair = buildExposurePair({
      exposures: [exposure({ comparability_key: key(7) })],
      metric_versions: VERSIONS,
      data_quality: 'INSUFFICIENT',
    });
    expect(pair?.previous).toBeNull();
    expect(pair?.comparable).toBe(false);
    for (const row of pair!.rows) {
      expect(row.previous.value).toBeNull();
      expect(row.deltaAbs).toBeNull();
      expect(row.deltaPct).toBeNull();
    }
  });

  it('calcula variação absoluta e percentual entre as duas últimas exposições comparáveis', () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(7), best_load_kg: 60, e1rm: 75, total_reps: 24 }),
        exposure({ comparability_key: key(7), best_load_kg: 66, e1rm: 82.5, total_reps: 24 }),
      ],
      metric_versions: VERSIONS,
      data_quality: 'MEDIUM',
    });
    expect(pair?.comparable).toBe(true);
    const carga = pair!.rows.find((row) => row.code === 'best_load_kg')!;
    expect(carga.previous.value).toBe(60);
    expect(carga.current.value).toBe(66);
    expect(carga.deltaAbs).toBe(6);
    expect(carga.deltaPct).toBeCloseTo(10, 6);
    expect(carga.betterWhen).toBe('up');
  });

  it('par não comparável não produz variação (comparar aparelhos diferentes seria conclusão errada)', () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(9), mismatch_with_current: 'equipment', best_load_kg: 60 }),
        exposure({ comparability_key: key(7), best_load_kg: 40 }),
      ],
      metric_versions: VERSIONS,
      data_quality: 'MEDIUM',
    });
    expect(pair?.comparable).toBe(false);
    expect(pair?.mismatch).toBe('equipment');
    expect(pair!.rows.every((row) => row.deltaAbs === null && row.deltaPct === null)).toBe(true);
    // Os valores dos dois lados continuam visíveis — o que some é a conclusão.
    expect(pair!.rows.find((row) => row.code === 'best_load_kg')!.previous.value).toBe(60);
  });

  it('e1RM ausente vira null no envelope (nunca 0) e carrega natureza ESTIMATED', () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(7), e1rm: null }),
        exposure({ comparability_key: key(7), e1rm: null }),
      ],
      metric_versions: VERSIONS,
      data_quality: 'MEDIUM',
    });
    const linha = pair!.rows.find((row) => row.code === 'e1rm')!;
    expect(linha.current.value).toBeNull();
    expect(linha.current.nature).toBe('ESTIMATED');
    expect(linha.current.metric_version).toBe('epley-rir-v1');
    expect(linha.deltaAbs).toBeNull();
  });

  it('RPE melhora quando cai; carga assistida também', () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(7, 'assisted') }),
        exposure({ comparability_key: key(7, 'assisted') }),
      ],
      metric_versions: VERSIONS,
      data_quality: 'HIGH',
    });
    expect(pair!.rows.find((row) => row.code === 'avg_rpe')!.betterWhen).toBe('down');
    expect(pair!.rows.find((row) => row.code === 'best_load_kg')!.betterWhen).toBe('down');
  });

  it('versão de métrica ausente no payload não quebra o envelope', () => {
    const pair = buildExposurePair({
      exposures: [exposure({ comparability_key: key(7) })],
      metric_versions: {},
      data_quality: 'HIGH',
    });
    expect(pair!.rows[0].current.metric_version).toBe('desconhecida');
  });
});

describe('buildExposurePair — reps emparelhadas e e1RM decisivo (regra única)', () => {
  const casoReal = [
    exposure({ comparability_key: key(7), best_load_kg: 40, e1rm: 54.7, total_reps: 31, e1rm_reps: 11, e1rm_reliable: true }),
    exposure({ comparability_key: key(7), best_load_kg: 40, e1rm: 59.3, total_reps: 27, e1rm_reps: 14, e1rm_reliable: false }),
  ];
  const latest = {
    comparable: true,
    paired_reps: { pairs: 2, previous: [11, 10], current: [14, 13], previous_sum: 21, current_sum: 27, delta: 6 },
    e1rm_decisive: false,
    e1rm_max_reliable_reps: 12,
  };

  it('compara reps POR SÉRIE emparelhada (não a soma de todas as séries)', () => {
    const pair = buildExposurePair({
      exposures: casoReal,
      metric_versions: VERSIONS,
      data_quality: 'HIGH',
      latest_comparison: latest,
    });
    const reps = pair!.rows.find((row) => row.code === 'paired_reps')!;
    expect(reps.previous.value).toBe(21);
    expect(reps.current.value).toBe(27);
    expect(reps.deltaAbs).toBe(6);
    expect(reps.betterWhen).toBe('up');
    expect(reps.previousDetail).toBe('11+10');
    expect(reps.currentDetail).toBe('14+13');

    // O total (31 -> 27) continua visível, mas neutro: menos séries não é piora.
    const total = pair!.rows.find((row) => row.code === 'total_reps')!;
    expect(total.deltaAbs).toBe(-4);
    expect(total.betterWhen).toBe('neutral');
    expect(total.label).toContain('informativo');
  });

  it('e1RM acima de 12 reps não é pintado de verde/vermelho', () => {
    const pair = buildExposurePair({
      exposures: casoReal,
      metric_versions: VERSIONS,
      data_quality: 'HIGH',
      latest_comparison: latest,
    });
    const e1rm = pair!.rows.find((row) => row.code === 'e1rm')!;
    expect(e1rm.betterWhen).toBe('neutral');
    expect(e1rm.note).toBe(E1RM_NOT_DECISIVE_NOTE);
  });

  it('e1RM decisivo continua com direção de melhora', () => {
    const pair = buildExposurePair({
      exposures: casoReal,
      metric_versions: VERSIONS,
      data_quality: 'HIGH',
      latest_comparison: { ...latest, e1rm_decisive: true },
    });
    const e1rm = pair!.rows.find((row) => row.code === 'e1rm')!;
    expect(e1rm.betterWhen).toBe('up');
    expect(e1rm.note).toBeNull();
  });

  it('backend antigo (sem latest_comparison): usa e1rm_reliable e deixa reps emparelhadas vazias', () => {
    const pair = buildExposurePair({ exposures: casoReal, metric_versions: VERSIONS, data_quality: 'HIGH' });
    expect(pair!.rows.find((row) => row.code === 'e1rm')!.betterWhen).toBe('neutral');
    const reps = pair!.rows.find((row) => row.code === 'paired_reps')!;
    expect(reps.current.value).toBeNull();
    expect(reps.deltaAbs).toBeNull();
  });

  it('reps + RIR (M78): rótulo próprio e detalhe com as reps registradas, sem conta no frontend', () => {
    const pair = buildExposurePair({
      exposures: casoReal,
      metric_versions: VERSIONS,
      data_quality: 'HIGH',
      latest_comparison: {
        ...latest,
        paired_reps: {
          pairs: 2,
          previous: [12, 11],
          current: [12, 11.5],
          previous_sum: 23,
          current_sum: 23.5,
          delta: 0.5,
          effort_adjusted: true,
          previous_reps: [12, 11],
          current_reps: [11, 11],
        },
      },
    });
    const reps = pair!.rows.find((row) => row.code === 'paired_reps')!;
    expect(reps.label).toBe(PAIRED_REPS_EFFORT_LABEL);
    expect(reps.previous.value).toBe(23);
    expect(reps.current.value).toBe(23.5);
    expect(reps.previousDetail).toBe('12+11 (reps 12+11)');
    expect(reps.currentDetail).toBe('12+11,5 (reps 11+11)');
  });

  it('sem ajuste pelo esforço mantém o rótulo de reps', () => {
    const pair = buildExposurePair({
      exposures: casoReal,
      metric_versions: VERSIONS,
      data_quality: 'HIGH',
      latest_comparison: latest,
    });
    expect(pair!.rows.find((row) => row.code === 'paired_reps')!.label).not.toBe(PAIRED_REPS_EFFORT_LABEL);
    expect(statusReasonsText(['paired_reps_effort_adjusted'])[0].known).toBe(true);
    expect(statusReasonsText(['same_reps_higher_rpe'])[0].known).toBe(true);
  });

  it('par não comparável ignora as reps emparelhadas', () => {
    const pair = buildExposurePair({
      exposures: [exposure({ comparability_key: key(9), mismatch_with_current: 'equipment' }), exposure({ comparability_key: key(7) })],
      metric_versions: VERSIONS,
      data_quality: 'HIGH',
      latest_comparison: latest,
    });
    expect(pair!.rows.find((row) => row.code === 'paired_reps')!.current.value).toBeNull();
  });
});

describe('loadBetterWhen', () => {
  it.each([
    ['total', 'up'],
    ['per_side', 'up'],
    ['assisted', 'down'],
    ['bodyweight', 'neutral'],
  ] as const)('%s -> %s', (convention, expected) => {
    expect(loadBetterWhen(convention)).toBe(expected);
  });
});

describe('buildTrendSeries', () => {
  it('descarta as exposições não comparáveis e conta quantas saíram', () => {
    const current = key(7);
    const series = buildTrendSeries(
      [
        exposure({ comparability_key: key(9), mismatch_with_current: 'equipment', e1rm: 50 }),
        exposure({ comparability_key: key(7), e1rm: 70, local_date: '2026-09-05' }),
        exposure({ comparability_key: key(7), e1rm: 75, local_date: '2026-09-12' }),
      ],
      current,
    );
    expect(series.metric).toBe('e1rm');
    expect(series.excluded).toBe(1);
    expect(series.points.map((point) => point.value)).toEqual([70, 75]);
    expect(series.points[0].label).toBe('05/09/2026');
  });

  it('cai para a carga quando o e1RM não existe em ao menos duas exposições', () => {
    const current = key(7);
    const series = buildTrendSeries(
      [
        exposure({ comparability_key: key(7), e1rm: null, best_load_kg: 40 }),
        exposure({ comparability_key: key(7), e1rm: 80, best_load_kg: 45 }),
      ],
      current,
    );
    expect(series.metric).toBe('best_load_kg');
    expect(series.unit).toBe('kg');
    expect(series.points.map((point) => point.value)).toEqual([40, 45]);
  });

  it('bodyweight compara só repetições', () => {
    const current = key(7, 'bodyweight');
    const series = buildTrendSeries(
      [
        exposure({ comparability_key: key(7, 'bodyweight'), e1rm: null, best_load_kg: null, total_reps: 20 }),
        exposure({ comparability_key: key(7, 'bodyweight'), e1rm: null, best_load_kg: null, total_reps: 26 }),
      ],
      current,
    );
    expect(series.metric).toBe('total_reps');
    expect(series.unit).toBeNull();
  });

  it('uma única exposição comparável não vira linha de tendência', () => {
    const current = key(7);
    const series = buildTrendSeries([exposure({ comparability_key: key(7) })], current);
    expect(series.metric).toBeNull();
    expect(series.points).toEqual([]);
  });

  it('histórico vazio devolve série vazia sem exclusões', () => {
    expect(buildTrendSeries([], null)).toEqual({
      metric: null,
      label: '',
      unit: null,
      points: [],
      excluded: 0,
    });
  });
});

describe('decisões do motor (evidência secundária)', () => {
  const decisions: ProgressionDecision[] = [
    {
      id: 1,
      kind: 'SESSION',
      decision: 'INCREASE_LOAD',
      phase_before: 'F1',
      phase_after: 'F2',
      engine_version: 'progression-v2.0.0',
      created_at: '2026-09-12T10:00:00',
      equipment_id: 7,
    },
    {
      id: 2,
      kind: 'SESSION',
      decision: 'HOLD',
      phase_before: 'F2',
      phase_after: 'F2',
      engine_version: 'progression-v2.0.0',
      created_at: '2026-09-05T10:00:00',
      equipment_id: 9,
    },
    {
      id: 3,
      kind: 'SESSION',
      decision: 'SKIP',
      phase_before: null,
      phase_after: null,
      engine_version: 'progression-v2.0.0',
      created_at: '2026-08-30T10:00:00',
      equipment_id: null,
    },
  ];

  it('mantém as da chave atual e as sem equipamento, descarta as de outra trilha', () => {
    expect(decisionsForKey(decisions, key(7)).map((item) => item.id)).toEqual([1, 3]);
  });

  it('chave sem equipamento resolvido mantém tudo', () => {
    expect(decisionsForKey(decisions, key(null, 'total', true))).toHaveLength(3);
    expect(decisionsForKey(decisions, null)).toHaveLength(3);
  });

  it('rotula a decisão em pt-BR e devolve o código quando é desconhecida', () => {
    expect(progressionDecisionLabel('INCREASE_LOAD')).toBe('Aumentar carga');
    expect(progressionDecisionLabel('ALGO_NOVO')).toBe('ALGO_NOVO');
    expect(progressionDecisionLabel(null)).toBe('—');
  });
});


// ---------------------------------------------------------------------------
// RPE médio por SESSÃO (F2.5 / M35)
// ---------------------------------------------------------------------------

describe('buildRpeSeries', () => {
  const atual = key(3);

  it('uma exposição com RPE não desenha linha — não há com o que comparar', () => {
    const serie = buildRpeSeries([exposure({ comparability_key: atual, avg_rpe: 8 })]);
    expect(serie.hasSeries).toBe(false);
    expect(serie.average).toBe(8);
  });

  it('a média usa só as sessões COM RPE, e sessão sem RPE é declarada', () => {
    // Sem isto, a sessão sem RPE viraria zero e derrubaria a média — o erro
    // clássico de tratar ausência como valor.
    const serie = buildRpeSeries(
      [
        exposure({ activity_id: 1, comparability_key: atual, avg_rpe: 8 }),
        exposure({ activity_id: 2, comparability_key: atual, avg_rpe: null }),
        exposure({ activity_id: 3, comparability_key: atual, avg_rpe: 9 }),
      ],
    );
    expect(serie.average).toBe(8.5);
    expect(serie.missing).toBe(1);
    expect(serie.hasSeries).toBe(true);
    // O ponto sem RPE continua no gráfico, com valor nulo — o buraco é visível.
    expect(serie.points.map((point) => point.value)).toEqual([8, null, 9]);
  });

  it('exposição de outro equipamento fica fora e é declarada', () => {
    const serie = buildRpeSeries(
      [
        exposure({ activity_id: 1, comparability_key: atual, avg_rpe: 8 }),
        exposure({ activity_id: 2, comparability_key: atual, avg_rpe: 9 }),
        exposure({ activity_id: 3, comparability_key: key(99), mismatch_with_current: 'equipment', avg_rpe: 5 }),
      ],
    );
    expect(serie.excluded).toBe(1);
    expect(serie.points).toHaveLength(2);
    expect(serie.average).toBe(8.5);
  });

  it('nenhuma sessão com RPE: média nula, nunca zero', () => {
    const serie = buildRpeSeries(
      [
        exposure({ activity_id: 1, comparability_key: atual, avg_rpe: null }),
        exposure({ activity_id: 2, comparability_key: atual, avg_rpe: null }),
      ],
    );
    expect(serie.average).toBeNull();
    expect(serie.hasSeries).toBe(false);
  });
});

describe('sessionEvolutionView (seta "Evolução" de /dashboard/progressao)', () => {
  it('só traduz o status que veio pronto do backend', () => {
    expect(sessionEvolutionView({ status: 'PROGRESSING', reasons: [] }).kind).toBe('up');
    expect(sessionEvolutionView({ status: 'REGRESSING', reasons: [] }).kind).toBe('down');
    expect(sessionEvolutionView({ status: 'STABLE', reasons: [] }).kind).toBe('flat');
    expect(sessionEvolutionView({ status: 'NOT_COMPARABLE', reasons: [] }).kind).toBe('not_comparable');
  });

  it('sem anterior comparável não desenha seta', () => {
    expect(sessionEvolutionView(null)).toEqual({ kind: 'none', label: '—', tooltip: '' });
    expect(sessionEvolutionView(undefined).kind).toBe('none');
    expect(sessionEvolutionView({ status: 'CODIGO_NOVO', reasons: [] }).kind).toBe('none');
  });

  it('cenário sintético do agachamento: progresso com aviso de menos séries no tooltip', () => {
    const view = sessionEvolutionView({
      status: 'PROGRESSING',
      reasons: ['same_load_more_reps', 'fewer_working_sets'],
    });
    expect(view.label).toBe('Progrediu');
    expect(view.tooltip).toContain(STATUS_REASON_TEXTS.same_load_more_reps);
    expect(view.tooltip).toContain(STATUS_REASON_TEXTS.fewer_working_sets);
  });

  it('troca de equipamento explica por que não compara', () => {
    const view = sessionEvolutionView({ status: 'NOT_COMPARABLE', reasons: ['comparability_key_changed'] });
    expect(view.tooltip).toBe(STATUS_REASON_TEXTS.comparability_key_changed);
  });
});

describe('e1rmReliabilityNote (M76 Lote 3d)', () => {
  const base = { value: 58.7, activity_id: 1, local_date: '2026-09-20' };

  it('e1RM não confiável vem marcado com o teto do payload', () => {
    expect(e1rmReliabilityNote({ ...base, reliable: false, max_reliable_reps: 12 })).toBe(
      'não confiável (>12 reps)',
    );
  });

  it('confiável, resposta antiga ou sem e1RM: sem marca', () => {
    expect(e1rmReliabilityNote({ ...base, reliable: true, max_reliable_reps: 12 })).toBeNull();
    expect(e1rmReliabilityNote(base)).toBeNull();
    expect(e1rmReliabilityNote(null)).toBeNull();
    expect(e1rmReliabilityNote({ ...base, value: null, reliable: false })).toBeNull();
  });

  it('sem o teto no payload não inventa o número', () => {
    expect(e1rmReliabilityNote({ ...base, reliable: false })).toBe('não confiável');
  });
});

describe('contexto de fadiga prévia (PR 2 da fadiga prévia)', () => {
  const pre = {
    band: 'pre_fatigued' as const,
    band_label: 'pré-fatigado',
    muscle_group_id: 1,
    muscle_group_name: 'Peitoral',
    prior_sets: 3,
  };

  it('o rótulo vem pronto do backend e acompanha o status', () => {
    expect(fatigueContextLabel(pre)).toBe('pré-fatigado');
    expect(fatigueContextLabel(null)).toBeNull();
    expect(statusWithContextLabel('Progresso', pre)).toBe('Progresso · pré-fatigado');
    expect(statusWithContextLabel('Progresso', null)).toBe('Progresso');
  });

  it('o motivo novo tem rótulo, dica e frase', () => {
    expect(COMPARABILITY_MISMATCH_LABELS.fatigue_context_changed).toBeTruthy();
    expect(COMPARABILITY_MISMATCH_HINTS.fatigue_context_changed).toBeTruthy();
    expect(STATUS_REASON_TEXTS.first_exposure_in_fatigue_context).toBeTruthy();
    expect(STATUS_REASON_TEXTS.other_fatigue_context_exposures_skipped).toBeTruthy();
  });

  it('o par anterior/atual pula exposições de outro contexto, mas não a troca de aparelho', () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(7), activity_id: 1, best_load_kg: 30 }),
        exposure({
          comparability_key: key(7),
          activity_id: 2,
          best_load_kg: 20,
          mismatch_with_current: 'fatigue_context_changed',
        }),
        exposure({ comparability_key: key(7), activity_id: 3, best_load_kg: 32 }),
      ],
      metric_versions: VERSIONS,
      data_quality: 'HIGH',
    });
    expect(pair?.previous?.activity_id).toBe(1);
    expect(pair?.comparable).toBe(true);

    const quebrado = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(7), activity_id: 1 }),
        exposure({ comparability_key: key(9), activity_id: 2, mismatch_with_current: 'equipment' }),
        exposure({ comparability_key: key(7), activity_id: 3 }),
      ],
      metric_versions: VERSIONS,
      data_quality: 'HIGH',
    });
    expect(quebrado?.previous?.activity_id).toBe(2);
    expect(quebrado?.comparable).toBe(false);
  });
});
