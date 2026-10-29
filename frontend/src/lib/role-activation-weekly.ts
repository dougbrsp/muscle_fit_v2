import type { PreviewActivationResponse } from '@/lib/workout-plan-templates-types';

/**
 * Fase 7 (Frente A) — janela "Ativação por participação": por grupo, a soma
 * das séries equivalentes de `days[].groups[]` do preview, separada por papel
 * (principal, secundário, estabilizador). O backend já devolve `days[]` na
 * janela de 7 dias da metodologia (`evaluation_window_days()` em
 * `api/app/intelligence/methodology_volume.py`): começa no primeiro slot de
 * treino, dá a volta no ciclo curto e corta o que passa do 7º dia. Aqui é só
 * a soma, sem escala — mesma conta da tabela "Preview de ativação semanal".
 */

export type RoleActivationWeeklyRow = {
  muscleGroup: string;
  primary: number;
  secondary: number;
  stabilizer: number;
  total: number;
};

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

export function buildRoleActivationWeekly(
  response: PreviewActivationResponse | null | undefined,
): RoleActivationWeeklyRow[] {
  if (!response?.days?.length) return [];
  const byGroup = new Map<string, { primary: number; secondary: number; stabilizer: number }>();

  for (const day of response.days) {
    for (const group of day.groups ?? []) {
      const acc = byGroup.get(group.muscle_group) ?? { primary: 0, secondary: 0, stabilizer: 0 };
      acc.primary += group.primary_equivalent || 0;
      acc.secondary += group.secondary_equivalent || 0;
      acc.stabilizer += group.stabilizer_equivalent || 0;
      byGroup.set(group.muscle_group, acc);
    }
  }

  const rows: RoleActivationWeeklyRow[] = [];
  for (const [muscleGroup, acc] of byGroup) {
    // Arredonda só no fim, para a soma dos papéis bater com o total exibido.
    const primary = round1(acc.primary);
    const secondary = round1(acc.secondary);
    const stabilizer = round1(acc.stabilizer);
    const total = round1(primary + secondary + stabilizer);
    if (total <= 0) continue;
    rows.push({ muscleGroup, primary, secondary, stabilizer, total });
  }

  return rows.sort((a, b) => b.total - a.total || a.muscleGroup.localeCompare(b.muscleGroup, 'pt-BR'));
}
