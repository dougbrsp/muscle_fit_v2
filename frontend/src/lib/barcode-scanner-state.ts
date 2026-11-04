// Estado da tela do leitor de código de barras (N3), como redutor puro para ser testado sem DOM.
// Bug da revisão: depois de uma falha (ex.: permissão negada) o `error` ficava preso e o vídeo
// nunca mais era montado ao reabrir; abrir de novo SEMPRE volta ao estado "iniciando".

export type ScannerUiState = { starting: boolean; error: string | null };

export type ScannerUiAction = { type: 'open' } | { type: 'started' } | { type: 'failed'; message: string };

export const SCANNER_INITIAL_STATE: ScannerUiState = { starting: true, error: null };

export function scannerReducer(state: ScannerUiState, action: ScannerUiAction): ScannerUiState {
  switch (action.type) {
    case 'open':
      return SCANNER_INITIAL_STATE;
    case 'started':
      return { ...state, starting: false };
    case 'failed':
      return { starting: false, error: action.message };
    default:
      return state;
  }
}

/** Tempo sem leitura para a tela mostrar a linha de diagnóstico. */
export const DIAGNOSTIC_AFTER_MS = 8000;
/** Atualização da linha de diagnóstico. */
export const DIAGNOSTIC_REFRESH_MS = 1000;

/** Linha discreta de diagnóstico: dimensões do vídeo, estado, tentativas e último erro do leitor. */
export function formatDiagnostics(
  video: { videoWidth: number; videoHeight: number; readyState: number },
  scan: { attempts: number; lastError: string | null } | null,
): string {
  const base = `${video.videoWidth}x${video.videoHeight}, estado ${video.readyState}`;
  if (!scan) return `Diagnóstico: ${base}, leitor nativo`;
  return `Diagnóstico: ${base}, ${scan.attempts} tentativas, ${scan.lastError ?? 'sem erro'}`;
}

/**
 * Depois de `DIAGNOSTIC_AFTER_MS` sem leitura chama `show(read())` e repete a cada
 * `DIAGNOSTIC_REFRESH_MS`. Devolve o cancelamento (limpa os dois timers); seguro repetir.
 */
export function scheduleDiagnostics(read: () => string, show: (line: string) => void): () => void {
  let refresh: ReturnType<typeof setInterval> | null = null;
  const first = setTimeout(() => {
    show(read());
    refresh = setInterval(() => show(read()), DIAGNOSTIC_REFRESH_MS);
  }, DIAGNOSTIC_AFTER_MS);
  return () => {
    clearTimeout(first);
    if (refresh !== null) clearInterval(refresh);
    refresh = null;
  };
}
