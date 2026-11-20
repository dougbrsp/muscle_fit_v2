import { describe, expect, it } from 'vitest';
import { rpePayloadFields, rpePerSetPatch, rpePerSetView } from './plan-rpe-per-set';

describe('RPE por série na linha da ficha (#184)', () => {
  it('linha só com texto (faixa) mostra "Alvo gravado" e envia o texto', () => {
    const row = { rpe_target: '7-8', rpe_targets_by_set: [], sets_count: 3 };
    expect(rpePerSetView(row)).toEqual({ targets: [], storedText: '7-8' });
    expect(rpePayloadFields(row)).toEqual({ rpe_target: '7-8' });
  });

  it('linha com lista devolvida pela API mostra a lista e, sem mexer, reenvia o texto', () => {
    const row = { rpe_target: '8', rpe_targets_by_set: [8, 8, 8], sets_count: 3 };
    expect(rpePerSetView(row)).toEqual({ targets: [8, 8, 8], storedText: null });
    expect(rpePayloadFields(row)).toEqual({ rpe_target: '8' });
  });

  it('definir por série envia a lista e não o texto', () => {
    const row = { rpe_target: '7-8', sets_count: 3, ...rpePerSetPatch([7, 8, 9]) };
    expect(rpePayloadFields(row)).toEqual({ rpe_targets_by_set: [7, 8, 9] });
    expect(rpePerSetView(row)).toEqual({ targets: [7, 8, 9], storedText: null });
  });

  it('mudar sets_count ajusta a lista enviada', () => {
    const base = rpePerSetPatch([7, 8, 9]);
    expect(rpePayloadFields({ ...base, sets_count: 2 }).rpe_targets_by_set).toEqual([7, 8]);
    expect(rpePayloadFields({ ...base, sets_count: 5 }).rpe_targets_by_set).toEqual([7, 8, 9, 9, 9]);
  });

  it('limpar tudo envia lista vazia (sem alvo)', () => {
    expect(rpePayloadFields({ sets_count: 3, ...rpePerSetPatch([]) })).toEqual({ rpe_targets_by_set: [] });
  });

  it('linha nova sem RPE continua enviando rpe_target null', () => {
    expect(rpePayloadFields({ sets_count: 3 })).toEqual({ rpe_target: null });
    expect(rpePerSetView({ sets_count: 3 })).toEqual({ targets: [], storedText: null });
  });
});
