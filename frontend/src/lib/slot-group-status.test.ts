import { describe, expect, it } from 'vitest';
import { statusVisual } from './dashboard-status';
import { buildItemDirectSetsShareLines } from './methodology-card-share';
import { cardShare, FIXTURE_CARD_SHARE_EVALUATION } from './methodology-volume-fixtures';
import type { SlotGroupSetsRow } from './slot-group-sets';
import { slotGroupSetsWithStatus } from './slot-group-status';

// `_formSets` documenta o número do FORMULÁRIO (soma dos itens), que a linha
// não usa mais (M76 E5): as séries vêm do preview do backend.
const row = (
  muscleGroupId: number,
  name: string,
  _formSets: number,
  memberGroupIds: number[] = [muscleGroupId],
): SlotGroupSetsRow => ({
  muscleGroupId,
  name,
  exercises: 1,
  memberGroupIds,
  label: '1 exercício',
});

function build(
  rows: SlotGroupSetsRow[],
  overrides: Partial<Parameters<typeof slotGroupSetsWithStatus>[0]> = {},
) {
  return slotGroupSetsWithStatus({
    rows,
    volumeMode: 'equipment_series',
    slotSequence: 1,
    evaluation: FIXTURE_CARD_SHARE_EVALUATION,
    ...overrides,
  });
}

describe('slotGroupSetsWithStatus', () => {
  it('grupo na faixa: mesmo status, cor e números (dia/ciclo) do card', () => {
    const [result] = build([row(4, 'Quadríceps', 9)]);
    expect(result.planning?.statusCode).toBe('WITHIN_RANGE');
    expect(result.planning?.status).toBe(statusVisual('methodologyVolume', 'WITHIN_RANGE'));
    expect(result.planning?.valueText).toBe('9/12');
    expect(result.planning?.title).toBe('Na faixa: 9 séries diretas neste dia / 12 no Modelo (ciclo).');
    expect(result.planning?.ariaLabel).toBe('Quadríceps: Na faixa, 9/12');
  });

  it('é exatamente o status que o card do mesmo grupo e dia exibe', () => {
    const [card] = buildItemDirectSetsShareLines({
      volumeMode: 'equipment_series',
      slotSequence: 2,
      share: cardShare(2, [{ id: 5, name: 'Bíceps', sets: 3 }]),
      evaluation: FIXTURE_CARD_SHARE_EVALUATION,
    });
    const [slotRow] = build([row(5, 'Bíceps', 3)], { slotSequence: 2 });
    expect(slotRow.planning?.statusCode).toBe(card.statusCode);
    expect(slotRow.planning?.status).toBe(card.status);
    expect(slotRow.planning?.slotSets).toBe(card.slotSets);
    expect(slotRow.planning?.cycleSets).toBe(card.cycleSets);
  });

  it('abaixo e acima da faixa trazem o rótulo; desvio informativo é dito', () => {
    // Ordem por séries do backend: Ombros (10) antes de Bíceps (3).
    const [ombros, biceps] = build([row(5, 'Bíceps', 3), row(7, 'Ombros', 10)], { slotSequence: 2 });
    expect(biceps.planning?.status.label).toBe('Abaixo da faixa');
    expect(biceps.planning?.status.color).toBe('orange');
    expect(ombros.planning?.status.label).toBe('Acima da faixa');
    expect(ombros.planning?.isInformational).toBe(true);
    expect(ombros.planning?.title).toContain('desvio esperado pela intenção declarada');
    expect(ombros.planning?.ariaLabel).toBe('Ombros: Acima da faixa (esperado), 10/10');
  });

  it('fatia usa o bloco-pai e cita o bloco no tooltip', () => {
    const [result] = build([row(12, 'Peitoral superior', 3)], { slotSequence: 2 });
    expect(result.planning?.valueText).toBe('16/16');
    expect(result.planning?.title).toContain('(bloco Peitoral)');
  });

  it('NOT_EVALUABLE, grupo sem bloco ou sem sessão neste dia: sem indicador', () => {
    const rows = [row(9, 'Trapézio', 4), row(99, 'Antebraço', 3), row(5, 'Bíceps', 3)];
    const result = build(rows, { slotSequence: 3 });
    expect(result.map((r) => r.planning)).toEqual([null, null, null]);
  });

  it('sem preview ou fora do Modo B: sem séries e sem indicador (só os exercícios)', () => {
    const rows = [row(4, 'Quadríceps', 9)];
    expect(build(rows, { evaluation: undefined })).toEqual([{ ...rows[0], sets: null, planning: null }]);
    expect(build(rows, { volumeMode: 'percent_budget' })[0].planning).toBeNull();
  });

  it('séries da linha = direct_sets do backend (o 1º número do "9/12"), não a soma do formulário', () => {
    // O formulário somaria 7; o backend (PRIMARY, com técnica) diz 9.
    const [result] = build([row(4, 'Quadríceps', 7)]);
    expect(result.sets).toBe(9);
    expect(result.sets).toBe(result.planning?.slotSets);
    expect(result.label).toBe('9 séries/1 exercício');
  });

  it('sem sessão do bloco neste dia: linha só com exercícios', () => {
    const [result] = build([row(99, 'Antebraço', 3)], { slotSequence: 3 });
    expect(result.sets).toBeNull();
    expect(result.label).toBe('1 exercício');
  });

  it('linha agregada no grupo principal usa o bloco dele, sem nota de fatia', () => {
    const [result] = build([row(52, 'Peitoral', 16, [12, 52])], { slotSequence: 2 });
    expect(result.planning?.statusCode).toBe('WITHIN_RANGE');
    expect(result.planning?.valueText).toBe('16/16');
    expect(result.planning?.title).not.toContain('(bloco');
  });

  it('subgrupos em blocos diferentes (avaliação defasada): vale o pior estado', () => {
    // Peitoral (na faixa) + Bíceps (abaixo): o aviso de "abaixo" não some.
    const [belowWins] = build([row(52, 'Peitoral', 19, [12, 5])], { slotSequence: 2 });
    expect(belowWins.planning?.statusCode).toBe('BELOW_RANGE');
    expect(belowWins.planning?.title).toContain('(bloco Bíceps)');
    // Desvio esperado (Ombros, informativo) ainda é pior que "na faixa"...
    const [informational] = build([row(52, 'Peitoral', 26, [52, 7])], { slotSequence: 2 });
    expect(informational.planning?.statusCode).toBe('ABOVE_RANGE');
    expect(informational.planning?.isInformational).toBe(true);
    // ...mas um desvio real vence o informativo.
    const [real] = build([row(7, 'Ombros', 13, [7, 5])], { slotSequence: 2 });
    expect(real.planning?.statusCode).toBe('BELOW_RANGE');
  });

  it('ordena pelas séries do backend (sem número por último)', () => {
    const result = build([row(99, 'Outro', 2), row(4, 'Quadríceps', 9)]);
    expect(result.map(({ muscleGroupId, sets }) => ({ muscleGroupId, sets }))).toEqual([
      { muscleGroupId: 4, sets: 9 },
      { muscleGroupId: 99, sets: null },
    ]);
  });
});
