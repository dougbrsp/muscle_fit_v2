'use client';

import { Alert, Button, Loader, Modal, Stack, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconCameraOff } from '@tabler/icons-react';
import { useEffect, useReducer, useRef, useState } from 'react';
import { CAMERA_ERROR_MESSAGES, CameraError, startBarcodeScan, type ScanHandle } from '@/lib/barcode-scanner';
import { registerOpenScanner } from '@/lib/barcode-scanner-open';
import {
  SCANNER_INITIAL_STATE,
  formatDiagnostics,
  scannerReducer,
  scheduleDiagnostics,
} from '@/lib/barcode-scanner-state';
import { usePresentationActive } from '@/lib/presentation-slots';
import { BackToCloseHere } from '@/mobile/screens/telas/BackToCloseHere';
import { BRAND } from '@/theme';

type Props = {
  opened: boolean;
  /** Fechar sem ler (digitar o código ou desistir). A câmera é liberada ao desmontar. */
  onClose: () => void;
  /** Código lido (só dígitos); a API valida o EAN. */
  onDetected: (code: string) => void;
};

// Tela cheia no celular (e no shell mobile): o vídeo ocupa a área útil, com a área segura do
// aparelho (notch e barra de gestos) respeitada e botão com alvo de toque de 44 px.
const MOBILE_QUERY = '(max-width: 48em)';
const TOUCH_TARGET_PX = 44;

/**
 * Leitor de código de barras pela câmera (D4). `BarcodeDetector` nativo ou `@zxing/browser`
 * (import dinâmico em `lib/barcode-scanner`, só aqui). Sem permissão, sem câmera ou sem suporte,
 * mostra a mensagem clara e o botão de digitar o código; a digitação continua na tela de baixo.
 */
export function BarcodeScanner({ opened, onClose, onDetected }: Props) {
  const fullScreen = useMediaQuery(MOBILE_QUERY) ?? false;
  const [video, setVideo] = useState<HTMLVideoElement | null>(null);
  const [{ starting, error }, dispatch] = useReducer(scannerReducer, SCANNER_INITIAL_STATE);
  const [diagnostico, setDiagnostico] = useState<string | null>(null);
  const onDetectedRef = useRef(onDetected);
  onDetectedRef.current = onDetected;
  const noCelular = usePresentationActive();

  // Avisa o cadastro por baixo que há leitor aberto (Esc fecha só o leitor).
  useEffect(() => (opened ? registerOpenScanner() : undefined), [opened]);

  // Cada abertura recomeça limpa: sem isto, a mensagem de uma falha anterior impedia o vídeo de
  // montar e a câmera nunca mais abria (o efeito de baixo depende do elemento <video>).
  useEffect(() => {
    if (opened) dispatch({ type: 'open' });
  }, [opened]);

  useEffect(() => {
    if (!opened || !video) return undefined;
    let cancelled = false;
    let handle: ScanHandle | null = null;
    // Depois de um tempo sem ler, mostra a linha de diagnóstico e a mantém atualizada.
    const cancelarDiagnostico = scheduleDiagnostics(
      () => formatDiagnostics(video, handle ? (handle.diagnostics?.() ?? 'nativo') : null),
      (linha) => {
        if (!cancelled) setDiagnostico(linha);
      },
    );
    const falhou = (falha: unknown) => {
      if (cancelled) return;
      dispatch({
        type: 'failed',
        message: falha instanceof CameraError ? falha.message : CAMERA_ERROR_MESSAGES.unknown,
      });
    };
    startBarcodeScan(
      video,
      (code) => {
        if (!cancelled) onDetectedRef.current(code);
      },
      undefined,
      falhou,
    )
      .then((started) => {
        if (cancelled) {
          started.stop();
          return;
        }
        handle = started;
        dispatch({ type: 'started' });
      })
      .catch(falhou);
    return () => {
      cancelled = true;
      cancelarDiagnostico();
      setDiagnostico(null);
      handle?.stop(); // libera a câmera ao fechar ou desmontar
    };
  }, [opened, video]);

  return (
    <>
    {/* Fora do Modal e sempre montado: fechar por código desfaz a entrada do histórico. */}
    {noCelular ? <BackToCloseHere open={opened} onClose={onClose} /> : null}
    <Modal
      opened={opened}
      onClose={onClose}
      title="Ler código de barras"
      fullScreen={fullScreen}
      centered={!fullScreen}
      size="lg"
      styles={{
        content: fullScreen
          ? { paddingTop: 'env(safe-area-inset-top)', paddingBottom: 'env(safe-area-inset-bottom)' }
          : undefined,
        body: { display: 'flex', flexDirection: 'column', gap: 'var(--mantine-spacing-sm)' },
      }}
    >
      <Stack gap="sm">
        {error ? (
          <Alert color="yellow" icon={<IconCameraOff size={18} />} title="Câmera indisponível">
            {error}
          </Alert>
        ) : (
          <>
            <div style={{ position: 'relative', background: '#000', borderRadius: 12, overflow: 'hidden' }}>
              <video
                ref={setVideo}
                playsInline
                muted
                autoPlay
                aria-label="Imagem da câmera para ler o código de barras"
                style={{
                  display: 'block',
                  width: '100%',
                  maxHeight: fullScreen ? '60dvh' : '50vh',
                  objectFit: 'cover',
                }}
              />
              <div
                aria-hidden
                style={{
                  position: 'absolute',
                  top: '30%',
                  bottom: '30%',
                  left: '10%',
                  right: '10%',
                  border: `2px solid ${BRAND.orange}`,
                  borderRadius: 8,
                  pointerEvents: 'none',
                }}
              />
              {starting && (
                <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }}>
                  <Loader color="brand" />
                </div>
              )}
            </div>
            <Text size="sm" c={BRAND.textMuted} role="status" aria-live="polite">
              Aponte a câmera para o código de barras da embalagem e mantenha o produto parado.
            </Text>
            {diagnostico ? (
              <Text size="xs" c={BRAND.textMuted} data-testid="scanner-diagnostico" style={{ wordBreak: 'break-word' }}>
                {diagnostico}
              </Text>
            ) : null}
          </>
        )}
        <Button
          variant={error ? 'filled' : 'default'}
          color="brand"
          onClick={onClose}
          style={{ minHeight: TOUCH_TARGET_PX }}
        >
          Digitar o código
        </Button>
      </Stack>
    </Modal>
    </>
  );
}
