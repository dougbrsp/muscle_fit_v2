import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import type { CardioRecommendationView } from './analytics-types';
import { applicableDates, applyErrorMessage, planActionLabel, templateEditorPath } from './cardio-recommendation-apply';

const session = (date: string, applicable: boolean) => ({
  date,
  date_label: date,
  prescription_text: '5 min aquec. + 30 min Z2 + 5 min recuperação',
  prescription_short: 'Normal 30 min Z2 · 5+5 min',
  total_minutes_label: '40 min',
  rationale: null,
  signals: [],
  adjustment_labels: [],
  slot_name: applicable ? 'Lower - TER' : null,
  plan_action: applicable ? ('replace' as const) : null,
  applicable,
});

describe('cardio-recommendation-apply', () => {
  it('aplicar todas usa só as sessões que a API marcou como aplicáveis', () => {
    const view = {
      sessions: [session('2026-10-28', true), session('2026-10-29', false), session('2026-10-30', true)],
    } as unknown as CardioRecommendationView;
    expect(applicableDates(view)).toEqual(['2026-10-28', '2026-10-30']);
    expect(applicableDates(null)).toEqual([]);
  });

  it('mensagem do 409 vem do detail.message do backend', () => {
    const error = new ApiError('Erro (HTTP 409).', 409, { reason: 'template_not_active', message: 'Não é o ativo.' });
    expect(applyErrorMessage(error)).toBe('Não é o ativo.');
    expect(applyErrorMessage(new ApiError('Acesso restrito.', 403))).toBe('Acesso restrito.');
  });

  it('rótulos do vínculo e caminho do editor', () => {
    expect(planActionLabel('replace', 'Lower - TER')).toBe('Substitui o cardio de Lower - TER');
    expect(planActionLabel('add', 'Upper')).toBe('Acrescenta cardio em Upper');
    expect(planActionLabel(null, 'Upper')).toBeNull();
    expect(templateEditorPath(36)).toBe('/modelos-treino/36');
  });
});
