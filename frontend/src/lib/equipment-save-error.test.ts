import { describe, expect, it } from 'vitest';

import { ApiError } from './api';
import { equipmentSaveErrorMessage } from './equipment-save-error';

describe('equipmentSaveErrorMessage', () => {
  it('403 mostra o texto de permissão', () => {
    expect(equipmentSaveErrorMessage(new ApiError('Forbidden', 403))).toBe('Ação restrita a administradores.');
  });

  it('ApiError (ex.: 409) mostra a mensagem do servidor', () => {
    expect(equipmentSaveErrorMessage(new ApiError('Já existe um equipamento com esse nome.', 409))).toBe(
      'Já existe um equipamento com esse nome.',
    );
  });

  it('erro que não é ApiError usa o fallback', () => {
    expect(equipmentSaveErrorMessage(new Error('boom'))).toBe('Não foi possível salvar o equipamento.');
    expect(equipmentSaveErrorMessage(null)).toBe('Não foi possível salvar o equipamento.');
  });
});
