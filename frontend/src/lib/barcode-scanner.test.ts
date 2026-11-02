import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  CAMERA_ERROR_MESSAGES,
  CameraError,
  classifyCameraError,
  digitsOnly,
  startBarcodeScan,
  type BarcodeDetectorCtor,
  type ScannerDeps,
  type ZxingLoader,
} from './barcode-scanner';

function fakeStream() {
  const track = { stop: vi.fn() };
  return { stream: { getTracks: () => [track] } as unknown as MediaStream, track };
}

function fakeVideo() {
  return { srcObject: null, play: vi.fn().mockResolvedValue(undefined) } as unknown as HTMLVideoElement;
}

function domError(name: string) {
  return Object.assign(new Error(name), { name });
}

describe('digitsOnly', () => {
  it('aceita só dígitos e descarta QR, URL e vazio', () => {
    expect(digitsOnly(' 7891000100103 ')).toBe('7891000100103');
    expect(digitsOnly('https://x.com/1')).toBeNull();
    expect(digitsOnly('ABC123')).toBeNull();
    expect(digitsOnly('')).toBeNull();
    expect(digitsOnly(null)).toBeNull();
    expect(digitsOnly(undefined)).toBeNull();
  });
});

describe('classifyCameraError', () => {
  it('separa permissão, sem câmera, navegador sem suporte e o resto', () => {
    expect(classifyCameraError(domError('NotAllowedError'))).toBe('permission_denied');
    expect(classifyCameraError(domError('SecurityError'))).toBe('permission_denied');
    expect(classifyCameraError(domError('NotFoundError'))).toBe('no_camera');
    expect(classifyCameraError(domError('OverconstrainedError'))).toBe('no_camera');
    expect(classifyCameraError(domError('TypeError'))).toBe('unsupported');
    expect(classifyCameraError(new Error('x'))).toBe('unknown');
    expect(classifyCameraError(null)).toBe('unknown');
    expect(classifyCameraError(new CameraError('no_camera'))).toBe('no_camera');
  });

  it('toda classe tem mensagem clara que manda digitar o código', () => {
    for (const mensagem of Object.values(CAMERA_ERROR_MESSAGES)) expect(mensagem.length).toBeGreaterThan(20);
    expect(new CameraError('permission_denied').message).toBe(CAMERA_ERROR_MESSAGES.permission_denied);
  });
});

