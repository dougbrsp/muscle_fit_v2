import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), apiFetch }));

const {
  createMeal,
  deleteMeal,
  diaryKeys,
  fetchNutritionDay,
  fetchNutritionWeek,
  previewMeal,
  saveNutritionDayLog,
  updateMeal,
} = await import('./nutrition-api');

describe('nutrition-api (diário L4): só transporte para as rotas do motor', () => {
  beforeEach(() => {
    apiFetch.mockReset();
    apiFetch.mockResolvedValue({});
  });

  it('dia e semana são sempre do usuário logado: nenhuma URL leva `user_id`', async () => {
    await fetchNutritionDay('2026-10-17');
    await fetchNutritionWeek(null);
    await fetchNutritionWeek('2026-10-14');
    expect(apiFetch.mock.calls.map(([caminho]) => caminho)).toEqual([
      '/nutrition/days/2026-10-17',
      '/nutrition/weeks',
      '/nutrition/weeks?start=2026-10-14',
    ]);
  });

  it('refeição, preview e dia usam os métodos e corpos do contrato', async () => {
    const corpo = { local_date: '2026-10-17', meal_slot: 'lunch', eaten_at: null, notes: null, items: [] };
    await previewMeal({ ...corpo, meal_id: 4 });
    await createMeal({ ...corpo, user_id: 2 });
    await updateMeal(4, corpo);
    await deleteMeal(4);
    await saveNutritionDayLog('2026-10-17', { notes: 'ok' });
    const chamadas = apiFetch.mock.calls.map(([caminho, init]) => [caminho, init?.method]);
    expect(chamadas).toEqual([
      ['/nutrition/meals/preview', 'POST'],
      ['/nutrition/meals', 'POST'],
      ['/nutrition/meals/4', 'PUT'],
      ['/nutrition/meals/4', 'DELETE'],
      ['/nutrition/days/2026-10-17', 'PUT'],
    ]);
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({ ...corpo, meal_id: 4 });
  });

  it('chaves do React Query separam dia e semana', () => {
    expect(diaryKeys.day('2026-10-17')).toEqual(['nutrition-diary', 'day', '2026-10-17']);
    expect(diaryKeys.week(null)).toEqual(['nutrition-diary', 'week', 'today']);
    expect(diaryKeys.day('x').slice(0, 1)).toEqual(diaryKeys.all);
  });
});
