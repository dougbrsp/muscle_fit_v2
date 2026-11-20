import { describe, expect, it } from 'vitest';
import { loadConventionLabel } from './equipment-exercises-types';
import { equipmentChangeConfirmation, equipmentChangeForSave } from './equipment-change-confirm';

describe('equipmentChangeConfirmation', () => {
  it('não pede confirmação com equipamento inalterado', () => {
    expect(equipmentChangeConfirmation({ previousLinkId: 5, nextLinkId: 5, loggedSetsCount: 3 })).toBeNull();
  });

  it('não pede confirmação sem séries', () => {
    expect(equipmentChangeConfirmation({ previousLinkId: 5, nextLinkId: 6, loggedSetsCount: 0 })).toBeNull();
  });

  it('pede confirmação ao mudar com séries e diz que todas passam para o novo', () => {
    const result = equipmentChangeConfirmation({ previousLinkId: 5, nextLinkId: 6, loggedSetsCount: 3 });
    expect(result?.message).toContain('Todas as 3 séries');
    expect(result?.message).toContain('histórico, o recorde e a progressão');
    expect(result?.conventionWarning).toBeNull();
  });

  it('1 série ao mudar: texto no singular e equipamento antes indefinido', () => {
    const result = equipmentChangeConfirmation({ previousLinkId: null, nextLinkId: 6, loggedSetsCount: 1 });
    expect(result?.message).toBe(
      'A série deste exercício passa para o novo equipamento, e o histórico, o recorde e a progressão do exercício passam a contar nele.',
    );
  });

  it('várias séries ao mudar: texto no plural', () => {
    const result = equipmentChangeConfirmation({ previousLinkId: 5, nextLinkId: 6, loggedSetsCount: 3 });
    expect(result?.message).toBe(
      'Todas as 3 séries deste exercício passam para o novo equipamento, e o histórico, o recorde e a progressão do exercício passam a contar nele.',
    );
  });

  it('limpar o equipamento com 1 série: texto no singular', () => {
    const result = equipmentChangeConfirmation({ previousLinkId: 5, nextLinkId: null, loggedSetsCount: 1 });
    expect(result?.message).toBe(
      'A série deste exercício fica sem equipamento, e o histórico, o recorde e a progressão do exercício deixam de contar no equipamento atual.',
    );
  });

  it('limpar o equipamento com várias séries: texto no plural', () => {
    const result = equipmentChangeConfirmation({ previousLinkId: 5, nextLinkId: null, loggedSetsCount: 2 });
    expect(result?.message).toBe(
      'Todas as 2 séries deste exercício ficam sem equipamento, e o histórico, o recorde e a progressão do exercício deixam de contar no equipamento atual.',
    );
  });

  it('avisa a convenção só quando conhecidas e diferentes', () => {
    const base = { previousLinkId: 5, nextLinkId: 6, loggedSetsCount: 2 };
    expect(
      equipmentChangeConfirmation({ ...base, previousConvention: 'total', nextConvention: 'per_side' })?.conventionWarning,
    ).toBe(`Neste equipamento a carga é registrada: ${loadConventionLabel('per_side')}.`);
    expect(
      equipmentChangeConfirmation({ ...base, previousConvention: 'total', nextConvention: 'total' })?.conventionWarning,
    ).toBeNull();
    expect(
      equipmentChangeConfirmation({ ...base, previousConvention: null, nextConvention: 'per_side' })?.conventionWarning,
    ).toBeNull();
  });
});

describe('equipmentChangeForSave', () => {
  const links = [
    { id: 5, load_convention: 'total' as const },
    { id: 6, load_convention: 'per_side' as const },
  ];
  it('lançamento novo não confirma', () => {
    expect(equipmentChangeForSave({ row: null, formEquipmentId: '6', links })).toBeNull();
  });
  it('edição com troca e convenções conhecidas confirma com aviso', () => {
    const result = equipmentChangeForSave({ row: { equipment_id: 5, sets: [{}, {}] }, formEquipmentId: '6', links });
    expect(result?.message).toContain('Todas as 2 séries');
    expect(result?.conventionWarning).not.toBeNull();
  });
  it('mesmo equipamento (string do formulário) não confirma', () => {
    expect(equipmentChangeForSave({ row: { equipment_id: 5, sets: [{}] }, formEquipmentId: '5', links })).toBeNull();
  });
  it('convenção do equipamento antigo fora da lista = sem aviso de convenção', () => {
    const result = equipmentChangeForSave({ row: { equipment_id: 99, sets: [{}] }, formEquipmentId: '6', links });
    expect(result?.conventionWarning).toBeNull();
  });
});
