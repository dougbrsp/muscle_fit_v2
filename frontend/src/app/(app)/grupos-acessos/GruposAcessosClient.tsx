'use client';

import { Alert, Badge, Button, Card, Group, Loader, Modal, NavLink, Stack, Tabs, Text, Textarea, TextInput, Title } from '@mantine/core';
import { IconAlertTriangle, IconPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FormEvent, useCallback, useState } from 'react';
import {
  accessErrorMessage,
  accessKeys,
  createAccessGroup,
  fetchAccessCatalog,
  fetchAccessGroups,
} from '@/lib/access-admin-api';
import {
  GROUP_DESCRIPTION_MAX,
  GROUP_NAME_MAX,
  shouldConfirmDiscard,
  validateGroupKey,
  validateGroupName,
} from '@/lib/access-grants';
import { apiFetch, ApiError } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import { gruposControls } from '@/lib/screen-permissions';
import type { UserAdminListResponse } from '@/lib/users-admin-types';
import { BRAND } from '@/theme';
import { GrupoDetalhe } from './GrupoDetalhe';
import { VinculosTreinador } from './VinculosTreinador';

/**
 * Tela admin "Grupos e acessos" (RBAC por grupos, ADR 0011). Mesmo padrão de
 * `/usuarios`: só aparece com a permissão `admin.grupos`/ver; a garantia real é o 403 do
 * backend (`admin.grupos`). Grupo selecionado fica em estado (export estático,
 * sem rota dinâmica).
 */
