import { describe, expect, it } from 'vitest';

import { buildActivationPreviewPayload } from './activation-preview-payload';
import { getAdvancedTechniqueDefaultSetsCount, isSetsOnlyTechnique } from './advanced-techniques';
import {
  buildItemDirectSetsShareLines,
  formatItemPlannedSetsNote,
  itemTechniqueEquivalent,
  type ItemTechniqueInput,
} from './methodology-card-share';
import { buildRowView, buildSessionRows, formatSetsWithTechnique } from './methodology-volume';
import { cardShare, FIXTURE_CARD_SHARE_EVALUATION } from './methodology-volume-fixtures';
import type { MethodologyVolumeEvaluation } from './methodology-volume-types';
import { describePeriodizationEntry } from './periodization';
import { PERIODIZATION_REPORT_CODE_LABEL } from './periodization-types';
import type { TechniqueCatalog } from './technique-accounting';
import type { WorkoutPlanTemplateSlotItemPayload, WorkoutPlanTemplateSlotPayload } from './workout-plan-templates-types';

/**
 * Fase 5, PR 5 — volume PLANEJADO do Modelo com as técnicas (espelho de
 * `api/tests/unit/test_fase5_planejado_tecnica.py`): mesmos exemplos do
 * realizado (rest-pause ×2 -> 0,8; teto; FST-7/GVT -> 0), o corpo do preview
 * levando a técnica, o card "4 séries + 0,8 equiv. (rest-pause ×2)" e o painel
 * EXG com a parcela da técnica.
 */

const CATALOG: TechniqueCatalog = {
  rest_pause: { activation_set_equivalent: 0.4, max_equivalent_per_anchor: 1.0, default_sets_count: null },
  drop_set: { activation_set_equivalent: 0.5, max_equivalent_per_anchor: 1.0, default_sets_count: null },
  forced_reps: { activation_set_equivalent: 0.25, max_equivalent_per_anchor: 0.5, default_sets_count: null },
  fst7: { activation_set_equivalent: 0, max_equivalent_per_anchor: 0, default_sets_count: 7 },
  gvt: { activation_set_equivalent: 0, max_equivalent_per_anchor: 0, default_sets_count: 10 },
};

const technique = (code: string | null, applications: number | null, label?: string): ItemTechniqueInput => ({
  code,
  applications,
  catalog: CATALOG,
  label,
});

describe('equivalente planejado do card (tabela)', () => {
  it.each([
    [4, 'rest_pause', 2, 0.8],
    [4, 'rest_pause', 3, 1.0], // 1,2 -> teto 1,0
    [4, 'drop_set', 3, 1.0], // 1,5 -> teto 1,0
    [3, 'forced_reps', 4, 0.5], // 1,0 -> teto 0,5
    [7, 'fst7', 3, 0],
    [10, 'gvt', 1, 0],
    [4, 'rest_pause', null, 0],
    [0, 'rest_pause', 2, 0], // técnica sem série não é trabalho
    [4, null, 2, 0],
  ])('%s séries + %s ×%s = +%s', (sets, code, apps, expected) => {
    expect(itemTechniqueEquivalent(sets, technique(code, apps))).toBeCloseTo(expected, 10);
  });

  it('nota do card: "4 séries + 0,8 equiv. (rest-pause ×2)"; sem nota quando nada soma', () => {
    expect(formatItemPlannedSetsNote(4, technique('rest_pause', 2, 'Rest-pause'))).toBe(
      '4 séries + 0,8 equiv. (rest-pause ×2)',
    );
    expect(formatItemPlannedSetsNote(1, technique('drop_set', 1, 'Drop-set'))).toBe('1 série + 0,5 equiv. (drop-set ×1)');
    expect(formatItemPlannedSetsNote(7, technique('fst7', 3, 'FST-7'))).toBeNull();
    expect(formatItemPlannedSetsNote(4, null)).toBeNull();
    expect(formatItemPlannedSetsNote(4, technique('rest_pause', null))).toBeNull();
  });

  it('linha M43 mostra o número do backend (com o equivalente) e explica no tooltip', () => {
    // Lote 3: o 4,8 vem pronto do payload (`item_shares`); a nota é só texto.
    const [line] = buildItemDirectSetsShareLines({
      volumeMode: 'equipment_series',
      slotSequence: 1,
      share: cardShare(1, [{ id: 4, name: 'Quadríceps', sets: 4.8, technique: 0.8 }]),
      evaluation: FIXTURE_CARD_SHARE_EVALUATION,
      techniqueNote: formatItemPlannedSetsNote(4, technique('rest_pause', 2, 'Rest-pause')),
    });
    expect(line.cardSets).toBeCloseTo(4.8, 10);
    expect(line.text).toBe('Quadríceps: 4,8/9/12');
    expect(line.title).toContain('4,8 neste exercício (4 séries + 0,8 equiv. (rest-pause ×2))');
    expect(line.title).toContain('estimativa do catálogo');
  });

  it('FST-7/GVT: séries padrão do catálogo, sem aplicações nem pausa', () => {
    expect(getAdvancedTechniqueDefaultSetsCount('fst7')).toBe(7);
    expect(getAdvancedTechniqueDefaultSetsCount('gvt')).toBe(10);
    expect(getAdvancedTechniqueDefaultSetsCount('rest_pause')).toBeNull();
    expect(getAdvancedTechniqueDefaultSetsCount(null)).toBeNull();
    expect(isSetsOnlyTechnique('fst7')).toBe(true);
    expect(isSetsOnlyTechnique('drop_set')).toBe(false);
  });
});

