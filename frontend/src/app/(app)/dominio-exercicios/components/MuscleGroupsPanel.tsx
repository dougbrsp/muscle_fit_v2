'use client';

import {
  Alert,
  Button,
  Group,
  Loader,
  Modal,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconPencil, IconPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type {
  MuscleGroupItem,
  MuscleGroupListResponse,
  MuscleGroupUpsertPayload,
  MuscleGroupUpsertResponse,
} from '@/lib/exercise-catalog-admin-types';
import { VOLUME_CLASSES_QUERY_KEY, volumeClassLabelForGroup } from '@/lib/volume-classes';
import type { VolumeClassListResponse } from '@/lib/volume-classes-types';
import { BRAND } from '@/theme';

const MACRO_GROUP_HINT = 'Editável na aba “Macro-grupos de volume”.';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

/**
 * CRUD de `muscle_groups` (catálogo canônico de grupos musculares).
 *
 * Sem exclusão: o backend (`api/app/exercise_mapping_catalog.py`,
 * `muscle_groups_router`) só expõe `GET`/`POST`/`PUT` — nenhum `DELETE`.
 * `primary_muscles`/`secondary_muscles` são texto livre (não lista/array),
 * confirmado lendo o router: passam por `_clean_optional`, que só faz
 * `str(...).strip()`, sem parse de JSON/CSV.
 *
 * Após salvar, invalida também `exercise-catalog-admin-muscle-groups` — a
 * query que alimenta o seletor de "adicionar contribuição muscular" no
 * drawer de exercício (`MuscleContributionsSection.tsx`) — para o grupo
 * novo/editado aparecer lá sem precisar recarregar a página.
 *
 * `methodology_parent_group_id` (agrega em, migração 055) é o vocabulário da
 * metodologia de volume por grupo. O macro-grupo (Fase 6, migração 057) é só
 * LEITURA aqui: a associação é feita na aba "Macro-grupos de volume". O porte
 * (`size_class`) da 055 não é mais lido pelo motor e o formulário não o envia
 * — ausente no payload, o backend preserva o valor gravado.
 *
 * Duas regras do backend que esta UI respeita:
 * - o `POST` (upsert por nome) NÃO mexe na agregação de um grupo que já
 *   existe; mudar a agregação é sempre o `PUT`;
 * - `methodology_parent_group_id` ausente preserva e `null` desagrega, então
 *   o formulário sempre envia a chave.
 */
export function MuscleGroupsPanel({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();

  const groupsQuery = useQuery({
    queryKey: ['exercise-catalog-admin-muscle-groups-crud'],
    queryFn: () => apiFetch<MuscleGroupListResponse>('/muscle-groups'),
  });
  const groups = groupsQuery.data?.items || [];
  // Mesma chave da aba "Macro-grupos de volume": salvar lá atualiza a coluna aqui.
  const classesQuery = useQuery({
    queryKey: VOLUME_CLASSES_QUERY_KEY,
    queryFn: () => apiFetch<VolumeClassListResponse>('/muscle-volume-classes'),
  });
  const classes = classesQuery.data?.items || [];

  const [formOpen, { open: openForm, close: closeFormRaw }] = useDisclosure(false);
  const [editing, setEditing] = useState<MuscleGroupItem | null>(null);
  const [name, setName] = useState('');
  const [primaryMuscles, setPrimaryMuscles] = useState('');
  const [secondaryMuscles, setSecondaryMuscles] = useState('');
  const [parentGroupId, setParentGroupId] = useState<string | null>(null);

  const groupById = useMemo(() => new Map(groups.map((group) => [group.id, group])), [groups]);
  const nameById = useMemo(
    () => new Map(groups.map((group) => [group.id, group.name])),
    [groups],
  );

  function macroGroupLabel(group: MuscleGroupItem): string {
    if (classesQuery.isLoading) return '…';
    if (classesQuery.isError) return '-';
    return volumeClassLabelForGroup(group, groupById, classes);
  }

  // Candidatos a "agrega em": todos menos o próprio grupo (auto-referência é
  // 422 no backend) e menos os grupos que já são fatias DESTE — apontar para
  // uma fatia sua fecharia um ciclo, que o backend também recusa. Filtrar
  // aqui é só para o admin não bater na recusa por engano.
  const parentOptions = useMemo(() => {
    const editingId = editing?.id;
    return groups
      .filter((group) => group.id !== editingId)
      .filter((group) => editingId == null || group.methodology_parent_group_id !== editingId)
      .map((group) => ({ value: String(group.id), label: group.name }));
  }, [groups, editing]);

  function closeForm() {
    closeFormRaw();
    saveMutation.reset();
  }

  function startCreate() {
    setEditing(null);
    setName('');
    setPrimaryMuscles('');
    setSecondaryMuscles('');
    setParentGroupId(null);
    openForm();
  }

  function startEdit(group: MuscleGroupItem) {
    setEditing(group);
    setName(group.name);
    setPrimaryMuscles(group.primary_muscles || '');
    setSecondaryMuscles(group.secondary_muscles || '');
    setParentGroupId(
      group.methodology_parent_group_id == null ? null : String(group.methodology_parent_group_id),
    );
    openForm();
  }

  const saveMutation = useMutation({
    mutationFn: (payload: MuscleGroupUpsertPayload) =>
      editing
        ? apiFetch<MuscleGroupUpsertResponse>(`/muscle-groups/${editing.id}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
          })
        : apiFetch<MuscleGroupUpsertResponse>('/muscle-groups', {
            method: 'POST',
            body: JSON.stringify(payload),
          }),
    onSuccess: async () => {
      closeForm();
      await qc.invalidateQueries({ queryKey: ['exercise-catalog-admin-muscle-groups-crud'] });
      // Mesma query usada pelo seletor de "adicionar contribuição muscular"
      // no drawer de exercício — sem isso o grupo novo só apareceria lá após
      // recarregar a página.
      await qc.invalidateQueries({ queryKey: ['exercise-catalog-admin-muscle-groups'] });
    },
  });

  function submitForm() {
    saveMutation.mutate({
      name: name.trim(),
      primary_muscles: primaryMuscles.trim() || null,
      secondary_muscles: secondaryMuscles.trim() || null,
      // `null` aqui é o pedido explícito de DESAGREGAR — o backend só limpa o
      // pai quando a chave vem no payload (ausente = preserva).
      methodology_parent_group_id: parentGroupId == null ? null : Number(parentGroupId),
    });
  }

  return (
    <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Group justify="space-between" align="flex-end" mb="md" wrap="wrap">
        <div>
          <Text fw={600} c={BRAND.textPrimary}>
            Grupos musculares
          </Text>
          <Text size="sm" c={BRAND.textMuted}>
            Catálogo canônico (<code>muscle_groups</code>) usado nas contribuições musculares de cada exercício e na
            recuperação por grupamento.
          </Text>
        </div>
        {isAdmin ? (
          <Button color="brand" leftSection={<IconPlus size={16} />} onClick={startCreate}>
            Novo grupo
          </Button>
        ) : (
          <Tooltip label="Ação restrita a administradores">
            <Button color="brand" leftSection={<IconPlus size={16} />} disabled>
              Novo grupo
            </Button>
          </Tooltip>
        )}
      </Group>

      {groupsQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
          {errorMessage(groupsQuery.error, 'Não foi possível carregar os grupos musculares.')}
        </Alert>
      )}

      {groupsQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando grupos musculares...</Text>
        </Group>
      )}

      {!groupsQuery.isLoading && !groupsQuery.isError && (
        <Table.ScrollContainer minWidth={900}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Nome</Table.Th>
                <Table.Th>
                  <Tooltip label={MACRO_GROUP_HINT}>
                    <span>Macro-grupo</span>
                  </Tooltip>
                </Table.Th>
                <Table.Th>Agrega em</Table.Th>
                <Table.Th>Músculos primários</Table.Th>
                <Table.Th>Músculos secundários</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {groups.map((group) => (
                <Table.Tr key={group.id}>
                  <Table.Td c={BRAND.textPrimary}>{group.name}</Table.Td>
                  <Table.Td c={BRAND.textMuted}>{macroGroupLabel(group)}</Table.Td>
                  <Table.Td c={BRAND.textMuted}>
                    {group.methodology_parent_group_id == null
                      ? '-'
                      : nameById.get(group.methodology_parent_group_id) ||
                        `#${group.methodology_parent_group_id}`}
                  </Table.Td>
                  <Table.Td c={BRAND.textMuted}>{group.primary_muscles || '-'}</Table.Td>
                  <Table.Td c={BRAND.textMuted}>{group.secondary_muscles || '-'}</Table.Td>
                  <Table.Td>
                    {isAdmin ? (
                      <Tooltip label="Editar grupo">
                        <Button size="xs" variant="light" leftSection={<IconPencil size={14} />} onClick={() => startEdit(group)}>
                          Editar
                        </Button>
                      </Tooltip>
                    ) : (
                      <Tooltip label="Ação restrita a administradores">
                        <Button size="xs" variant="light" leftSection={<IconPencil size={14} />} disabled>
                          Editar
                        </Button>
                      </Tooltip>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
              {groups.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={6}>
                    <Text c={BRAND.textMuted} ta="center">
                      Nenhum grupo muscular cadastrado.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      <Modal opened={formOpen} onClose={closeForm} title={editing ? 'Editar grupo muscular' : 'Novo grupo muscular'} centered>
        <Stack gap="sm">
          <TextInput
            label="Nome"
            placeholder="Ex.: Peito"
            value={name}
            onChange={(event) => setName(event.currentTarget.value)}
            required
          />
          {editing && (
            <TextInput
              label="Macro-grupo"
              description={`Só leitura. ${MACRO_GROUP_HINT}`}
              value={macroGroupLabel(editing)}
              readOnly
              disabled
            />
          )}
          <Select
            label="Agrega em"
            description="Marque quando este grupo for uma FATIA de outro (ex.: “Peitoral superior” agrega em “Peitoral”). Vazio = o próprio grupo já é o bloco."
            placeholder="Nenhum — é o próprio bloco"
            data={parentOptions}
            value={parentGroupId}
            onChange={setParentGroupId}
            searchable
            clearable
          />
          <TextInput
            label="Músculos primários"
            description="Texto livre, ex.: peitoral maior, peitoral menor"
            value={primaryMuscles}
            onChange={(event) => setPrimaryMuscles(event.currentTarget.value)}
          />
          <TextInput
            label="Músculos secundários"
            description="Texto livre, ex.: tríceps, deltoide anterior"
            value={secondaryMuscles}
            onChange={(event) => setSecondaryMuscles(event.currentTarget.value)}
          />
          {saveMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {isForbidden(saveMutation.error)
                ? 'Ação restrita a administradores.'
                : errorMessage(saveMutation.error, 'Não foi possível salvar o grupo muscular.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeForm}>
              Cancelar
            </Button>
            <Button color="brand" loading={saveMutation.isPending} disabled={!name.trim()} onClick={submitForm}>
              Salvar
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Paper>
  );
}
