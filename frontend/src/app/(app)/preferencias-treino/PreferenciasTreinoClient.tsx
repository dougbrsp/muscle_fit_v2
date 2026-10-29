'use client';

import {
  Alert,
  Badge,
  Group,
  Loader,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
  UnstyledButton,
} from '@mantine/core';
import { IconAlertTriangle, IconSearch } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { PhotoLightbox, usePhotoLightbox } from '@/app/(fullscreen)/modo-treino/PhotoLightbox';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import { PairStarsControl, usePairStars } from '@/components/PairStarsControl';
import { apiFetch, ApiError } from '@/lib/api';
import {
  buildTrainingPreferencesPath,
  displayedPairStars,
  groupTrainingPreferences,
  PAIR_STARS_HELP,
  TRAINING_PREFERENCES_QUERY_KEY,
  type TrainingPreferenceItem,
  type TrainingPreferencesResponse,
} from '@/lib/equipment-recommend';
import type { ApiListResponse, Gym } from '@/lib/gyms-types';
import { BRAND } from '@/theme';

const THUMB_SIZE = 56;

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/**
 * "Preferências de Treino" (2026-10-13): para a academia escolhida, lista os
 * exercícios com os equipamentos disponíveis (`GET /me/training-preferences`)
 * e deixa o usuário dar a nota de 1 a 5 estrelas a cada par
 * exercício+equipamento. A nota é gravada em `/me/equipment-exercise-preferences`
 * (mesmo hook do Domínio, com atualização otimista) e alimenta o ranking de
 * equipamento do backend (`domain/equipment_ranking.py`) nas fichas, no Modo
 * Treino e nas trocas. A tela não ordena nem calcula nada: só exibe e grava.
 */
