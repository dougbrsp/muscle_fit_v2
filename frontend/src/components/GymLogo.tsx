'use client';

import { IconBuildingWarehouse } from '@tabler/icons-react';
import { BRAND } from '@/theme';

/**
 * Miniatura de logo de academia, servida como arquivo estático via
 * CloudFront + S3 (bucket `musclefit-uploads`) — ver
 * `api/app/gyms.py::_row_jsonable` (campo `logo_url`, `null` quando não há
 * logo). Mesma motivação/troca de `EquipmentPhoto.tsx`: substituiu o fluxo
 * antigo de `GET /gyms/{id}/logo` autenticado + blob + Object URL.
 */
type Props = {
  logoUrl?: string | null;
  alt: string;
  size?: number;
};

export function GymLogo({ logoUrl, alt, size = 32 }: Props) {
  if (logoUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img src={logoUrl} alt={alt} width={size} height={size} style={{ objectFit: 'cover', borderRadius: 8 }} />
    );
  }

  return <IconBuildingWarehouse size={size - 4} color={BRAND.textMuted} />;
}
