import { describe, expect, it } from 'vitest';
import {
  DEFAULT_WEIGHT_UNIT,
  DISPLAY_DECIMALS,
  formatLoadTarget,
  formatWeight,
  fromKg,
  isWeightUnit,
  LB_IN_KG,
  normalizeWeightUnit,
  toKg,
  weightFieldFromKg,
  weightFieldOnInput,
  weightFieldOnUnitChange,
  loadStepperStep,
  WEIGHT_STEP,
  WEIGHT_UNITS,
  type WeightUnit,
} from './weight-units';

// Grade de valores em lb que o usuário consegue DIGITAR (1 casa decimal, o
// `decimalScale` do campo quando a unidade é lb). Cobre de meia libra até
// 400 lb, que é mais do que qualquer barra de academia.
const LB_INPUT_GRID: number[] = [];
for (let tenths = 5; tenths <= 4000; tenths += 1) {
  LB_INPUT_GRID.push(tenths / 10);
}

// Grade em kg com a precisão da coluna `DECIMAL(8,2)`.
const KG_INPUT_GRID: number[] = [];
for (let cents = 25; cents <= 30000; cents += 1) {
  KG_INPUT_GRID.push(cents / 100);
}

describe('constantes', () => {
  it('usa o fator exato da libra avoirdupois', () => {
    expect(LB_IN_KG).toBe(0.45359237);
  });

  it('arredonda kg na precisão da coluna e lb em 1 casa', () => {
    expect(DISPLAY_DECIMALS).toEqual({ kg: 2, lb: 1 });
  });

  it('o passo fino do modo avançado continua 0,25 kg', () => {
    expect(WEIGHT_STEP.kg.fine).toBe(0.25);
  });

  it('os passos em lb são valores nativos da unidade, não 2.5kg convertidos', () => {
    expect(WEIGHT_STEP.lb).toEqual({ coarse: 5, fine: 0.5 });
  });
});

describe('loadStepperStep (passo do equipamento vindo da API, M76 C7)', () => {
  it('usa o load_increment_kg do equipamento em kg', () => {
    expect(loadStepperStep('kg', 1)).toBe(1);
    expect(loadStepperStep('kg', 5)).toBe(5);
    expect(loadStepperStep('kg', 2.27)).toBe(2.27);
  });

  it('em lb converte o passo do equipamento', () => {
    expect(loadStepperStep('lb', 2.27)).toBe(5);
    expect(loadStepperStep('lb', 2.5)).toBe(5.5);
  });

  it('sem passo informado cai no passo padrão da unidade', () => {
    expect(loadStepperStep('kg', null)).toBe(2.5);
    expect(loadStepperStep('kg', undefined)).toBe(2.5);
    expect(loadStepperStep('kg', 0)).toBe(2.5);
    expect(loadStepperStep('lb', null)).toBe(5);
  });
});

describe('normalizeWeightUnit', () => {
  it.each([
    ['kg', 'kg'],
    ['lb', 'lb'],
    ['KG', 'kg'],
    [' Lb ', 'lb'],
  ])('normaliza %s -> %s', (raw, expected) => {
    expect(normalizeWeightUnit(raw)).toBe(expected);
  });

  it.each([null, undefined, '', 'libra', 'pounds', 42, {}])('cai para o default com %s', (raw) => {
    expect(normalizeWeightUnit(raw)).toBe(DEFAULT_WEIGHT_UNIT);
  });

  it('o default é kg — quem nunca escolheu nada não muda de comportamento', () => {
    expect(DEFAULT_WEIGHT_UNIT).toBe('kg');
    expect(WEIGHT_UNITS).toEqual(['kg', 'lb']);
  });

  it('isWeightUnit só aceita as duas unidades', () => {
    expect(isWeightUnit('kg')).toBe(true);
    expect(isWeightUnit('lb')).toBe(true);
    expect(isWeightUnit('g')).toBe(false);
    expect(isWeightUnit(null)).toBe(false);
  });
});

