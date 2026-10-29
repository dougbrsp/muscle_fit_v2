// Testes da lógica pura do Motor de Periodização no frontend (PR-17).
//
// O que é coberto aqui é exatamente o que não dá para verificar clicando sem
// chave da OpenAI: a montagem do pedido, a leitura do campo `error` de um job
// `failed` (que é JSON quando a falha é estrutural) e o mapeamento do rascunho
// para o formato que o builder hidrata.
//
// Os payloads usados são os do backend já mergeado (PR-16): `PeriodizationDraftIn`,
// `PeriodizationError.detail` e `run_periodization_draft`.

import { describe, expect, it } from 'vitest';
import {
  PERIODIZATION_DRAFT_STORAGE_KEY,
  PERIODIZATION_GENERIC_FAILURE,
  PERIODIZATION_INSTRUCTIONS_MAX_LENGTH,
  buildPeriodizationRequest,
  clearPeriodizationSeed,
  countPeriodizationReport,
  describePeriodizationEntry,
  hasPeriodizationOriginFlag,
  newPeriodizationForm,
  parsePeriodizationJobError,
  periodizationJobResultToSeed,
  periodizationOriginQuery,
  readPeriodizationSeed,
  storePeriodizationSeed,
  validatePeriodizationForm,
} from './periodization';
import { PERIODIZATION_REPORT_CODE_LABEL } from './periodization-types';
import type { PeriodizationFormState, PeriodizationJobResult } from './periodization-types';

function form(overrides: Partial<PeriodizationFormState> = {}): PeriodizationFormState {
  return { ...newPeriodizationForm(), ...overrides };
}

function fakeStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
    size: () => data.size,
  };
}

// --- Montagem do pedido -----------------------------------------------------

describe('buildPeriodizationRequest', () => {
  it('monta o payload do §7.2 convertendo ids de string para número', () => {
    expect(
      buildPeriodizationRequest(
        form({
          ragSources: ['2. ABC'],
          trainingTypeId: '4',
          volumeMode: 'percent_budget',
          gymId: null,
          daysPerCycle: 4,
          instructions: 'Ênfase em costas, joelho sensível.',
        }),
      ),
    ).toEqual({
      rag_sources: ['2. ABC'],
      training_type_id: 4,
      volume_mode: 'percent_budget',
      gym_id: null,
      days_per_cycle: 4,
      instructions: 'Ênfase em costas, joelho sensível.',
    });
  });

  it('manda instruções em branco como null, não como string vazia', () => {
    expect(buildPeriodizationRequest(form({ instructions: '   \n ' })).instructions).toBeNull();
  });

  it('trunca instruções no teto de 2.000 caracteres', () => {
    const request = buildPeriodizationRequest(form({ instructions: 'a'.repeat(2500) }));
    expect(request.instructions).toHaveLength(PERIODIZATION_INSTRUCTIONS_MAX_LENGTH);
  });

  it('envia a academia escolhida também no Modo A (opcional, não descartada)', () => {
    expect(buildPeriodizationRequest(form({ gymId: '9' })).gym_id).toBe(9);
  });

  it('não compartilha o array de fontes com o estado do formulário', () => {
    const state = form({ ragSources: ['1. Full Body'] });
    const request = buildPeriodizationRequest(state);
    request.rag_sources.push('outra');
    expect(state.ragSources).toEqual(['1. Full Body']);
  });
});

