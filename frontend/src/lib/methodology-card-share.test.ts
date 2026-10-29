import { describe, expect, it } from 'vitest';
import { statusVisual } from './dashboard-status';
import {
  buildItemDirectSetsShareLines,
  buildItemDirectShareLookup,
  formatDirectSetsShareText,
} from './methodology-card-share';
import { cardShare, FIXTURE_CARD_SHARE_EVALUATION } from './methodology-volume-fixtures';
import type { MethodologyVolumeEvaluation } from './methodology-volume-types';
import { COUNTING_MODES, DEFAULT_COUNTING_MODE, setsUnitOf } from './volume-counting-mode';
import type { WorkoutPlanTemplateSlotItemPayload, WorkoutPlanTemplateSlotPayload } from './workout-plan-templates-types';

/**
 * M43 + Lote 3 — linha de participação de cada card do Modo B:
 * `<bloco>: <séries do card>/<séries do grupo no dia>/<séries do grupo no
 * Modelo>` + status do bloco. Os três números vêm do payload (a fatia do card
 * é `item_shares[].groups[].direct_sets`, já no modo de contagem): o card não
 * recalcula nada.
 */

const quadriceps = (sets = 3, slot = 1) => cardShare(slot, [{ id: 4, name: 'Quadríceps', sets }]);

function lines(overrides: Partial<Parameters<typeof buildItemDirectSetsShareLines>[0]> = {}) {
  return buildItemDirectSetsShareLines({
    volumeMode: 'equipment_series',
    slotSequence: 1,
    share: quadriceps(),
    evaluation: FIXTURE_CARD_SHARE_EVALUATION,
    ...overrides,
  });
}

const WEIGHTED = COUNTING_MODES.find((mode) => mode.value !== DEFAULT_COUNTING_MODE)!;

