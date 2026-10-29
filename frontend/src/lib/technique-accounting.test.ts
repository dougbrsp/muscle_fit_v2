import { describe, expect, it } from 'vitest';

import parity from './technique-accounting.parity.json';
import {
  applicationEquivalent,
  effortFactor,
  formatEquivalent,
  plannedEquivalent,
  realizedBlockEquivalent,
  referenceReps,
  type TechniqueApplication,
  type TechniqueCatalog,
} from './technique-accounting';

// Paridade com o DONO (`api/app/domain/technique_accounting.py`): catálogos,
// entradas e saídas gerados por `api/tests/unit/test_m76_lote1b_espelhos_fe.py
// --gravar` (o número mostrado no Modo Treino é o que o backend conta). Sem
// casos escritos à mão: mudou a regra, muda o dono e regenera a fixture.

const CATALOGS = parity.catalogs as unknown as Record<string, TechniqueCatalog>;
const CATALOG = CATALOGS.migracao_060;

type ApplicationJson = { technique_code: string | null; reps: number | null; rpe: number | null };

function toApplication(app: ApplicationJson): TechniqueApplication {
  return { techniqueCode: app.technique_code, reps: app.reps, rpe: app.rpe };
}

describe('effortFactor (paridade)', () => {
  it.each(parity.effort_factor.map((c) => [c.rpe_application, c.rpe_reference, c] as const))(
    'aplicação %s × referência %s',
    (_app, _ref, c) => {
      expect(effortFactor(c.rpe_application, c.rpe_reference)).toBeCloseTo(c.expected, 12);
    },
  );
});

describe('applicationEquivalent (paridade)', () => {
  it.each(parity.application_equivalent.map((c) => [JSON.stringify(c.application), c.ref_reps, c.anchor_rpe, c] as const))(
    '%s · ref %s · RPE âncora %s',
    (_app, _ref, _rpe, c) => {
      const valor = applicationEquivalent(toApplication(c.application as ApplicationJson), {
        refReps: c.ref_reps,
        anchorRpe: c.anchor_rpe,
        catalog: CATALOG,
      });
      expect(valor).toBeCloseTo(c.expected, 12);
    },
  );
});

describe('realizedBlockEquivalent (paridade)', () => {
  it.each(parity.realized_block_equivalent.map((c) => [c.name, c] as const))('%s', (_nome, c) => {
    const bloco = realizedBlockEquivalent((c.applications as ApplicationJson[]).map(toApplication), {
      anchorReps: c.anchor_reps,
      anchorRpe: c.anchor_rpe,
      repsTarget: c.reps_target,
      catalog: CATALOGS[c.catalog],
    });
    expect(bloco.total).toBeCloseTo(c.expected.total, 12);
    expect(bloco.refReps).toBe(c.expected.ref_reps);
    expect(bloco.capped).toBe(c.expected.capped);
    const byTechnique = c.expected.by_technique as unknown as Record<string, number>;
    const rawByTechnique = c.expected.raw_by_technique as unknown as Record<string, number>;
    expect(Object.keys(bloco.byTechnique).sort()).toEqual(Object.keys(byTechnique).sort());
    for (const [code, valor] of Object.entries(byTechnique)) {
      expect(bloco.byTechnique[code]).toBeCloseTo(valor, 12);
      expect(bloco.rawByTechnique[code]).toBeCloseTo(rawByTechnique[code], 12);
    }
  });
});

describe('plannedEquivalent (paridade)', () => {
  it.each(parity.planned_equivalent.map((c) => [c.technique_code, c.applications, c] as const))(
    '%s × %s',
    (_code, _n, c) => {
      expect(plannedEquivalent(c.technique_code, c.applications, CATALOG)).toBeCloseTo(c.expected, 12);
    },
  );
});

describe('referenceReps (paridade)', () => {
  it.each(parity.reference_reps.map((c) => [c.anchor_reps, c.reps_target, c] as const))(
    'âncora %s · alvo %s',
    (_reps, _alvo, c) => {
      expect(referenceReps(c.anchor_reps, c.reps_target)).toBe(c.expected);
    },
  );
});

describe('formatEquivalent', () => {
  it('formata com vírgula e ao menos uma casa', () => {
    expect(formatEquivalent(0.5)).toBe('0,5');
    expect(formatEquivalent(1)).toBe('1,0');
    expect(formatEquivalent(0.2857)).toBe('0,29');
  });
});
