'use client';

import { IconPhoto } from '@tabler/icons-react';
import { useState } from 'react';
import { BRAND } from '@/theme';
import styles from '../workout-mode.module.css';

/**
 * Foto do equipamento/exercício no Modo Treino. Mesma fonte e mesma lógica de
 * `components/EquipmentPhoto.tsx`: `photoUrl` já vem pronto do backend
 * (arquivo estático via CloudFront + S3, sem request autenticado). Sem foto
 * — ou se a imagem falhar ao carregar — mostra um placeholder neutro do
 * MESMO tamanho, para o layout não pular nem desalinhar.
 */
export function ExerciseThumbnail({
  photoUrl,
  alt,
  size = 112,
}: {
  photoUrl?: string | null;
  alt: string;
  size?: number;
}) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const showPhoto = Boolean(photoUrl) && failedUrl !== photoUrl;

  return (
    <div className={styles.thumbnail} style={{ width: size, height: size }} data-testid="exercise-thumbnail">
      {showPhoto ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={photoUrl as string}
          alt={alt}
          width={size}
          height={size}
          decoding="async"
          onError={() => setFailedUrl(photoUrl ?? null)}
        />
      ) : (
        <IconPhoto size={Math.round(size * 0.4)} color={BRAND.textMuted} role="img" aria-label={`Sem foto: ${alt}`} />
      )}
    </div>
  );
}