describe('buildItemDirectSetsShareLines', () => {
  it('grupo simples: exemplo literal do pedido "Quadríceps: 3/9/12" com ícone verde', () => {
    const [line, ...rest] = lines();
    expect(rest).toHaveLength(0);
    expect(line.text).toBe('Quadríceps: 3/9/12');
    expect([line.cardSets, line.slotSets, line.cycleSets]).toEqual([3, 9, 12]);
    expect(line.sliceName).toBeNull();
    expect(line.isSecondary).toBe(false);
    expect(line.statusCode).toBe('WITHIN_RANGE');
    expect(line.status).toBe(statusVisual('methodologyVolume', 'WITHIN_RANGE'));
    expect(line.ariaLabel).toBe('Quadríceps: Na faixa');
    expect(line.title).toContain('Séries diretas de Quadríceps: 3 neste exercício / 9 neste dia / 12 no Modelo (ciclo)');
    expect(line.title).toContain('faixa 4–8 por sessão');
  });

  it('a série do card é a do payload, sem conta local (inclusive fracionária)', () => {
    expect(lines({ share: quadriceps(4.8) })[0].text).toBe('Quadríceps: 4,8/9/12');
    expect(lines({ share: quadriceps(4, 3), slotSequence: 3 })[0].text).toBe('Quadríceps: 4/3/12');
  });

  it('fatia agrega no bloco-pai: nome e números do bloco, fatia entre parênteses', () => {
    const [line] = lines({
      slotSequence: 2,
      share: cardShare(2, [{ id: 52, name: 'Peitoral', sets: 3, slice: { id: 12, name: 'Peitoral superior' } }]),
    });
    expect(line.groupName).toBe('Peitoral');
    expect(line.sliceName).toBe('Peitoral superior');
    expect(line.text).toBe('Peitoral (Peitoral superior): 3/16/16');
    expect(line.title).toContain('Peitoral superior é fatia de Peitoral');
    expect(line.key).toBe(52);
  });

  it('modo ponderado: o secundário vem marcado e a unidade é a do modo', () => {
    const evaluation: MethodologyVolumeEvaluation = { ...FIXTURE_CARD_SHARE_EVALUATION, counting_mode: WEIGHTED.value };
    const [ombros, biceps] = lines({
      slotSequence: 2,
      evaluation,
      share: cardShare(2, [
        { id: 7, name: 'Ombros', sets: 3 },
        { id: 5, name: 'Bíceps', sets: 1.5, weight: 0.5, primary: false },
      ]),
    });
    expect(ombros.isSecondary).toBe(false);
    expect(biceps.isSecondary).toBe(true);
    expect(biceps.text).toBe('Bíceps (sec. ×0,5): 1,5/3/3');
    expect(biceps.title).toContain(`${WEIGHTED.sets_unit_label.charAt(0).toUpperCase()}`);
    expect(biceps.title).toContain(setsUnitOf(WEIGHTED.value).plural.slice(1));
    expect(biceps.title).toContain('participação cadastrada (×0,5)');
  });

  it('sem fatia, bloco desconhecido ou sem sessão do dia: nada é inventado', () => {
    expect(lines({ share: undefined })).toEqual([]);
    expect(lines({ share: cardShare(1, []) })).toEqual([]);
    expect(lines({ share: cardShare(1, [{ id: 999, name: 'Antebraço', sets: 3 }]) })).toEqual([]);
    // Quadríceps não tem sessão 2 no ciclo: não há denominador do dia.
    expect(lines({ slotSequence: 2 })).toEqual([]);
    expect(lines({ evaluation: undefined })).toEqual([]);
    expect(lines({ evaluation: null })).toEqual([]);
  });

  it('Modo A (percent_budget) não renderiza nada, mesmo com avaliação', () => {
    expect(lines({ volumeMode: 'percent_budget' })).toEqual([]);
    expect(lines({ volumeMode: undefined })).toEqual([]);
  });

  it('BELOW_RANGE é laranja, com conflito dito no aria-label e no tooltip', () => {
    const [line] = lines({ slotSequence: 2, share: cardShare(2, [{ id: 5, name: 'Bíceps', sets: 3 }]) });
    expect(line.text).toBe('Bíceps: 3/3/3');
    expect(line.status?.color).toBe('orange');
    expect(line.ariaLabel).toBe('Bíceps: Abaixo da faixa, eixos em conflito');
    expect(line.title).toContain('faixa 4–6 por sessão; meta 6–12 por semana');
    expect(line.title).toContain('incoerentes');
  });

  it('NOT_EVALUABLE sai sem ícone e com o motivo no tooltip; nota de técnica vai no tooltip', () => {
    const [line] = lines({
      slotSequence: 3,
      share: cardShare(3, [{ id: 9, name: 'Trapézio', sets: 3 }]),
      techniqueNote: '4 séries + 0,8 equiv. (rest-pause ×2)',
    });
    expect(line.text).toBe('Trapézio: 3/4/4');
    expect(line.status).toBeNull();
    expect(line.ariaLabel).toBe('Trapézio: sem status, grupo sem macro-grupo');
    expect(line.title).toContain('(4 séries + 0,8 equiv. (rest-pause ×2))');
  });

  it('formatDirectSetsShareText segue o formato exato pedido', () => {
    expect(formatDirectSetsShareText('Quadríceps', null, 3, 9, 12)).toBe('Quadríceps: 3/9/12');
    expect(formatDirectSetsShareText('Peitoral', 'Peitoral superior', 2, 8, 16)).toBe(
      'Peitoral (Peitoral superior): 2/8/16',
    );
  });
});

describe('buildItemDirectShareLookup', () => {
  const item = (over: Partial<WorkoutPlanTemplateSlotItemPayload>): WorkoutPlanTemplateSlotItemPayload =>
    ({ item_kind: 'equipment', mapping_id: 100, muscle_group_id: null, sets_count: 3, ...over }) as WorkoutPlanTemplateSlotItemPayload;

  it('casa o eco pelo índice do corpo (cardio fora) e confere a identidade', () => {
    const slots = [
      {
        sequence: 1,
        slot_type: 'normal',
        items: [item({ item_kind: 'cardio', mapping_id: null }), item({ mapping_id: 100 }), item({ mapping_id: 101 })],
      } as WorkoutPlanTemplateSlotPayload,
    ];
    const evaluation: MethodologyVolumeEvaluation = {
      ...FIXTURE_CARD_SHARE_EVALUATION,
      item_shares: [cardShare(1, [{ id: 4, name: 'Quadríceps', sets: 3 }], 0), cardShare(1, [], 1)],
    };
    const lookup = buildItemDirectShareLookup(slots, evaluation);
    // estado [cardio, 100, 101] -> corpo [100 (0), 101 (1)]
    expect(lookup.get('1:1')?.mapping_id).toBe(100);
    expect(lookup.get('1:2')?.mapping_id).toBe(101);
    expect(lookup.has('1:0')).toBe(false);
    // resposta de um corpo anterior com outro exercício na posição: não casa
    const stale = buildItemDirectShareLookup([{ ...slots[0], items: [item({ mapping_id: 555 })] }], evaluation);
    expect(stale.size).toBe(0);
  });
});