describe('validatePeriodizationForm', () => {
  it('exige academia no Modo B (mesma regra de _validate_periodization_request)', () => {
    expect(validatePeriodizationForm(form({ volumeMode: 'equipment_series' }))).toMatch(/exige uma academia/);
    expect(validatePeriodizationForm(form({ volumeMode: 'equipment_series', gymId: '3' }))).toBeNull();
  });

  it('aceita o Modo A sem academia', () => {
    expect(validatePeriodizationForm(form())).toBeNull();
  });

  it('recusa mais de 10 fontes de RAG', () => {
    const sources = Array.from({ length: 11 }, (_, i) => `fonte ${i}`);
    expect(validatePeriodizationForm(form({ ragSources: sources }))).toMatch(/no máximo 10/);
  });

  it('recusa dias fora da faixa 1..8', () => {
    expect(validatePeriodizationForm(form({ daysPerCycle: 0 }))).toMatch(/entre 1 e 8/);
    expect(validatePeriodizationForm(form({ daysPerCycle: 9 }))).toMatch(/entre 1 e 8/);
    expect(validatePeriodizationForm(form({ daysPerCycle: 3.5 }))).toMatch(/entre 1 e 8/);
    expect(validatePeriodizationForm(form({ daysPerCycle: 8 }))).toBeNull();
  });

  it('recusa instruções acima do teto', () => {
    expect(validatePeriodizationForm(form({ instructions: 'a'.repeat(2001) }))).toMatch(/2000 caracteres/);
  });
});

// --- Falha do job -----------------------------------------------------------

describe('parsePeriodizationJobError', () => {
  it('lê o relatório do validador quando a falha é estrutural (job.error em JSON)', () => {
    const raw = JSON.stringify({
      message: 'O rascunho gerado nao passou na validacao deterministica.',
      validation_report: {
        errors: [{ code: 'MUSCLE_GROUP_NOT_FOUND', muscle_group_id: 99 }],
        warnings: [{ code: 'SLOT_COUNT_MISMATCH', expected: 4, received: 3 }],
        auto_fixes: [],
      },
      generation_log_id: 55,
      model: 'gpt-5.4-mini',
      prompt_version: 'periodization-v1',
    });
    const failure = parsePeriodizationJobError(raw);
    expect(failure.message).toBe('O rascunho gerado nao passou na validacao deterministica.');
    expect(failure.validationReport?.errors).toEqual([{ code: 'MUSCLE_GROUP_NOT_FOUND', muscle_group_id: 99 }]);
    expect(failure.validationReport?.warnings).toHaveLength(1);
    expect(failure.generationLogId).toBe(55);
    expect(failure.model).toBe('gpt-5.4-mini');
    expect(failure.promptVersion).toBe('periodization-v1');
  });

  it('usa o texto como mensagem quando a falha é de configuração (não é JSON)', () => {
    const raw =
      'OPENAI_MODEL_PERIODIZATION nao configurada no backend: a geracao automatica de modelo esta indisponivel (nenhum dado foi alterado).';
    const failure = parsePeriodizationJobError(raw);
    expect(failure.message).toBe(raw);
    expect(failure.validationReport).toBeNull();
  });

  it('cai numa mensagem genérica quando não há texto nenhum', () => {
    expect(parsePeriodizationJobError(null).message).toBe(PERIODIZATION_GENERIC_FAILURE);
    expect(parsePeriodizationJobError('   ').message).toBe(PERIODIZATION_GENERIC_FAILURE);
    expect(parsePeriodizationJobError(undefined).validationReport).toBeNull();
  });

  it('não confunde JSON que não é objeto com relatório', () => {
    expect(parsePeriodizationJobError('42').message).toBe('42');
    expect(parsePeriodizationJobError('[1,2]').message).toBe('[1,2]');
    expect(parsePeriodizationJobError('[1,2]').validationReport).toBeNull();
  });

  it('tolera relatório com campos ausentes ou de tipo errado', () => {
    const failure = parsePeriodizationJobError(
      JSON.stringify({ message: 'falhou', validation_report: { errors: 'nao-e-array' } }),
    );
    expect(failure.validationReport).toEqual({ errors: [], warnings: [], auto_fixes: [] });
    expect(failure.generationLogId).toBeNull();
  });
});

