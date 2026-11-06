'use client';

import { useId } from 'react';
import { Button, Card, Stack, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { ActivityDayList } from './ActivityDayList';
import { AdaptiveEnergyStatus } from './AdaptiveEnergyCard';
import { TdeeBreakdownLines } from './TdeeBreakdown';
import type { NutritionDayEnergyDetail } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

/**
 * "Gasto do dia" do diário: composição do gasto (parcelas, notas e total do motor), atividades importadas do dia
 * com o estado confirmado/pendente e, no adaptativo, confiança e pendências. Só exibe o que a API mandou
 * (`energy_detail`); sem ele, ou com tudo nulo, não renderiza nada.
 */
export function DayEnergySection({ detail }: { detail: NutritionDayEnergyDetail | null | undefined }) {
  const [aberto, { toggle }] = useDisclosure(false);
  const painelId = useId();
  const breakdown = detail?.breakdown && detail.breakdown.parts.length > 0 ? detail.breakdown : null;
  const atividades = detail?.activities ?? null;
  const energia = detail?.energy ?? null;
  if (!breakdown && !atividades && !energia) return null;
  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface} data-testid="gasto-do-dia">
      <Stack gap="xs">
        <Title order={4} c={BRAND.textPrimary}>
          Gasto do dia
        </Title>
        <Button
          variant="subtle"
          size="compact-xs"
          onClick={toggle}
          style={{ alignSelf: 'flex-start' }}
          data-testid="gasto-do-dia-toggle"
          aria-expanded={aberto}
          aria-controls={painelId}
        >
          {aberto ? 'Ocultar detalhes do gasto' : 'Ver como o gasto foi calculado'}
        </Button>
        <div id={painelId} hidden={!aberto}>
          {aberto && (
            <Stack gap="sm" data-testid="gasto-do-dia-painel">
              {breakdown && <TdeeBreakdownLines breakdown={breakdown} />}
              {energia && (
                <Stack gap={4} data-testid="gasto-do-dia-energia">
                  <AdaptiveEnergyStatus state={energia} />
                </Stack>
              )}
              {atividades && (
                <Stack gap={4} data-testid="gasto-do-dia-atividades">
                  <Text size="sm" fw={600} c={BRAND.textPrimary}>
                    Atividades do dia
                  </Text>
                  <ActivityDayList day={atividades} showDate={false} editable />
                </Stack>
              )}
            </Stack>
          )}
        </div>
      </Stack>
    </Card>
  );
}
