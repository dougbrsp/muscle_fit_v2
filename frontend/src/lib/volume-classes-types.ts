import type { MethodologyFrequencyBucket, MethodologyRangeSource } from '@/lib/methodology-volume-types';

/**
 * Contrato de `/muscle-volume-classes` (Fase 6, migração 057 —
 * `api/app/muscle_volume_classes.py`): macro-grupos de volume editáveis.
 *
 * Um macro-grupo reúne grupos musculares que dividem as mesmas metas: séries
 * (e, opcionalmente, exercícios) POR TREINO para cada frequência semanal
 * (1x / 2x / 3x+) e uma meta SEMANAL. Os números são DECIMAL(5,1) no banco —
 * podem ser fracionários (12,5). PyMySQL devolve número; a Data API pode
 * devolver string, então a UI sempre passa por `Number(...)`.
 */

export type VolumeClassNumber = number | string | null;

export type VolumeClassRange = {
  frequency_bucket: MethodologyFrequencyBucket;
  sets_min: VolumeClassNumber;
  sets_max: VolumeClassNumber;
  exercises_min: VolumeClassNumber;
  exercises_max: VolumeClassNumber;
  sets_source: MethodologyRangeSource | null;
  sets_evidence: string | null;
  exercises_source: MethodologyRangeSource | null;
  exercises_evidence: string | null;
  /** 073: modo de onde a linha foi copiada (null = valor próprio do modo). */
  copied_from_counting_mode?: string | null;
  /** 073: cópia ainda não calibrada (a API deriva de `copied_from_counting_mode`). */
  uncalibrated?: boolean;
};

export type VolumeClassItem = {
  id: number;
  name: string;
  description: string | null;
  sort_order: number;
  weekly_sets_min: VolumeClassNumber;
  weekly_sets_max: VolumeClassNumber;
  weekly_source: MethodologyRangeSource | null;
  weekly_evidence: string | null;
  /** 073: modo de contagem das faixas e da meta semanal desta resposta. */
  counting_mode?: string;
  weekly_copied_from_counting_mode?: string | null;
  weekly_uncalibrated?: boolean;
  /** Só as frequências com linha no cadastro, na ordem 1x, 2x, 3x+. */
  ranges: VolumeClassRange[];
  /** Grupos musculares associados (nunca fatias). */
  muscle_group_ids: number[];
  created_at?: string;
  updated_at?: string;
};

export type VolumeClassListResponse = { items: VolumeClassItem[]; counting_mode?: string };
export type VolumeClassResponse = { status: 'ok'; item: VolumeClassItem };

/** `POST` (criação) e `PUT /{id}` (edição parcial: campo ausente preserva). */
export type VolumeClassUpsertPayload = {
  name?: string;
  description?: string | null;
  sort_order?: number;
  weekly_sets_min?: number | null;
  weekly_sets_max?: number | null;
};

/**
 * `PUT /{id}/ranges`: substitui TODAS as linhas. Frequência ausente fica sem
 * linha (`NO_RANGE_FOR_FREQUENCY` no motor). Valor igual ao atual preserva a
 * origem; valor novo grava `admin`.
 */
export type VolumeClassRangePayload = {
  frequency_bucket: MethodologyFrequencyBucket;
  sets_min: number;
  sets_max: number;
  exercises_min: number | null;
  exercises_max: number | null;
};

/** `PUT /{id}/groups`: substitui o conjunto; grupo que está em outro macro-grupo é MOVIDO. */
export type VolumeClassGroupsPayload = { muscle_group_ids: number[] };