describe('corpo do preview leva a técnica (preview e metodologia)', () => {
  const equipment = (over: Partial<WorkoutPlanTemplateSlotItemPayload>): WorkoutPlanTemplateSlotItemPayload => ({
    item_order: 1,
    item_kind: 'equipment',
    muscle_group_id: 3,
    mapping_id: 11,
    equipment_id: 7,
    sets_count: 4,
    reps_target: '8-12',
    rpe_target: '8',
    prioritize: false,
    cardio_duration_minutes: null,
    priority_min_pct: null,
    priority_max_pct: null,
    advanced_technique: null,
    advanced_technique_applications: null,
    advanced_technique_duration_seconds: null,
    warmup_sets_count: null,
    warmup_reps: null,
    warmup_rest_seconds: null,
    feeler_set_sets_count: null,
    feeler_set_reps: null,
    feeler_set_rest_seconds: null,
    peak_contraction_seconds: null,
    rest_seconds: null,
    ...over,
  });
  const body = (item: WorkoutPlanTemplateSlotItemPayload) => {
    const slot: WorkoutPlanTemplateSlotPayload = {
      sequence: 1,
      name: 'A',
      weekday: null,
      slot_type: 'normal',
      priority_min_pct: null,
      priority_max_pct: null,
      items: [item],
    };
    return buildActivationPreviewPayload([slot], 'equipment_series').slots[0].items[0];
  };

  it.each([
    ['rest_pause', 2, 45, 'rest_pause', 2],
    ['fst7', 3, 30, 'fst7', null], // FST-7 não soma: aplicações fora do corpo
    ['gvt', 1, 60, 'gvt', null],
    [null, 2, null, null, null], // aplicações sem técnica não contam
  ])('%s ×%s (pausa %s) -> técnica %s, aplicações %s; pausa nunca vai', (code, apps, pause, expCode, expApps) => {
    const out = body(
      equipment({
        advanced_technique: code,
        advanced_technique_applications: apps,
        advanced_technique_duration_seconds: pause,
      }),
    );
    expect(out.advanced_technique).toBe(expCode);
    expect(out.advanced_technique_applications).toBe(expApps);
    expect(out.advanced_technique_duration_seconds).toBeNull();
  });
});

describe('painel EXG escreve a parcela da técnica', () => {
  it('formatSetsWithTechnique', () => {
    expect(formatSetsWithTechnique(9.8, 0.8)).toBe('9,8 (9 séries + 0,8 equiv.)');
    expect(formatSetsWithTechnique(1.5, 0.5)).toBe('1,5 (1 série + 0,5 equiv.)');
    expect(formatSetsWithTechnique(12, 0)).toBe('12');
    expect(formatSetsWithTechnique(12, undefined)).toBe('12');
  });

  it('sessão e totais do ciclo mostram o equivalente; payload antigo (v3) sem o campo segue igual', () => {
    const base = FIXTURE_CARD_SHARE_EVALUATION.groups[0];
    const group: MethodologyVolumeEvaluation['groups'][number] = {
      ...base,
      direct_sets_cycle_total: 12.8,
      technique_equivalent_cycle_total: 0.8,
      sessions: [{ slot_sequence: 1, slot_name: 'Pernas A', direct_sets: 9.8, direct_exercises: 3, technique_equivalent: 0.8 }],
    };
    const row = buildRowView(group, 7);
    expect(row.techniqueNote).toBe('inclui 0,8 equiv. de técnica no ciclo');
    expect(row.cycleTotalsText).toContain('12,8 séries diretas no ciclo');
    expect(row.sessions[0].setsText).toBe('9,8 (9 séries + 0,8 equiv.)');

    expect(buildRowView(base, 7).techniqueNote).toBeNull();
    expect(buildSessionRows(base.sessions ?? [])[0].setsText).toBe('9');
  });
});

describe('validador da IA: auto-fix de FST-7/GVT', () => {
  it('descreve o conserto com posição e rótulo', () => {
    expect(
      describePeriodizationEntry({
        code: 'TECHNIQUE_FIELDS_CLEARED',
        slot: 2,
        item: 3,
        technique: 'fst7',
        applications: 3,
        duration_seconds: 30,
      }),
    ).toBe('Técnica "fst7" (dia 2, item 3) só define o número de séries: aplicações e pausa foram zeradas.');
    expect(PERIODIZATION_REPORT_CODE_LABEL.TECHNIQUE_FIELDS_CLEARED).toBe('Aplicações/pausa zeradas (FST-7/GVT)');
  });
});
