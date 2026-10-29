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

  it('dia e semana do próprio usuário ou do aluno (admin/PT) por `user_id`', async () => {
    await fetchNutritionDay('2026-10-17', null);
    await fetchNutritionDay('2026-10-17', 2);
    await fetchNutritionWeek(null, null);
    await fetchNutritionWeek('2026-10-14', 2);
    expect(apiFetch.mock.calls.map(([caminho]) => caminho)).toEqual([
      '/nutrition/days/2026-10-17',
      '/nutrition/days/2026-10-17?user_id=2',
      '/nutrition/weeks',
      '/nutrition/weeks?start=2026-10-14&user_id=2',
    ]);
  });

  it('refeição, preview e dia usam os métodos e corpos do contrato', async () => {
    const corpo = { local_date: '2026-10-17', meal_slot: 'lunch', eaten_at: null, notes: null, items: [] };
    await previewMeal({ ...corpo, meal_id: 4 });
    await createMeal({ ...corpo, user_id: 2 });
    await updateMeal(4, corpo);
    await deleteMeal(4);
    await saveNutritionDayLog('2026-10-17', { water_ml: 2000, hunger: 3, notes: null }, 2);
    const chamadas = apiFetch.mock.calls.map(([caminho, init]) => [caminho, init?.method]);
    expect(chamadas).toEqual([
      ['/nutrition/meals/preview', 'POST'],
      ['/nutrition/meals', 'POST'],
      ['/nutrition/meals/4', 'PUT'],
      ['/nutrition/meals/4', 'DELETE'],
      ['/nutrition/days/2026-10-17?user_id=2', 'PUT'],
    ]);
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({ ...corpo, meal_id: 4 });
  });

  it('chaves do React Query separam aluno, dia e semana', () => {
    expect(diaryKeys.day(null, '2026-10-17')).toEqual(['nutrition-diary', 'day', 'me', '2026-10-17']);
    expect(diaryKeys.week(2, null)).toEqual(['nutrition-diary', 'week', 2, 'today']);
    expect(diaryKeys.day(2, 'x').slice(0, 1)).toEqual(diaryKeys.all);
  });
});
