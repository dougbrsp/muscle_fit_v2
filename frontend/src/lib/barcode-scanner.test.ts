import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  CAMERA_ERROR_MESSAGES,
  CameraError,
  classifyCameraError,
  digitsOnly,
  isZxingFrameError,
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

/** Erro do zxing como sai do build de produção: `name` minificado (`t`), `getKind()` estático intacto. */
function zxingError(kind: string, minifiedName = 't') {
  return Object.assign(new Error(kind), { name: minifiedName, getKind: () => kind });
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
    expect(classifyCameraError(domError('NotReadableError'))).toBe('camera_busy');
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

  it('nativo e zxing pedem 1280x720 ideais, nunca exatos, mantendo a câmera traseira ideal', async () => {
    const { stream } = fakeStream();
    const getUserMedia = vi.fn().mockResolvedValue(stream);
    const ctor = vi.fn().mockImplementation(() => ({ detect: vi.fn().mockResolvedValue([]) })) as unknown as BarcodeDetectorCtor;
    const nativo = await startBarcodeScan(fakeVideo(), vi.fn(), { mediaDevices: { getUserMedia }, detectorCtor: ctor });
    nativo.stop();
    const esperado = {
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    };
    expect(getUserMedia).toHaveBeenCalledWith(esperado);
    const decode = vi.fn().mockResolvedValue({ stop: vi.fn() });
    const getUserMedia2 = vi.fn().mockResolvedValue(fakeStream().stream);
    await startBarcodeScan(fakeVideo(), vi.fn(), {
      mediaDevices: { getUserMedia: getUserMedia2 },
      detectorCtor: null,
      loadZxing: async () => ({ decodeFromStream: decode }),
    });
    expect(getUserMedia2).toHaveBeenCalledWith(esperado);
  });

  it('zxing: expõe contagem de tentativas e o último tipo de erro; nativo não expõe', async () => {
    const stop = vi.fn();
    type Callback = (result: { getText: () => string } | undefined, error: unknown, controls: { stop: () => void }) => void;
    const capturado: { cb: Callback | null } = { cb: null };
    const handle = await startBarcodeScan(fakeVideo(), vi.fn(), {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(fakeStream().stream) },
      detectorCtor: null,
      loadZxing: async () => ({
        decodeFromStream: async (_c, _v, callback) => {
          capturado.cb = callback;
          return { stop };
        },
      }),
    });
    expect(handle.diagnostics?.()).toMatchObject({ attempts: 0, lastError: null });
    const cb = capturado.cb as Callback;
    cb(undefined, zxingError('NotFoundException'), { stop });
    cb(undefined, zxingError('ChecksumException', 'e'), { stop });
    cb(undefined, zxingError('NotFoundException'), { stop });
    expect(handle.diagnostics?.()).toMatchObject({ attempts: 3, lastError: 'NotFoundException' });
    const { stream } = fakeStream();
    const ctor = vi.fn().mockImplementation(() => ({ detect: vi.fn().mockResolvedValue([]) })) as unknown as BarcodeDetectorCtor;
    const nativo = await startBarcodeScan(fakeVideo(), vi.fn(), {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) },
      detectorCtor: ctor,
    });
    expect(nativo.diagnostics).toBeUndefined();
    nativo.stop();
  });

  it('o leitor de reserva real nasce com TRY_HARDER, os 3 formatos e pausa curta entre tentativas', async () => {
    const construtor = vi.fn();
    vi.doMock('@zxing/browser', () => ({
      BrowserMultiFormatReader: class {
        constructor(...args: unknown[]) {
          construtor(...args);
        }
        decodeFromStream = vi.fn().mockResolvedValue({ stop: vi.fn() });
      },
    }));
    try {
      const { DecodeHintType, BarcodeFormat } = await import('@zxing/library');
      await startBarcodeScan(fakeVideo(), vi.fn(), { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(fakeStream().stream) }, detectorCtor: null });
      const [hints, opcoes] = construtor.mock.calls[0] as [Map<unknown, unknown>, { delayBetweenScanAttempts: number }];
      expect(hints.get(DecodeHintType.TRY_HARDER)).toBe(true);
      expect(hints.get(DecodeHintType.POSSIBLE_FORMATS)).toEqual([BarcodeFormat.EAN_13, BarcodeFormat.EAN_8, BarcodeFormat.UPC_A]);
      expect(opcoes.delayBetweenScanAttempts).toBe(300);
    } finally {
      vi.doUnmock('@zxing/browser');
    }
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
    const decodeFromStream = vi.fn().mockResolvedValue({ stop });
    const { stream } = fakeStream();
    const getUserMedia = vi.fn().mockResolvedValue(stream);
    await startBarcodeScan(fakeVideo(), vi.fn(), {
      mediaDevices: { getUserMedia },
      detectorCtor: ctor,
      loadZxing: async () => ({ decodeFromStream }),
    });
    expect(ctor).not.toHaveBeenCalled(); // o nativo não foi usado
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(decodeFromStream).toHaveBeenCalledTimes(1);
    expect(decodeFromStream.mock.calls[0][0]).toBe(stream);
  });

  it('zxing: play() rejeitado vira CameraError classificado, libera a câmera e não inicia a varredura', async () => {
    const { stream, track } = fakeStream();
    const video = fakeVideo();
    (video.play as ReturnType<typeof vi.fn>).mockRejectedValue(domError('NotAllowedError'));
    const decodeFromStream = vi.fn();
    await expect(
      startBarcodeScan(video, vi.fn(), {
        mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) },
        detectorCtor: null,
        loadZxing: async () => ({ decodeFromStream }),
      }),
    ).rejects.toMatchObject({ name: 'CameraError', kind: 'permission_denied' });
    expect(track.stop).toHaveBeenCalled();
    expect(video.srcObject).toBeNull();
    expect(decodeFromStream).not.toHaveBeenCalled();
  });

  it('zxing: stop() durante o play() libera o stream e não inicia a varredura; o handle já está disponível', async () => {
    const { stream, track } = fakeStream();
    const video = fakeVideo();
    let liberar: () => void = () => undefined;
    (video.play as ReturnType<typeof vi.fn>).mockReturnValue(new Promise<void>((r) => (liberar = r)));
    const decodeFromStream = vi.fn();
    const onHandle = vi.fn();
    const promessa = startBarcodeScan(
      video,
      vi.fn(),
      {
        mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) },
        detectorCtor: null,
        loadZxing: async () => ({ decodeFromStream }),
      },
      undefined,
      onHandle,
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(onHandle).toHaveBeenCalledTimes(1);
    const handle = onHandle.mock.calls[0][0] as { stop: () => void };
    handle.stop(); // o usuário fechou enquanto o vídeo ainda abria
    expect(track.stop).toHaveBeenCalled();
    liberar();
    await promessa;
    expect(decodeFromStream).not.toHaveBeenCalled();
  });

  it('zxing: stop() antes de decodeFromStream resolver para os controles e o stream', async () => {
    const { stream, track } = fakeStream();
    const stopControles = vi.fn();
    let resolver: (c: { stop: () => void }) => void = () => undefined;
    const onHandle = vi.fn();
    const promessa = startBarcodeScan(
      fakeVideo(),
      vi.fn(),
      {
        mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) },
        detectorCtor: null,
        loadZxing: async () => ({ decodeFromStream: () => new Promise((r) => (resolver = r)) }),
      },
      undefined,
      onHandle,
    );
    await vi.advanceTimersByTimeAsync(0);
    (onHandle.mock.calls[0][0] as { stop: () => void }).stop();
    expect(track.stop).toHaveBeenCalled();
    resolver({ stop: stopControles });
    await promessa;
    expect(stopControles).toHaveBeenCalledTimes(1);
  });

  it('zxing: o diagnóstico guarda o tamanho do vídeo no início da varredura', async () => {
    const video = fakeVideo() as unknown as { videoWidth: number; videoHeight: number } & HTMLVideoElement;
    video.videoWidth = 720;
    video.videoHeight = 1280;
    const handle = await startBarcodeScan(video, vi.fn(), {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(fakeStream().stream) },
      detectorCtor: null,
      loadZxing: async () => ({ decodeFromStream: vi.fn().mockResolvedValue({ stop: vi.fn() }) }),
    });
    video.videoWidth = 1280;
    video.videoHeight = 720;
    expect(handle.diagnostics?.()).toMatchObject({ startWidth: 720, startHeight: 1280 });
  });

  it('zxing: ignora quadros sem código e texto não numérico; entrega o primeiro código e para', async () => {
    const stop = vi.fn();
    const video = fakeVideo();
    type Callback = (result: { getText: () => string } | undefined, error: unknown, controls: { stop: () => void }) => void;
    const capturado: { cb: Callback | null } = { cb: null };
    const loader: ZxingLoader = async () => ({
      decodeFromStream: async (_c, _v, callback) => {
        capturado.cb = callback;
        return { stop };
      },
    });
    const onCode = vi.fn();
    await startBarcodeScan(video, onCode, { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(fakeStream().stream) }, detectorCtor: null, loadZxing: loader });
    const cb = capturado.cb as Callback;
    cb(undefined, zxingError('NotFoundException'), { stop });
    cb({ getText: () => 'https://qr.example' }, null, { stop });
    expect(onCode).not.toHaveBeenCalled();
    cb({ getText: () => '7891000100103' }, null, { stop });
    expect(onCode).toHaveBeenCalledWith('7891000100103');
    expect(stop).toHaveBeenCalled();
    cb({ getText: () => '7891000100103' }, null, { stop });
    expect(onCode).toHaveBeenCalledTimes(1);
  });

  it('zxing: várias leituras seguidas chamam onCode uma vez só', async () => {
    const stop = vi.fn();
    type Callback = (result: { getText: () => string } | undefined, error: unknown, controls: { stop: () => void }) => void;
    const capturado: { cb: Callback | null } = { cb: null };
    const loader: ZxingLoader = async () => ({
      decodeFromStream: async (_c, _v, callback) => {
        capturado.cb = callback;
        return { stop };
      },
    });
    const onCode = vi.fn();
    await startBarcodeScan(fakeVideo(), onCode, { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(fakeStream().stream) }, detectorCtor: null, loadZxing: loader });
    const cb = capturado.cb as Callback;
    for (const codigo of ['7891000100103', '7891000100103', '7891000315507']) cb({ getText: () => codigo }, null, { stop });
    expect(onCode).toHaveBeenCalledTimes(1);
    expect(onCode).toHaveBeenCalledWith('7891000100103');
  });

  it('zxing: stop() antes de o leitor abrir libera a câmera assim que ele resolve', async () => {
    const stopControles = vi.fn();
    let resolver: (c: { stop: () => void }) => void = () => undefined;
    const loader: ZxingLoader = async () => ({
      decodeFromStream: () => new Promise((resolve) => (resolver = resolve)),
    });
    const promessa = startBarcodeScan(fakeVideo(), vi.fn(), {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(fakeStream().stream) },
      detectorCtor: null,
      loadZxing: loader,
    });
    await vi.advanceTimersByTimeAsync(0);
    resolver({ stop: stopControles });
    const handle = await promessa;
    handle.stop();
    expect(stopControles).toHaveBeenCalled();
  });

  it('zxing: erro de quadro segue lendo; erro fatal para, libera e avisa a tela', async () => {
    const stop = vi.fn();
    type Callback = (result: { getText: () => string } | undefined, error: unknown, controls: { stop: () => void }) => void;
    const capturado: { cb: Callback | null } = { cb: null };
    const loader: ZxingLoader = async () => ({
      decodeFromStream: async (_c, _v, callback) => {
        capturado.cb = callback;
        return { stop };
      },
    });
    const onFatal = vi.fn();
    const onCode = vi.fn();
    await startBarcodeScan(
      fakeVideo(),
      onCode,
      { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(fakeStream().stream) }, detectorCtor: null, loadZxing: loader },
      onFatal,
    );
    const cb = capturado.cb as Callback;
    cb(undefined, zxingError('NotFoundException'), { stop });
    cb(undefined, zxingError('ChecksumException', 'e'), { stop });
    cb(undefined, zxingError('FormatException', 'n'), { stop });
    expect(onFatal).not.toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
    cb(undefined, domError('NotReadableError'), { stop });
    expect(stop).toHaveBeenCalledTimes(1);
    expect(onFatal).toHaveBeenCalledTimes(1);
    expect(onFatal.mock.calls[0][0]).toMatchObject({ name: 'CameraError', kind: 'camera_busy' });
    cb({ getText: () => '7891000100103' }, null, { stop });
    expect(onCode).not.toHaveBeenCalled(); // depois do erro fatal nada mais é entregue
  });

  it('nativo: só zera o srcObject se ele ainda for o nosso stream', async () => {
    const { stream } = fakeStream();
    const video = fakeVideo();
    const ctor = vi.fn().mockImplementation(() => ({ detect: vi.fn().mockResolvedValue([]) })) as unknown as BarcodeDetectorCtor;
    const handle = await startBarcodeScan(video, vi.fn(), {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) },
      detectorCtor: ctor,
    });
    const outro = {} as MediaStream;
    (video as { srcObject: MediaStream | null }).srcObject = outro;
    handle.stop();
    expect(video.srcObject).toBe(outro);
  });

  it('não pede UPC-E ao leitor nativo (o dono do GTIN não expande UPC-E)', async () => {
    const { stream } = fakeStream();
    const ctor = vi.fn().mockImplementation(() => ({ detect: vi.fn().mockResolvedValue([]) })) as unknown as BarcodeDetectorCtor;
    await startBarcodeScan(fakeVideo(), vi.fn(), { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) }, detectorCtor: ctor });
    const opcoes = (ctor as unknown as { mock: { calls: Array<[{ formats: string[] }]> } }).mock.calls[0][0];
    expect(opcoes.formats).toEqual(['ean_13', 'ean_8', 'upc_a']);
  });
});

describe('isZxingFrameError (independe do name minificado)', () => {
  it('reconhece os erros de quadro pelo getKind(), mesmo com name trocado', () => {
    for (const kind of ['NotFoundException', 'ChecksumException', 'FormatException']) {
      expect(isZxingFrameError(zxingError(kind, 'a'))).toBe(true);
    }
  });

  it('erro de zxing de outro tipo, DOMException da câmera e lixo são fatais, mesmo com name parecido', () => {
    expect(isZxingFrameError(zxingError('IllegalStateException'))).toBe(false);
    expect(isZxingFrameError(domError('NotFoundException'))).toBe(false); // sem getKind: não é erro de quadro
    expect(isZxingFrameError(domError('NotReadableError'))).toBe(false);
    expect(isZxingFrameError(null)).toBe(false);
    expect(isZxingFrameError('NotFoundException')).toBe(false);
  });
});
