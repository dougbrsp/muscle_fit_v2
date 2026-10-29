// M98: escolha do cardio no pedido de periodização. A tela só repassa a escolha;
// QUANDO o Modelo leva cardio no "Automático" é decidido no backend
// (`api/app/periodization/cardio_plan.py`, `cardio_requested`).

import { describe, expect, it } from 'vitest';
import { buildPeriodizationRequest, newPeriodizationForm } from './periodization';
import { PERIODIZATION_CARDIO_OPTIONS } from './periodization-types';
import type { PeriodizationFormState } from './periodization-types';

function form(overrides: Partial<PeriodizationFormState> = {}): PeriodizationFormState {
  return { ...newPeriodizationForm(), ...overrides };
}

describe('cardio no pedido de periodização', () => {
  it('o padrão é Automático e não envia o campo (o backend decide pelas preferências)', () => {
    expect(newPeriodizationForm().cardio).toBe('auto');
    const body = buildPeriodizationRequest(form({ instructions: 'quero cardio 2x' }));
    expect(body).not.toHaveProperty('include_cardio');
  });

  it('"Incluir cardio" envia include_cardio = true', () => {
    expect(buildPeriodizationRequest(form({ cardio: 'include' })).include_cardio).toBe(true);
  });

  it('"Sem cardio" envia include_cardio = false, mesmo com preferências que falam de cardio', () => {
    const body = buildPeriodizationRequest(form({ cardio: 'exclude', instructions: 'HIIT na esteira' }));
    expect(body.include_cardio).toBe(false);
    expect(body.instructions).toBe('HIIT na esteira');
  });

  it('o resto do corpo não muda com a escolha do cardio', () => {
    const { include_cardio: _ignored, ...semCardio } = buildPeriodizationRequest(form({ cardio: 'include' }));
    expect(semCardio).toEqual(buildPeriodizationRequest(form()));
  });

  it('as opções cobrem os três estados, com Automático primeiro', () => {
    expect(PERIODIZATION_CARDIO_OPTIONS.map((option) => option.value)).toEqual(['auto', 'include', 'exclude']);
    for (const option of PERIODIZATION_CARDIO_OPTIONS) {
      expect(option.label).not.toBe('');
      expect(option.description).not.toBe('');
    }
  });
});