describe('toKg — o que vai para o banco é sempre quilo', () => {
  it('em kg, só quantiza na precisão da coluna', () => {
    expect(toKg(22.5, 'kg')).toBe(22.5);
    expect(toKg(22.567, 'kg')).toBe(22.57);
  });

  it('converte libras com o fator exato', () => {
    // 50 lb = 22.6796185 kg -> 22.68 na coluna DECIMAL(8,2)
    expect(toKg(50, 'lb')).toBe(22.68);
    expect(toKg(100, 'lb')).toBe(45.36);
    expect(toKg(45, 'lb')).toBe(20.41);
    expect(toKg(1, 'lb')).toBe(0.45);
  });

  it('um valor em lb NUNCA é gravado como se fosse kg', () => {
    for (const lb of [5, 25, 50, 135, 225, 315]) {
      expect(toKg(lb, 'lb')).toBeLessThan(lb);
      expect(toKg(lb, 'lb')).toBeCloseTo(lb * LB_IN_KG, 2);
    }
  });

  it('nunca produz mais de 2 casas decimais (o banco truncaria)', () => {
    for (const lb of LB_INPUT_GRID) {
      const kg = toKg(lb, 'lb');
      expect(kg).toBe(Math.round(kg * 100) / 100);
    }
  });
});

describe('fromKg — o que é mostrado ao usuário', () => {
  it('mostra kg como veio, com no máximo 2 casas', () => {
    expect(fromKg(22.68, 'kg')).toBe(22.68);
    expect(fromKg(20, 'kg')).toBe(20);
  });

  it('mostra lb com 1 casa — nunca "22.6796 lb"', () => {
    expect(fromKg(22.68, 'lb')).toBe(50);
    expect(fromKg(20, 'lb')).toBe(44.1);
    expect(String(fromKg(10.3, 'lb'))).toBe('22.7');
  });
});

describe('round-trip: um valor digitado em lb não deriva a cada edição', () => {
  // Este é o risco central do M3: digitar 50 lb, salvar 22.68 kg, reabrir,
  // ver 49.9 lb, salvar de novo, ver 49.8... até o peso fugir do real.
  it('lb -> kg -> lb devolve EXATAMENTE o valor digitado, em toda a grade', () => {
    for (const lb of LB_INPUT_GRID) {
      expect(fromKg(toKg(lb, 'lb'), 'lb')).toBe(lb);
    }
  });

  it('salvar/reabrir/salvar em lb converge para o mesmo kg (ponto fixo)', () => {
    for (const lb of LB_INPUT_GRID) {
      const primeiraGravacao = toKg(lb, 'lb');
      let exibido = fromKg(primeiraGravacao, 'lb');
      let gravado = primeiraGravacao;
      for (let edicao = 0; edicao < 25; edicao += 1) {
        gravado = toKg(exibido, 'lb');
        exibido = fromKg(gravado, 'lb');
      }
      expect(gravado).toBe(primeiraGravacao);
      expect(exibido).toBe(lb);
    }
  });

  it('kg -> exibição -> kg é identidade na precisão da coluna', () => {
    for (const kg of KG_INPUT_GRID) {
      expect(toKg(fromKg(kg, 'kg'), 'kg')).toBe(kg);
    }
  });
});

