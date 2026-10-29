/**
 * Preferência "menu lateral recolhido" (só desktop).
 *
 * Lógica pura, sem React nem `window` direto: o storage é injetado para que
 * o layout leia a preferência só no cliente, depois da montagem (o primeiro
 * render — inclusive o do export estático — é sempre o menu expandido, sem
 * hydration mismatch), e para que os testes rodem em ambiente node.
 */

export const NAVBAR_COLLAPSED_STORAGE_KEY = 'musclefit:navbar-collapsed';

/** Largura do menu expandido (a mesma de antes do recurso existir). */
export const NAVBAR_EXPANDED_WIDTH = 240;
/** Largura do menu recolhido: só a coluna de ícones. */
export const NAVBAR_COLLAPSED_WIDTH = 72;

/** Subconjunto de `Storage` usado aqui (facilita fakes nos testes). */
export type PreferenceStorage = Pick<Storage, 'getItem' | 'setItem'>;

/**
 * Lê a preferência. Qualquer valor ausente, inválido ou erro de acesso
 * (modo privado, storage bloqueado) cai no default: expandido.
 */
export function readNavbarCollapsed(storage: PreferenceStorage | null | undefined): boolean {
  if (!storage) return false;
  try {
    return storage.getItem(NAVBAR_COLLAPSED_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

/** Grava a preferência; falhas de storage são ignoradas (a UI segue funcionando). */
export function writeNavbarCollapsed(
  storage: PreferenceStorage | null | undefined,
  collapsed: boolean,
): void {
  if (!storage) return;
  try {
    storage.setItem(NAVBAR_COLLAPSED_STORAGE_KEY, collapsed ? '1' : '0');
  } catch {
    // quota cheia / storage bloqueado: a preferência só não persiste.
  }
}

/**
 * `localStorage` do navegador, ou `null` fora dele (SSR/export) ou quando o
 * próprio acesso à propriedade lança (alguns navegadores com cookies bloqueados).
 */
export function browserStorage(): PreferenceStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

/**
 * O modo recolhido só vale no desktop: abaixo do breakpoint o menu é o
 * drawer do burger e sempre mostra os rótulos.
 */
export function isNavbarCollapsed(preferenceCollapsed: boolean, isDesktop: boolean): boolean {
  return preferenceCollapsed && isDesktop;
}

/** Largura do AppShell.Navbar para o estado efetivo. */
export function navbarWidth(collapsed: boolean): number {
  return collapsed ? NAVBAR_COLLAPSED_WIDTH : NAVBAR_EXPANDED_WIDTH;
}

/** Rótulo acessível do botão de alternância (descreve a ação). */
export function navbarToggleLabel(collapsed: boolean): string {
  return collapsed ? 'Expandir menu' : 'Recolher menu';
}
