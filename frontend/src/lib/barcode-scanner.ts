// Leitor de código de barras pela câmera (nutrição N3, decisão D4): `BarcodeDetector`
// nativo quando o navegador o tem (Chrome/Android) e `@zxing/browser` como reserva
// (Safari/iOS e desktop), carregado com import dinâmico SÓ ao abrir o leitor.
//
// Só transporte: o leitor devolve o TEXTO lido (apenas dígitos). Dígito verificador,
// comprimento e forma canônica do EAN/GTIN são da API (`external_sources.normalize_gtin`),
// que responde 400 com o texto do motor; nada de regra do código de barras aqui.
// A câmera é sempre liberada em `stop()` (fechar o leitor, ler um código ou desmontar).

export type CameraErrorKind =
  | 'permission_denied'
  | 'no_camera'
  | 'camera_busy'
  | 'insecure_context'
  | 'unsupported'
  | 'unknown';

/** Falha ao abrir a câmera, já classificada para a tela cair na digitação com a mensagem certa. */
export class CameraError extends Error {
  readonly kind: CameraErrorKind;

  constructor(kind: CameraErrorKind) {
    super(CAMERA_ERROR_MESSAGES[kind]);
    this.name = 'CameraError';
    this.kind = kind;
  }
}

export const CAMERA_ERROR_MESSAGES: Record<CameraErrorKind, string> = {
  permission_denied:
    'A câmera está bloqueada para este site. Libere o acesso à câmera nas permissões do navegador ou digite o código.',
  no_camera: 'Nenhuma câmera foi encontrada neste aparelho. Digite o código de barras.',
  camera_busy:
    'A câmera está em uso por outro aplicativo ou aba. Feche-o e tente de novo, ou digite o código de barras.',
  insecure_context: 'A câmera só funciona em conexão segura (https). Digite o código de barras.',
  unsupported: 'Este navegador não consegue usar a câmera para ler o código. Digite o código de barras.',
  unknown: 'Não foi possível abrir a câmera. Digite o código de barras.',
};

const PERMISSION_ERRORS = ['NotAllowedError', 'PermissionDeniedError', 'SecurityError'];
const NO_CAMERA_ERRORS = ['NotFoundError', 'DevicesNotFoundError', 'OverconstrainedError'];
const CAMERA_BUSY_ERRORS = ['NotReadableError', 'TrackStartError'];

/** Classifica o erro do `getUserMedia` (ou do leitor de reserva). */
export function classifyCameraError(error: unknown): CameraErrorKind {
  if (error instanceof CameraError) return error.kind;
  const nome = typeof error === 'object' && error !== null && 'name' in error ? String((error as { name: unknown }).name) : '';
  if (PERMISSION_ERRORS.includes(nome)) return 'permission_denied';
  if (NO_CAMERA_ERRORS.includes(nome)) return 'no_camera';
  if (CAMERA_BUSY_ERRORS.includes(nome)) return 'camera_busy';
  if (nome === 'TypeError') return 'unsupported';
  return 'unknown';
}

/** Texto lido só com dígitos (descarta QR code, URL e ruído); `null` se não sobrar nada. */
export function digitsOnly(text: string | null | undefined): string | null {
  const limpo = (text ?? '').trim();
  return /^\d+$/.test(limpo) ? limpo : null;
}

// Formatos de produto de varejo que o leitor tenta (códigos do `BarcodeDetector`). Não é regra
// de GTIN: a API decide o que vale; isto só evita gastar quadro com QR/Code128.
// UPC-E fica de fora: `normalize_gtin` (dono do GTIN) não expande UPC-E para 12 dígitos, então a API recusaria.
const NATIVE_FORMATS = ['ean_13', 'ean_8', 'upc_a'] as const;
const NATIVE_POLL_MS = 200;

type DetectedBarcode = { rawValue?: string };
type BarcodeDetectorLike = { detect: (source: HTMLVideoElement) => Promise<DetectedBarcode[]> };
export type BarcodeDetectorCtor = {
  new (options?: { formats?: string[] }): BarcodeDetectorLike;
  getSupportedFormats?: () => Promise<string[]>;
};