describe('describePeriodizationEntry', () => {
  it('cardio (M98 C5): mostra o texto pronto do backend, com fallback', () => {
    const message = 'Dia 1 (Peito): cardio — HIIT vetado, trocado por Normal (véspera de perna)';
    expect(describePeriodizationEntry({ code: 'CARDIO_ADJUSTED', slot: 1, message })).toBe(message);
    expect(describePeriodizationEntry({ code: 'CARDIO_REMOVED', slot: 4 })).toBe(
      'Cardio do dia 4 ajustado pelo sistema.',
    );
  });

  it('traduz os códigos do validador para português', () => {
    expect(describePeriodizationEntry({ code: 'GYM_REQUIRED' })).toMatch(/nenhuma academia/);
    expect(
      describePeriodizationEntry({
        code: 'ACTIVATION_BELOW_MIN',
        muscle_group_name: 'Costas',
        projected: 6.5,
        min: 8,
        window_days: 7,
      }),
    ).toBe('Costas: projeção de 6.5 séries na janela de 7 dias, abaixo do mínimo de 8.');
    expect(describePeriodizationEntry({ code: 'RPE_CLAMPED', slot: 2, item: 3, from: '10', to: '9' })).toMatch(
      /de 10 para 9 \(dia 2, item 3\)/,
    );
  });

  it('não inventa texto para código desconhecido', () => {
    expect(describePeriodizationEntry({ code: 'CODIGO_NOVO', x: 1 })).toBe('{"code":"CODIGO_NOVO","x":1}');
  });

  it('descreve as faixas da metodologia por grupo com macro-grupo, frequência e evidência', () => {
    expect(
      describePeriodizationEntry({
        code: 'METHODOLOGY_BELOW_RANGE',
        muscle_group_id: 52,
        muscle_group_name: 'Peitoral',
        volume_class_id: 1,
        volume_class_name: 'Grandes',
        // Deprecated (Fase 6): ecoado pela API, ignorado pelo texto.
        size_class: 'small',
        frequency_bucket: '1x',
        frequency: 1,
        projected_sets: 9,
        min: 15,
        max: 20,
        evidence: 'EXG-015',
      }),
    ).toBe('Peitoral (macro-grupo Grandes, 1x/semana): 9 séries diretas por sessão, abaixo do mínimo de 15 (EXG-015).');
    expect(
      describePeriodizationEntry({
        code: 'METHODOLOGY_ABOVE_RANGE',
        muscle_group_name: 'Bíceps',
        volume_class_name: 'Pequenos',
        frequency_bucket: '3x_plus',
        projected_sets: 10.5,
        min: 3,
        max: 8,
        evidence: 'derived (proposta D2): celula pequenos 2x menos 1 exercicio por sessao',
      }),
    ).toBe(
      'Bíceps (macro-grupo Pequenos, 3x ou mais por semana): 10,5 séries diretas por sessão, acima do máximo de 8 (faixa derivada da metodologia).',
    );
    // Macro-grupo/frequência desconhecidos não viram texto inventado.
    expect(
      describePeriodizationEntry({ code: 'METHODOLOGY_BELOW_RANGE', muscle_group_id: 7, projected_sets: 2, min: 6 }),
    ).toBe('grupo #7 (sem macro-grupo, frequência não avaliada): 2 séries diretas por sessão, abaixo do mínimo de 6.');
  });

  it('descreve o eixo semanal do macro-grupo (Fase 6) em vez de cair no JSON', () => {
    expect(
      describePeriodizationEntry({
        code: 'METHODOLOGY_WEEKLY_BELOW_RANGE',
        muscle_group_name: 'Costas',
        volume_class_name: 'Grandes',
        frequency: 2,
        weekly_sets: 8,
        min: 12.5,
        max: 20,
        evidence: null,
      }),
    ).toBe('Costas (macro-grupo Grandes): 8 séries diretas por semana, abaixo do mínimo de 12,5 da meta semanal.');
    expect(
      describePeriodizationEntry({
        code: 'METHODOLOGY_WEEKLY_ABOVE_RANGE',
        muscle_group_name: 'Bíceps',
        volume_class_name: 'Pequenos',
        weekly_sets: 14.5,
        min: 6,
        max: 12,
        evidence: 'EXG-017',
      }),
    ).toBe('Bíceps (macro-grupo Pequenos): 14,5 séries diretas por semana, acima do máximo de 12 da meta semanal (EXG-017).');
  });

  it('descreve a sessão com poucos exercícios', () => {
    expect(
      describePeriodizationEntry({ code: 'SLOT_TOO_FEW_EXERCISES', slot: 2, slot_name: 'Costas', items: 3, min: 6 }),
    ).toBe('Dia 2 (Costas) tem 3 exercício(s); uma sessão completa tem pelo menos 6.');
    expect(describePeriodizationEntry({ code: 'SLOT_TOO_FEW_EXERCISES', slot: 1, items: 0, min: 6 })).toBe(
      'Dia 1 tem 0 exercício(s); uma sessão completa tem pelo menos 6.',
    );
  });
});