describe('campo de carga — digitar em lb salva kg, e alternar não corrompe', () => {
  it('o valor que chega do backend em kg é exibido na unidade preferida', () => {
    expect(weightFieldFromKg(22.68, 'kg')).toEqual({ kg: 22.68, display: 22.68, unit: 'kg' });
    expect(weightFieldFromKg(22.68, 'lb')).toEqual({ kg: 22.68, display: 50, unit: 'lb' });
  });

  it('digitar 50 em lb salva 22.68 kg — nunca 50 kg', () => {
    const campo = weightFieldOnInput(weightFieldFromKg(20, 'lb'), 50);
    expect(campo.display).toBe(50);
    expect(campo.kg).toBe(22.68);
  });

  it('digitar em kg salva o próprio número', () => {
    const campo = weightFieldOnInput(weightFieldFromKg(20, 'kg'), 42.5);
    expect(campo.kg).toBe(42.5);
  });

  it('campo vazio não inventa carga', () => {
    const campo = weightFieldOnInput(weightFieldFromKg(20, 'lb'), null);
    expect(campo).toMatchObject({ kg: null, display: null });
    expect(weightFieldFromKg(null, 'lb')).toMatchObject({ kg: null, display: null, unit: 'lb' });
  });

  it('alternar a unidade NÃO muda o peso que será salvo', () => {
    const digitado = weightFieldOnInput(weightFieldFromKg(20, 'lb'), 50);
    const emKg = weightFieldOnUnitChange(digitado, 'kg');
    expect(emKg.unit).toBe('kg');
    expect(emKg.display).toBe(22.68);
    expect(emKg.kg).toBe(digitado.kg);
  });

  it('alternar ida e volta devolve exatamente o valor digitado', () => {
    for (const [valor, unidade] of [
      [50, 'lb'],
      [2.5, 'kg'],
      [22.68, 'kg'],
      [135, 'lb'],
      [0.5, 'lb'],
    ] as Array<[number, WeightUnit]>) {
      const outra: WeightUnit = unidade === 'kg' ? 'lb' : 'kg';
      const digitado = weightFieldOnInput(weightFieldFromKg(10, unidade), valor);
      const voltou = weightFieldOnUnitChange(weightFieldOnUnitChange(digitado, outra), unidade);
      expect(voltou).toEqual(digitado);
    }
  });

  it('alternar 20 vezes seguidas não faz o número andar', () => {
    let campo = weightFieldOnInput(weightFieldFromKg(10, 'kg'), 2.5);
    const original = { ...campo };
    for (let i = 0; i < 20; i += 1) {
      campo = weightFieldOnUnitChange(campo, campo.unit === 'kg' ? 'lb' : 'kg');
    }
    expect(campo).toEqual(original);
  });

  it('trocar para a MESMA unidade é no-op', () => {
    const campo = weightFieldOnInput(weightFieldFromKg(10, 'lb'), 50);
    expect(weightFieldOnUnitChange(campo, 'lb')).toBe(campo);
  });

  it('salvar em lb, recarregar do banco e salvar de novo não deriva', () => {
    // O ciclo real: digita -> salva kg -> a próxima série abre com esse kg
    // como default -> salva de novo sem tocar no campo.
    for (const lb of [0.5, 5, 22.5, 45, 50, 135, 225, 315, 399.9]) {
      let campo = weightFieldOnInput(weightFieldFromKg(10, 'lb'), lb);
      const primeiroKg = campo.kg;
      for (let sessao = 0; sessao < 25; sessao += 1) {
        campo = weightFieldFromKg(campo.kg, 'lb');
        expect(campo.display).toBe(lb);
        expect(campo.kg).toBe(primeiroKg);
      }
    }
  });
});

describe('formatWeight — histórico/prescrição na unidade preferida', () => {
  it('formata o mesmo kg do banco em cada unidade', () => {
    expect(formatWeight(22.68, 'kg')).toBe('22.68 kg');
    expect(formatWeight(22.68, 'lb')).toBe('50 lb');
    expect(formatWeight(20, 'lb')).toBe('44.1 lb');
  });

  it('sem espaço mantém o formato já usado no modo treino', () => {
    expect(formatWeight(20, 'kg', { space: false })).toBe('20kg');
    expect(formatWeight(22.68, 'lb', { space: false })).toBe('50lb');
  });

  it('não deixa zero à direita aparecer', () => {
    expect(formatWeight(20.0, 'kg')).toBe('20 kg');
    expect(formatWeight(22.68, 'lb')).not.toContain('.0');
  });
});

describe('formatLoadTarget — alvo da prescrição', () => {
  it('reescreve um alvo numérico simples na unidade preferida', () => {
    expect(formatLoadTarget('40', 'lb')).toBe('88.2lb');
    expect(formatLoadTarget('40kg', 'lb')).toBe('88.2lb');
    expect(formatLoadTarget('40', 'kg')).toBe('40kg');
  });

  it('interpreta o alvo sem sufixo como kg (é o que a ficha grava)', () => {
    expect(formatLoadTarget('40', 'kg')).toBe('40kg');
  });

  it('devolve texto livre intacto em vez de adivinhar', () => {
    expect(formatLoadTarget('40-45kg', 'lb')).toBe('40-45kg');
    expect(formatLoadTarget('peso corporal', 'lb')).toBe('peso corporal');
    expect(formatLoadTarget('', 'lb')).toBe('');
    expect(formatLoadTarget(null, 'lb')).toBeNull();
    expect(formatLoadTarget(undefined, 'lb')).toBeNull();
  });
});
