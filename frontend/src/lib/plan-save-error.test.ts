import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import { planSaveErrorMessage } from './plan-save-error';

describe('planSaveErrorMessage', () => {
  it('usa a mensagem do detail objeto do servidor', () => {
    const error = new ApiError('Erro ao consultar /workout-plans (HTTP 422).', 422, {
      reason: 'muscle_group_locked',
      message: 'Grupo travado.',
    });
    expect(planSaveErrorMessage(error, 'x')).toBe('Grupo travado.');
  });

  it('sem message em texto, mantém a mensagem do ApiError', () => {
    expect(planSaveErrorMessage(new ApiError('Falhou', 422, { message: '  ' }), 'x')).toBe('Falhou');
    expect(planSaveErrorMessage(new ApiError('Falhou', 500), 'x')).toBe('Falhou');
  });

  it('lê o primeiro item da lista do Pydantic, sem o prefixo "Value error, "', () => {
    const list = [
      { loc: ['body', 'items'], msg: 'Value error, RPE por série excede as séries.' },
      { loc: ['body'], msg: 'outro' },
    ];
    expect(planSaveErrorMessage(new ApiError('Falhou', 422, list), 'x')).toBe('RPE por série excede as séries.');
    expect(planSaveErrorMessage(new ApiError('Falhou', 422, [{ msg: 'Campo obrigatório' }]), 'x')).toBe(
      'Campo obrigatório',
    );
  });

  it('lista vazia ou formato desconhecido mantém a mensagem do ApiError', () => {
    expect(planSaveErrorMessage(new ApiError('Falhou', 422, []), 'x')).toBe('Falhou');
    expect(planSaveErrorMessage(new ApiError('Falhou', 422, [{ loc: ['a'] }]), 'x')).toBe('Falhou');
    expect(planSaveErrorMessage(new ApiError('Falhou', 422, 42), 'x')).toBe('Falhou');
  });

  it('erro que não é ApiError usa o fallback', () => {
    expect(planSaveErrorMessage(new Error('boom'), 'fallback')).toBe('fallback');
  });
});