describe('describePeriodizationEntry — auto-fixes estruturais', () => {
  it('descreve a troca de equipamento pelo par da academia', () => {
    expect(
      describePeriodizationEntry({
        code: 'EQUIPMENT_REPLACED',
        slot: 3,
        item: 2,
        from: { mapping_id: 12, equipment_id: 32 },
        to: { mapping_id: 12, equipment_id: 41 },
        exercise_name: 'Supino reto',
        equipment_name: 'Halteres',
      }),
    ).toBe(
      'Exercício 12 / equipamento 32 não existe nesta academia (dia 3, item 2): trocado por 12 / 41 (Supino reto / Halteres), do mesmo grupo muscular. Confira antes de salvar.',
    );
  });

  it('descreve os consertos de estrutura do ciclo', () => {
    expect(describePeriodizationEntry({ code: 'SLOT_REMOVED', slot: 9, slot_name: 'Descanso', expected: 8 })).toBe(
      'Dia 9 (Descanso) foi removido: o rascunho veio com mais dias do que os 8 pedidos.',
    );
    expect(describePeriodizationEntry({ code: 'SLOT_ADDED', slot: 8, expected: 8 })).toBe(
      'Dia 8 foi acrescentado como descanso para fechar os 8 dias pedidos.',
    );
    expect(describePeriodizationEntry({ code: 'SLOT_SEQUENCE_RENUMBERED', from: 5, to: 3 })).toBe(
      'Ordem dos dias corrigida: o dia 5 passou a ser o 3.',
    );
    expect(describePeriodizationEntry({ code: 'SLOT_TYPE_FIXED', slot: 4 })).toBe(
      'Dia 4 veio marcado como descanso mas tinha exercícios: virou dia de treino.',
    );
    expect(describePeriodizationEntry({ code: 'PRIORITIZE_UNMARKED', slot: 2, item: 5, received: 5, max: 3 })).toBe(
      'Prioridade desmarcada (dia 2, item 5): o dia tinha 5 itens priorizados e o máximo é 3.',
    );
    expect(describePeriodizationEntry({ code: 'SETS_COUNT_FIXED', slot: 1, item: 4, from: null, to: 3 })).toBe(
      'Séries ajustadas de vazio para 3 (dia 1, item 4): todo exercício precisa de pelo menos uma série.',
    );
    expect(
      describePeriodizationEntry({ code: 'ITEM_REMOVED', slot: 5, item: 2, reason: 'trio incompleto' }),
    ).toBe('Item removido (dia 5, item 2): trio incompleto. O resto do dia foi mantido.');
  });
});

