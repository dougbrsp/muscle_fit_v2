// Módulo folha: SEM imports de api/auth/nutrition-api/app-logout (evita ciclo com api.ts).
export const RECIPE_DRAFT_PREFIX = 'muscle_fit:receita-rascunho:';

/** Rascunhos de receita (texto digitado) não sobrevivem ao logout/401: aparelho compartilhado. */
export function clearRecipeDrafts(): void {
  try {
    const keys: string[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i);
      if (k?.startsWith(RECIPE_DRAFT_PREFIX)) keys.push(k);
    }
    keys.forEach((k) => window.localStorage.removeItem(k));
  } catch {
    // storage indisponível: sem rascunho a apagar.
  }
}
