import { describe, expect, it } from 'vitest';
import {
  adjustmentLabel,
  buildBriefingSections,
  firstStatement,
  musclesSummary,
  formatExpectedLoad,
  formatExpectedRpe,
  formatStrengthLoadRange,
  formatLocalDate,
  formatSetLine,
  formatSetsAndReps,
  groupMusclesByRole,
  parseExecutionDescription,
  adjustmentText,
  performanceBadge,
  performanceExplanation,
  summarizeLastExecution,
} from './exercise-briefing';
import type {
  BriefingLastExecution,
  BriefingPerformance,
  BriefingPrescription,
} from './exercise-briefing-types';

const PRESCRICAO: BriefingPrescription = {
  sets_count: 3,
  reps_target: '10-12',
  load_target: '40',
  suggested_load_kg: 42.5,
  rpe_target: '9',
  effective_rpe_target: 8,
  effective_rpe_ceiling: 9,
  failure_allowed: false,
  advanced_technique: null,
  advanced_technique_applications: null,
  rest_seconds: 90,
  status: 'FROZEN',
  warnings: [],
};

describe('formatExpectedLoad', () => {
  it('prefere a carga sugerida pelo motor ao texto livre da ficha', () => {
    expect(formatExpectedLoad(PRESCRICAO)).toBe('42,5 kg');
  });

  it('cai para o load_target quando não há sugestão numérica', () => {
    expect(formatExpectedLoad({ suggested_load_kg: null, load_target: '40' })).toBe('40 kg');
  });

  it('devolve texto livre como está, sem inventar unidade', () => {
    expect(formatExpectedLoad({ suggested_load_kg: null, load_target: 'peso corporal' })).toBe('peso corporal');
    expect(formatExpectedLoad({ suggested_load_kg: null, load_target: '2 x 12 kg' })).toBe('2 x 12 kg');
  });

  it('sem carga nenhuma mostra um traço em vez de quebrar', () => {
    expect(formatExpectedLoad({ suggested_load_kg: null, load_target: null })).toBe('—');
    expect(formatExpectedLoad({ suggested_load_kg: 0, load_target: '' })).toBe('—');
  });

  it('formata com vírgula decimal e sem zeros arrastados', () => {
    expect(formatExpectedLoad({ suggested_load_kg: 40, load_target: null })).toBe('40 kg');
    expect(formatExpectedLoad({ suggested_load_kg: 42.5, load_target: null })).toBe('42,5 kg');
  });
});

describe('formatSetsAndReps / formatExpectedRpe', () => {
  it('monta séries x repetições', () => {
    expect(formatSetsAndReps(PRESCRICAO)).toBe('3 x 10-12');
    expect(formatSetsAndReps({ sets_count: 1, reps_target: null })).toBe('1 série');
    expect(formatSetsAndReps({ sets_count: null, reps_target: null })).toBe('—');
  });

  it('mostra o RPE efetivo e o teto quando o overlay de segurança baixou o alvo', () => {
    expect(formatExpectedRpe(PRESCRICAO)).toBe('RPE 8 (teto 9)');
  });

  it('sem teto diferente, mostra só o alvo', () => {
    expect(formatExpectedRpe({ ...PRESCRICAO, effective_rpe_ceiling: 8 })).toBe('RPE 8');
  });

  it('cai para o rpe_target congelado quando não há alvo efetivo', () => {
    expect(formatExpectedRpe({ ...PRESCRICAO, effective_rpe_target: null, effective_rpe_ceiling: null })).toBe('RPE 9');
  });

  it('sem RPE nenhum não quebra', () => {
    expect(
      formatExpectedRpe({ ...PRESCRICAO, rpe_target: null, effective_rpe_target: null, effective_rpe_ceiling: null }),
    ).toBe('—');
  });
});

const DESEMPENHO: BriefingPerformance = {
  status: 'PROGRESSING',
  reasons: ['same_load_more_reps'],
  comparable: true,
  scope: 'equipment',
  comparable_exposures: 2,
  equipment_name: 'Banco supino',
  load_convention: 'total',
  last_local_date: '2026-10-01',
};