describe('PERIODIZATION_REPORT_CODE_LABEL', () => {
  it('tem rótulo em português para os códigos da metodologia', () => {
    expect(PERIODIZATION_REPORT_CODE_LABEL.METHODOLOGY_BELOW_RANGE).toBe(
      'Séries diretas abaixo da faixa da metodologia',
    );
    expect(PERIODIZATION_REPORT_CODE_LABEL.METHODOLOGY_ABOVE_RANGE).toBe(
      'Séries diretas acima da faixa da metodologia',
    );
    expect(PERIODIZATION_REPORT_CODE_LABEL.METHODOLOGY_WEEKLY_BELOW_RANGE).toBe(
      'Séries diretas por semana abaixo da meta do macro-grupo',
    );
    expect(PERIODIZATION_REPORT_CODE_LABEL.METHODOLOGY_WEEKLY_ABOVE_RANGE).toBe(
      'Séries diretas por semana acima da meta do macro-grupo',
    );
    expect(PERIODIZATION_REPORT_CODE_LABEL.SLOT_TOO_FEW_EXERCISES).toBe('Sessão com poucos exercícios');
  });

  it('tem rótulo em português para os auto-fixes estruturais', () => {
    expect(PERIODIZATION_REPORT_CODE_LABEL.EQUIPMENT_REPLACED).toBe('Equipamento trocado por um da academia');
    expect(PERIODIZATION_REPORT_CODE_LABEL.SLOT_REMOVED).toBe('Dia extra removido do ciclo');
    expect(PERIODIZATION_REPORT_CODE_LABEL.SLOT_ADDED).toBe('Dia de descanso acrescentado ao ciclo');
    expect(PERIODIZATION_REPORT_CODE_LABEL.SLOT_SEQUENCE_RENUMBERED).toBe('Ordem dos dias renumerada');
    expect(PERIODIZATION_REPORT_CODE_LABEL.SLOT_TYPE_FIXED).toBe('Descanso com exercícios virou dia de treino');
    expect(PERIODIZATION_REPORT_CODE_LABEL.PRIORITIZE_UNMARKED).toBe('Prioridade extra desmarcada');
    expect(PERIODIZATION_REPORT_CODE_LABEL.SETS_COUNT_FIXED).toBe('Número de séries corrigido');
    expect(PERIODIZATION_REPORT_CODE_LABEL.ITEM_REMOVED).toBe('Item incompleto removido');
  });
});

describe('countPeriodizationReport', () => {
  it('soma as três listas e tolera null', () => {
    expect(countPeriodizationReport(null)).toBe(0);
    expect(
      countPeriodizationReport({ errors: [{ code: 'A' }], warnings: [{ code: 'B' }, { code: 'C' }], auto_fixes: [] }),
    ).toBe(3);
  });
});

// --- Rascunho -> builder ----------------------------------------------------

