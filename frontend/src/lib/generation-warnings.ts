type ExcludedItem = { exercise_name?: string | null; muscle_primary?: string | null };

const clean = (v?: string | null): string => (typeof v === 'string' ? v.trim() : '');

/**
 * Grupos musculares (sem repetição, ordem preservada) dos itens removidos por
 * recuperação insuficiente. O motor envia o GRUPO em `muscle_primary`; se ele
 * faltar, cai para `exercise_name`; itens sem nenhum dos dois são ignorados.
 */
export function recoveryExcludedGroups(items?: ExcludedItem[] | null): string[] {
  const seen = new Set<string>();
  for (const it of items ?? []) {
    const label = clean(it?.muscle_primary) || clean(it?.exercise_name);
    if (label) seen.add(label);
  }
  return [...seen];
}

/** "Nome (Grupo)" sem espaço solto quando um dos campos faltar; '' se ambos faltarem. */
export function exerciseWithGroupLabel(item?: ExcludedItem | null): string {
  const name = clean(item?.exercise_name);
  const group = clean(item?.muscle_primary);
  if (name && group) return `${name} (${group})`;
  return name || group;
}