describe('performanceBadge', () => {
  it('usa o rótulo e a cor do dashboard (fonte única)', () => {
    expect(performanceBadge(DESEMPENHO)).toMatchObject({ label: 'Progredindo', color: 'green' });
    expect(performanceBadge({ ...DESEMPENHO, status: 'REGRESSING' })).toMatchObject({ label: 'Regredindo', color: 'red' });
    expect(performanceBadge({ ...DESEMPENHO, status: 'STABLE' })).toMatchObject({ label: 'Estável' });
  });

  it('sem comparação (ou resposta antiga) não inventa veredito', () => {
    expect(performanceBadge({ ...DESEMPENHO, status: 'INSUFFICIENT_DATA', comparable: false })).toEqual({
      label: 'Sem comparação',
      color: 'gray',
    });
    expect(performanceBadge({ ...DESEMPENHO, status: 'NOT_COMPARABLE', comparable: false }).label).toBe(
      'Sem comparação',
    );
    expect(performanceBadge(undefined).label).toBe('Sem comparação');
  });
});

describe('performanceExplanation', () => {
  it('diz onde comparou e por quê', () => {
    const texto = performanceExplanation(DESEMPENHO);
    expect(texto).toContain('neste equipamento (Banco supino)');
    expect(texto).toContain('mais repetições por série');
  });

  it('sem comparação explica a falta', () => {
    expect(performanceExplanation(null)).toContain('duas execuções comparáveis');
  });
});

describe('adjustmentText', () => {
  it('a decisão do motor aparece como ação, nunca como Progredindo/Regredindo', () => {
    const texto = adjustmentText({ decision: 'DECREASE_LOAD', label: 'Reduzir carga', reason: null, source: 'engine_decision' });
    expect(texto).toBe('Ajuste de hoje: Reduzir carga');
    expect(texto).not.toMatch(/Progredindo|Regredindo|Mantendo/);
    expect(
      adjustmentText({ decision: 'INCREASE_LOAD', label: 'Aumentar carga', reason: null, source: 'engine_decision' }),
    ).toBe('Ajuste de hoje: Aumentar carga');
  });

  it('decisão desconhecida usa o rótulo do backend; sem decisão, nada', () => {
    expect(adjustmentText({ decision: 'NOVA_ACAO', label: 'Nova ação', reason: null, source: 'engine_decision' })).toBe(
      'Ajuste de hoje: Nova ação',
    );
    expect(adjustmentText(null)).toBeNull();
    expect(adjustmentText(undefined)).toBeNull();
  });
});

describe('groupMusclesByRole', () => {
  it('agrupa na ordem primário, secundário, estabilizador e some com grupo vazio', () => {
    const grupos = groupMusclesByRole([
      { muscle_group_id: 2, name: 'Tríceps', role: 'SECONDARY', stimulus_weight: 0.5 },
      { muscle_group_id: 1, name: 'Peitoral', role: 'PRIMARY', stimulus_weight: 1 },
    ]);
    expect(grupos.map((g) => g.label)).toEqual(['Primário', 'Secundário']);
    expect(grupos[0].names).toEqual(['Peitoral']);
  });

  it('exercício sem músculos mapeados devolve lista vazia', () => {
    expect(groupMusclesByRole([])).toEqual([]);
  });
});

describe('parseExecutionDescription', () => {
  it('quebra o texto persistido em blocos com título e passos', () => {
    const blocos = parseExecutionDescription(
      'Preparação:\n- Deite no banco.\n\nExecução:\n- Desça a barra.\n- Empurre.',
    );
    expect(blocos).toEqual([
      { title: 'Preparação', steps: ['Deite no banco.'] },
      { title: 'Execução', steps: ['Desça a barra.', 'Empurre.'] },
    ]);
  });

  it('texto sem rótulo vira um bloco sem título', () => {
    expect(parseExecutionDescription('- Empurre a barra.')).toEqual([{ title: null, steps: ['Empurre a barra.'] }]);
  });

  it('descrição ainda não gerada vira lista vazia (a tela omite a seção)', () => {
    expect(parseExecutionDescription(null)).toEqual([]);
    expect(parseExecutionDescription('')).toEqual([]);
  });
});