function jobResult(overrides: Partial<PeriodizationJobResult> = {}): PeriodizationJobResult {
  return {
    draft: {
      name: 'ABC gerado',
      description: null,
      training_type_id: 4,
      volume_mode: 'percent_budget',
      is_active: true,
      generation_log_id: null,
      slots: [
        {
          sequence: 2,
          name: 'B - Empurrar',
          slot_type: 'normal',
          weekday: null,
          priority_min_pct: null,
          priority_max_pct: null,
          items: [
            {
              item_order: 2,
              item_kind: 'muscle_group',
              muscle_group_id: 8,
              mapping_id: null,
              equipment_id: null,
              sets_count: 3,
              reps_target: '8-12',
              rpe_target: '9',
              prioritize: false,
              cardio_duration_minutes: null,
              priority_min_pct: 30,
              priority_max_pct: 40,
              advanced_technique: null,
              advanced_technique_applications: null,
              advanced_technique_duration_seconds: null,
              warmup_sets_count: null,
              warmup_reps: null,
              warmup_rest_seconds: null,
              feeler_set_sets_count: null,
              feeler_set_reps: null,
              feeler_set_rest_seconds: null,
              peak_contraction_seconds: null,
              rest_seconds: null,
            },
            {
              item_order: 1,
              item_kind: 'muscle_group',
              muscle_group_id: 7,
              mapping_id: null,
              equipment_id: null,
              sets_count: 4,
              reps_target: '8-12',
              rpe_target: '9',
              prioritize: true,
              cardio_duration_minutes: null,
              priority_min_pct: 40,
              priority_max_pct: 60,
              advanced_technique: null,
              advanced_technique_applications: null,
              advanced_technique_duration_seconds: null,
              warmup_sets_count: null,
              warmup_reps: null,
              warmup_rest_seconds: null,
              feeler_set_sets_count: null,
              feeler_set_reps: null,
              feeler_set_rest_seconds: null,
              peak_contraction_seconds: null,
              rest_seconds: null,
            },
          ],
        },
        {
          sequence: 1,
          name: 'A - Puxar',
          slot_type: 'normal',
          weekday: null,
          priority_min_pct: null,
          priority_max_pct: null,
          items: [],
        },
      ],
    },
    validation_report: {
      errors: [],
      warnings: [{ code: 'ACTIVATION_BELOW_MIN', muscle_group_id: 12, projected: 6.5, min: 8 }],
      auto_fixes: [{ code: 'RPE_CLAMPED', slot: 2, item: 3, from: '10', to: '9' }],
    },
    rationale: 'Divisão ABC com ênfase em costas.',
    source_citations: ['TREINO_A.md#bloco-1'],
    generation_log_id: 55,
    model: 'gpt-5.4-mini',
    prompt_version: 'periodization-v1',
    ...overrides,
  };
}

describe('periodizationJobResultToSeed', () => {
  it('mapeia o cabeçalho do rascunho para o estado do builder', () => {
    const seed = periodizationJobResultToSeed(jobResult());
    expect(seed.header).toEqual({
      name: 'ABC gerado',
      description: '',
      trainingTypeId: '4', // string: é o que o Select do Mantine usa
      volumeMode: 'percent_budget',
      isActive: true,
    });
  });

  it('leva a grade sugerida do rascunho; sem ela, null', () => {
    const result = jobResult();
    expect(periodizationJobResultToSeed(result).grid).toBeNull();
    result.draft.grid_rows = 1;
    result.draft.grid_cols = 2;
    expect(periodizationJobResultToSeed(result).grid).toEqual({ rows: 1, cols: 2 });
  });

  it('preserva o generation_log_id, que é o que marca origin=periodization no save', () => {
    expect(periodizationJobResultToSeed(jobResult()).generationLogId).toBe(55);
    expect(periodizationJobResultToSeed(jobResult({ generation_log_id: null })).generationLogId).toBeNull();
  });

  it('ordena slots por sequence e itens por item_order', () => {
    const seed = periodizationJobResultToSeed(jobResult());
    expect(seed.slots.map((slot) => slot.sequence)).toEqual([1, 2]);
    expect(seed.slots[1].items.map((item) => item.item_order)).toEqual([1, 2]);
    expect(seed.slots[1].items[0].muscle_group_id).toBe(7);
  });

  it('descarta item_kind fora do contrato do builder', () => {
    const result = jobResult();
    result.draft.slots[0].items.push({
      ...result.draft.slots[0].items[0],
      item_order: 3,
      item_kind: 'exercise' as never,
    });
    const seed = periodizationJobResultToSeed(result);
    expect(seed.slots[1].items).toHaveLength(2);
  });

  it('leva relatório, justificativa, fontes, modelo e versão do prompt', () => {
    const seed = periodizationJobResultToSeed(jobResult());
    expect(seed.report.warnings).toHaveLength(1);
    expect(seed.report.auto_fixes).toHaveLength(1);
    expect(seed.rationale).toBe('Divisão ABC com ênfase em costas.');
    expect(seed.sourceCitations).toEqual(['TREINO_A.md#bloco-1']);
    expect(seed.model).toBe('gpt-5.4-mini');
    expect(seed.promptVersion).toBe('periodization-v1');
  });

  it('normaliza um resultado sem relatório/fontes em vez de quebrar', () => {
    const seed = periodizationJobResultToSeed(
      jobResult({ validation_report: undefined as never, source_citations: undefined as never, rationale: '' }),
    );
    expect(seed.report).toEqual({ errors: [], warnings: [], auto_fixes: [] });
    expect(seed.sourceCitations).toEqual([]);
    expect(seed.rationale).toBe('');
  });
});

