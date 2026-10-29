'use client';

import { Stack, Table, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { fmtDate, fmtDecimal, fmtInt, fmtKg, fmtKgTotal, fmtPercent, fmtRpe } from '@/lib/dashboard-format';
import type {
  ExerciseProgressionContextItem,
  ExerciseProgressionExposure,
  WorkoutsAnalysisTraining,
} from '@/lib/workouts-analysis-types';
import { BRAND } from '@/theme';

/**
 * Tabelas do SNAPSHOT que a IA recebeu junto com a análise (`training` da
 * resposta de `POST /workouts-analysis/generate`). Mostradas para o usuário
 * conferir em que dados a análise se baseou — não são a leitura "oficial"
 * das outras abas (a prontidão por grupo vigente fica em `/dashboard/recovery`).
 */
export function AnaliseIaSnapshot({ training }: { training: WorkoutsAnalysisTraining }) {
  return (
    <Stack gap="lg">
      {training.weekly_trend.length > 0 && (
        <Secao
          titulo="Tendência semanal (janela móvel)"
          descricao="Janela móvel de 7 dias ancorada em hoje — não é semana de calendário (ISO) como no gráfico de evolução."
        >
          <Table.ScrollContainer minWidth={600}>
            <Table striped withTableBorder verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Período</Table.Th>
                  <Table.Th ta="right">Treinos</Table.Th>
                  <Table.Th ta="right">Séries</Table.Th>
                  <Table.Th ta="right">Volume</Table.Th>
                  <Table.Th ta="right">RPE médio</Table.Th>
                  <Table.Th ta="right">Séries RPE alto</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {training.weekly_trend.map((item) => (
                  <Table.Tr key={item.period}>
                    <Table.Td>{item.period}</Table.Td>
                    <Table.Td ta="right">{fmtInt(item.workouts)}</Table.Td>
                    <Table.Td ta="right">{fmtInt(item.sets_count)}</Table.Td>
                    <Table.Td ta="right">{fmtKgTotal(item.volume_kg)}</Table.Td>
                    <Table.Td ta="right">{fmtRpe(item.avg_rpe)}</Table.Td>
                    <Table.Td ta="right">{fmtInt(item.high_rpe_sets)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Secao>
      )}

      {training.volume_overview.length > 0 && (
        <Secao titulo="Volume por grupo muscular">
          <Table.ScrollContainer minWidth={640}>
            <Table striped withTableBorder verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Grupo muscular</Table.Th>
                  <Table.Th ta="right">Sessões</Table.Th>
                  <Table.Th ta="right">Séries</Table.Th>
                  <Table.Th ta="right">Volume</Table.Th>
                  <Table.Th ta="right">RPE médio</Table.Th>
                  <Table.Th ta="right">Séries equivalentes</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {training.volume_overview.map((item) => (
                  <Table.Tr key={item.muscle_group}>
                    <Table.Td>{item.muscle_group}</Table.Td>
                    <Table.Td ta="right">{fmtInt(item.sessions)}</Table.Td>
                    <Table.Td ta="right">{fmtInt(item.sets_count)}</Table.Td>
                    <Table.Td ta="right">{fmtKgTotal(item.volume_kg)}</Table.Td>
                    <Table.Td ta="right">{fmtRpe(item.avg_rpe)}</Table.Td>
                    <Table.Td ta="right">{fmtDecimal(item.stimulus_equivalent_sets)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Secao>
      )}

      {(training.exercise_progression?.length ?? 0) > 0 && (
        <Secao
          titulo="Status de progressão por exercício"
          descricao="Regra única do app (a mesma da visão geral e do drill-down): compara só execuções do mesmo exercício, no mesmo equipamento e com a mesma convenção de carga. A IA usa este status — não conclui progresso por conta própria."
        >
          <Table.ScrollContainer minWidth={720}>
            <Table striped withTableBorder verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Exercício</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th>Comparado em</Table.Th>
                  <Table.Th ta="right">Anterior</Table.Th>
                  <Table.Th ta="right">Última</Table.Th>
                  <Table.Th ta="right">Reps por série (topo)</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(training.exercise_progression ?? []).map((item) => (
                  <Table.Tr key={item.exercise_id}>
                    <Table.Td>{item.exercise_name ?? `Exercício ${item.exercise_id}`}</Table.Td>
                    <Table.Td>
                      <StatusBadge domain="exerciseProgression" code={item.status} size="xs" />
                    </Table.Td>
                    <Table.Td>{item.equipment_name ?? EM_DASH}</Table.Td>
                    <Table.Td ta="right">{exposicaoResumo(item.previous_comparable)}</Table.Td>
                    <Table.Td ta="right">{exposicaoResumo(item.latest)}</Table.Td>
                    <Table.Td ta="right">
                      {item.paired_top_reps ? repsEmparelhadas(item.paired_top_reps) : EM_DASH}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Secao>
      )}

      {(training.recent_records?.length ?? 0) > 0 && (
        <Secao
          titulo="Recordes de carga (60 dias)"
          descricao="Carga maior que todas as execuções anteriores no mesmo exercício, equipamento e convenção de carga. A primeira execução não conta como recorde; as repetições são só informação."
        >
          <Table.ScrollContainer minWidth={560}>
            <Table striped withTableBorder verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Exercício</Table.Th>
                  <Table.Th>Equipamento</Table.Th>
                  <Table.Th ta="right">Recorde</Table.Th>
                  <Table.Th ta="right">Marca anterior</Table.Th>
                  <Table.Th ta="right">Data</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {(training.recent_records ?? []).map((item) => (
                  <Table.Tr key={`${item.exercise_id}-${item.equipment_id ?? 'x'}-${item.load_convention}`}>
                    <Table.Td>{item.exercise_name ?? `Exercício ${item.exercise_id}`}</Table.Td>
                    <Table.Td>{item.equipment_name ?? EM_DASH}</Table.Td>
                    <Table.Td ta="right">
                      {recordeResumo(item.record_weight_kg, item.reps, item.load_convention)}
                    </Table.Td>
                    <Table.Td ta="right">
                      {recordeResumo(item.previous_best_weight_kg, null, item.load_convention)}
                    </Table.Td>
                    <Table.Td ta="right">{fmtDate(item.local_date)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Secao>
      )}

      {training.recovery.length > 0 && (
        <Secao
          titulo="Recuperação muscular (snapshot da IA)"
          descricao="Como estava no momento da análise. A prontidão vigente, com fatores e sinais, fica em Recuperação."
        >
          <Table.ScrollContainer minWidth={520}>
            <Table striped withTableBorder verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Grupo muscular</Table.Th>
                  <Table.Th ta="right">Recuperação</Table.Th>
                  <Table.Th>Prontidão</Table.Th>
                  <Table.Th ta="right">Horas restantes</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {training.recovery.map((item) => (
                  <Table.Tr key={item.muscle_group}>
                    <Table.Td>{item.muscle_group}</Table.Td>
                    <Table.Td ta="right">{fmtPercent(item.recovery_pct, 0)}</Table.Td>
                    <Table.Td>
                      <StatusBadge domain="readiness" code={item.readiness_status} size="xs" />
                    </Table.Td>
                    <Table.Td ta="right">{`${fmtInt(item.remaining_hours)} h`}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Secao>
      )}
    </Stack>
  );
}

const EM_DASH = '—';

/** "40 kg · 14+13" — carga de topo e reps das séries de topo da exposição. */
export function exposicaoResumo(exposure: ExerciseProgressionExposure | null | undefined): string {
  if (!exposure) return EM_DASH;
  const carga = exposure.best_load_kg != null ? fmtKg(exposure.best_load_kg) : EM_DASH;
  const reps = exposure.top_set_reps.length > 0 ? exposure.top_set_reps.join('+') : EM_DASH;
  return `${carga} · ${reps}`;
}

/**
 * "11+10 → 14+13" — as reps emparelhadas que a regra comparou, prontas do
 * backend. Com `effort_adjusted` (M78) os números são reps + RIR e o texto diz.
 */
export function repsEmparelhadas(paired: ExerciseProgressionContextItem['paired_top_reps']): string {
  if (!paired) return EM_DASH;
  const lado = (values: readonly number[]) => values.map((value) => fmtDecimal(value, 1)).join('+');
  const texto = `${lado(paired.previous)} → ${lado(paired.current)}`;
  return paired.effort_adjusted ? `${texto} (reps + RIR)` : texto;
}

/** "82,5 kg × 6" — carga do recorde (já decidida no backend) e reps só informativas. */
export function recordeResumo(weightKg: number | null | undefined, reps: number | null | undefined, convention: string): string {
  if (weightKg == null) return EM_DASH;
  const carga = convention === 'assisted' ? `${fmtKg(weightKg)} de assistência` : fmtKg(weightKg);
  return reps != null && reps > 0 ? `${carga} × ${reps}` : carga;
}

function Secao({ titulo, descricao, children }: { titulo: string; descricao?: string; children: ReactNode }) {
  return (
    <Stack gap={6}>
      <Title order={5} c={BRAND.textPrimary}>
        {titulo}
      </Title>
      {descricao && (
        <Text size="xs" c={BRAND.textMuted}>
          {descricao}
        </Text>
      )}
      {children}
    </Stack>
  );
}
