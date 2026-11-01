import { Group, Paper, Text } from '@mantine/core';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { fmtDate, fmtInt } from '@/lib/dashboard-format';
import type { ExerciseProgressionSummary } from '@/lib/dashboard-v2-types';
import { BRAND } from '@/theme';

/**
 * Resumo da progressão por EXERCÍCIO, o mesmo da home. Só exibe o que o
 * backend enviou (`exercise_progression`): nenhuma contagem ou classificação
 * é feita aqui. Sem o campo, não renderiza nada.
 */
export function ExerciseProgressionSummaryCard({ summary }: { summary: ExerciseProgressionSummary | null | undefined }) {
  if (!summary) return null;
  const { counts } = summary;
  const badges = [
    { code: 'PROGRESSING', count: counts.progressing },
    { code: 'STABLE', count: counts.stable },
    { code: 'PLATEAU_SUSPECTED', count: counts.plateau_suspected },
    { code: 'REGRESSING', count: counts.regressing },
  ] as const;
  return (
    <Paper
      withBorder
      radius="lg"
      p="md"
      id="progressao-por-exercicio"
      data-testid="exercise-progression-summary"
      style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
    >
      <Text fw={700} c={BRAND.textPrimary}>
        Progressão por exercício
      </Text>
      <Text size="sm" c={BRAND.textPrimary} mb="xs">
        {summary.answer}
      </Text>
      <Group gap={6} wrap="wrap">
        {badges.map(({ code, count }) => (
          <StatusBadge key={code} domain="exerciseProgression" code={code} size="xs" suffix={` ${count}`} />
        ))}
      </Group>
      <Text size="xs" c={BRAND.textMuted} mt={6}>
        {fmtInt(counts.comparable_total)} exercícios comparáveis, de {fmtDate(summary.period_start)} a{' '}
        {fmtDate(summary.period_end)}.
      </Text>
      {summary.truncated === true && (
        <Text size="xs" c={BRAND.textMuted} mt={4}>
          Leitura parcial: janela muito longa; considere uma janela menor.
        </Text>
      )}
    </Paper>
  );
}
