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
