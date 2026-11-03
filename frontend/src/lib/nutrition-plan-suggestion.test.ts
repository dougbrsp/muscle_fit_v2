import { beforeEach, describe, expect, it, vi } from 'vitest';

// Sugestão de metas por IA (N6): o FE só transporta e preenche o formulário. Faixas, gramas, "o que mudou" e motivos
// vêm da API (motor). Sem chamada real.
const apiFetch = vi.fn();
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), apiFetch }));

const {
  applySuggestion,
  fetchPlanSuggestionUsage,
  keepSuggestion,
  previewPlanDraft,
  requestPlanSuggestion,
  suggestionCurrentPayload,
  suggestionFormPatch,
  undoSuggestion,
} = await import('./nutrition-plan-suggestion');
import type { NutritionPlanSettingsPayload } from './nutrition-types';

const FORM: NutritionPlanSettingsPayload = {
  strategy: 'cutting',
  advanced: false,
  protein_g_per_kg: 2.6,
  fat_kcal_share: 0.25,
  weekly_rate_share: 0.007,
  surplus_share: null,
  carb_min_g_per_kg: null,
  bmr_formula: 'mifflin',
  tdee_mode: 'explicit_training',
  activity_level: null,
  meals_per_day: 4,
  goal_body_fat_percent: 15,
  goal_weight_kg: 75,
  macro_priority: 'balanced',
  manual_targets: { protein_g: 180, carb_g: 200, fat_g: 60 },
  manual_training_mode: 'fixed',
  cycle_days: { '0': 'high' },
  notes: 'nota da pessoa',
};

const RESULTADO = {
  fields: { strategy: 'cutting', advanced: false },
  reasons: [{ code: 'limited_data', label: 'Há poucos dados recentes.' }],
  changes: [],
};

beforeEach(() => {
  apiFetch.mockReset();
});

describe('pedido da sugestão', () => {
  it('leva o estado do formulário sem as gramas, o ciclo nem as observações', () => {
    const atual = suggestionCurrentPayload(FORM, true);
    expect(atual.manual_targets).toBe(true);
    expect(atual.protein_g_per_kg).toBe(2.6);
    const texto = JSON.stringify(atual);
    expect(texto).not.toContain('nota da pessoa');
    expect(texto).not.toContain('180');
    expect(texto).not.toContain('cycle_days');
  });

  it('estratégia vazia vai como null', () => {
    expect(suggestionCurrentPayload({ ...FORM, strategy: '' }, false).strategy).toBeNull();
  });

  it('dispara e espera o job até o prazo da API; sem user_id para o próprio usuário', async () => {
    apiFetch.mockImplementation(async (path: string) =>
      path === '/nutrition/plan-suggestion'
        ? { job_id: 'j1', status: 'pending', poll_timeout_seconds: 120 }
        : { job_id: 'j1', status: 'done', result: RESULTADO },
    );
    const resultado = await requestPlanSuggestion(suggestionCurrentPayload(FORM, false), null);
    expect(resultado).toEqual(RESULTADO);
    const [caminho, init] = apiFetch.mock.calls[0];
    expect(caminho).toBe('/nutrition/plan-suggestion');
    expect(JSON.parse(init.body).user_id).toBeUndefined();
    expect(apiFetch.mock.calls[1][0]).toBe('/jobs/j1');
  });

  it('admin/PT manda o aluno', async () => {
    apiFetch.mockImplementation(async (path: string) =>
      path === '/nutrition/plan-suggestion'
        ? { job_id: 'j1', status: 'pending', poll_timeout_seconds: null }
        : { job_id: 'j1', status: 'done', result: RESULTADO },
    );
    await requestPlanSuggestion(suggestionCurrentPayload(FORM, false), 12);
    expect(JSON.parse(apiFetch.mock.calls[0][1].body).user_id).toBe(12);
  });

  it('job recusado pelo motor chega com a mensagem do motor', async () => {
    apiFetch.mockImplementation(async (path: string) =>
      path === '/nutrition/plan-suggestion'
        ? { job_id: 'j1', status: 'pending', poll_timeout_seconds: 120 }
        : { job_id: 'j1', status: 'failed', error: 'A sugestão da IA ficou fora das regras do motor.' },
    );
    await expect(requestPlanSuggestion(suggestionCurrentPayload(FORM, false), null)).rejects.toThrow('fora das regras');
  });

  it('o erro de cota da API não é engolido', async () => {
    apiFetch.mockRejectedValueOnce(new Error('Limite atingido'));
    await expect(requestPlanSuggestion(suggestionCurrentPayload(FORM, false), null)).rejects.toThrow('Limite atingido');
  });

  it('a cota vem da API', async () => {
    apiFetch.mockResolvedValueOnce({ feature: 'plan_suggestion', limit: 5, used: 1, remaining: 4, allowed: true });
    expect((await fetchPlanSuggestionUsage()).remaining).toBe(4);
    expect(apiFetch).toHaveBeenCalledWith('/nutrition/plan-suggestion/usage');
  });
});