export function PreferenciasTreinoClient() {
  const qc = useQueryClient();
  const lightbox = usePhotoLightbox();
  const [selectedGymId, setSelectedGymId] = useState<number | null>(null);
  const [search, setSearch] = useState('');

  const gymsQuery = useQuery({
    queryKey: ['gyms'],
    queryFn: () => apiFetch<ApiListResponse<Gym>>('/gyms?active_only=true'),
  });
  const gyms = useMemo(() => gymsQuery.data?.items ?? [], [gymsQuery.data]);

  // Sem academia escolhida, o backend responde com a academia da ficha mais
  // recente (`default_gym_id`).
  const preferencesQuery = useQuery({
    queryKey: [...TRAINING_PREFERENCES_QUERY_KEY, selectedGymId],
    queryFn: () => apiFetch<TrainingPreferencesResponse>(buildTrainingPreferencesPath(selectedGymId)),
    retry: false,
  });

  // Usuário sem ficha com academia: cai na primeira academia da lista.
  const firstGymId = gyms[0]?.id ?? null;
  const resolvedGymId = preferencesQuery.data?.gym_id ?? null;
  useEffect(() => {
    if (selectedGymId === null && preferencesQuery.isSuccess && resolvedGymId === null && firstGymId !== null) {
      setSelectedGymId(firstGymId);
    }
  }, [selectedGymId, preferencesQuery.isSuccess, resolvedGymId, firstGymId]);

  const activeGymId = selectedGymId ?? resolvedGymId;
  const pairStars = usePairStars();
  const starsCache = pairStars.query.isSuccess ? pairStars.stars : null;

  const groups = useMemo(
    () => groupTrainingPreferences(preferencesQuery.data?.items, search),
    [preferencesQuery.data, search],
  );
  const totalPairs = preferencesQuery.data?.items.length ?? 0;

  function changeStars(item: TrainingPreferenceItem, stars: number | null) {
    pairStars.mutation.mutate(
      {
        equipmentExerciseId: item.equipment_exercise_id,
        equipmentId: item.equipment_id,
        exerciseId: item.exercise_id,
        equipmentName: item.equipment_name,
        exerciseName: item.exercise_name,
        stars,
      },
      { onSettled: () => void qc.invalidateQueries({ queryKey: TRAINING_PREFERENCES_QUERY_KEY }) },
    );
  }

  return (
    <Stack gap="lg">
      <div>
        <Title order={1} c={BRAND.textPrimary}>
          Preferências de Treino
        </Title>
        <Text c={BRAND.textMuted} size="md">
          Dê sua nota a cada exercício em cada equipamento da academia. {PAIR_STARS_HELP}
        </Text>
      </div>

      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
        <Select
          label="Academia"
          placeholder={gymsQuery.isLoading ? 'Carregando academias...' : 'Escolha a academia'}
          data={gyms.map((gym) => ({ value: String(gym.id), label: gym.name }))}
          value={activeGymId ? String(activeGymId) : null}
          onChange={(value) => setSelectedGymId(value ? Number(value) : null)}
          allowDeselect={false}
          searchable
          nothingFoundMessage="Nenhuma academia"
        />
        <TextInput
          label="Buscar"
          placeholder="Exercício, equipamento ou grupo muscular"
          leftSection={<IconSearch size={16} />}
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
        />
      </SimpleGrid>

      {gymsQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(gymsQuery.error, 'Não foi possível carregar as academias.')}
        </Alert>
      )}
      {pairStars.mutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(pairStars.mutation.error, 'Não foi possível salvar sua nota. Tente de novo.')}
        </Alert>
      )}

      {preferencesQuery.isLoading ? (
        <Group justify="center" py="xl">
          <Loader color="brand" />
        </Group>
      ) : preferencesQuery.isError ? (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(preferencesQuery.error, 'Não foi possível carregar os exercícios desta academia.')}
        </Alert>
      ) : activeGymId === null ? (
        <Text c={BRAND.textMuted}>Cadastre uma academia para avaliar os equipamentos.</Text>
      ) : totalPairs === 0 ? (
        <Text c={BRAND.textMuted}>Esta academia ainda não tem equipamentos vinculados a exercícios.</Text>
      ) : groups.length === 0 ? (
        <Text c={BRAND.textMuted}>Nada encontrado para &quot;{search}&quot;.</Text>
      ) : (
        groups.map((group) => (
          <Stack key={group.group} gap="sm">
            <Title order={3} c={BRAND.textPrimary}>
              {group.group}
            </Title>
            <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
              {group.exercises.map((exercise) => (
                <Paper key={exercise.exerciseId} withBorder radius="md" p="md">
                  <Group justify="space-between" mb="xs" wrap="nowrap">
                    <Text fw={600} c={BRAND.textPrimary}>
                      {exercise.exerciseName}
                    </Text>
                    <Badge variant="light" color="gray">
                      {exercise.pairs.length} {exercise.pairs.length === 1 ? 'equipamento' : 'equipamentos'}
                    </Badge>
                  </Group>
                  <Stack gap="xs">
                    {exercise.pairs.map((pair) => {
                      const equipmentName = pair.equipment_name || `Equipamento #${pair.equipment_id}`;
                      const pairLabel = `${exercise.exerciseName} em ${equipmentName}`;
                      return (
                        <Group key={pair.equipment_exercise_id} justify="space-between" gap="sm">
                          <Group gap="sm" wrap="nowrap" style={{ minWidth: 0, flex: 1 }}>
                            <UnstyledButton
                              aria-label={pair.photo_url ? `Ampliar foto de ${equipmentName}` : `${equipmentName} sem foto`}
                              disabled={!pair.photo_url}
                              onClick={() => pair.photo_url && lightbox.open({ src: pair.photo_url, alt: equipmentName })}
                              style={{
                                width: THUMB_SIZE,
                                height: THUMB_SIZE,
                                flex: `0 0 ${THUMB_SIZE}px`,
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'center',
                                cursor: pair.photo_url ? 'zoom-in' : 'default',
                              }}
                            >
                              <EquipmentPhoto photoUrl={pair.photo_url} alt={equipmentName} size={THUMB_SIZE} />
                            </UnstyledButton>
                            <Text size="sm" c={BRAND.textPrimary} style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
                              {equipmentName}
                            </Text>
                          </Group>
                          <PairStarsControl
                            value={displayedPairStars(pair, starsCache)}
                            label={pairLabel}
                            onChange={(stars) => changeStars(pair, stars)}
                          />
                        </Group>
                      );
                    })}
                  </Stack>
                </Paper>
              ))}
            </SimpleGrid>
          </Stack>
        ))
      )}

      <PhotoLightbox photo={lightbox.photo} onClose={lightbox.close} />
    </Stack>
  );
}
