/** Chave do localStorage com o instante (ms) em que o usuário dispensou o convite de instalação. */
export const INSTALL_DISMISS_STORAGE_KEY = 'musclefit.pwa.install-dismissed-at';

/** Dias até o convite de instalação reaparecer depois de dispensado (plano mobile 3.7). */
export const INSTALL_REAPPEAR_DAYS = 30;

export const MS_PER_DAY = 24 * 60 * 60 * 1000;

/** Rotas em que nenhum convite/aviso do PWA pode aparecer (treino em andamento). */
export const PWA_SILENT_PATH_PREFIXES = ['/modo-treino'] as const;