describe('prévia do formulário', () => {
  it('manda o corpo como está, para o motor calcular', async () => {
    apiFetch.mockResolvedValueOnce({ plan: null });
    await previewPlanDraft({ ...FORM, user_id: 12 });
    const [caminho, init] = apiFetch.mock.calls[0];
    expect(caminho).toBe('/nutrition/plan-preview');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body).user_id).toBe(12);
  });
});

describe('preenchimento do formulário', () => {
  it('converte as frações para o % do campo e desliga as gramas manuais', () => {
    const patch = suggestionFormPatch({
      strategy: 'cutting',
      advanced: false,
      protein_g_per_kg: 2.6,
      fat_kcal_share: 0.25,
      weekly_rate_share: 0.007,
      surplus_share: null,
      carb_min_g_per_kg: null,
      macro_priority: 'high_carb',
      meals_per_day: 4,
    });
    expect(patch).toEqual({
      strategy: 'cutting',
      advanced: false,
      protein: 2.6,
      fatPercent: 25,
      ratePercent: 0.7,
      surplusPercent: '',
      carbMin: '',
      macroPriority: 'high_carb',
      meals: 4,
      manualOn: false,
    });
  });
});

describe('aplicar, desfazer e manter', () => {
  const CAMPOS = {
    strategy: 'bulking',
    advanced: false,
    protein_g_per_kg: 2,
    fat_kcal_share: 0.25,
    weekly_rate_share: null,
    surplus_share: 0.1,
    carb_min_g_per_kg: 3,
    macro_priority: 'high_carb',
    meals_per_day: 5,
  };
  const FORM_INICIAL = {
    strategy: 'cutting' as string | null,
    advanced: false,
    protein: 2.6 as number | string,
    fatPercent: 25 as number | string,
    ratePercent: 0.7 as number | string,
    surplusPercent: '' as number | string,
    carbMin: '' as number | string,
    macroPriority: 'balanced',
    meals: 4 as number | string,
    manualOn: true,
    goalWeight: 75,
    notes: 'minha nota',
  };

  it('aplicar preenche só o que a IA sugere e guarda como estava', () => {
    const { form, undo } = applySuggestion({ form: FORM_INICIAL, undo: null }, CAMPOS);
    expect(form.strategy).toBe('bulking');
    expect(form.manualOn).toBe(false);
    expect(form.goalWeight).toBe(75);
    expect(undo?.strategy).toBe('cutting');
    expect(undo).not.toHaveProperty('notes');
  });

  it('desfazer volta só as chaves da sugestão e não perde a edição de outro campo', () => {
    const aplicado = applySuggestion({ form: FORM_INICIAL, undo: null }, CAMPOS);
    const editado = { ...aplicado, form: { ...aplicado.form, notes: 'editei depois', goalWeight: 70 } };
    const { form, undo } = undoSuggestion(editado);
    expect(form).toEqual({ ...FORM_INICIAL, notes: 'editei depois', goalWeight: 70 });
    expect(undo).toBeNull();
  });

  it('duas sugestões seguidas: desfazer volta ao estado anterior à primeira', () => {
    const primeira = applySuggestion({ form: FORM_INICIAL, undo: null }, CAMPOS);
    const segunda = applySuggestion(primeira, { ...CAMPOS, strategy: 'maintenance', meals_per_day: 3 });
    expect(segunda.form.strategy).toBe('maintenance');
    expect(segunda.undo?.strategy).toBe('cutting');
    expect(undoSuggestion(segunda).form).toEqual(FORM_INICIAL);
  });

  it('depois de manter, a sugestão seguinte desfaz para o que foi mantido', () => {
    const mantido = keepSuggestion(applySuggestion({ form: FORM_INICIAL, undo: null }, CAMPOS));
    const nova = applySuggestion(mantido, { ...CAMPOS, strategy: 'maintenance' });
    expect(undoSuggestion(nova).form).toEqual(mantido.form);
  });

  it('manter fica com o formulário e não há mais o que desfazer', () => {
    const aplicado = applySuggestion({ form: FORM_INICIAL, undo: null }, CAMPOS);
    const mantido = keepSuggestion(aplicado);
    expect(mantido.form).toEqual(aplicado.form);
    expect(mantido.undo).toBeNull();
    expect(undoSuggestion(mantido).form).toEqual(aplicado.form);
  });

  it('o pedido leva o que muda a conta do motor', () => {
    const atual = suggestionCurrentPayload({ ...FORM, activity_level: 'active' }, false);
    expect(atual.activity_level).toBe('active');
    expect(atual.tdee_mode).toBe('explicit_training');
    expect(atual.bmr_formula).toBe('mifflin');
  });
});
