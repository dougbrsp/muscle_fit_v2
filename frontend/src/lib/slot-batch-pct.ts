// Atalho "Min %/Max % em lote" do header do slot no builder de Modelos de
// Treino (volume_mode='percent_budget').
//
// Regra de produto (M1): o percentual geral do slot NÃO propaga mais para os
// exercícios enquanto o usuário digita. O que é digitado fica num rascunho
// local (`BatchPctDraft`) e só é escrito nos itens quando o botão "Aplicar"
// é clicado. Consequência deliberada: digitar e sair da tela sem aplicar não
// altera nada — o rascunho é puramente local, não vai ao payload do backend
// nem é persistido entre carregamentos.
//
// A matemática de distribuição continua sendo exatamente a que já existia:
// copiar o valor para `priority_min_pct`/`priority_max_pct` de todos os itens
// com `item_kind='muscle_group'` do slot (itens de outro tipo não têm esses
// campos). Nada foi reinventado aqui — só passou a ser disparado por um clique.

export type BatchPctField = 'priority_min_pct' | 'priority_max_pct';

export const BATCH_PCT_FIELDS: readonly BatchPctField[] = ['priority_min_pct', 'priority_max_pct'];

/** Valor de cada campo no rascunho: `''` significa "não digitado". */
export type BatchPctDraft = {
  priority_min_pct: number | '';
  priority_max_pct: number | '';
};

export const EMPTY_BATCH_PCT_DRAFT: BatchPctDraft = {
  priority_min_pct: '',
  priority_max_pct: '',
};

/** Forma mínima de item que o atalho enxerga (o builder usa `ItemState`). */
export type BatchPctTargetItem = {
  item_kind: string;
  priority_min_pct: number | null;
  priority_max_pct: number | null;
};

/**
 * O atalho (e portanto o botão "Aplicar") só existe no modo por grupo
 * muscular. Em 'equipment_series' os itens são equipamentos e não têm faixa
 * de % da meta semanal.
 */
export function supportsBatchPct(volumeMode: string): boolean {
  return volumeMode === 'percent_budget';
}

/** Campos digitados que ainda não foram aplicados aos itens do slot. */
export function pendingBatchPctFields(draft: BatchPctDraft, applied: BatchPctDraft): BatchPctField[] {
  return BATCH_PCT_FIELDS.filter((field) => draft[field] !== '' && draft[field] !== applied[field]);
}

/** Há alteração digitada e não aplicada? Governa o estado do botão "Aplicar". */
export function hasPendingBatchPct(draft: BatchPctDraft, applied: BatchPctDraft): boolean {
  return pendingBatchPctFields(draft, applied).length > 0;
}

/**
 * Aplica o rascunho aos itens do slot — mesma regra de distribuição de antes:
 * todo item `item_kind='muscle_group'` recebe o valor; os demais passam
 * intactos. Campos não digitados (`''`) não tocam em nada.
 *
 * Retorna o array original (mesma referência) quando não há nada a aplicar,
 * para não forçar re-render à toa.
 */
export function applyBatchPctToItems<T extends BatchPctTargetItem>(items: T[], draft: BatchPctDraft): T[] {
  const fields = BATCH_PCT_FIELDS.filter((field) => draft[field] !== '');
  if (fields.length === 0) return items;
  return items.map((item) => {
    if (item.item_kind !== 'muscle_group') return item;
    const patch: Partial<Record<BatchPctField, number>> = {};
    for (const field of fields) patch[field] = Number(draft[field]);
    return { ...item, ...patch };
  });
}
