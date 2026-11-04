import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  DIAGNOSTIC_AFTER_MS,
  DIAGNOSTIC_REFRESH_MS,
  SCANNER_INITIAL_STATE,
  formatDiagnostics,
  scannerReducer,
  scheduleDiagnostics,
} from './barcode-scanner-state';

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

describe('formatDiagnostics', () => {
  const video = { videoWidth: 1280, videoHeight: 720, readyState: 4 };

  const zxing = { attempts: 42, lastError: 'NotFoundException', startWidth: 1280, startHeight: 720 };

  it('mostra dimensões, estado, tentativas e último erro do zxing', () => {
    expect(formatDiagnostics(video, zxing)).toBe('Diagnóstico: 1280x720, estado 4, 42 tentativas, NotFoundException');
  });

  it('vídeo que girou depois do início mostra início e agora (o canvas do zxing fica com o tamanho velho)', () => {
    expect(formatDiagnostics(video, { ...zxing, startWidth: 720, startHeight: 1280 })).toBe(
      'Diagnóstico: início 720x1280, agora 1280x720, estado 4, 42 tentativas, NotFoundException',
    );
  });

  it('vídeo com 0x0 durante toda a varredura (hipótese do standalone) fica visível, sem erro de quadro', () => {
    const zero = { attempts: 12, lastError: null, startWidth: 0, startHeight: 0 };
    expect(formatDiagnostics({ videoWidth: 0, videoHeight: 0, readyState: 2 }, zero)).toBe(
      'Diagnóstico: 0x0, estado 2, 12 tentativas, sem erro',
    );
  });

  it('sem handle ainda: diz que a câmera está iniciando, não que é o leitor nativo', () => {
    expect(formatDiagnostics({ videoWidth: 0, videoHeight: 0, readyState: 0 }, null)).toBe(
      'Diagnóstico: 0x0, estado 0, câmera iniciando',
    );
  });

  it('leitor nativo não tem contagem', () => {
    expect(formatDiagnostics(video, 'nativo')).toBe('Diagnóstico: 1280x720, estado 4, leitor nativo');
  });
});

describe('scheduleDiagnostics', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('só aparece depois do atraso e se atualiza periodicamente', () => {
    let n = 0;
    const show = vi.fn();
    scheduleDiagnostics(() => `linha ${++n}`, show);
    vi.advanceTimersByTime(DIAGNOSTIC_AFTER_MS - 1);
    expect(show).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(show).toHaveBeenLastCalledWith('linha 1');
    vi.advanceTimersByTime(DIAGNOSTIC_REFRESH_MS * 2);
    expect(show).toHaveBeenCalledTimes(3);
    expect(show).toHaveBeenLastCalledWith('linha 3');
  });

  it('cancelar antes do atraso ou depois limpa os timers', () => {
    const show = vi.fn();
    const antes = scheduleDiagnostics(() => 'x', show);
    antes();
    vi.advanceTimersByTime(DIAGNOSTIC_AFTER_MS * 2);
    expect(show).not.toHaveBeenCalled();
    const depois = scheduleDiagnostics(() => 'y', show);
    vi.advanceTimersByTime(DIAGNOSTIC_AFTER_MS);
    depois();
    depois();
    show.mockClear();
    vi.advanceTimersByTime(DIAGNOSTIC_REFRESH_MS * 5);
    expect(show).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});
