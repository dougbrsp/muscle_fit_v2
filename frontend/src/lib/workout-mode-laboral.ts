// Exercício laboral no Modo Treino (M100). Nenhuma regra numérica aqui:
// séries, reps e carga vêm prontas do backend (`planExercise.laboral`, dono
// `api/app/domain/laboral.py`); o vocabulário da série vem do espelho
// `set-types.ts`. Este módulo só decide o fluxo da tela: o card só leitura
// com "Concluir" (grava as séries `laboral` prescritas) e "Pular" (tira da
// fila sem gravar nada).

import { formatWeight, type WeightUnit } from './weight-units';
import { isLaboralSetType, LABORAL_SET_TYPE } from './set-types';
import type { LaboralPrescription, WorkoutPlanExercise } from './workout-plans-types';

/** Item da ficha que o Modo Treino trata como laboral (flag + padrão pronto). */
export function laboralPrescriptionOf(
  planExercise: Pick<WorkoutPlanExercise, 'is_laboral' | 'laboral'> | null | undefined,
): LaboralPrescription | null {
  if (!planExercise?.is_laboral || !planExercise.laboral) return null;
  return planExercise.laboral;
}

export function isLaboralPlanExercise(
  planExercise: Pick<WorkoutPlanExercise, 'is_laboral' | 'laboral'> | null | undefined,
): boolean {
  return laboralPrescriptionOf(planExercise) != null;
}

/** O laboral está concluído quando o servidor já tem série `laboral` dele. */
export function isLaboralItemDone(loggedSets: ReadonlyArray<{ set_type?: string | null }> | null | undefined): boolean {
  return (loggedSets || []).some((set) => isLaboralSetType(set.set_type));
}

export type LaboralSetRecord = {
  set_number: number;
  set_type: string;
  reps: number;
  weight_kg: number | null;
};

/** As N séries prescritas, a partir do próximo `set_number` físico. */
export function laboralSetRecords(prescription: LaboralPrescription, firstSetNumber: number): LaboralSetRecord[] {
  return Array.from({ length: prescription.sets }, (_unused, index) => ({
    set_number: firstSetNumber + index,
    set_type: LABORAL_SET_TYPE,
    reps: prescription.reps,
    weight_kg: prescription.load_kg,
  }));
}

/** "3 × 15 · 2 kg" (sem carga: "3 × 15"). */
export function laboralSummaryLabel(prescription: LaboralPrescription, unit: WeightUnit): string {
  const base = `${prescription.sets} × ${prescription.reps}`;
  return prescription.load_kg != null ? `${base} · ${formatWeight(prescription.load_kg, unit, { space: true })}` : base;
}
