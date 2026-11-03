'use client';

import { Alert, Button, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconCheck } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useState } from 'react';
import { CheckinReview } from '@/components/nutrition/CheckinReview';
import { NutritionLimitations } from '@/components/nutrition/NutritionLimitations';
import { NutritionSubnav } from '@/components/nutrition/NutritionSubnav';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import { nutritionErrorInfo } from '@/lib/nutrition-api';
import { acceptCheckin, checkinKeys, declineCheckin, fetchCheckin } from '@/lib/nutrition-checkin';
import { targetsChangedKeys } from '@/lib/nutrition-plan-form';
import { BRAND } from '@/theme';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

/**
 * `/nutricao/checkin` — revisão do check-in semanal (N7): a semana contra o alvo, o que muda e por quê, e
 * aceitar ou manter as metas. O motor decide tudo (`domain/nutrition/weekly_checkin`); a tela só exibe. Aceitar
 * publica uma nova versão do plano; por isso invalida o plano, o diário e o painel (`targetsChangedKeys`).
 */
export function CheckinClient() {
  const qc = useQueryClient();
  const [resultado, setResultado] = useState<string | null>(null);

  const currentUserQuery = useQuery({ queryKey: ['current-user'], queryFn: fetchCurrentUser, staleTime: 60_000, retry: false });

  const consulta = useQuery({
    queryKey: checkinKeys.current(),
    queryFn: () => fetchCheckin(),
    enabled: currentUserQuery.isSuccess,
    retry: false,
  });

  const depois = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: checkinKeys.all }),
      ...targetsChangedKeys().map((queryKey) => qc.invalidateQueries({ queryKey })),
    ]);
  };
  const aceitar = useMutation({
    mutationFn: (id: number) => acceptCheckin(id),
    onSuccess: async (resposta) => {
      setResultado(
        resposta.already_accepted ? 'Esse check-in já tinha sido aceito.' : 'Novas metas aceitas: o plano foi atualizado a partir de hoje.',
      );
      await depois();
    },
    onError: depois,
  });
  const recusar = useMutation({
    mutationFn: (id: number) => declineCheckin(id),
    onSuccess: async () => {
      setResultado('Tudo certo: as metas atuais continuam.');
      await depois();
    },
    onError: depois,
  });

  const checkin = consulta.data?.checkin ?? null;
  const erro = aceitar.isError
    ? nutritionErrorInfo(aceitar.error, 'Não foi possível aceitar agora.').message
    : recusar.isError
      ? nutritionErrorInfo(recusar.error, 'Não foi possível responder agora.').message
      : null;

  return (
    <Stack gap="lg">
      <Stack gap="xs">
        <NutritionSubnav active="/nutricao/checkin" />
        <Title order={1} c={BRAND.textPrimary}>
          Check-in semanal
        </Title>
        <Text c={BRAND.textMuted}>
          Uma vez por semana a semana fechada é avaliada com o seu gasto, o que você comeu, o peso e a bioimpedância. Se as metas
          precisarem mudar, você confirma com um toque.{' '}
          <Link href="/nutricao/plano" style={{ color: BRAND.orange }}>
            Escolher o dia do check-in
          </Link>
        </Text>
      </Stack>

      {resultado && (
        <Alert color="green" icon={<IconCheck size={16} />} role="status">
          {resultado}
        </Alert>
      )}
      {(currentUserQuery.isLoading || consulta.isLoading) && <Text c={BRAND.textMuted}>Carregando...</Text>}
      {consulta.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {nutritionErrorInfo(consulta.error, 'Não foi possível carregar o check-in.').message}
          <Button variant="subtle" size="xs" ml="xs" onClick={() => void consulta.refetch()}>
            Tentar de novo
          </Button>
        </Alert>
      )}
      {consulta.data && !checkin && (
        <Stack gap="xs">
          <NutritionLimitations items={consulta.data.none_items} title="Ainda não há check-in" tone="info" />
          <Text size="sm" c={BRAND.textMuted}>
            Próximo check-in: {consulta.data.settings.weekday_label}, depois que a semana fechar.
          </Text>
        </Stack>
      )}
      {checkin && (
        <CheckinReview
          checkin={checkin}
          busy={aceitar.isPending || recusar.isPending}
          error={erro}
          onAccept={() => aceitar.mutate(checkin.id)}
          onDecline={() => recusar.mutate(checkin.id)}
        />
      )}
    </Stack>
  );
}
