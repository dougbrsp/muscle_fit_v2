'use client';

import { Modal } from '@mantine/core';
import { useState } from 'react';

export type LightboxPhoto = { src: string; alt: string };

/**
 * Hook simples para abrir/fechar o lightbox de foto de equipamento —
 * reaproveitado por `SetLoggerCard` (e, na Parte B, pelo card de
 * aquecimento). `src` deve ser a URL pública estática já resolvida (campo
 * `photo_url` da API, servido via CloudFront + S3 — ver
 * `api/app/gyms.py::_row_jsonable`) — este componente não faz fetch, só
 * amplia o que já foi carregado.
 */
export function usePhotoLightbox() {
  const [photo, setPhoto] = useState<LightboxPhoto | null>(null);
  return {
    photo,
    open: (photo: LightboxPhoto) => setPhoto(photo),
    close: () => setPhoto(null),
  };
}

type Props = {
  photo: LightboxPhoto | null;
  onClose: () => void;
};

// Modal de ampliação de foto — `<img>` com maxHeight 80vh/objectFit contain.
export function PhotoLightbox({ photo, onClose }: Props) {
  return (
    <Modal opened={!!photo} onClose={onClose} title={photo?.alt} size="xl" centered>
      {photo && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={photo.src}
          alt={photo.alt}
          style={{ maxWidth: '100%', maxHeight: '80vh', objectFit: 'contain', display: 'block', margin: '0 auto' }}
        />
      )}
    </Modal>
  );
}
