'use client';

import { useQuery } from '@tanstack/react-query';
import { Center, Text } from '@mantine/core';
import { IconVectorTriangle } from '@tabler/icons-react';
import { fetchPlanMuscleMapUrl } from '@/lib/plan-muscle-map';
import { BRAND } from '@/theme';

type Props = {
  planId: number;
};

/**
 * Mapa muscular (frente + costas) de um plano salvo — mesma imagem exibida
 * no app do relógio Zepp (`GET /workout-plans/{id}/muscle-map.png`,
 * autenticada via blob, ver `src/lib/plan-muscle-map.ts`). Só disponível
 * para planos já persistidos (precisa de `plan_id`) — não aparece no
 * rascunho ainda não salvo.
 */
export function PlanMuscleMap({ planId }: Props) {
  const mapQuery = useQuery({
    queryKey: ['plan-muscle-map', planId],
    queryFn: () => fetchPlanMuscleMapUrl(planId),
    staleTime: Infinity,
  });

  if (mapQuery.isLoading) {
    return null;
  }

  if (!mapQuery.data) {
    return (
      <Center style={{ flexDirection: 'column', gap: 8, minHeight: 160 }}>
        <IconVectorTriangle size={32} color={BRAND.textMuted} />
        <Text size="sm" c={BRAND.textMuted}>
          Mapa muscular indisponível para esta ficha.
        </Text>
      </Center>
    );
  }

  return (
    <img
      src={mapQuery.data}
      alt="Mapa muscular da ficha"
      style={{ maxWidth: '100%', height: 'auto', display: 'block', margin: '0 auto' }}
    />
  );
}
