'use client';

import { Alert, Badge, Button, Group, Modal, Stack, Tabs, Text, Textarea, TextInput, Title, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconPencil, IconTrash } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { accessErrorMessage, accessKeys, deleteAccessGroup, updateAccessGroup } from '@/lib/access-admin-api';
import type { AccessCatalog, AccessGroup } from '@/lib/access-admin-types';
import { GROUP_DESCRIPTION_MAX, GROUP_NAME_MAX, validateGroupName } from '@/lib/access-grants';
import type { UserAdminItem } from '@/lib/users-admin-types';
import { BRAND } from '@/theme';
import { AuditoriaGrupo } from './AuditoriaGrupo';
import { MatrizConcessoes } from './MatrizConcessoes';
import { MembrosGrupo } from './MembrosGrupo';

type Props = {
  group: AccessGroup;
  catalog: AccessCatalog;
  users: UserAdminItem[];
  currentUserId: number | null;
  canEdit: boolean;
  canDelete: boolean;
  canListUsers: boolean;
  onDeleted: () => void;
  onDirtyChange: (dirty: boolean) => void;
};

export function GrupoDetalhe({ group, catalog, users, currentUserId, canEdit, canDelete, canListUsers, onDeleted, onDirtyChange }: Props) {
  const qc = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const updateMutation = useMutation({
    mutationFn: () => updateAccessGroup(group.id, { name: name.trim(), description: description.trim() || null }),
    onSuccess: async () => {
      setEditOpen(false);
      await qc.invalidateQueries({ queryKey: accessKeys.groups });
      await qc.invalidateQueries({ queryKey: accessKeys.audit(group.id) });
    },
  });
  const deleteMutation = useMutation({
    mutationFn: () => deleteAccessGroup(group.id),
    onSuccess: async () => {
      setDeleteOpen(false);
      onDeleted();
      await qc.invalidateQueries({ queryKey: accessKeys.groups });
    },
  });

  function openEdit() {
    setName(group.name);
    setDescription(group.description ?? '');
    updateMutation.reset();
    setEditOpen(true);
  }

  const nameError = validateGroupName(name);

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-start" wrap="wrap">
        <div>
          <Group gap="xs">
            <Title order={2} size="h3" c={BRAND.textPrimary}>
              {group.name}
            </Title>
            {group.is_system && <Badge color="gray">Sistema</Badge>}
          </Group>
          <Text size="xs" c={BRAND.textMuted}>
            {group.group_key} · {group.member_count} {group.member_count === 1 ? 'membro' : 'membros'}
          </Text>
          {group.description && <Text size="sm" c={BRAND.textMuted}>{group.description}</Text>}
        </div>
        {group.editable && (canEdit || canDelete) && (
          <Group gap="xs">
            {canEdit && (
              <Button variant="light" color="brand" leftSection={<IconPencil size={16} />} onClick={openEdit}>
                Editar
              </Button>
            )}
            {canDelete && (
            <Tooltip label="Remova os membros antes de excluir" disabled={group.member_count === 0}>
              <Button
                variant="light"
                color="red"
                leftSection={<IconTrash size={16} />}
                disabled={group.member_count > 0}
                onClick={() => {
                  deleteMutation.reset();
                  setDeleteOpen(true);
                }}
              >
                Excluir
              </Button>
            </Tooltip>
            )}
          </Group>
        )}
      </Group>

      <Tabs defaultValue="concessoes">
        <Tabs.List>
          <Tabs.Tab value="concessoes">Concessões</Tabs.Tab>
          <Tabs.Tab value="membros">Membros</Tabs.Tab>
          <Tabs.Tab value="auditoria">Auditoria</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="concessoes" pt="md">
          <MatrizConcessoes group={group} catalog={catalog} canEdit={canEdit} onDirtyChange={onDirtyChange} />
        </Tabs.Panel>
        <Tabs.Panel value="membros" pt="md">
          <MembrosGrupo
            group={group}
            users={users}
            currentUserId={currentUserId}
            canEdit={canEdit}
            canDelete={canDelete}
            canListUsers={canListUsers}
          />
        </Tabs.Panel>
        <Tabs.Panel value="auditoria" pt="md">
          <AuditoriaGrupo group={group} users={users} />
        </Tabs.Panel>
      </Tabs>

      <Modal opened={editOpen} onClose={() => setEditOpen(false)} title="Editar grupo">
        <Stack gap="md">
          <TextInput
            label="Nome"
            value={name}
            maxLength={GROUP_NAME_MAX}
            onChange={(e) => setName(e.currentTarget.value)}
            error={name ? nameError : null}
            required
          />
          <Textarea
            label="Descrição"
            value={description}
            maxLength={GROUP_DESCRIPTION_MAX}
            onChange={(e) => setDescription(e.currentTarget.value)}
            autosize
            minRows={2}
          />
          {updateMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {accessErrorMessage(updateMutation.error, 'Não foi possível salvar o grupo.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setEditOpen(false)}>
              Cancelar
            </Button>
            <Button color="brand" disabled={nameError !== null} loading={updateMutation.isPending} onClick={() => updateMutation.mutate()}>
              Salvar
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={deleteOpen} onClose={() => setDeleteOpen(false)} title="Confirmar exclusão" size="sm">
        <Stack gap="md">
          <Text>Excluir o grupo &quot;{group.name}&quot;? As concessões dele deixam de valer.</Text>
          {deleteMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {accessErrorMessage(deleteMutation.error, 'Não foi possível excluir o grupo.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setDeleteOpen(false)}>
              Cancelar
            </Button>
            <Button color="red" loading={deleteMutation.isPending} onClick={() => deleteMutation.mutate()}>
              Excluir
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
