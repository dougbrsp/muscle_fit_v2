'use client';

import { Alert, Button, Loader, Modal, Stack, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconCameraOff } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import { CAMERA_ERROR_MESSAGES, CameraError, startBarcodeScan, type ScanHandle } from '@/lib/barcode-scanner';
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
  const [starting, setStarting] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const onDetectedRef = useRef(onDetected);
  onDetectedRef.current = onDetected;

  useEffect(() => {
    if (!opened || !video) return undefined;
    let cancelled = false;
    let handle: ScanHandle | null = null;
    setStarting(true);
    setError(null);
    startBarcodeScan(video, (code) => {
      if (!cancelled) onDetectedRef.current(code);
    })
      .then((started) => {
        if (cancelled) {
          started.stop();
          return;
        }
        handle = started;
        setStarting(false);
      })
      .catch((falha: unknown) => {
        if (cancelled) return;
        setError(falha instanceof CameraError ? falha.message : CAMERA_ERROR_MESSAGES.unknown);
        setStarting(false);
      });
    return () => {
      cancelled = true;
      handle?.stop(); // libera a câmera ao fechar ou desmontar
    };
  }, [opened, video]);

  return (
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
  );
}
