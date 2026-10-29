import type { MuscleGroupItem } from '@/lib/exercise-catalog-admin-types';
import type { MethodologyFrequencyBucket } from '@/lib/methodology-volume-types';
import motorConstants from '@/lib/motor-constants.generated.json';
import { isDefaultCountingMode, UNCLASSIFIED_VOLUME_CLASS_LABEL } from '@/lib/volume-counting-mode';
import type {
  VolumeClassItem,
  VolumeClassNumber,
  VolumeClassRange,
  VolumeClassRangePayload,
} from '@/lib/volume-classes-types';

/**
 * Lógica PURA da aba "Macro-grupos de volume" (Fase 6, PR 3): formatação,
 * rascunho das faixas e opções de grupos. Fora do componente para ser
 * testável sem DOM.
 */

export const VOLUME_CLASSES_QUERY_KEY = ['muscle-volume-classes'] as const;

/**
 * Modo de contagem (073, Lote 3): o modo default usa a chave e a rota de
 * sempre (o mesmo cache da aba de grupos); os outros, `?counting_mode=`.
 */
export function volumeClassesQueryKey(countingMode: string): readonly string[] {
  return isDefaultCountingMode(countingMode) ? VOLUME_CLASSES_QUERY_KEY : [...VOLUME_CLASSES_QUERY_KEY, countingMode];
}

/** Sufixo `?counting_mode=` das rotas do CRUD (vazio no modo default). */
export function countingModeQuery(countingMode: string): string {
  return isDefaultCountingMode(countingMode) ? '' : `?counting_mode=${encodeURIComponent(countingMode)}`;
}

/** No modo ponderado, o que ainda falta e o botão "copiar" pode preencher. */
export function missingInMode(item: VolumeClassItem): { buckets: number; weekly: boolean } {
  const present = new Set(item.ranges.map((range) => range.frequency_bucket));
  return {
    buckets: FREQUENCY_BUCKETS.filter((bucket) => !present.has(bucket)).length,
    weekly: item.weekly_sets_min == null && item.weekly_sets_max == null,
  };
}

// Ordem e valores do motor (`methodology_volume.FREQUENCY_BUCKETS`), via o JSON gerado.
export const FREQUENCY_BUCKETS: readonly MethodologyFrequencyBucket[] = motorConstants.methodology_volume
  .frequency_buckets as MethodologyFrequencyBucket[];

export const FREQUENCY_BUCKET_LABEL: Record<MethodologyFrequencyBucket, string> = {
  '1x': '1x/semana',
  '2x': '2x/semana',
  '3x_plus': '3x+/semana',
};

