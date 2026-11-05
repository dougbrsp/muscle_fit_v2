import { Badge } from '@mantine/core';
import type { FoodOrigin } from '@/lib/nutrition-types';

type Props = {
  /** Origem pronta da API (`origin` do alimento); sem ela usa `fallback` (alimento de resposta antiga). */
  origin?: FoodOrigin | null;
  fallback?: string | null;
  isPublic?: boolean;
  size?: 'xs' | 'sm';
};

/** Selo único da origem do alimento (IA, rótulo, código de barras, digitado, tabela). Não decide nada. */
export function FoodOriginBadge({ origin, fallback = null, isPublic = true, size = 'sm' }: Props) {
  const texto = origin?.label ?? fallback;
  if (!texto) return null;
  return (
    <Badge size={size} variant="light" color={isPublic ? 'brand' : 'gray'} data-origin={origin?.code} style={{ flex: '0 0 auto' }}>
      {texto}
    </Badge>
  );
}