type ZxingControls = { stop: () => void };
type ZxingReaderLike = {
  decodeFromConstraints: (
    constraints: MediaStreamConstraints,
    video: HTMLVideoElement,
    callback: (result: { getText: () => string } | undefined, error: unknown, controls: ZxingControls) => void,
  ) => Promise<ZxingControls>;
};
export type ZxingLoader = () => Promise<ZxingReaderLike>;

export type ScannerDeps = {
  mediaDevices?: Pick<MediaDevices, 'getUserMedia'> | null;
  isSecureContext?: boolean;
  detectorCtor?: BarcodeDetectorCtor | null;
  loadZxing?: ZxingLoader;
};

/** Contagem do leitor de reserva, para a tela de diagnóstico (leitor nativo não tem). */
export type ScanDiagnostics = { attempts: number; lastError: string | null };

export type ScanHandle = { stop: () => void; diagnostics?: () => ScanDiagnostics };

// Tudo `ideal`, nunca `exact`: o aparelho que não oferecer 1280x720 ou a traseira abre a câmera mesmo assim.
// Resolução maior dá mais pixels por módulo ao código (abaixo de ~2 px por módulo o zxing falha).
const CONSTRAINTS: MediaStreamConstraints = {
  video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
  audio: false,
};

/** Pausa entre tentativas do zxing (padrão da biblioteca: 500 ms). */
export const ZXING_SCAN_INTERVAL_MS = 150;

/** Carrega o `@zxing/browser` (chunk separado) só quando o leitor de reserva é necessário. */
const loadZxingReader: ZxingLoader = async () => {
  const [{ BrowserMultiFormatReader }, { BarcodeFormat, DecodeHintType }] = await Promise.all([
    import('@zxing/browser'),
    import('@zxing/library'),
  ]);
  const hints = new Map<unknown, unknown>([
    [DecodeHintType.POSSIBLE_FORMATS, [BarcodeFormat.EAN_13, BarcodeFormat.EAN_8, BarcodeFormat.UPC_A]],
    // Sem isto a leitura levava 1 a 3 s e falhava com o código girado; com ele, ~0,1 s (medido em Chromium).
    [DecodeHintType.TRY_HARDER, true],
  ]);
  return new BrowserMultiFormatReader(hints as never, {
    delayBetweenScanAttempts: ZXING_SCAN_INTERVAL_MS,
  } as never) as unknown as ZxingReaderLike;
};

function browserDeps(): ScannerDeps {
  const nav = typeof navigator === 'undefined' ? undefined : navigator;
  return {
    mediaDevices: nav?.mediaDevices ?? null,
    isSecureContext: typeof window === 'undefined' ? true : window.isSecureContext,
    detectorCtor: (globalThis as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector ?? null,
    loadZxing: loadZxingReader,
  };
}

async function nativeFormatsSupported(ctor: BarcodeDetectorCtor): Promise<boolean> {
  try {
    const suportados = await ctor.getSupportedFormats?.();
    if (!suportados) return true;
    return NATIVE_FORMATS.some((formato) => suportados.includes(formato));
  } catch {
    return false;
  }
}

function stopStream(video: HTMLVideoElement, stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop());
  if (video.srcObject === stream) video.srcObject = null; // outro leitor pode ter assumido o vídeo
}

async function startNative(
  video: HTMLVideoElement,
  ctor: BarcodeDetectorCtor,
  mediaDevices: Pick<MediaDevices, 'getUserMedia'>,
  onCode: (code: string) => void,
): Promise<ScanHandle> {
  const stream = await mediaDevices.getUserMedia(CONSTRAINTS);
  let parado = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const stop = () => {
    parado = true;
    if (timer !== null) clearTimeout(timer);
    stopStream(video, stream);
  };
  try {
    video.srcObject = stream;
    await video.play();
    const detector = new ctor({ formats: [...NATIVE_FORMATS] });
    const varrer = async () => {
      if (parado) return;
      try {
        const achados = await detector.detect(video);
        const codigo = achados.map((a) => digitsOnly(a.rawValue)).find((c): c is string => c !== null);
        if (codigo && !parado) {
          stop();
          onCode(codigo);
          return;
        }
      } catch {
        // Quadro sem imagem ainda (vídeo aquecendo): tenta no próximo ciclo.
      }
      if (!parado) timer = setTimeout(varrer, NATIVE_POLL_MS);
    };
    timer = setTimeout(varrer, NATIVE_POLL_MS);
  } catch (error) {
    stop();
    throw error;
  }
  return { stop };
}

