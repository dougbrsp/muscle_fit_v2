'use client';

import { Alert, Badge, Button, Group, Loader, Modal, Paper, Stack, Switch, Table, Text, TextInput, Tooltip } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconPencil, IconPlus, IconSearch } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type { ExerciseListResponse, ExerciseUpsertResponse } from '@/lib/exercise-catalog-admin-types';
import { isRepBased, measureLabel } from '@/lib/set-measures';
import { BRAND } from '@/theme';
import { ExecutionDescriptionBackfillPanel } from './ExecutionDescriptionBackfillPanel';
import { ExerciseDetailDrawer } from './ExerciseDetailDrawer';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

/**
 * Lista + busca do catálogo canônico (`GET /exercises`) e ponto de entrada
 * para criar (`POST /exercises`) e editar (drawer com `GET /exercises/{id}`
 * agregado + aliases + mapeamentos externos).
 */
export function ExercisesPanel({
  isAdmin,
  exerciseCardsEnabled = false,
}: {
  isAdmin: boolean;
  /** `features.exercise_cards` de `/auth/me` — repassado ao drawer (seção de card ilustrado). */
  exerciseCardsEnabled?: boolean;
}) {
  const qc = useQueryClient();

  const [search, setSearch] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  const [activeOnly, setActiveOnly] = useState(true);

  const exercisesQuery = useQuery({
    queryKey: ['exercise-catalog-admin-exercises', appliedSearch, activeOnly],
    queryFn: () => {
      const params = new URLSearchParams({ query: appliedSearch, active_only: String(activeOnly), limit: '500' });
      return apiFetch<ExerciseListResponse>(`/exercises?${params.toString()}`);
    },
  });

  const items = exercisesQuery.data?.items || [];

  function runSearch() {
    setAppliedSearch(search.trim());
  }

  // ---- Drawer de detalhe/edição --------------------------------------------
  const [detailOpen, { open: openDetail, close: closeDetailRaw }] = useDisclosure(false);
  const [selectedExerciseId, setSelectedExerciseId] = useState<number | null>(null);

  function closeDetail() {
    closeDetailRaw();
    setSelectedExerciseId(null);
  }

  function startEdit(id: number) {
    setSelectedExerciseId(id);
    openDetail();
  }

  // ---- Modal de criação (POST /exercises, admin) ---------------------------
  const [createOpen, { open: openCreateRaw, close: closeCreateRaw }] = useDisclosure(false);
  const [newName, setNewName] = useState('');

  function openCreate() {
    setNewName('');
    createMutation.reset();
    openCreateRaw();
  }

  function closeCreate() {
    closeCreateRaw();
    setNewName('');
    createMutation.reset();
  }

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch<ExerciseUpsertResponse>('/exercises', {
        method: 'POST',
        body: JSON.stringify({ name: newName.trim() }),
      }),
    onSuccess: async (data) => {
      closeCreate();
      await qc.invalidateQueries({ queryKey: ['exercise-catalog-admin-exercises'] });
      // Abre direto a edição do exercício recém-criado, para completar
      // aliases/mapeamentos/demais campos sem precisar buscar de novo na lista.
      startEdit(data.item.id);
    },
  });

  return (
    <Stack gap="md">
      {/* Backfill das descrições de execução (M4): fica ACIMA da lista porque
          é uma ação sobre o catálogo inteiro, não sobre o exercício
          selecionado — e porque o contador de pendentes é a informação que o
          admin precisa ver ao chegar na tela. */}
      <ExecutionDescriptionBackfillPanel isAdmin={isAdmin} />

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" align="flex-end" mb="md" wrap="wrap">
          <Group align="flex-end" wrap="wrap" gap="sm">
            <TextInput
              label="Buscar"
              placeholder="Nome, movimento, palavra-chave..."
              value={search}
              onChange={(event) => setSearch(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') runSearch();
              }}
              w={320}
            />
            <Button variant="default" leftSection={<IconSearch size={16} />} onClick={runSearch}>
              Buscar
            </Button>
            <Switch
              label="Somente ativos"
              checked={activeOnly}
              onChange={(event) => setActiveOnly(event.currentTarget.checked)}
              mb={4}
            />
          </Group>
          {isAdmin ? (
            <Button color="brand" leftSection={<IconPlus size={16} />} onClick={openCreate}>
              Novo exercício
            </Button>
          ) : (
            <Tooltip label="Ação restrita a administradores">
              <Button color="brand" leftSection={<IconPlus size={16} />} disabled>
                Novo exercício
              </Button>
            </Tooltip>
          )}
        </Group>

        {exercisesQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
            {errorMessage(exercisesQuery.error, 'Não foi possível carregar o catálogo de exercícios.')}
          </Alert>
        )}

        {exercisesQuery.isLoading && (
          <Group>
            <Loader size="sm" color="brand" />
            <Text c={BRAND.textMuted}>Carregando exercícios...</Text>
          </Group>
        )}

        {!exercisesQuery.isLoading && !exercisesQuery.isError && (
          <>
            <Text size="sm" c={BRAND.textMuted} mb="sm">
              {exercisesQuery.data?.total?.toLocaleString('pt-BR') ?? 0} exercício(s) encontrado(s).
            </Text>
            <Table.ScrollContainer minWidth={700}>
              <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Nome</Table.Th>
                    <Table.Th>Movimento</Table.Th>
                    <Table.Th>Padrão biomecânico</Table.Th>
                    <Table.Th>Flags</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {items.map((exercise) => (
                    <Table.Tr key={exercise.id}>
                      <Table.Td c={BRAND.textPrimary}>{exercise.name}</Table.Td>
                      <Table.Td c={BRAND.textMuted}>{exercise.movement || '-'}</Table.Td>
                      <Table.Td c={BRAND.textMuted}>{exercise.biomechanical_pattern || '-'}</Table.Td>
                      <Table.Td>
                        <Group gap={4}>
                          {exercise.is_compound ? (
                            <Badge size="sm" variant="light" color="grape">
                              Composto
                            </Badge>
                          ) : null}
                          {exercise.is_unilateral ? (
                            <Badge size="sm" variant="light" color="cyan">
                              Unilateral
                            </Badge>
                          ) : null}
                          {Number(exercise.is_laboral ?? 0) ? (
                            <Badge size="sm" variant="light" color="orange" title="Sem progressão: Concluir/Pular no Modo Treino">
                              Laboral
                            </Badge>
                          ) : null}
                          {!isRepBased(exercise.default_measure) ? (
                            <Badge size="sm" variant="light" color="teal" title="Medida padrão da série">
                              {measureLabel(exercise.default_measure)}
                            </Badge>
                          ) : null}
                        </Group>
                      </Table.Td>
                      <Table.Td>
                        <Badge color={exercise.is_active ? 'green' : 'gray'} variant="light">
                          {exercise.is_active ? 'Ativo' : 'Inativo'}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Tooltip label={isAdmin ? 'Editar exercício' : 'Ver detalhe (somente leitura)'}>
                          <Button size="xs" variant="light" leftSection={<IconPencil size={14} />} onClick={() => startEdit(exercise.id)}>
                            {isAdmin ? 'Editar' : 'Ver'}
                          </Button>
                        </Tooltip>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                  {items.length === 0 && (
                    <Table.Tr>
                      <Table.Td colSpan={6}>
                        <Text c={BRAND.textMuted} ta="center">
                          Nenhum exercício encontrado.
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </>
        )}
      </Paper>

      {/* ---- Modal: novo exercício (POST /exercises) ------------------------ */}
      <Modal opened={createOpen} onClose={closeCreate} title="Novo exercício" centered>
        <Stack gap="sm">
          <Text size="sm" c={BRAND.textMuted}>
            Informe o nome para criar o registro; os demais campos (movimento, padrão biomecânico, apelidos,
            mapeamentos externos...) são preenchidos na tela de edição, aberta automaticamente após criar.
          </Text>
          <TextInput
            label="Nome do exercício"
            placeholder="Ex.: Supino reto na máquina"
            value={newName}
            onChange={(event) => setNewName(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && newName.trim()) createMutation.mutate();
            }}
            required
          />
          {createMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {isForbidden(createMutation.error)
                ? 'Ação restrita a administradores.'
                : errorMessage(createMutation.error, 'Não foi possível criar o exercício.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeCreate}>
              Cancelar
            </Button>
            <Button
              color="brand"
              loading={createMutation.isPending}
              disabled={!newName.trim()}
              onClick={() => createMutation.mutate()}
            >
              Criar e continuar editando
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* ---- Drawer: detalhe/edição (GET/PUT /exercises/{id} + relacionados) */}
      <ExerciseDetailDrawer
        opened={detailOpen}
        exerciseId={selectedExerciseId}
        isAdmin={isAdmin}
        exerciseCardsEnabled={exerciseCardsEnabled}
        onClose={closeDetail}
        onSaved={() => qc.invalidateQueries({ queryKey: ['exercise-catalog-admin-exercises'] })}
      />
    </Stack>
  );
}