describe('startBarcodeScan', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('sem getUserMedia: contexto inseguro ou sem suporte', async () => {
    await expect(startBarcodeScan(fakeVideo(), vi.fn(), { mediaDevices: null, isSecureContext: false })).rejects.toMatchObject({
      kind: 'insecure_context',
    });
    await expect(startBarcodeScan(fakeVideo(), vi.fn(), { mediaDevices: null, isSecureContext: true })).rejects.toMatchObject({
      kind: 'unsupported',
    });
  });

  it('permissão negada vira CameraError e nada fica aberto', async () => {
    const getUserMedia = vi.fn().mockRejectedValue(domError('NotAllowedError'));
    const deps: ScannerDeps = { mediaDevices: { getUserMedia }, detectorCtor: null, loadZxing: undefined };
    const zxing: ZxingLoader = vi.fn().mockRejectedValue(domError('NotAllowedError'));
    await expect(startBarcodeScan(fakeVideo(), vi.fn(), { ...deps, loadZxing: zxing })).rejects.toMatchObject({
      name: 'CameraError',
      kind: 'permission_denied',
    });
  });

  it('nativo: lê o primeiro código só de dígitos, para o stream e chama onCode uma vez', async () => {
    const { stream, track } = fakeStream();
    const video = fakeVideo();
    const detect = vi
      .fn()
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ rawValue: 'https://qr.example' }])
      .mockResolvedValue([{ rawValue: '7891000100103' }]);
    const ctor = vi.fn().mockImplementation(() => ({ detect })) as unknown as BarcodeDetectorCtor;
    (ctor as { getSupportedFormats?: () => Promise<string[]> }).getSupportedFormats = async () => ['ean_13', 'qr_code'];
    const onCode = vi.fn();
    const handle = await startBarcodeScan(video, onCode, {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) },
      detectorCtor: ctor,
    });
    await vi.advanceTimersByTimeAsync(1000);
    expect(onCode).toHaveBeenCalledTimes(1);
    expect(onCode).toHaveBeenCalledWith('7891000100103');
    expect(track.stop).toHaveBeenCalled();
    expect(video.srcObject).toBeNull();
    handle.stop(); // repetir é seguro
    await vi.advanceTimersByTimeAsync(1000);
    expect(onCode).toHaveBeenCalledTimes(1);
  });

  it('nativo: stop() antes de ler libera a câmera e interrompe a varredura', async () => {
    const { stream, track } = fakeStream();
    const detect = vi.fn().mockResolvedValue([]);
    const ctor = vi.fn().mockImplementation(() => ({ detect })) as unknown as BarcodeDetectorCtor;
    const onCode = vi.fn();
    const handle = await startBarcodeScan(fakeVideo(), onCode, {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) },
      detectorCtor: ctor,
    });
    await vi.advanceTimersByTimeAsync(500);
    const chamadas = detect.mock.calls.length;
    handle.stop();
    expect(track.stop).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(detect.mock.calls.length).toBe(chamadas);
    expect(onCode).not.toHaveBeenCalled();
  });

  it('nativo: se o play falhar, a câmera é liberada e o erro volta classificado', async () => {
    const { stream, track } = fakeStream();
    const video = { srcObject: null, play: vi.fn().mockRejectedValue(domError('NotAllowedError')) } as unknown as HTMLVideoElement;
    const ctor = vi.fn() as unknown as BarcodeDetectorCtor;
    await expect(
      startBarcodeScan(video, vi.fn(), { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) }, detectorCtor: ctor }),
    ).rejects.toMatchObject({ kind: 'permission_denied' });
    expect(track.stop).toHaveBeenCalled();
  });

  it('BarcodeDetector sem formato de produto cai no zxing', async () => {
    const ctor = vi.fn() as unknown as BarcodeDetectorCtor;
    (ctor as { getSupportedFormats?: () => Promise<string[]> }).getSupportedFormats = async () => ['qr_code'];
    const stop = vi.fn();
    const decodeFromConstraints = vi.fn().mockResolvedValue({ stop });
    const getUserMedia = vi.fn();
    await startBarcodeScan(fakeVideo(), vi.fn(), {
      mediaDevices: { getUserMedia },
      detectorCtor: ctor,
      loadZxing: async () => ({ decodeFromConstraints }),
    });
    expect(decodeFromConstraints).toHaveBeenCalledTimes(1);
    expect(getUserMedia).not.toHaveBeenCalled(); // quem abre a câmera é o zxing
  });

  it('zxing: ignora quadros sem código e texto não numérico; entrega o primeiro código e para', async () => {
    const stop = vi.fn();
    const video = fakeVideo();
    type Callback = (result: { getText: () => string } | undefined, error: unknown, controls: { stop: () => void }) => void;
    const capturado: { cb: Callback | null } = { cb: null };
    const loader: ZxingLoader = async () => ({
      decodeFromConstraints: async (_c, _v, callback) => {
        capturado.cb = callback;
        return { stop };
      },
    });
    const onCode = vi.fn();
    await startBarcodeScan(video, onCode, { mediaDevices: { getUserMedia: vi.fn() }, detectorCtor: null, loadZxing: loader });
    const cb = capturado.cb as Callback;
    cb(undefined, domError('NotFoundException'), { stop });
    cb({ getText: () => 'https://qr.example' }, null, { stop });
    expect(onCode).not.toHaveBeenCalled();
    cb({ getText: () => '7891000100103' }, null, { stop });
    expect(onCode).toHaveBeenCalledWith('7891000100103');
    expect(stop).toHaveBeenCalled();
    cb({ getText: () => '7891000100103' }, null, { stop });
    expect(onCode).toHaveBeenCalledTimes(1);
  });
});
