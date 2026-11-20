import { describe, expect, it } from 'vitest';
import { ApiError } from '@/lib/api';
import { templateSaveError } from './template-save-error';

describe('templateSaveError', () => {
  it('bloqueio de segurança mantém o título e não fecha sozinho', () => {
    const e = new ApiError('Itens novos ou alterados com incompatibilidade de segurança: Slot A.', 422);
    expect(templateSaveError(e, 'x')).toMatchObject({ titulo: 'Bloqueado por segurança', seguranca: true });
  });

  it('validação com loc de item aponta slot, item e campo (base 1) sem o prefixo técnico', () => {
    const e = new ApiError('HTTP 422', 422, [
      { loc: ['body', 'slots', 0, 'items', 2, 'rpe_targets_by_set'], msg: 'Value error, RPE fora da escala', type: 'value_error' },
    ]);
    expect(templateSaveError(e, 'x')).toEqual({
      titulo: 'Dados inválidos',
      mensagem: 'Slot 1, item 3: RPE fora da escala',
      campo: 'slots.0.items.2.rpe_targets_by_set',
      seguranca: false,
    });
  });

  it('validação sem loc de item usa só a mensagem do servidor', () => {
    const e = new ApiError('HTTP 422', 422, [{ loc: ['body', 'name'], msg: 'Field required', type: 'missing' }]);
    expect(templateSaveError(e, 'x')).toMatchObject({ titulo: 'Dados inválidos', mensagem: 'Field required', seguranca: false });
  });

  it('422 de regra de negócio (detail string) não é bloqueio de segurança', () => {
    const e = new ApiError('Um modelo precisa de pelo menos 1 slot.', 422);
    expect(templateSaveError(e, 'x')).toMatchObject({ titulo: 'Dados inválidos', seguranca: false });
  });

  it('erro que não é ApiError usa o fallback e o título padrão', () => {
    expect(templateSaveError(new Error('boom'), 'Falhou', 'Erro ao salvar rascunho')).toEqual({
      titulo: 'Erro ao salvar rascunho',
      mensagem: 'Falhou',
      seguranca: false,
    });
  });
});
