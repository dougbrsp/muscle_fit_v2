'use client';

import { StatusBadge } from '@/components/dashboard/StatusBadge';
import {
  exposicaoResumo,
  recordeResumo,
  repsEmparelhadas,
} from '@/app/(app)/dashboard/treinos/AnaliseIaSnapshot';
import { EM_DASH, fmtDate, fmtDecimal, fmtInt, fmtKgTotal, fmtPercent, fmtRpe } from '@/lib/dashboard-format';
import type { WorkoutsAnalysisTraining } from '@/lib/workouts-analysis-types';
import { ListGroup, ListRow } from '../../ui';

/**
 * Snapshot que a IA recebeu (mesmos dados das tabelas do desktop), em listas:
 * 1 dado principal por linha e os demais no subtítulo. Textos auxiliares das
 * funções puras são os do desktop (importados, não copiados).
 */
export function AnaliseIaSnapshotMobile({ training }: { training: WorkoutsAnalysisTraining }) {
  return (
    <>
      {training.weekly_trend.length > 0 ? (
        <ListGroup
          title="Tendência semanal (janela móvel)"
          footer="Janela móvel de 7 dias ancorada em hoje; não é semana de calendário (ISO)."
        >
          {training.weekly_trend.map((i) => (
            <ListRow
              key={i.period}
              title={i.period}
              subtitle={`${fmtInt(i.workouts)} treinos · ${fmtInt(i.sets_count)} séries · RPE ${fmtRpe(i.avg_rpe)} · RPE alto ${fmtInt(i.high_rpe_sets)}`}
              value={fmtKgTotal(i.volume_kg)}
            />
          ))}
        </ListGroup>
      ) : null}

      {training.volume_overview.length > 0 ? (
        <ListGroup title="Volume por grupo muscular">
          {training.volume_overview.map((i) => (
            <ListRow
              key={i.muscle_group}
              title={i.muscle_group}
              subtitle={`${fmtInt(i.sessions)} sessões · ${fmtInt(i.sets_count)} séries · RPE ${fmtRpe(i.avg_rpe)} · ${fmtDecimal(i.stimulus_equivalent_sets)} equiv.`}
              value={fmtKgTotal(i.volume_kg)}
            />
          ))}
        </ListGroup>
      ) : null}

      {(training.exercise_progression?.length ?? 0) > 0 ? (
        <ListGroup
          title="Progressão por exercício"
          footer="Regra única do app: compara só execuções do mesmo exercício, equipamento e convenção de carga."
        >
          {(training.exercise_progression ?? []).map((i) => (
            <ListRow
              key={i.exercise_id}
              title={i.exercise_name ?? `Exercício ${i.exercise_id}`}
              subtitle={`${i.equipment_name ?? EM_DASH} · anterior ${exposicaoResumo(i.previous_comparable)} · última ${exposicaoResumo(i.latest)}${i.paired_top_reps ? ` · ${repsEmparelhadas(i.paired_top_reps)}` : ''}`}
              trailing={<StatusBadge domain="exerciseProgression" code={i.status} size="xs" />}
            />
          ))}
        </ListGroup>
      ) : null}

      {(training.recent_records?.length ?? 0) > 0 ? (
        <ListGroup
          title="Recordes de carga (60 dias)"
          footer="Carga maior que todas as execuções anteriores no mesmo exercício, equipamento e convenção de carga."
        >
          {(training.recent_records ?? []).map((i) => (
            <ListRow
              key={`${i.exercise_id}-${i.equipment_id ?? 'x'}-${i.load_convention}`}
              title={i.exercise_name ?? `Exercício ${i.exercise_id}`}
              subtitle={`${i.equipment_name ?? EM_DASH} · ${fmtDate(i.local_date)} · antes ${recordeResumo(i.previous_best_weight_kg, null, i.load_convention)}`}
              value={recordeResumo(i.record_weight_kg, i.reps, i.load_convention)}
            />
          ))}
        </ListGroup>
      ) : null}

      {training.recovery.length > 0 ? (
        <ListGroup title="Recuperação muscular (snapshot)" footer="Como estava no momento da análise. A prontidão vigente fica em Recuperação.">
          {training.recovery.map((i) => (
            <ListRow
              key={i.muscle_group}
              title={i.muscle_group}
              subtitle={`${fmtPercent(i.recovery_pct, 0)} · ${fmtInt(i.remaining_hours)} h restantes`}
              trailing={<StatusBadge domain="readiness" code={i.readiness_status} size="xs" />}
            />
          ))}
        </ListGroup>
      ) : null}
    </>
  );
}
