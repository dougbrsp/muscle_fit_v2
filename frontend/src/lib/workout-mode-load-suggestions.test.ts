import { describe, expect, it } from 'vitest';

import parity from './session-loads.parity.json';
import {
  feelerSetLoad,
  LOAD_ADJUST_SOURCES,
  loadStep,
  mirrorQuickLoadAdjust,
  QUICK_ADJUST_ACTIONS,
  mirrorFeelerLoad,
  mirrorTechniqueApplicationLoad,
  mirrorWarmupLoads,
  roundToStep,
  techniqueApplicationLoad,
  warmupLoad,
  warmupLoads,
  type SessionLoadPlan,
} from './workout-mode-load-suggestions';

// Paridade com o dono (`api/app/domain/session_loads.py`). Os casos são
// exportados pelo backend (`api/tests/unit/test_m76_lote1b_espelhos_fe.py
// --gravar`); se a regra mudar lá, o JSON muda e este teste acusa o espelho.
describe('espelho de session_loads.py (paridade com o backend)', () => {
  const cases = parity.cases.map((c) => [`âncora ${c.anchor_kg} kg, passo ${c.load_increment_kg ?? 'default'}`, c] as const);

  it.each(cases)('%s', (_name, c) => {
    const step = c.load_increment_kg;
    expect(roundToStep(c.anchor_kg, step)).toBe(c.round_to_step);
    expect(mirrorWarmupLoads(c.anchor_kg, step)).toEqual(c.warmup_loads_kg);
    expect(c.feeler_loads_kg.map((_, i) => mirrorFeelerLoad(c.anchor_kg, i + 1, step))).toEqual(c.feeler_loads_kg);
    expect(c.drop_set_loads_kg.map((_, i) => mirrorTechniqueApplicationLoad('drop_set', c.anchor_kg, i + 1, step))).toEqual(
      c.drop_set_loads_kg,
    );
    expect(
      c.rest_pause_loads_kg.map((_, i) => mirrorTechniqueApplicationLoad('rest_pause', c.anchor_kg, i + 1, step)),
    ).toEqual(c.rest_pause_loads_kg);
  });

  // Modo Intensivo: ajuste rápido (`session_loads.quick_load_adjust`), em kg,
  // em lb (o cliente já converteu carga e passo) e nas bordas.
  const quickCases = parity.quick_load_adjust_cases.map(
    (c) => [`${c.current} ${c.unit}, passo ${c.step ?? 'default'}`, c] as const,
  );
  it.each(quickCases)('ajuste rápido: %s', (_name, c) => {
    for (const action of QUICK_ADJUST_ACTIONS) {
      expect(mirrorQuickLoadAdjust(c.current, c.step, action)).toBe((c.results as Record<string, number>)[action]);
    }
  });

  it('ajuste rápido: vocabulário do backend e ação desconhecida', () => {
    expect([...QUICK_ADJUST_ACTIONS].sort()).toEqual(
      Object.keys(parity.quick_load_adjust_cases[0].results).sort(),
    );
    expect(LOAD_ADJUST_SOURCES).toContain('fatigue');
    expect(LOAD_ADJUST_SOURCES).toContain('manual');
    expect(() => mirrorQuickLoadAdjust(70, 5, 'minus_50pct' as never)).toThrow();
  });

  it('passo ausente ou inválido vale o default do backend', () => {
    expect(loadStep(null)).toBe(2.5);
    expect(loadStep(0)).toBe(2.5);
    expect(loadStep(2.27)).toBe(2.27);
  });
});

describe('cargas lidas da API quando a âncora é a prescrita', () => {
  // Valores propositalmente diferentes do espelho: prova que a tela LÊ a API.
  const plan: SessionLoadPlan = {
    load_anchor_kg: 100,
    load_increment_kg: 5,
    warmup_loads_kg: [21, 41],
    feeler_loads_kg: [51, 71],
    application_loads_kg: [81, 66],
  };

  it('âncora = a da prescrição: usa as listas da API', () => {
    expect(warmupLoads(100, { plan, step: 5 })).toEqual([21, 41]);
    expect(warmupLoad(100, { plan, step: 5 })).toBe(21);
    expect(feelerSetLoad(100, 2, { plan, step: 5 })).toBe(71);
    expect(techniqueApplicationLoad('drop_set', 100, 2, { plan, step: 5 })).toBe(66);
  });

  it('âncora dinâmica (série real diferente da prescrita): espelho no passo do equipamento', () => {
    expect(warmupLoads(90, { plan, step: 5 })).toEqual([20, 35]);
    expect(techniqueApplicationLoad('drop_set', 90, 1, { plan, step: 5 })).toBe(70);
  });

  it('índice além do que a API mandou cai no espelho', () => {
    expect(feelerSetLoad(100, 3, { plan, step: 5 })).toBe(90);
    expect(techniqueApplicationLoad('drop_set', 100, 3, { plan, step: 5 })).toBe(50);
  });

  it('sem plano (troca de equipamento, exercício extra): espelho', () => {
    expect(warmupLoads(100)).toEqual([20, 40]);
    expect(feelerSetLoad(80, 2, { step: 2.5 })).toBe(55);
    expect(techniqueApplicationLoad('rest_pause', 101, 3)).toBe(101);
  });
});