describe('última execução', () => {
  const ULTIMA: BriefingLastExecution = {
    activity_id: 9,
    local_date: '2026-09-08',
    equipment_name: null,
    sets: [
      { set_number: 1, set_type: 'warmup', reps: 15, weight_kg: 20, rpe: null, is_working: false },
      { set_number: 2, set_type: 'working', reps: 10, weight_kg: 40, rpe: 9, is_working: true },
    ],
    working_sets: 3,
    top_weight_kg: 40,
    reps_min: 8,
    reps_max: 10,
    top_rpe: 9,
  };

  it('resume séries, carga, faixa de reps e RPE', () => {
    expect(summarizeLastExecution(ULTIMA)).toBe('3 séries · 40 kg · 8-10 reps · RPE 9');
  });

  it('reps iguais não viram faixa', () => {
    expect(summarizeLastExecution({ ...ULTIMA, reps_min: 10, reps_max: 10 })).toBe('3 séries · 40 kg · 10 reps · RPE 9');
  });

  it('sem execução anterior devolve null (a tela mostra "primeira vez")', () => {
    expect(summarizeLastExecution(null)).toBeNull();
  });

  it('série a série, marcando o que não foi série de trabalho', () => {
    expect(formatSetLine(ULTIMA.sets[0])).toBe('1ª [warmup]: 15 x 20 kg');
    expect(formatSetLine(ULTIMA.sets[1])).toBe('2ª: 10 x 40 kg · RPE 9');
  });

  it('série sem reps/carga registradas não quebra a linha', () => {
    expect(formatSetLine({ set_number: 3, set_type: 'working', reps: null, weight_kg: null, rpe: null, is_working: true })).toBe(
      '3ª: — x —',
    );
  });

  it('formata a data local no padrão brasileiro', () => {
    expect(formatLocalDate('2026-09-08')).toBe('08/09/2026');
    expect(formatLocalDate(null)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Novo visual do briefing (Modo Treino PR1/3)
// ---------------------------------------------------------------------------

describe('carga em lb no briefing (weight-units)', () => {
  it('sugestão do motor convertida pela borda única (fromKg), sem mudar o kg', () => {
    expect(formatExpectedLoad(PRESCRICAO, 'lb')).toBe('93,7 lb');
    expect(formatExpectedLoad(PRESCRICAO, 'kg')).toBe('42,5 kg');
  });

  it('load_target numérico (com ou sem unidade) é convertido; texto livre volta como veio', () => {
    expect(formatExpectedLoad({ suggested_load_kg: null, load_target: '40' }, 'lb')).toBe('88,2 lb');
    expect(formatExpectedLoad({ suggested_load_kg: null, load_target: '40kg' }, 'lb')).toBe('88,2 lb');
    expect(formatExpectedLoad({ suggested_load_kg: null, load_target: '50 lb' }, 'lb')).toBe('50 lb');
    expect(formatExpectedLoad({ suggested_load_kg: null, load_target: '40-45' }, 'lb')).toBe('40-45');
    expect(formatExpectedLoad({ suggested_load_kg: null, load_target: 'peso corporal' }, 'lb')).toBe('peso corporal');
    expect(formatExpectedLoad({ suggested_load_kg: null, load_target: null }, 'lb')).toBe('—');
  });

  it('última execução e linhas de série também respeitam a unidade', () => {
    const ultima: BriefingLastExecution = {
      activity_id: 9,
      local_date: '2026-09-08',
      equipment_name: null,
      sets: [{ set_number: 1, set_type: 'working', reps: 10, weight_kg: 40, rpe: 9, is_working: true }],
      working_sets: 1,
      top_weight_kg: 40,
      reps_min: 10,
      reps_max: 10,
      top_rpe: 9,
    };
    expect(summarizeLastExecution(ultima, 'lb')).toBe('1 série · 88,2 lb · 10 reps · RPE 9');
    expect(formatSetLine(ultima.sets[0], 'lb')).toBe('1ª: 10 x 88,2 lb · RPE 9');
    expect(summarizeLastExecution(ultima)).toBe('1 série · 40 kg · 10 reps · RPE 9');
  });
});

describe('adjustmentLabel', () => {
  it('HOLD aparece como "Manter" (rótulo gerado do motor), sem o prefixo', () => {
    expect(adjustmentLabel({ decision: 'HOLD', label: 'HOLD', reason: null, source: 'engine_decision' })).toBe('Manter');
    expect(adjustmentLabel(null)).toBeNull();
  });
});

describe('buildBriefingSections / firstStatement', () => {
  const TEXTO = [
    'Preparação:',
    '- Suba na estação, encoste as costas e ajuste a pegada. Mantenha os ombros baixos.',
    '- Pés fora do chão.',
    'Execução:',
    '- Eleve os joelhos até a linha do quadril.',
    'Atenção:',
    '- Evite impulso! Não balance o tronco.',
    '- Mantenha o pescoço neutro.',
  ].join('\n');

  it('separa Preparação, Execução e Atenção com resumo = 1º enunciado do próprio texto', () => {
    const sections = buildBriefingSections(parseExecutionDescription(TEXTO));
    expect(sections.map((item) => item.key)).toEqual(['preparation', 'execution', 'attention']);
    expect(sections[0].summary).toBe('Suba na estação, encoste as costas e ajuste a pegada.');
    expect(sections[0].steps).toHaveLength(2);
    // O texto íntegro continua nos passos (nada é reescrito).
    expect(sections[0].steps[0]).toBe('Suba na estação, encoste as costas e ajuste a pegada. Mantenha os ombros baixos.');
    expect(sections[2].summary).toBe('Evite impulso!');
    expect(sections[2].steps).toEqual(['Evite impulso! Não balance o tronco.', 'Mantenha o pescoço neutro.']);
  });

  it('ordem fixa mesmo com o texto fora de ordem; passos sem título vão para Execução', () => {
    const sections = buildBriefingSections(
      parseExecutionDescription('- Passo solto\nAtenção:\n- Cuidado\nPreparação:\n- Ajuste o banco'),
    );
    expect(sections.map((item) => [item.key, item.summary])).toEqual([
      ['preparation', 'Ajuste o banco'],
      ['execution', 'Passo solto'],
      ['attention', 'Cuidado'],
    ]);
  });

  it('título desconhecido vira seção própria; título sem passos não aparece', () => {
    const sections = buildBriefingSections(parseExecutionDescription('Respiração:\n- Expire ao subir\nExecução:'));
    expect(sections).toEqual([
      { key: 'other', title: 'Respiração', summary: 'Expire ao subir', steps: ['Expire ao subir'] },
    ]);
    expect(buildBriefingSections(parseExecutionDescription(null))).toEqual([]);
  });

  it('firstStatement é prefixo exato e não corta abreviação sem espaço depois', () => {
    expect(firstStatement('Frase única')).toBe('Frase única');
    expect(firstStatement('Use 2.5 kg. Depois suba.')).toBe('Use 2.5 kg.');
    expect(firstStatement('')).toBe('');
  });

  it('musclesSummary resume os papéis numa linha', () => {
    expect(
      musclesSummary([
        { label: 'Primário', names: ['Abdômen'] },
        { label: 'Secundário', names: ['Flexores do quadril', 'Oblíquos'] },
      ]),
    ).toBe('Primário: Abdômen · Secundário: Flexores do quadril, Oblíquos');
  });
});

// Fix M97: faixa de carga de força por %1RM. O objeto é o que o backend congela
// (`progression_engine.E1rmLoadRange.to_dict`, e1RM 123,33 kg no passo de 2,5 kg);
// a tela só formata.
describe('formatStrengthLoadRange', () => {
  const FAIXA = {
    e1rm_kg: 123.33,
    pct_min: 80,
    pct_max: 85,
    load_min_kg: 100,
    load_max_kg: 102.5,
    suggested_kg: 100,
    step_kg: 2.5,
  };

  it('mostra a faixa e os percentuais vindos do backend', () => {
    expect(formatStrengthLoadRange(FAIXA)).toBe('100–102,5 kg (80–85% 1RM)');
  });

  it('faixa de um valor só mostra uma carga', () => {
    expect(formatStrengthLoadRange({ ...FAIXA, load_max_kg: 100 })).toBe('100 kg (80–85% 1RM)');
  });

  it('converte a carga para libras sem mexer nos percentuais', () => {
    const texto = formatStrengthLoadRange(FAIXA, 'lb') ?? '';
    expect(texto).not.toContain('kg');
    expect(texto.endsWith(' lb (80–85% 1RM)')).toBe(true);
  });

  it('sem faixa (hipertrofia ou força sem e1RM) não mostra nada', () => {
    expect(formatStrengthLoadRange(null)).toBeNull();
    expect(formatStrengthLoadRange(undefined)).toBeNull();
  });
});
