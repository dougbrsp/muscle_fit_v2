import { beforeEach, describe, expect, it, vi } from 'vitest';

// Plano autogerido (N2): o próprio usuário cria o plano sem informar `user_id`, "puxar do Modelo" e o
// corpo do formulário. O FE não valida faixa nem calcula: a API (motor) responde 400 com o código.
const apiFetch = vi.fn();
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), apiFetch }));

const { createNutritionPlan, fetchCycleDaysFromTemplate, fetchNutritionPlans, nutritionKeys, updateNutritionPlan } =
  await import('./nutrition-api');
const { DAY_KIND_OPTIONS, DEFAULT_MACRO_PRIORITY, MACRO_PRIORITY_OPTIONS, NUTRITION_NOTICES, WEEKDAY_OPTIONS } =
  await import('./nutrition-constants');
const { cycleDaysPayload, macroPriorityPayload, manualTargetsPayload } = await import('./nutrition-plan-form');

beforeEach(() => apiFetch.mockReset());

describe('plano autogerido: transporte', () => {
  it('lista os planos do próprio usuário sem user_id e os do aluno com user_id (admin/PT)', async () => {
    apiFetch.mockResolvedValue({ user: {}, items: [] });
    await fetchNutritionPlans();
    await fetchNutritionPlans(null);
    await fetchNutritionPlans(7);
    expect(apiFetch.mock.calls.map((c) => c[0])).toEqual([
      '/nutrition/plans',
      '/nutrition/plans',
      '/nutrition/plans?user_id=7',
    ]);
  });

  it('cria o rascunho sem user_id quando é o próprio usuário e devolve os avisos do motor', async () => {
    apiFetch.mockResolvedValue({ id: 3, warnings: [{ code: 'manual_below_range:protein_g', label: 'x' }] });
    const corpo = { strategy: 'maintenance' } as never;
    const salvo = await createNutritionPlan(corpo);
    const [url, opcoes] = apiFetch.mock.calls[0];
    expect(url).toBe('/nutrition/plans');
    expect(opcoes.method).toBe('POST');
    expect(JSON.parse(opcoes.body)).toEqual({ strategy: 'maintenance' });
    expect(salvo.warnings[0].code).toBe('manual_below_range:protein_g');
    await updateNutritionPlan(3, corpo);
    expect(apiFetch.mock.calls[1][0]).toBe('/nutrition/plans/3');
  });

  it('"puxar do Modelo" chama a rota certa, do próprio usuário ou do aluno', async () => {
    apiFetch.mockResolvedValue({ cycle_days: null, notes: [] });
    await fetchCycleDaysFromTemplate();
    await fetchCycleDaysFromTemplate(9);
    expect(apiFetch.mock.calls.map((c) => c[0])).toEqual([
      '/nutrition/cycle-days/from-template',
      '/nutrition/cycle-days/from-template?user_id=9',
    ]);
  });

  it('a chave de cache aceita o usuário ainda não escolhido', () => {
    expect(nutritionKeys.plans(null)).toEqual(['nutrition-plans', null]);
  });
});

describe('plano autogerido: corpo do formulário', () => {
  it('gramas manuais desligadas = null; campo vazio vai null para a API recusar com o código do motor', () => {
    expect(manualTargetsPayload(false, 150, 250, 70)).toBeNull();
    expect(manualTargetsPayload(true, '150', '250,5', '')).toEqual({ protein_g: 150, carb_g: 250.5, fat_g: null });
    expect(manualTargetsPayload(true, 'abc', 0, 70)).toEqual({ protein_g: null, carb_g: 0, fat_g: 70 });
  });

  it('com gramas manuais a prioridade vai no padrão do motor', () => {
    const outra = MACRO_PRIORITY_OPTIONS.find((o) => o.value !== DEFAULT_MACRO_PRIORITY)?.value as string;
    expect(macroPriorityPayload(true, outra)).toBe(DEFAULT_MACRO_PRIORITY);
    expect(macroPriorityPayload(false, outra)).toBe(outra);
  });

  it('dias desligados = null; ligados vão como o usuário marcou, uma chave por dia da semana', () => {
    const dias = Object.fromEntries(WEEKDAY_OPTIONS.map((d, i) => [d.value, DAY_KIND_OPTIONS[i % 3].value]));
    expect(cycleDaysPayload(false, dias)).toBeNull();
    expect(cycleDaysPayload(true, dias)).toEqual(dias);
    expect(cycleDaysPayload(true, dias)).not.toBe(dias);
  });
});

describe('plano autogerido: vocabulário do motor', () => {
  it('dias da semana de segunda ("0") a domingo ("6") e três tipos de dia, tudo do dono', () => {
    expect(WEEKDAY_OPTIONS.map((d) => d.value)).toEqual(['0', '1', '2', '3', '4', '5', '6']);
    expect(WEEKDAY_OPTIONS.every((d) => d.label.length > 0)).toBe(true);
    expect(DAY_KIND_OPTIONS).toHaveLength(3);
    expect(MACRO_PRIORITY_OPTIONS.map((o) => o.value)).toContain(DEFAULT_MACRO_PRIORITY);
  });

  it('o aviso "ciclo indisponível" saiu do espelho', () => {
    expect(Object.keys(NUTRITION_NOTICES)).not.toContain('carb_cycling_unavailable');
  });
});
