import { describe, expect, it } from 'vitest';
import { SCANNER_INITIAL_STATE, scannerReducer } from './barcode-scanner-state';

describe('scannerReducer', () => {
  it('começa iniciando e sem erro', () => {
    expect(SCANNER_INITIAL_STATE).toEqual({ starting: true, error: null });
  });

  it('abrir a câmera encerra o "iniciando"; falhar guarda a mensagem', () => {
    const aberto = scannerReducer(SCANNER_INITIAL_STATE, { type: 'started' });
    expect(aberto).toEqual({ starting: false, error: null });
    expect(scannerReducer(SCANNER_INITIAL_STATE, { type: 'failed', message: 'sem permissão' })).toEqual({
      starting: false,
      error: 'sem permissão',
    });
  });

  it('abrir de novo depois de uma falha limpa o erro e volta a iniciar (a câmera tenta outra vez)', () => {
    const falhou = scannerReducer(SCANNER_INITIAL_STATE, { type: 'failed', message: 'sem permissão' });
    expect(scannerReducer(falhou, { type: 'open' })).toEqual({ starting: true, error: null });
    const falhaTardia = scannerReducer({ starting: false, error: null }, { type: 'failed', message: 'câmera caiu' });
    expect(scannerReducer(falhaTardia, { type: 'open' })).toEqual(SCANNER_INITIAL_STATE);
  });
});