// --- Marca de origem e sessão ----------------------------------------------

describe('hasPeriodizationOriginFlag', () => {
  it('só reconhece a marca do fluxo de periodização', () => {
    expect(hasPeriodizationOriginFlag(periodizationOriginQuery())).toBe(true);
    expect(hasPeriodizationOriginFlag('?origem=periodizacao&x=1')).toBe(true);
    expect(hasPeriodizationOriginFlag('')).toBe(false);
    expect(hasPeriodizationOriginFlag('?origem=outra')).toBe(false);
    expect(hasPeriodizationOriginFlag('?outro=periodizacao')).toBe(false);
  });
});

describe('sessão do rascunho', () => {
  it('guarda, lê e limpa o rascunho sem tocar em banco', () => {
    const storage = fakeStorage();
    const seed = periodizationJobResultToSeed(jobResult());

    expect(readPeriodizationSeed(storage)).toBeNull();
    storePeriodizationSeed(seed, storage);
    expect(readPeriodizationSeed(storage)).toEqual(seed);

    clearPeriodizationSeed(storage);
    expect(readPeriodizationSeed(storage)).toBeNull();
    expect(storage.size()).toBe(0);
  });

  it('ignora conteúdo corrompido na sessão', () => {
    const storage = fakeStorage();
    storage.setItem(PERIODIZATION_DRAFT_STORAGE_KEY, '{nao-e-json');
    expect(readPeriodizationSeed(storage)).toBeNull();
    storage.setItem(PERIODIZATION_DRAFT_STORAGE_KEY, JSON.stringify({ header: {} }));
    expect(readPeriodizationSeed(storage)).toBeNull();
  });

  it('não explode quando não há storage disponível', () => {
    expect(readPeriodizationSeed(null)).toBeNull();
    expect(() => storePeriodizationSeed(periodizationJobResultToSeed(jobResult()), null)).not.toThrow();
    expect(() => clearPeriodizationSeed(null)).not.toThrow();
  });
});

