// Resumo SOMENTE LEITURA de uma versão do Modelo (histórico de versões).
// Função pura: transforma `GET /workout-plan-templates/{id}/versions/{vid}`
// nas linhas que o cartão de cada slot mostra — sem nenhum controle do
// editor. Reaproveita os rótulos que já existem: técnica avançada
// (`getAdvancedTechniqueLabel`), descanso (`formatRestSeconds`), modo do
// Modelo (`VOLUME_MODE_OPTIONS`) e dia da semana (`WEEKDAY_OPTIONS`).

import { getAdvancedTechniqueLabel } from './advanced-techniques';
import { formatRestSeconds } from './template-time-estimate';
import { versionLabel, versionStartLabel, versionSummaryText } from './template-versions';
import {
  VOLUME_MODE_OPTIONS,
  WEEKDAY_OPTIONS,
  type WorkoutPlanTemplateVersionDetail,
  type WorkoutPlanTemplateVersionSlotItem,
} from './workout-plan-templates-types';

export type VersionSummaryItem = {
  key: string;
  /** Exercício (Modo B) ou grupo muscular (Modo A); "Cardio" no cardio. */
  title: string;
  /** Equipamento (Modo B) ou o grupo do exercício. */
  subtitle: string | null;
  /** "3 × 8-10", "RPE 8", "Descanso 1min 30s", "Técnica: Drop-set"... na ordem. */
  details: string[];
};

export type VersionSummarySlot = {
  key: string;
  title: string;
  kindLabel: 'Treino' | 'Descanso';
  weekdayLabel: string | null;
  isRest: boolean;
  items: VersionSummaryItem[];
};

export type VersionSummary = {
  title: string;
  modeLabel: string;
  startLabel: string;
  authorLabel: string;
  changeLabel: string;
  slots: VersionSummarySlot[];
};

function weekday(value: number | null): string | null {
  if (value == null) return null;
  return WEEKDAY_OPTIONS.find((option) => option.value === String(value))?.label ?? null;
}

function setsAndReps(item: WorkoutPlanTemplateVersionSlotItem): string | null {
  const reps = item.reps_target?.trim();
  if (item.sets_count != null && reps) return `${item.sets_count} × ${reps}`;
  if (item.sets_count != null) return `${item.sets_count} ${item.sets_count === 1 ? 'série' : 'séries'}`;
  if (reps) return `${reps} reps`;
  return null;
}

function techniqueDetail(item: WorkoutPlanTemplateVersionSlotItem): string | null {
  const label = getAdvancedTechniqueLabel(item.advanced_technique);
  if (!label) return null;
  const extras: string[] = [];
  if (item.advanced_technique_applications != null) extras.push(`${item.advanced_technique_applications}×`);
  if (item.advanced_technique_duration_seconds != null) {
    extras.push(formatRestSeconds(item.advanced_technique_duration_seconds));
  }
  return extras.length ? `Técnica: ${label} (${extras.join(', ')})` : `Técnica: ${label}`;
}

function summarizeItem(item: WorkoutPlanTemplateVersionSlotItem, index: number): VersionSummaryItem {
  const key = `${item.item_order}-${index}`;
  if (item.item_kind === 'cardio') {
    return {
      key,
      title: 'Cardio',
      subtitle: null,
      details: item.cardio_duration_minutes != null ? [`${item.cardio_duration_minutes} min`] : [],
    };
  }
  const group = item.muscle_group_name?.trim() || null;
  if (item.item_kind === 'muscle_group') {
    // Modo A: o motor escolhe exercício e equipamento ao gerar a ficha.
    const details: string[] = [];
    if (item.priority_min_pct != null && item.priority_max_pct != null) {
      details.push(`${item.priority_min_pct}–${item.priority_max_pct}% da meta semanal`);
    }
    if (item.prioritize) details.push('Priorizado');
    return { key, title: group ?? 'Grupo muscular', subtitle: 'Exercício escolhido pelo motor', details };
  }
  const details = [
    setsAndReps(item),
    item.rpe_target?.trim() ? `RPE ${item.rpe_target.trim()}` : null,
    techniqueDetail(item),
    item.rest_seconds != null ? `Descanso ${formatRestSeconds(item.rest_seconds)}` : null,
  ].filter((value): value is string => Boolean(value));
  const equipment = item.equipment_name?.trim() || null;
  return {
    key,
    title: item.exercise_name?.trim() || group || 'Exercício',
    subtitle: [equipment, group].filter(Boolean).join(' · ') || null,
    details,
  };
}

export function buildVersionSummary(detail: WorkoutPlanTemplateVersionDetail): VersionSummary {
  const version = detail.version;
  const slots = [...detail.slots].sort((a, b) => a.sequence - b.sequence);
  return {
    title: `${versionLabel(version.number) ?? 'Versão'} · ${detail.name}`,
    modeLabel: VOLUME_MODE_OPTIONS.find((option) => option.value === detail.volume_mode)?.label ?? detail.volume_mode,
    startLabel: versionStartLabel(version.effective_from),
    authorLabel: version.author_name?.trim() || 'Autor não registrado',
    changeLabel: versionSummaryText({
      number: version.number,
      effective_from: version.effective_from,
      reverted_from_number: version.reverted_from_number ?? null,
      change_summary: version.change_summary,
    }),
    slots: slots.map((slot) => {
      const isRest = slot.slot_type === 'descanso';
      const items = [...(slot.items as WorkoutPlanTemplateVersionSlotItem[])].sort(
        (a, b) => a.item_order - b.item_order,
      );
      return {
        key: `${slot.sequence}`,
        title: slot.name?.trim() || `Slot ${slot.sequence}`,
        kindLabel: isRest ? 'Descanso' : 'Treino',
        weekdayLabel: weekday(slot.weekday),
        isRest,
        // Descanso mostra só "Descanso" (um cardio opcional ainda aparece).
        items: (isRest ? items.filter((item) => item.item_kind === 'cardio') : items).map(summarizeItem),
      };
    }),
  };
}