export function GruposAcessosClient() {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const [pendingId, setPendingId] = useState<number | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [key, setKey] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: () => apiFetch<SessionUser>('/auth/me'),
    staleTime: 60_000,
    retry: false,
  });
  const currentUser = currentUserQuery.data;
  // Permissão (RBAC), não role: o backend segue barrando com 403 em `admin.grupos`.
  const ctrl = gruposControls(currentUser?.permissions);
  const isAdmin = ctrl.canAccess;

  const catalogQuery = useQuery({
    queryKey: accessKeys.catalog,
    queryFn: fetchAccessCatalog,
    enabled: isAdmin,
    retry: false,
    staleTime: 5 * 60_000,
  });
  const groupsQuery = useQuery({
    queryKey: accessKeys.groups,
    queryFn: fetchAccessGroups,
    enabled: isAdmin,
    retry: false,
    refetchOnWindowFocus: false,
  });
  // Mesma chamada e query key de UsuariosClient (cache compartilhado).
  const usersQuery = useQuery({
    queryKey: ['users-admin'],
    queryFn: () => apiFetch<UserAdminListResponse>('/users'),
    // GET /users exige admin.usuarios/ver; sem ela a tela segue sem nomes/seletor (aviso abaixo).
    enabled: isAdmin && ctrl.canListUsers,
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      createAccessGroup({ group_key: key.trim(), name: name.trim(), description: description.trim() || null }),
    onSuccess: async (group) => {
      setCreateOpen(false);
      setKey('');
      setName('');
      setDescription('');
      await qc.invalidateQueries({ queryKey: accessKeys.groups });
      requestSelect(group.id);
    },
  });

  const handleDirtyChange = useCallback((value: boolean) => setDirty(value), []);

  /** Troca a seleção; com rascunho de concessões não salvo, pede confirmação antes de descartar. */
  function requestSelect(nextId: number | null) {
    if (shouldConfirmDiscard(dirty, selectedId, nextId)) {
      setPendingId(nextId);
      return;
    }
    setSelectedId(nextId);
  }

  function confirmDiscard() {
    setDirty(false);
    setSelectedId(pendingId);
    setPendingId(null);
  }

  if (currentUserQuery.isLoading) {
    return (
      <Group>
        <Loader size="sm" color="brand" />
        <Text c={BRAND.textMuted}>Carregando...</Text>
      </Group>
    );
  }

  const forbidden = [catalogQuery.error, groupsQuery.error].some((e) => e instanceof ApiError && e.status === 403);
  if (!isAdmin || forbidden) {
    return (
      <Stack gap="md">
        <Title order={1} c={BRAND.textPrimary}>
          Grupos e acessos
        </Title>
        <Alert color="red" icon={<IconAlertTriangle size={16} />} radius="md">
          Acesso restrito a administradores.
        </Alert>
      </Stack>
    );
  }

  const usersForbidden = usersQuery.error instanceof ApiError && usersQuery.error.status === 403;
  const groups = groupsQuery.data ?? [];
  const catalog = catalogQuery.data;
  const selected = groups.find((g) => g.id === selectedId) ?? null;
  const loading = catalogQuery.isLoading || groupsQuery.isLoading;
  const loadError = catalogQuery.error ?? groupsQuery.error;

  const keyError = key ? validateGroupKey(key) : null;
  const canCreate = validateGroupKey(key) === null && validateGroupName(name) === null;

  function submitCreate(event: FormEvent) {
    event.preventDefault();
    if (canCreate) createMutation.mutate();
  }

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Grupos e acessos
          </Title>
          <Text c={BRAND.textMuted}>
            Grupos de permissão, concessões por recurso e ação, membros e auditoria. Restrito a administradores.
          </Text>
        </div>
        {ctrl.canEdit && (
          <Button
            color="brand"
            leftSection={<IconPlus size={16} />}
            onClick={() => {
              createMutation.reset();
              setCreateOpen(true);
            }}
          >
            Novo grupo
          </Button>
        )}
      </Group>

      {loading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando grupos...</Text>
        </Group>
      )}
      {loadError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {accessErrorMessage(loadError, 'Não foi possível carregar os grupos.')}
        </Alert>
      )}

      {isAdmin && (!ctrl.canListUsers || usersForbidden) && (
        <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />}>
          Seu acesso não inclui a lista de usuários: a auditoria mostra ids em vez de nomes e não é possível escolher
          novos membros.
        </Alert>
      )}

      {groupsQuery.isSuccess && catalog && (
        <Tabs defaultValue="grupos" keepMounted>
          <Tabs.List>
            <Tabs.Tab value="grupos">Grupos</Tabs.Tab>
            <Tabs.Tab value="vinculos">Vínculos</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="grupos" pt="md">
        <Group align="flex-start" wrap="wrap" gap="lg">
          <Card withBorder padding="xs" style={{ flex: '1 1 240px', maxWidth: 340 }} component="nav" aria-label="Grupos">
            {groups.length === 0 && (
              <Text c={BRAND.textMuted} ta="center" p="sm">
                Nenhum grupo cadastrado.
              </Text>
            )}
            {groups.map((group) => (
              <NavLink
                key={group.id}
                active={group.id === selectedId}
                onClick={() => requestSelect(group.id)}
                label={group.name}
                description={`${group.member_count} ${group.member_count === 1 ? 'membro' : 'membros'}`}
                rightSection={
                  group.is_system ? (
                    <Badge size="xs" color="gray">
                      Sistema
                    </Badge>
                  ) : undefined
                }
              />
            ))}
          </Card>

          <div style={{ flex: '3 1 480px', minWidth: 0 }}>
            {selected ? (
              <GrupoDetalhe
                key={selected.id}
                group={selected}
                catalog={catalog}
                users={usersQuery.data?.items ?? []}
                currentUserId={currentUser?.id ?? null}
                canEdit={ctrl.canEdit}
                canDelete={ctrl.canDelete}
                canListUsers={ctrl.canListUsers && !usersForbidden}
                onDeleted={() => {
                  setDirty(false);
                  setSelectedId(null);
                }}
                onDirtyChange={handleDirtyChange}
              />
            ) : (
              <Text c={BRAND.textMuted}>Selecione um grupo para ver concessões, membros e auditoria.</Text>
            )}
          </div>
        </Group>
          </Tabs.Panel>
          <Tabs.Panel value="vinculos" pt="md">
            <VinculosTreinador
              groups={groups}
              users={usersQuery.data?.items ?? []}
              currentUserId={currentUser?.id ?? null}
              canEdit={ctrl.canEdit}
              canDelete={ctrl.canDelete}
              canListUsers={ctrl.canListUsers && !usersForbidden}
            />
          </Tabs.Panel>
        </Tabs>
      )}

      <Modal opened={pendingId !== null} onClose={() => setPendingId(null)} title="Descartar alterações?" size="sm">
        <Stack gap="md">
          <Text>Há concessões alteradas e não salvas neste grupo. Se continuar, elas serão descartadas.</Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setPendingId(null)}>
              Continuar editando
            </Button>
            <Button color="red" onClick={confirmDiscard}>
              Descartar e continuar
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={createOpen} onClose={() => setCreateOpen(false)} title="Novo grupo">
        <form onSubmit={submitCreate}>
          <Stack gap="md">
            <TextInput
              label="Chave"
              description="Identificador fixo, não pode ser mudado depois."
              placeholder="ex.: coordenacao_treino"
              value={key}
              onChange={(e) => setKey(e.currentTarget.value)}
              error={keyError}
              required
            />
            <TextInput
              label="Nome"
              value={name}
              maxLength={GROUP_NAME_MAX}
              onChange={(e) => setName(e.currentTarget.value)}
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
            {createMutation.isError && (
              <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                {accessErrorMessage(createMutation.error, 'Não foi possível criar o grupo.')}
              </Alert>
            )}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setCreateOpen(false)}>
                Cancelar
              </Button>
              <Button type="submit" color="brand" disabled={!canCreate} loading={createMutation.isPending}>
                Criar grupo
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}