describe('describePeriodizationEntry — complemento de volume (M46)', () => {
  const base = {
    muscle_group_name: 'Posterior de coxa',
    axis: 'session',
    slot: 2,
    item: 6,
    exercise_name: 'Mesa flexora',
    equipment_name: 'Máquina',
    deficit_before: 3,
    deficit_after: 0,
  };

  it('série acrescentada dentro do ideal', () => {
    expect(describePeriodizationEntry({ code: 'VOLUME_SETS_ADDED', ...base, from: 3, to: 4 })).toBe(
      'Posterior de coxa (dia 2, item 6) — Mesa flexora / Máquina: séries de 3 para 4; faltavam 3 séries diretas por sessão e agora faltam 0.',
    );
  });

  it('série acima do ideal avisa o ideal', () => {
    expect(
      describePeriodizationEntry({ code: 'VOLUME_SETS_ABOVE_IDEAL', ...base, axis: 'weekly', from: 4, to: 5, ideal: 4, max_sets: 6 }),
    ).toMatch(/de 4 para 5; faltavam 3 séries diretas por semana .* \(acima do ideal de 4 por exercício; teto de 6\)\.$/);
  });

  it('exercício novo e técnica', () => {
    expect(describePeriodizationEntry({ code: 'VOLUME_EXERCISE_ADDED', ...base, sets: 3 })).toMatch(
      /exercício novo da academia com 3 série\(s\).*Confira antes de salvar\.$/,
    );
    expect(
      describePeriodizationEntry({ code: 'VOLUME_TECHNIQUE_ADDED', ...base, technique: 'drop_set', applications: 2, equivalent: 1 }),
    ).toMatch(/drop set ×2 \(\+1 série equivalente\)/);
  });

  it('déficit sem solução lista os motivos em português', () => {
    expect(
      describePeriodizationEntry({
        code: 'VOLUME_GAP_UNRESOLVED',
        muscle_group_name: 'Panturrilha',
        axis: 'weekly',
        value_after: 7.5,
        min: 12,
        reasons: ['EXERCISE_CAP', 'TIME_LIMIT', 'MAX_SETS_PER_EXERCISE'],
      }),
    ).toBe(
      'Panturrilha: 7,5 séries diretas por semana, ainda abaixo do mínimo de 12; o complemento automático parou (dia já com 9 exercícios; passaria de 90 min no dia; exercícios do grupo já com 6 séries).',
    );
  });

  it('todos os códigos novos têm rótulo', () => {
    for (const code of [
      'VOLUME_SETS_ADDED',
      'VOLUME_EXERCISE_ADDED',
      'VOLUME_TECHNIQUE_ADDED',
      'VOLUME_SETS_ABOVE_IDEAL',
      'VOLUME_GAP_UNRESOLVED',
      'VOLUME_REDUCED',
      'MUSCLE_GROUP_RELABELED',
      'OPTIONAL_ZERO_NULLED',
    ]) {
      expect(PERIODIZATION_REPORT_CODE_LABEL[code]).toBeTruthy();
    }
  });

  it('série retirada de grupo acima da faixa', () => {
    expect(
      describePeriodizationEntry({ code: 'VOLUME_REDUCED', ...base, from: 5, to: 3, max: 6, excess_before: 4, excess_after: 0 }),
    ).toBe(
      'Posterior de coxa (dia 2, item 6) — Mesa flexora / Máquina: séries de 5 para 3; passavam 4 séries diretas por sessão do máximo de 6 e agora passam 0.',
    );
    expect(
      describePeriodizationEntry({
        code: 'VOLUME_REDUCED', ...base, axis: 'weekly', from: 5, to: 4, max: 14, excess_before: 2, excess_after: 1, prioritized: true,
      }),
    ).toMatch(/por semana do máximo de 14 e agora passam 1 \(exercício priorizado: sem outra opção de corte\)\.$/);
  });

  it('zero em campo opcional vira vazio', () => {
    expect(
      describePeriodizationEntry({ code: 'OPTIONAL_ZERO_NULLED', slot: 1, item: 2, fields: ['warmup_sets_count', 'feeler_set_reps'] }),
    ).toBe(
      'Campos com 0 (dia 1, item 2) ficaram vazios (0 significa "nenhum"): séries de aquecimento, repetições da série de ajuste.',
    );
  });

  it('grupo do exercício corrigido para o do catálogo', () => {
    expect(
      describePeriodizationEntry({
        code: 'MUSCLE_GROUP_RELABELED',
        slot: 1,
        item: 3,
        exercise_name: 'Supino declinado',
        equipment_name: 'Barra',
        from: 12,
        to: 52,
        from_name: 'Peitoral',
        to_name: 'Peitoral inferior',
      }),
    ).toBe(
      'Supino declinado / Barra (dia 1, item 3) veio marcado como Peitoral; na academia ele está em Peitoral inferior, e o rascunho passou a usar esse grupo.',
    );
    expect(describePeriodizationEntry({ code: 'MUSCLE_GROUP_RELABELED', from: 6, to: 7 })).toBe(
      'Exercício veio marcado como grupo #6; na academia ele está em grupo #7, e o rascunho passou a usar esse grupo.',
    );
  });
});
