'use client';

import { Button, Modal } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import type { ExerciseCardSummary } from '@/lib/workout-plans-types';
import { BRAND } from '@/theme';

type Props = {
  // Card PUBLICADO do exercício efetivo. `null` não abre nada (quem chama só
  // oferece o atalho quando há card — ver `SetLoggerCard`/`ExerciseBriefingScreen`).
  card: ExerciseCardSummary | null;
  opened: boolean;
  onClose: () => void;
  exerciseName: string;
  // Só para teste (`renderToStaticMarkup` não renderiza portal). Padrão true.
  withinPortal?: boolean;
};

/**
 * "Como executar" no Modo Treino (cards ilustrados, PR5): o card 2:3 INTEIRO
 * do exercício (logo, título, cenas "1 Posição inicial"/"2 Movimento",
 * músculos — tudo já composto na imagem pelo backend, Estratégia B).
 *
 * - Celular: `Modal fullScreen` (mesmo padrão do `EquipmentSubstituteModal`);
 *   a imagem ocupa a área livre com `object-fit: contain`, então cabe inteira
 *   sem rolagem nem corte, e o "Fechar" fica embaixo, ao alcance do polegar
 *   (respeitando a área segura do iPhone). Desktop: modal centrado, imagem
 *   limitada a 75vh.
 * - iOS: `touch-action: manipulation` tira o zoom por toque duplo (que em
 *   overlay fixo deixa a tela ampliada e o "Fechar" fora de alcance) sem
 *   bloquear o pinça nativo; nada de zoom próprio.
 * - `<img>` nativo (o projeto usa `images.unoptimized`). A miniatura — já
 *   pré-carregada ao abrir a ficha — fica de fundo, no MESMO enquadramento,
 *   enquanto a imagem cheia chega (carregada sob demanda, só ao abrir).
 * - Sem `onError`: o card só chega aqui quando `exercise_card` não é nulo, e
 *   o backend só grava a versão depois dos 3 uploads (a CDN devolve
 *   `index.html` 200 em 403/404, então `onError` nem dispararia).
 */
export function ExerciseCardViewer({ card, opened, onClose, exerciseName, withinPortal = true }: Props) {
  const isMobile = useMediaQuery('(max-width: 48em)');
  const fullScreen = Boolean(isMobile);

  return (
    <Modal
      opened={opened && card != null}
      onClose={onClose}
      title="Como executar"
      size="lg"
      centered={!fullScreen}
      fullScreen={fullScreen}
      radius={fullScreen ? 0 : 'xl'}
      withinPortal={withinPortal}
      styles={{
        content: {
          background: BRAND.bg,
          display: 'flex',
          flexDirection: 'column',
          ...(fullScreen ? { height: '100dvh' } : {}),
        },
        header: { background: BRAND.bg },
        body: {
          flex: 1,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 'var(--mantine-spacing-md)',
          paddingBottom: 'calc(var(--mantine-spacing-md) + env(safe-area-inset-bottom))',
        },
      }}
    >
      {card && (
        <>
          <div
            data-testid="exercise-card-viewer-frame"
            style={{
              flex: 1,
              minHeight: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              touchAction: 'manipulation',
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={card.url}
              alt={`Como executar: ${exerciseName}`}
              width={1024}
              height={1536}
              decoding="async"
              style={{
                display: 'block',
                width: '100%',
                height: fullScreen ? '100%' : 'auto',
                maxHeight: fullScreen ? '100%' : '75vh',
                objectFit: 'contain',
                backgroundImage: `url("${card.thumbnail_url}")`,
                backgroundSize: 'contain',
                backgroundRepeat: 'no-repeat',
                backgroundPosition: 'center',
              }}
            />
          </div>
          <Button size="lg" radius="xl" h={52} fullWidth variant="light" color="gray" onClick={onClose}>
            Fechar
          </Button>
        </>
      )}
    </Modal>
  );
}