/** DECIMAL do cadastro -> número (a Data API pode devolver string). */
export function toNumber(value: VolumeClassNumber | undefined): number | null {
  if (value == null || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

/** "12", "12,5" ou "—": vírgula decimal pt-BR, inteiro quando inteiro. */
export function formatDecimal(value: VolumeClassNumber | undefined): string {
  const number = toNumber(value);
  if (number == null) return '—';
  return number.toLocaleString('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 1 });
}

/** "9–16", "12,5" (mín = máx) ou "—". */
export function formatDecimalRange(min: VolumeClassNumber | undefined, max: VolumeClassNumber | undefined): string {
  const low = toNumber(min);
  const high = toNumber(max);
  if (low == null || high == null) return '—';
  return low === high ? formatDecimal(low) : `${formatDecimal(low)}–${formatDecimal(high)}`;
}

export function sortVolumeClasses(classes: VolumeClassItem[]): VolumeClassItem[] {
  return [...classes].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, 'pt-BR'));
}

// ---------------------------------------------------------------------------
// Rascunho das faixas (tabela 1x / 2x / 3x+)
// ---------------------------------------------------------------------------

/** Valor de um `NumberInput` do Mantine: número, ou string vazia quando limpo. */
export type DraftNumber = number | string;

export type VolumeClassRangeDraft = {
  frequency_bucket: MethodologyFrequencyBucket;
  sets_min: DraftNumber;
  sets_max: DraftNumber;
  exercises_min: DraftNumber;
  exercises_max: DraftNumber;
};

function draftValue(value: VolumeClassNumber | undefined): DraftNumber {
  const number = toNumber(value);
  return number == null ? '' : number;
}

export function parseDraftNumber(value: DraftNumber): number | null {
  if (value === '' || value == null) return null;
  const number = typeof value === 'number' ? value : Number(String(value).replace(',', '.'));
  return Number.isFinite(number) ? number : null;
}

/** Sempre as três frequências; frequência sem linha no cadastro vem vazia. */
export function rangeDrafts(ranges: VolumeClassRange[]): VolumeClassRangeDraft[] {
  return FREQUENCY_BUCKETS.map((bucket) => {
    const row = ranges.find((item) => item.frequency_bucket === bucket);
    return {
      frequency_bucket: bucket,
      sets_min: draftValue(row?.sets_min),
      sets_max: draftValue(row?.sets_max),
      exercises_min: draftValue(row?.exercises_min),
      exercises_max: draftValue(row?.exercises_max),
    };
  });
}

/**
 * Corpo de `PUT /{id}/ranges`. Linha inteiramente vazia sai do payload (o
 * backend apaga a linha: aquela frequência fica sem faixa). Linha parcial vai
 * como está — a validação de par mínimo/máximo é do backend (422 com a
 * mensagem, que a tela mostra).
 */
export function buildRangesPayload(drafts: VolumeClassRangeDraft[]): VolumeClassRangePayload[] {
  return drafts
    .map((draft) => ({
      frequency_bucket: draft.frequency_bucket,
      sets_min: parseDraftNumber(draft.sets_min),
      sets_max: parseDraftNumber(draft.sets_max),
      exercises_min: parseDraftNumber(draft.exercises_min),
      exercises_max: parseDraftNumber(draft.exercises_max),
    }))
    .filter((row) => [row.sets_min, row.sets_max, row.exercises_min, row.exercises_max].some((v) => v != null))
    .map((row) => ({ ...row, sets_min: row.sets_min as number, sets_max: row.sets_max as number }));
}

export function sameRanges(drafts: VolumeClassRangeDraft[], ranges: VolumeClassRange[]): boolean {
  return JSON.stringify(buildRangesPayload(drafts)) === JSON.stringify(buildRangesPayload(rangeDrafts(ranges)));
}

// ---------------------------------------------------------------------------
// Grupos
// ---------------------------------------------------------------------------

/** Fatia (`methodology_parent_group_id`) soma no pai e usa o macro-grupo dele. */
export function isSliceGroup(group: MuscleGroupItem): boolean {
  return group.methodology_parent_group_id != null;
}

/** `grupo -> macro-grupo`, a partir do cadastro (fonte de verdade da associação). */
export function classByGroupId(classes: VolumeClassItem[]): Map<number, VolumeClassItem> {
  const map = new Map<number, VolumeClassItem>();
  for (const item of classes) for (const id of item.muscle_group_ids) map.set(id, item);
  return map;
}

/**
 * Opções do `MultiSelect` de um macro-grupo: só grupos não-fatia. Grupo que
 * está em OUTRO macro-grupo leva "(em <outro>)" — salvar o move.
 */
export function groupOptionsFor(
  classId: number,
  groups: MuscleGroupItem[],
  classes: VolumeClassItem[],
): Array<{ value: string; label: string }> {
  const owner = classByGroupId(classes);
  return groups
    .filter((group) => !isSliceGroup(group))
    .sort((a, b) => a.name.localeCompare(b.name, 'pt-BR'))
    .map((group) => {
      const other = owner.get(group.id);
      const suffix = other && other.id !== classId ? ` (em ${other.name})` : '';
      return { value: String(group.id), label: `${group.name}${suffix}` };
    });
}

/** Grupos não-fatia sem macro-grupo: não são avaliados pela metodologia. */
export function unassignedGroups(groups: MuscleGroupItem[], classes: VolumeClassItem[]): MuscleGroupItem[] {
  const owner = classByGroupId(classes);
  return groups
    .filter((group) => !isSliceGroup(group) && !owner.has(group.id))
    .sort((a, b) => a.name.localeCompare(b.name, 'pt-BR'));
}

/**
 * Nome do macro-grupo de um grupo para a coluna só-leitura de "Grupos
 * musculares". Fatia usa o do pai (é avaliada no bloco dele).
 */
export function volumeClassLabelForGroup(
  group: MuscleGroupItem,
  groupsById: ReadonlyMap<number, MuscleGroupItem>,
  classes: VolumeClassItem[],
): string {
  const owner = classByGroupId(classes);
  if (isSliceGroup(group)) {
    const parent = groupsById.get(group.methodology_parent_group_id as number);
    const parentClass = parent ? owner.get(parent.id) : undefined;
    return parentClass ? `${parentClass.name} (do grupo pai)` : 'Do grupo pai';
  }
  return owner.get(group.id)?.name ?? UNCLASSIFIED_VOLUME_CLASS_LABEL;
}