// Erros por quadro do zxing (nenhum código na imagem, dígito verificador, formato): o leitor segue lendo.
// Identificados por `getKind()` (string ESTÁTICA da classe, `NotFoundException.kind`), nunca por `name`:
// o `ts-custom-error` do zxing deriva o `name` de `new.target.name`, que a minificação do build de
// produção troca por uma letra, e o primeiro quadro sem código viraria erro fatal.
const ZXING_FRAME_ERRORS = ['NotFoundException', 'ChecksumException', 'FormatException'];

/** `kind` do erro do zxing (`Exception.getKind()`); `null` para qualquer outro erro (ex.: DOMException da câmera). */
export function zxingErrorKind(error: unknown): string | null {
  const getKind = typeof error === 'object' && error !== null ? (error as { getKind?: unknown }).getKind : undefined;
  if (typeof getKind !== 'function') return null;
  const kind = (getKind as () => unknown).call(error);
  return typeof kind === 'string' ? kind : null;
}

/** O erro do callback do zxing é só "este quadro não tinha código legível"? */
export function isZxingFrameError(error: unknown): boolean {
  const kind = zxingErrorKind(error);
  return kind !== null && ZXING_FRAME_ERRORS.includes(kind);
}

async function startZxing(
  video: HTMLVideoElement,
  loadZxing: ZxingLoader,
  onCode: (code: string) => void,
  onFatal?: (error: CameraError) => void,
): Promise<ScanHandle> {
  const reader = await loadZxing();
  let parado = false;
  let controles: ZxingControls | null = null;
  let attempts = 0;
  let lastError: string | null = null;
  const stop = () => {
    parado = true;
    controles?.stop();
  };
  controles = await reader.decodeFromConstraints(CONSTRAINTS, video, (result, error, controls) => {
    controles = controls;
    if (parado) return;
    attempts += 1;
    if (!result && error) lastError = zxingErrorKind(error) ?? (error instanceof Error ? error.name : 'erro');
    if (!result) {
      if (error && !isZxingFrameError(error)) {
        // Erro que não é de quadro (câmera caiu, stream encerrado): para, libera e avisa a tela.
        stop();
        onFatal?.(new CameraError(classifyCameraError(error)));
      }
      return;
    }
    const codigo = digitsOnly(result.getText());
    if (codigo) {
      stop();
      onCode(codigo);
    }
  });
  if (parado) controles.stop(); // leu antes de `decodeFromConstraints` resolver
  return { stop, diagnostics: () => ({ attempts, lastError }) };
}

/**
 * Abre a câmera no `video` e chama `onCode` UMA vez com o primeiro código só de dígitos.
 * Rejeita com `CameraError` (permissão, sem câmera, contexto inseguro, navegador sem
 * suporte); nesse caso a câmera já foi liberada. `stop()` libera a câmera e é seguro repetir.
 */
export async function startBarcodeScan(
  video: HTMLVideoElement,
  onCode: (code: string) => void,
  deps: ScannerDeps = browserDeps(),
  /** Falha DEPOIS de aberta (leitor de reserva): a câmera já foi liberada; a tela mostra e oferece digitar. */
  onFatal?: (error: CameraError) => void,
): Promise<ScanHandle> {
  const mediaDevices = deps.mediaDevices;
  if (!mediaDevices?.getUserMedia) {
    throw new CameraError(deps.isSecureContext === false ? 'insecure_context' : 'unsupported');
  }
  try {
    if (deps.detectorCtor && (await nativeFormatsSupported(deps.detectorCtor))) {
      return await startNative(video, deps.detectorCtor, mediaDevices, onCode);
    }
    return await startZxing(video, deps.loadZxing ?? loadZxingReader, onCode, onFatal);
  } catch (error) {
    throw error instanceof CameraError ? error : new CameraError(classifyCameraError(error));
  }
}
