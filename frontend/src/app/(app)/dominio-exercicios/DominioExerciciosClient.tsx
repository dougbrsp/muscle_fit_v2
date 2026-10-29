'use client';

import { Stack, Tabs, Text, Title } from '@mantine/core';
import { IconBarbell, IconChartBar, IconLink, IconQuestionMark, IconTools, IconStretching } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import { exerciseCardsFeatureEnabled } from '@/lib/exercise-cards';
import { BRAND } from '@/theme';
import { EquipmentTypesPanel } from './components/EquipmentTypesPanel';
import { ExercisesPanel } from './components/ExercisesPanel';
import { ExternalMappingsPanel } from './components/ExternalMappingsPanel';
import { MuscleGroupsPanel } from './components/MuscleGroupsPanel';
import { UnmappedExercisesPanel } from './components/UnmappedExercisesPanel';
import { VolumeClassesPanel } from './components/VolumeClassesPanel';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

/**
 * Tela "Admin / Domínio de Exercícios" (Fase 4, Etapa 18): CRUD do catálogo
 * canônico de exercícios (`exercise_catalog`) — cadastro, aliases,
 * mapeamentos externos (Amazfit/Zepp) e tipos de equipamento.
 *
 * Leitura aberta a qualquer autenticado; toda escrita é admin-only, mesmo
 * padrão estrito de `/mapeamentos` (Etapa 16) e `/equipamentos-exercicios`
 * (Etapa 17): `role === "admin"` via `/auth/me`, botões desabilitados +
 * tooltip para não-admin, e o 403 do backend continua sendo a garantia real.
 */
export function DominioExerciciosClient() {
  // Mesma query key usada pelo AppShellLayout/outras telas — o React Query
  // dedupa, não gera uma chamada extra a /auth/me.
  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: false,
  });
  const isAdmin = currentUserQuery.data?.role === 'admin';
  // Flag do AMBIENTE (não do usuário): liga a seção de card ilustrado no drawer.
  const exerciseCardsEnabled = exerciseCardsFeatureEnabled(currentUserQuery.data);

  return (
    <Stack gap="xl">
      <div>
        <Title order={1} c={BRAND.textPrimary}>
          Domínio de Exercícios
        </Title>
        <Text c={BRAND.textMuted} size="md">
          Catálogo canônico de exercícios (<code>exercise_catalog</code>): cadastro, apelidos (aliases) e
          mapeamentos externos (ex.: nome usado pelo Amazfit/Zepp — aba{' '}
          <Text component="span" c={BRAND.orange} fw={600}>
            Mapeamentos externos
          </Text>
          ), além dos tipos de equipamento do catálogo. Para vincular equipamento a exercício, use{' '}
          <Text component="span" c={BRAND.orange} fw={600}>
            Equipamentos x Exercícios
          </Text>
          .
        </Text>
      </div>

      <Tabs defaultValue="exercicios" keepMounted={false} color="brand">
        <Tabs.List>
          <Tabs.Tab value="exercicios" leftSection={<IconBarbell size={16} />}>
            Exercícios
          </Tabs.Tab>
          <Tabs.Tab value="equipamentos" leftSection={<IconTools size={16} />}>
            Tipos de equipamento
          </Tabs.Tab>
          <Tabs.Tab value="mapeamentos-externos" leftSection={<IconLink size={16} />}>
            Mapeamentos externos
          </Tabs.Tab>
          <Tabs.Tab value="grupos-musculares" leftSection={<IconStretching size={16} />}>
            Grupos musculares
          </Tabs.Tab>
          <Tabs.Tab value="macro-grupos" leftSection={<IconChartBar size={16} />}>
            Macro-grupos de volume
          </Tabs.Tab>
          <Tabs.Tab value="sem-classificacao" leftSection={<IconQuestionMark size={16} />}>
            Sem classificação
          </Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="exercicios" pt="md">
          <ExercisesPanel isAdmin={isAdmin} exerciseCardsEnabled={exerciseCardsEnabled} />
        </Tabs.Panel>

        <Tabs.Panel value="equipamentos" pt="md">
          <EquipmentTypesPanel isAdmin={isAdmin} />
        </Tabs.Panel>

        <Tabs.Panel value="mapeamentos-externos" pt="md">
          <ExternalMappingsPanel isAdmin={isAdmin} />
        </Tabs.Panel>

        <Tabs.Panel value="grupos-musculares" pt="md">
          <MuscleGroupsPanel isAdmin={isAdmin} />
        </Tabs.Panel>

        <Tabs.Panel value="macro-grupos" pt="md">
          <VolumeClassesPanel isAdmin={isAdmin} />
        </Tabs.Panel>

        <Tabs.Panel value="sem-classificacao" pt="md">
          <UnmappedExercisesPanel isAdmin={isAdmin} />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
