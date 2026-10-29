'use client';

import { IconPhoto } from '@tabler/icons-react';
import { BRAND } from '@/theme';

/**
 * Miniatura de foto de equipamento do catálogo, servida como arquivo
 * estático via CloudFront + S3 (bucket `musclefit-uploads`) — ver
 * `api/app/gyms.py::_row_jsonable` (campo `photo_url`, `null` quando não há
 * foto). Substituiu o fluxo antigo de `GET /equipment/{id}/photo`
 * autenticado + blob + Object URL: cada `<img>` disparava uma invocação da
 * Lambda `musclefit-api-core`, e uma tela com várias fotos de equipamento
 * estourava o limite de concorrência da conta (10, compartilhado entre as 6
 * Lambdas do projeto), resultando em 500 no navegador (Throttles no
 * CloudWatch correlacionado com picos de Invocations).
 *
 * Sem `useQuery`/cache manual aqui: `photoUrl` já vem pronto e estável do
 * backend (campo `photo_url` de cada endpoint que devolve equipamento), e o
 * navegador cacheia a imagem normalmente via HTTP (CloudFront
 * CachingOptimized) — sem falha nunca bloqueante: sem `photoUrl`, cai no
 * ícone placeholder.
 */
type Props = {
  photoUrl?: string | null;
  alt: string;
  size?: number;
};

export function EquipmentPhoto({ photoUrl, alt, size = 32 }: Props) {
  if (photoUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={photoUrl} alt={alt} width={size} height={size} style={{ objectFit: 'cover', borderRadius: 8 }} />
    );
  }

  return <IconPhoto size={size - 4} color={BRAND.textMuted} />;
}
