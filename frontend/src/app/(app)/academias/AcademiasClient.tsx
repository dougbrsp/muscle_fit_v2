'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconPencil,
  IconPlus,
  IconTrash,
  IconUpload,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChangeEvent, useMemo, useRef, useState } from 'react';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import { GymLogo } from '@/components/GymLogo';
import { apiFetch, ApiError } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import {
  ApiListResponse,
  Equipment,
  Gym,
  GymEquipmentCatalogLink,
  RecommendAllResponse,
  WEEKDAYS,
  toTimeInputValue,
  weekdayLabels,
} from '@/lib/gyms-types';
import { BRAND } from '@/theme';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function AcademiasClient() {
  const qc = useQueryClient();
  const isMobile = useMediaQuery('(max-width: 48em)');

  // Mesma query key usada pelo AppShellLayout — o React Query dedupa e não
  // dispara uma segunda chamada de verdade a /auth/me.
  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: false,
  });
  const isAdmin = currentUserQuery.data?.role === 'admin';

  // ---- Nova academia -------------------------------------------------
  const [newGymName, setNewGymName] = useState('');
  const [newGymDays, setNewGymDays] = useState<number[]>([]);
  const [newGymRecurring, setNewGymRecurring] = useState(true);

  const [selectedGymId, setSelectedGymId] = useState<number | null>(null);

  const gymsQuery = useQuery({
    queryKey: ['gyms'],
    queryFn: () => apiFetch<ApiListResponse<Gym>>('/gyms?active_only=true'),
  });

  const equipmentQuery = useQuery({
    queryKey: ['equipment-catalog'],
    queryFn: () => apiFetch<ApiListResponse<Equipment>>('/equipment?active_only=true'),
  });

  const activeGymId = selectedGymId ?? gymsQuery.data?.items[0]?.id ?? null;

  const gymCatalogQuery = useQuery({
    queryKey: ['gym-equipment-catalog', activeGymId],
    queryFn: () =>
      apiFetch<ApiListResponse<GymEquipmentCatalogLink>>(
        `/gym-equipment-catalog?gym_id=${activeGymId}&active_only=true`,
      ),
    enabled: !!activeGymId,
  });

  const recommendAllQuery = useQuery({
    queryKey: ['gym-equipment-recommend-all', activeGymId],
    queryFn: () => apiFetch<RecommendAllResponse>(`/gym-equipment/recommend-all?gym_id=${activeGymId}`),
    enabled: !!activeGymId,
  });

  const recommendGroups = useMemo(() => {
    const groups = recommendAllQuery.data?.groups || {};
    return Object.entries(groups).sort((a, b) => a[0].localeCompare(b[0]));
  }, [recommendAllQuery.data]);

  const gymHasEquipmentId = useMemo(() => {
    const map = new Map<number, number>();
    for (const item of gymCatalogQuery.data?.items || []) {
      map.set(item.equipment_id, item.id);
    }
    return map;
  }, [gymCatalogQuery.data]);

  const createGymMutation = useMutation({
    mutationFn: () =>
      apiFetch('/gyms', {
        method: 'POST',
        body: JSON.stringify({
          name: newGymName,
          usual_weekdays: newGymRecurring ? newGymDays : [],
          is_recurring: newGymRecurring,
        }),
      }),
    onSuccess: async () => {
      setNewGymName('');
      setNewGymDays([]);
      setNewGymRecurring(true);
      await qc.invalidateQueries({ queryKey: ['gyms'] });
    },
  });

  const updateGymDaysMutation = useMutation({
    mutationFn: ({ id, usual_weekdays }: { id: number; usual_weekdays: number[] }) =>
      apiFetch(`/gyms/${id}`, { method: 'PUT', body: JSON.stringify({ usual_weekdays }) }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['gyms'] });
    },
  });

  const updateGymRecurringMutation = useMutation({
    mutationFn: ({ id, is_recurring }: { id: number; is_recurring: boolean }) =>
      apiFetch(`/gyms/${id}`, { method: 'PUT', body: JSON.stringify({ is_recurring }) }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['gyms'] });
    },
  });

  // ---- Edição de academia (admin-only no backend: nome/logo/horário) ----
  const [editGymOpen, { open: openEditGymRaw, close: closeEditGym }] = useDisclosure(false);
  const [editGymTarget, setEditGymTarget] = useState<Gym | null>(null);
  const [editGymName, setEditGymName] = useState('');
  const [editGymOpensAt, setEditGymOpensAt] = useState('');
  const [editGymClosesAt, setEditGymClosesAt] = useState('');
  const gymLogoInputRef = useRef<HTMLInputElement | null>(null);

  function openEditGym(gym: Gym) {
    setEditGymTarget(gym);
    setEditGymName(gym.name);
    setEditGymOpensAt(toTimeInputValue(gym.opens_at));
    setEditGymClosesAt(toTimeInputValue(gym.closes_at));
    openEditGymRaw();
  }

  const updateGymDetailsMutation = useMutation({
    mutationFn: ({
      id,
      name,
      opens_at,
      closes_at,
    }: {
      id: number;
      name: string;
      opens_at: string | null;
      closes_at: string | null;
    }) => apiFetch(`/gyms/${id}`, { method: 'PUT', body: JSON.stringify({ name, opens_at, closes_at }) }),
    onSuccess: async () => {
      closeEditGym();
      setEditGymTarget(null);
      await qc.invalidateQueries({ queryKey: ['gyms'] });
    },
  });

  function saveEditGym() {
    if (!editGymTarget) return;
    updateGymDetailsMutation.mutate({
      id: editGymTarget.id,
      name: editGymName.trim(),
      opens_at: editGymOpensAt || null,
      closes_at: editGymClosesAt || null,
    });
  }

  const uploadGymLogoMutation = useMutation({
    mutationFn: async ({ id, file }: { id: number; file: File }) => {
      const form = new FormData();
      form.append('file', file);
      return apiFetch(`/gyms/${id}/logo`, { method: 'POST', body: form });
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['gyms'] });
    },
  });

  function triggerGymLogoUpload() {
    gymLogoInputRef.current?.click();
  }

  function handleGymLogoSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (file && editGymTarget) {
      uploadGymLogoMutation.mutate({ id: editGymTarget.id, file });
    }
  }

  const [removeGymLogoOpen, { open: openRemoveGymLogo, close: closeRemoveGymLogo }] = useDisclosure(false);

  const removeGymLogoMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/gyms/${id}/logo`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeRemoveGymLogo();
      await qc.invalidateQueries({ queryKey: ['gyms'] });
    },
  });

  // Reflete o has_logo/updated_at mais recente da lista (após upload/remoção)
  // sem perder o formulário de edição em andamento no modal.
  const liveEditGym = editGymTarget
    ? gymsQuery.data?.items.find((g) => g.id === editGymTarget.id) ?? editGymTarget
    : null;

  const [deleteGymOpen, { open: openDeleteGym, close: closeDeleteGym }] = useDisclosure(false);
  const [deleteGymTarget, setDeleteGymTarget] = useState<Gym | null>(null);

  function openDeleteGymConfirm(gym: Gym) {
    setDeleteGymTarget(gym);
    openDeleteGym();
  }

  const deactivateGymMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/gyms/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeDeleteGym();
      setDeleteGymTarget(null);
      await qc.invalidateQueries({ queryKey: ['gyms'] });
    },
  });

  const addGymEquipmentMutation = useMutation({
    mutationFn: (equipmentId: number) =>
      apiFetch('/gym-equipment-catalog', {
        method: 'POST',
        body: JSON.stringify({ gym_id: activeGymId, equipment_id: equipmentId }),
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['gym-equipment-catalog', activeGymId] });
    },
  });

  const [removeEquipmentOpen, { open: openRemoveEquipment, close: closeRemoveEquipment }] = useDisclosure(false);
  const [removeEquipmentTarget, setRemoveEquipmentTarget] = useState<Equipment | null>(null);

  const removeGymEquipmentMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/gym-equipment-catalog/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeRemoveEquipment();
      setRemoveEquipmentTarget(null);
      await qc.invalidateQueries({ queryKey: ['gym-equipment-catalog', activeGymId] });
    },
  });

  function toggleGymEquipment(equipment: Equipment, checked: boolean) {
    if (!activeGymId) return;
    if (checked) {
      addGymEquipmentMutation.mutate(equipment.id);
    } else {
      setRemoveEquipmentTarget(equipment);
      openRemoveEquipment();
    }
  }

  function confirmRemoveGymEquipment() {
    if (!removeEquipmentTarget) return;
    const linkId = gymHasEquipmentId.get(removeEquipmentTarget.id);
    if (linkId) removeGymEquipmentMutation.mutate(linkId);
  }

  const gyms = gymsQuery.data?.items || [];
  const equipmentItems = equipmentQuery.data?.items || [];

  return (
    <Stack gap="xl">
      <div>
        <Title order={1} c={BRAND.textPrimary}>
          Academias
        </Title>
        <Text c={BRAND.textMuted} size="md">
          Cadastre academias e marque quais equipamentos cada uma possui. Para cadastrar o catálogo de
          equipamentos (uma vez, com foto) e dizer quais exercícios/grupos musculares cada equipamento atende, use
          a tela Equipamentos x Exercícios.
        </Text>
      </div>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Title order={3} mb="md" c={BRAND.textPrimary}>
          Nova academia
        </Title>
        <TextInput
          label="Nome"
          placeholder="Ex.: Gaviões"
          value={newGymName}
          onChange={(event) => setNewGymName(event.currentTarget.value)}
        />
        <Stack gap={4} mt="sm">
          <Checkbox
            label="Academia avulsa (visita única, sem frequência recorrente)"
            checked={!newGymRecurring}
            onChange={(event) => {
              const avulsa = event.currentTarget.checked;
              setNewGymRecurring(!avulsa);
              if (avulsa) setNewGymDays([]);
            }}
          />
        </Stack>
        {newGymRecurring && (
          <Stack gap={4} mt="sm">
            <Text size="sm" fw={600} c={BRAND.textPrimary}>
              Frequência normal
            </Text>
            <Group gap="md">
              {WEEKDAYS.map((day) => (
                <Checkbox
                  key={day.value}
                  label={day.label}
                  checked={newGymDays.includes(day.value)}
                  onChange={(event) => {
                    const checked = event.currentTarget.checked;
                    setNewGymDays((current) =>
                      checked ? [...current, day.value] : current.filter((d) => d !== day.value),
                    );
                  }}
                />
              ))}
            </Group>
          </Stack>
        )}
        <Group mt="md">
          <Button
            color="brand"
            leftSection={<IconPlus size={16} />}
            disabled={!newGymName.trim()}
            loading={createGymMutation.isPending}
            onClick={() => createGymMutation.mutate()}
          >
            Adicionar academia
          </Button>
        </Group>
        {createGymMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} mt="sm">
            {errorMessage(createGymMutation.error, 'Não foi possível criar a academia.')}
          </Alert>
        )}
      </Paper>

      {gymsQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando academias...</Text>
        </Group>
      )}
      {gymsQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(gymsQuery.error, 'Não foi possível carregar as academias.')}
        </Alert>
      )}

      {!gymsQuery.isLoading && !gymsQuery.isError && (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md">
          {gyms.map((gym) => (
            <Paper
              key={gym.id}
              withBorder
              radius="lg"
              p="lg"
              style={{
                background: BRAND.bgElevated,
                borderColor: activeGymId === gym.id ? BRAND.orange : BRAND.border,
                cursor: 'pointer',
              }}
              onClick={() => setSelectedGymId(gym.id)}
            >
              <Group justify="space-between" mb="xs" wrap="nowrap">
                <Group gap="xs" wrap="nowrap">
                  <GymLogo logoUrl={gym.logo_url} alt={gym.name} size={28} />
                  <Title order={4} c={BRAND.textPrimary}>
                    {gym.name}
                  </Title>
                </Group>
                <Group gap="xs" wrap="nowrap">
                  {isAdmin && (
                    <Tooltip label="Editar academia">
                      <ActionIcon
                        color="brand"
                        variant="subtle"
                        onClick={(event) => {
                          event.stopPropagation();
                          openEditGym(gym);
                        }}
                      >
                        <IconPencil size={16} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                  <Tooltip label="Excluir academia">
                    <ActionIcon
                      color="red"
                      variant="subtle"
                      onClick={(event) => {
                        event.stopPropagation();
                        openDeleteGymConfirm(gym);
                      }}
                    >
                      <IconTrash size={16} />
                    </ActionIcon>
                  </Tooltip>
                </Group>
              </Group>
              {gym.is_recurring ? (
                <>
                  <Text size="sm" c={BRAND.textMuted} mb="sm">
                    {weekdayLabels(gym.usual_weekdays)}
                  </Text>
                  <Group gap={6}>
                    {WEEKDAYS.map((day) => {
                      const checked = (gym.usual_weekdays || []).includes(day.value);
                      return (
                        <Badge
                          key={day.value}
                          variant={checked ? 'filled' : 'outline'}
                          color={checked ? 'brand' : 'gray'}
                          style={{ cursor: 'pointer' }}
                          onClick={(event) => {
                            event.stopPropagation();
                            const current = gym.usual_weekdays || [];
                            const next = checked ? current.filter((d) => d !== day.value) : [...current, day.value];
                            updateGymDaysMutation.mutate({ id: gym.id, usual_weekdays: next });
                          }}
                        >
                          {day.label.slice(0, 3)}
                        </Badge>
                      );
                    })}
                  </Group>
                </>
              ) : (
                <Badge
                  variant="outline"
                  color="grape"
                  style={{ cursor: 'pointer' }}
                  title="Academia avulsa — clique para tornar recorrente"
                  onClick={(event) => {
                    event.stopPropagation();
                    updateGymRecurringMutation.mutate({ id: gym.id, is_recurring: true });
                  }}
                >
                  Avulsa (clique para tornar recorrente)
                </Badge>
              )}
            </Paper>
          ))}
          {gyms.length === 0 && (
            <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Text c={BRAND.textMuted}>Nenhuma academia cadastrada ainda.</Text>
            </Paper>
          )}
        </SimpleGrid>
      )}

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="md" wrap="wrap">
          <div>
            <Title order={3} c={BRAND.textPrimary}>
              Equipamentos desta academia
            </Title>
            <Text size="sm" c={BRAND.textMuted}>
              Marque quais equipamentos do catálogo esta academia possui. Para cadastrar um equipamento novo, use a
              tela Equipamentos x Exercícios.
            </Text>
          </div>
          <Select
            placeholder="Selecione a academia"
            data={gyms.map((g) => ({ value: String(g.id), label: g.name }))}
            value={activeGymId ? String(activeGymId) : null}
            onChange={(value) => setSelectedGymId(value ? Number(value) : null)}
            w={260}
          />
        </Group>

        {(addGymEquipmentMutation.isError || removeGymEquipmentMutation.isError) && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
            {errorMessage(
              addGymEquipmentMutation.error || removeGymEquipmentMutation.error,
              'Não foi possível atualizar os equipamentos.',
            )}
          </Alert>
        )}

        {(equipmentQuery.isLoading || gymCatalogQuery.isLoading) && (
          <Group>
            <Loader size="sm" color="brand" />
            <Text c={BRAND.textMuted}>Carregando equipamentos...</Text>
          </Group>
        )}
        {(equipmentQuery.isError || gymCatalogQuery.isError) && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(equipmentQuery.error || gymCatalogQuery.error, 'Não foi possível carregar os equipamentos.')}
          </Alert>
        )}

        {!equipmentQuery.isLoading &&
          !equipmentQuery.isError &&
          !gymCatalogQuery.isLoading &&
          !gymCatalogQuery.isError && (
            <Table.ScrollContainer minWidth={500}>
              <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th w={60}>Possui</Table.Th>
                    <Table.Th>Foto</Table.Th>
                    <Table.Th>Equipamento</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {equipmentItems.map((eq) => (
                    <Table.Tr key={eq.id}>
                      <Table.Td>
                        <Checkbox
                          checked={gymHasEquipmentId.has(eq.id)}
                          disabled={!activeGymId}
                          onChange={(event) => toggleGymEquipment(eq, event.currentTarget.checked)}
                        />
                      </Table.Td>
                      <Table.Td>
                        <EquipmentPhoto photoUrl={eq.photo_url} alt={eq.name} size={40} />
                      </Table.Td>
                      <Table.Td c={BRAND.textPrimary}>{eq.name}</Table.Td>
                    </Table.Tr>
                  ))}
                  {equipmentItems.length === 0 && (
                    <Table.Tr>
                      <Table.Td colSpan={3}>
                        <Text c={BRAND.textMuted}>
                          Nenhum equipamento no catálogo — cadastre um na tela Equipamentos x Exercícios.
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
      </Paper>

      {activeGymId && (
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Title order={3} mb="xs" c={BRAND.textPrimary}>
            Prévia de variação por grupo muscular
          </Title>
          <Text size="sm" c={BRAND.textMuted} mb="md">
            Somente leitura: mostra, para cada grupo muscular, qual equipamento desta academia entraria na próxima
            ficha (suas estrelas para o exercício naquele equipamento primeiro; na mesma nota, o que você usou há mais tempo) e quais alternativas existem para rotação. Vem do mesmo cálculo usado ao
            montar uma ficha de treino.
          </Text>

          {recommendAllQuery.isLoading && (
            <Group>
              <Loader size="sm" color="brand" />
              <Text c={BRAND.textMuted}>Carregando prévia...</Text>
            </Group>
          )}
          {recommendAllQuery.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {errorMessage(recommendAllQuery.error, 'Não foi possível carregar a prévia de variação de equipamentos.')}
            </Alert>
          )}

          {!recommendAllQuery.isLoading && !recommendAllQuery.isError && (
            <Table.ScrollContainer minWidth={600}>
              <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Grupo muscular</Table.Th>
                    <Table.Th>Recomendado</Table.Th>
                    <Table.Th>Alternativos</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {recommendGroups.map(([group, info]) => (
                    <Table.Tr key={group}>
                      <Table.Td>
                        <Group gap="xs" wrap="nowrap">
                          <Text fw={600} c={BRAND.textPrimary}>
                            {group}
                          </Text>
                          {!info.has_equipment_for_group && (
                            <Badge color="red" variant="light">
                              sem equipamento
                            </Badge>
                          )}
                        </Group>
                      </Table.Td>
                      <Table.Td>
                        {info.recommended ? (
                          <Text size="sm" c={BRAND.textPrimary}>
                            {info.recommended.equipment_name}
                            {info.recommended.user_preference_stars != null &&
                              ` (${info.recommended.user_preference_stars}★)`}
                          </Text>
                        ) : (
                          <Text size="sm" c={BRAND.textMuted}>
                            —
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        {info.alternatives.length ? (
                          <Group gap={4}>
                            {info.alternatives.map((alt) => (
                              <Badge key={alt.id} variant="outline" color="gray">
                                {alt.equipment_name}
                              </Badge>
                            ))}
                          </Group>
                        ) : (
                          <Text size="sm" c={BRAND.textMuted}>
                            —
                          </Text>
                        )}
                      </Table.Td>
                    </Table.Tr>
                  ))}
                  {!recommendGroups.length && (
                    <Table.Tr>
                      <Table.Td colSpan={3}>
                        <Text c={BRAND.textMuted}>Nenhum grupo muscular encontrado no catálogo canônico.</Text>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
        </Paper>
      )}

      <input
        ref={gymLogoInputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/gif"
        style={{ display: 'none' }}
        onChange={handleGymLogoSelected}
      />

      <Modal
        opened={deleteGymOpen}
        onClose={closeDeleteGym}
        title="Confirmar desativação"
        size="sm"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Desativar a academia &quot;{deleteGymTarget?.name}&quot;? Ela deixará de aparecer nas listagens, mas
            pode ser reativada depois se necessário.
          </Text>
          {deactivateGymMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {errorMessage(deactivateGymMutation.error, 'Não foi possível desativar a academia.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeDeleteGym}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={deactivateGymMutation.isPending}
              onClick={() => deleteGymTarget && deactivateGymMutation.mutate(deleteGymTarget.id)}
            >
              Desativar
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={editGymOpen}
        onClose={closeEditGym}
        title="Editar academia"
        size="md"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          <TextInput
            label="Nome"
            required
            value={editGymName}
            onChange={(event) => setEditGymName(event.currentTarget.value)}
          />

          <div>
            <Text size="sm" fw={600} mb={4} c={BRAND.textPrimary}>
              Logo
            </Text>
            <Group gap="sm" align="center">
              {liveEditGym && (
                <GymLogo logoUrl={liveEditGym.logo_url} alt={liveEditGym.name} size={56} />
              )}
              <Button
                variant="light"
                color="brand"
                size="xs"
                leftSection={<IconUpload size={14} />}
                loading={uploadGymLogoMutation.isPending}
                onClick={triggerGymLogoUpload}
              >
                Enviar logo
              </Button>
              {liveEditGym?.has_logo && (
                <Button variant="light" color="orange" size="xs" onClick={openRemoveGymLogo}>
                  Remover logo
                </Button>
              )}
            </Group>
            {uploadGymLogoMutation.isError && (
              <Text size="xs" c="red" mt={4}>
                Não foi possível enviar a logo.
              </Text>
            )}
          </div>

          <Group grow>
            <TextInput
              type="time"
              label="Abre às"
              value={editGymOpensAt}
              onChange={(event) => setEditGymOpensAt(event.currentTarget.value)}
            />
            <TextInput
              type="time"
              label="Fecha às"
              value={editGymClosesAt}
              onChange={(event) => setEditGymClosesAt(event.currentTarget.value)}
            />
          </Group>

          {updateGymDetailsMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {errorMessage(updateGymDetailsMutation.error, 'Não foi possível salvar a academia.')}
            </Alert>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={closeEditGym}>
              Cancelar
            </Button>
            <Button
              color="brand"
              disabled={!editGymName.trim()}
              loading={updateGymDetailsMutation.isPending}
              onClick={saveEditGym}
            >
              Salvar
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={removeGymLogoOpen}
        onClose={closeRemoveGymLogo}
        title="Confirmar exclusão"
        size="sm"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Excluir a logo de &quot;{editGymTarget?.name}&quot;? Essa ação não pode ser desfeita.
          </Text>
          {removeGymLogoMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {errorMessage(removeGymLogoMutation.error, 'Não foi possível excluir a logo.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeRemoveGymLogo}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={removeGymLogoMutation.isPending}
              onClick={() => editGymTarget && removeGymLogoMutation.mutate(editGymTarget.id)}
            >
              Excluir
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={removeEquipmentOpen}
        onClose={closeRemoveEquipment}
        title="Confirmar remoção"
        size="sm"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Remover o equipamento &quot;{removeEquipmentTarget?.name}&quot; desta academia? Você pode marcá-lo
            novamente a qualquer momento.
          </Text>
          {removeGymEquipmentMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {errorMessage(removeGymEquipmentMutation.error, 'Não foi possível remover o equipamento.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeRemoveEquipment}>
              Cancelar
            </Button>
            <Button color="red" loading={removeGymEquipmentMutation.isPending} onClick={confirmRemoveGymEquipment}>
              Remover
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
