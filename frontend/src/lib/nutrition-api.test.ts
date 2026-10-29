import { describe, expect, it } from 'vitest';

import { ApiError } from './api';
import { isNoPublishedPlan, nutritionErrorCode, nutritionErrorInfo } from './nutrition-api';

describe('nutrition-api: erros das fontes externas (L3b) chegam com o texto da API', () => {
  it('429 do limite do OFF e 422 da importação: mensagem e itens rotulados pelo motor', () => {
    const limite = new ApiError('x', 429, { error: 'off_daily_limit', message: 'Limite do dia atingido.', limit: 30 });
    expect(nutritionErrorCode(limite)).toBe('off_daily_limit');
    expect(nutritionErrorInfo(limite, 'falhou')).toEqual({ message: 'Limite do dia atingido.', items: [] });
    const recusada = new ApiError('x', 422, {
      error: 'invalid_external_food',
      message: 'Cadastre manualmente.',
      errors: [{ code: 'missing_field:energy_kcal', label: 'Valor energético (kcal): obrigatório.' }],
    });
    expect(nutritionErrorInfo(recusada, 'falhou').items).toHaveLength(1);
    const semChave = new ApiError('x', 503, { error: 'usda_not_configured', message: 'USDA não configurado.' });
    expect(nutritionErrorInfo(semChave, 'falhou').message).toBe('USDA não configurado.');
  });
});

describe('nutrition-api: leitura dos erros estruturados da API de nutrição', () => {
  it('"sem plano" só quando a API diz no_published_plan; 404 de outra origem é erro', () => {
    const semPlano = new ApiError('x', 404, { error: 'no_published_plan', message: 'Sem plano.' });
    expect(isNoPublishedPlan(semPlano)).toBe(true);
    expect(isNoPublishedPlan(new ApiError('Not Found', 404))).toBe(false);
    expect(isNoPublishedPlan(new ApiError('x', 404, { error: 'plan_not_found', message: 'x' }))).toBe(false);
    expect(isNoPublishedPlan(new Error('rede'))).toBe(false);
  });

  it('código e itens do motor vêm do detail; sem detail, a mensagem do ApiError', () => {
    const conflito = new ApiError('x', 409, { error: 'publish_conflict', message: 'Recarregue.', current_id: 7 });
    expect(nutritionErrorCode(conflito)).toBe('publish_conflict');
    expect(nutritionErrorInfo(conflito, 'falhou')).toEqual({ message: 'Recarregue.', items: [] });
    const invalido = new ApiError('x', 400, {
      error: 'invalid_plan',
      message: 'Fora da faixa.',
      errors: [{ code: 'param_out_of_range:protein_g_per_kg', label: 'Proteína (g/kg): fora da faixa da fonte.' }],
    });
    expect(nutritionErrorInfo(invalido, 'falhou').items).toHaveLength(1);
    expect(nutritionErrorInfo(new ApiError('Erro HTTP 500', 500), 'falhou').message).toBe('Erro HTTP 500');
    expect(nutritionErrorInfo(new Error('x'), 'falhou').message).toBe('falhou');
  });
});
