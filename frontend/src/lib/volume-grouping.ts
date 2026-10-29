/**
 * Agrupamento das tabelas de `/volume-grupamentos` por macro-grupo, grupo pai
 * ou grupo (pedido do usuário, 2026-10-17).
 *
 * Nenhuma regra mora aqui. Quem decide o pai de cada grupo (a fatia soma no
 * pai) e o macro-grupo (a classe do bloco; a fatia herda a do pai; sem classe =
 * o balde do dono) é o backend, `methodology_volume.resolve_group_hierarchy`,
 * que devolve por linha `parent_group_*` e `volume_class_*` já resolvidos
 * (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.5 e §50). Esta lib só escolhe a
 * chave da linha conforme o nível e soma números que a API já entrega — soma
 * por chave é apresentação (veredito do guardião dos motores): volume, séries e
 * repetições vêm de uma partição (cada exercício cai num grupo só), e as séries
 * equivalentes por pai/macro são a soma dos grupos, a mesma do realizado e do
 * relatório.
 *
 * A preferência fica no `localStorage` (por navegador). Leitura e escrita em
 * `try/catch`, com o storage injetado (mesmo padrão de `navbar-collapse.ts`):
 * sem storage, a página usa o padrão (macro-grupo).
 */

import type { PreferenceStorage } from './navbar-collapse';

export type VolumeGroupingLevel = 'volume_class' | 'parent_group' | 'muscle_group';

export const DEFAULT_VOLUME_GROUPING_LEVEL: VolumeGroupingLevel = 'volume_class';

export const VOLUME_GROUPING_STORAGE_KEY = 'musclefit:volume-grupamentos:agrupar-por';

/** Rótulo curto do nível (seletor "Agrupar por" e cabeçalho da coluna). */
export const VOLUME_GROUPING_LABELS: Record<VolumeGroupingLevel, string> = {
  volume_class: 'Macro-grupo',
  parent_group: 'Grupo pai',
  muscle_group: 'Grupo',
};

/** Opções do seletor, na ordem em que aparecem. */
export const VOLUME_GROUPING_OPTIONS: Array<{ value: VolumeGroupingLevel; label: string }> = (
  ['volume_class', 'parent_group', 'muscle_group'] as const
).map((value) => ({ value, label: VOLUME_GROUPING_LABELS[value] }));

/**
 * Campos que o backend devolve por linha. Opcionais aqui só para a janela de
 * deploy (tela nova com API antiga): sem eles a linha fica no próprio grupo.
 */
export type GroupableVolumeRow = {
  muscle_group: string;
  parent_group_id?: number | null;
  parent_group_name?: string;
  volume_class_id?: number | null;
  volume_class_name?: string;
  volume_class_order?: number | null;
};

/** Chave de agrupamento de uma linha: identidade, rótulo e ordem do cadastro. */
export type VolumeGroup = {
  key: string;
  label: string;
  /** Posição do macro-grupo no cadastro; `null` fora do nível macro e no balde sem macro-grupo. */
  order: number | null;
};

export function isVolumeGroupingLevel(value: unknown): value is VolumeGroupingLevel {
  return value === 'volume_class' || value === 'parent_group' || value === 'muscle_group';
}

function ownGroup(row: GroupableVolumeRow): VolumeGroup {
  // Mesmo fallback que a tela já usava para linha sem nome.
  const name = row.muscle_group || 'Outros';
  return { key: `grupo:${name}`, label: name, order: null };
}

/** Onde a linha cai no nível pedido. Sem campo da API, cai no próprio grupo. */
export function groupingKeyOf(row: GroupableVolumeRow, level: VolumeGroupingLevel): VolumeGroup {
  if (level === 'parent_group' && row.parent_group_name) {
    const key = row.parent_group_id != null ? `pai:${row.parent_group_id}` : `pai:${row.parent_group_name}`;
    return { key, label: row.parent_group_name, order: null };
  }
  if (level === 'volume_class' && row.volume_class_name) {
    const key = row.volume_class_id != null ? `macro:${row.volume_class_id}` : 'macro:sem-classe';
    return { key, label: row.volume_class_name, order: row.volume_class_order ?? null };
  }
  return ownGroup(row);
}

/**
 * Ordem das linhas: macro-grupos na ordem do cadastro (o balde sem macro-grupo
 * por último); nos outros níveis, alfabética, como a tela sempre fez.
 */
export function compareVolumeGroups(left: VolumeGroup, right: VolumeGroup): number {
  if (left.order !== right.order) {
    if (left.order == null) return 1;
    if (right.order == null) return -1;
    return left.order - right.order;
  }
  return left.label.localeCompare(right.label);
}

/** Soma `valueOf` por chave do nível, já ordenado. */
export function sumByVolumeGroup<T extends GroupableVolumeRow>(
  rows: readonly T[],
  level: VolumeGroupingLevel,
  valueOf: (row: T) => number,
): Array<{ group: VolumeGroup; total: number }> {
  const totals = new Map<string, { group: VolumeGroup; total: number }>();
  for (const row of rows) {
    const group = groupingKeyOf(row, level);
    const current = totals.get(group.key) ?? { group, total: 0 };
    current.total += Number(valueOf(row) || 0);
    totals.set(group.key, current);
  }
  return Array.from(totals.values()).sort((left, right) => compareVolumeGroups(left.group, right.group));
}

/** Lê a preferência; ausente, inválida ou storage indisponível = o padrão. */
export function readVolumeGroupingLevel(storage: PreferenceStorage | null | undefined): VolumeGroupingLevel {
  if (!storage) return DEFAULT_VOLUME_GROUPING_LEVEL;
  try {
    const stored = storage.getItem(VOLUME_GROUPING_STORAGE_KEY);
    return isVolumeGroupingLevel(stored) ? stored : DEFAULT_VOLUME_GROUPING_LEVEL;
  } catch {
    return DEFAULT_VOLUME_GROUPING_LEVEL;
  }
}

/** Grava a preferência; falha de storage é ignorada (a página segue funcionando). */
export function writeVolumeGroupingLevel(
  storage: PreferenceStorage | null | undefined,
  level: VolumeGroupingLevel,
): void {
  if (!storage) return;
  try {
    storage.setItem(VOLUME_GROUPING_STORAGE_KEY, level);
  } catch {
    // quota cheia / storage bloqueado: a escolha só não persiste.
  }
}
