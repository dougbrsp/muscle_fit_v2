'use client';

import { Alert, Badge, Button, Code, CopyButton, Group, PasswordInput, Select, Stack, Switch, Text, TextInput } from '@mantine/core';
import { IconAlertTriangle, IconKey, IconPlus, IconTrash, IconUpload, IconX } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChangeEvent, FormEvent, useRef, useState } from 'react';
import { UserAvatar } from '@/components/UserAvatar';
import { apiFetch, ApiError } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import { SEX_OPTIONS } from '@/lib/nutrition-constants';
import {
  CreateUserPayload,
  ResetPasswordResponse,
  isUserRole,
  ROLE_OPTIONS,
  UpdateUserPayload,
  UserAdminItem,
  UserAdminListResponse,
  UserAdminMutationResponse,
  UserRole,
  roleLabel,
} from '@/lib/users-admin-types';
import {
  BottomSheet,
  ListRow,
  LoadingState,
  ResponsiveDataList,
  ScreenHeader,
  StickyAction,
} from '../../ui';
import { SheetButton, useBackToClose } from './common';

const MIN_PASSWORD_LENGTH = 8;

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

type View = { kind: 'list' } | { kind: 'create' } | { kind: 'edit'; user: UserAdminItem };

/**
 * Usuários (celular). Admin-only como no desktop: o papel vem de `/auth/me` e a
 * listagem (`GET /users`) só dispara depois de saber que é admin; não admin
 * nunca chama `/users`. A garantia real continua sendo o 403 do backend.
 */
export function UsuariosScreen() {
  const qc = useQueryClient();
  const currentUserQuery = useQuery({ queryKey: ['current-user'], queryFn: fetchCurrentUser, staleTime: 60_000, retry: false });
  const currentUser = currentUserQuery.data;
  const isAdmin = currentUser?.role === 'admin';

  const usersQuery = useQuery({
    queryKey: ['users-admin'],
    queryFn: () => apiFetch<UserAdminListResponse>('/users'),
    // GET /users é admin-only: só depois de saber o papel E que é admin (não chama /users para os demais).
    enabled: currentUserQuery.isSuccess && isAdmin,
    retry: false,
  });
  const forbidden = usersQuery.error instanceof ApiError && usersQuery.error.status === 403;

  const [view, setView] = useState<View>({ kind: 'list' });
  const [photoVersions, setPhotoVersions] = useState<Record<number, number>>({});
  function bumpPhotoVersion(userId: number) {
    setPhotoVersions((current) => ({ ...current, [userId]: (current[userId] || 0) + 1 }));
  }

  useBackToClose(view.kind !== 'list', () => setView({ kind: 'list' }));

  // criar
  const [createUsername, setCreateUsername] = useState('');
  const [createPassword, setCreatePassword] = useState('');
  const [createFullName, setCreateFullName] = useState('');
  const [createEmail, setCreateEmail] = useState('');
  const [createRole, setCreateRole] = useState<UserRole>('regular');

  function resetCreateForm() {
    setCreateUsername('');
    setCreatePassword('');
    setCreateFullName('');
    setCreateEmail('');
    setCreateRole('regular');
  }

  const createMutation = useMutation({
    mutationFn: (payload: CreateUserPayload) =>
      apiFetch<UserAdminMutationResponse>('/users', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: async () => {
      resetCreateForm();
      setView({ kind: 'list' });
      await qc.invalidateQueries({ queryKey: ['users-admin'] });
    },
  });
  const canSubmitCreate = createUsername.trim().length > 0 && createPassword.length >= MIN_PASSWORD_LENGTH;
  function submitCreate(event?: FormEvent) {
    event?.preventDefault();
    if (!canSubmitCreate) return;
    createMutation.mutate({
      username: createUsername.trim(),
      password: createPassword,
      role: createRole,
      full_name: createFullName.trim() || null,
      email: createEmail.trim() || null,
    });
  }

  // editar
  const [editFullName, setEditFullName] = useState('');
  const [editEmail, setEditEmail] = useState('');
  const [editBirthDate, setEditBirthDate] = useState('');
  const [editSex, setEditSex] = useState<string | null>(null);
  const [editRole, setEditRole] = useState<UserRole>('regular');
  const [editActive, setEditActive] = useState(true);
  const editTarget = view.kind === 'edit' ? view.user : null;
  const liveEditTarget = editTarget ? (usersQuery.data?.items.find((item) => item.id === editTarget.id) ?? editTarget) : null;
  const isEditingSelf = editTarget != null && currentUser != null && editTarget.id === currentUser.id;

  function openEdit(user: UserAdminItem) {
    setEditFullName(user.full_name || '');
    setEditEmail(user.email || '');
    setEditBirthDate(user.birth_date ? user.birth_date.slice(0, 10) : '');
    setEditSex(user.sex || null);
    setEditRole(isUserRole(user.role) ? user.role : 'regular');
    setEditActive(user.is_active);
    updateMutation.reset();
    setView({ kind: 'edit', user });
  }

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: UpdateUserPayload }) =>
      apiFetch<UserAdminMutationResponse>(`/users/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
    onSuccess: async () => {
      setView({ kind: 'list' });
      await qc.invalidateQueries({ queryKey: ['users-admin'] });
    },
  });
  function saveEdit() {
    if (!editTarget) return;
    updateMutation.mutate({
      id: editTarget.id,
      payload: {
        full_name: editFullName.trim() || null,
        email: editEmail.trim() || null,
        birth_date: editBirthDate || null,
        sex: editSex || null,
        role: editRole,
        is_active: editActive,
      },
    });
  }

  // foto
  const photoInputRef = useRef<HTMLInputElement>(null);
  const uploadPhotoMutation = useMutation({
    mutationFn: async ({ id, file }: { id: number; file: File }) => {
      const form = new FormData();
      form.append('file', file);
      return apiFetch<UserAdminMutationResponse>(`/users/${id}/photo`, { method: 'POST', body: form });
    },
    onSuccess: async (_data, variables) => {
      bumpPhotoVersion(variables.id);
      await qc.invalidateQueries({ queryKey: ['users-admin'] });
    },
  });
  function handlePhotoSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (file && editTarget) uploadPhotoMutation.mutate({ id: editTarget.id, file });
  }
  const removePhotoMutation = useMutation({
    mutationFn: (id: number) => apiFetch<{ status: string; id: number; has_photo: boolean }>(`/users/${id}/photo`, { method: 'DELETE' }),
    onSuccess: async (_data, id) => {
      bumpPhotoVersion(id);
      await qc.invalidateQueries({ queryKey: ['users-admin'] });
    },
  });

  // resetar senha (a senha temporária só vive no estado da mutação até fechar)
  const [resetTarget, setResetTarget] = useState<UserAdminItem | null>(null);
  const resetPasswordMutation = useMutation({
    mutationFn: (id: number) => apiFetch<ResetPasswordResponse>(`/users/${id}/reset-password`, { method: 'POST' }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['users-admin'] });
    },
  });
  function openReset(user: UserAdminItem) {
    resetPasswordMutation.reset();
    setResetTarget(user);
  }
  function closeReset() {
    setResetTarget(null);
    resetPasswordMutation.reset();
  }

  // desativar
  const [deactivateTarget, setDeactivateTarget] = useState<UserAdminItem | null>(null);
  const deactivateMutation = useMutation({
    mutationFn: (id: number) => apiFetch<{ status: string; id: number; is_active: boolean }>(`/users/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      setDeactivateTarget(null);
      // O formulário de edição ainda guarda `editActive=true`: salvar depois reativaria a conta. Volta à lista.
      setView({ kind: 'list' });
      await qc.invalidateQueries({ queryKey: ['users-admin'] });
    },
  });

  // ---- Guardas de acesso (iguais às do desktop) ----
  if (currentUserQuery.isLoading) {
    return (
      <>
        <ScreenHeader title="Usuários" />
        <LoadingState label="Carregando..." />
      </>
    );
  }
  if (!isAdmin || forbidden) {
    return (
      <>
        <ScreenHeader title="Usuários" />
        <Alert color="red" icon={<IconAlertTriangle size={16} />} radius="md" role="alert">
          {forbidden ? errorMessage(usersQuery.error, 'Acesso restrito a administradores.') : 'Acesso restrito a administradores.'}
        </Alert>
      </>
    );
  }

  const resetSheet = (
    <BottomSheet
      opened={resetTarget !== null}
      onClose={closeReset}
      title={resetTarget ? `Resetar senha de ${resetTarget.username}` : 'Resetar senha'}
      footer={
        resetPasswordMutation.data ? (
          <SheetButton color="brand" onClick={closeReset}>
            Fechar
          </SheetButton>
        ) : (
          <>
            <SheetButton variant="default" onClick={closeReset}>
              Cancelar
            </SheetButton>
            <SheetButton
              color="brand"
              loading={resetPasswordMutation.isPending}
              onClick={() => resetTarget && resetPasswordMutation.mutate(resetTarget.id)}
            >
              Resetar senha
            </SheetButton>
          </>
        )
      }
    >
      <Stack gap="md">
        {resetPasswordMutation.data ? (
          <>
            <Alert color="teal" variant="light">
              Senha redefinida. Ela aparece <strong>uma única vez</strong>: copie agora e entregue ao usuário por um canal seguro. O
              usuário será obrigado a trocá-la no próximo login, e todas as sessões abertas dele foram encerradas (
              {resetPasswordMutation.data.revoked_sessions}).
            </Alert>
            <Group gap="sm" wrap="nowrap">
              <Code style={{ fontSize: 16, padding: '8px 12px', flex: 1, wordBreak: 'break-all' }}>
                {resetPasswordMutation.data.temporary_password}
              </Code>
              <CopyButton value={resetPasswordMutation.data.temporary_password}>
                {({ copied, copy }) => (
                  <Button variant="light" mih={44} color={copied ? 'teal' : 'brand'} onClick={copy}>
                    {copied ? 'Copiado' : 'Copiar'}
                  </Button>
                )}
              </CopyButton>
            </Group>
          </>
        ) : (
          <>
            <Text>
              Gerar uma senha temporária para &quot;{resetTarget?.username}&quot;? A senha atual deixa de funcionar imediatamente, todas
              as sessões abertas desse usuário são encerradas e ele terá de definir uma nova senha no próximo login.
            </Text>
            {resetPasswordMutation.isError ? (
              <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
                {isForbidden(resetPasswordMutation.error)
                  ? 'Ação restrita a administradores.'
                  : errorMessage(resetPasswordMutation.error, 'Não foi possível resetar a senha.')}
              </Alert>
            ) : null}
          </>
        )}
      </Stack>
    </BottomSheet>
  );

  const deactivateSheet = (
    <BottomSheet
      opened={deactivateTarget !== null}
      onClose={() => setDeactivateTarget(null)}
      title="Confirmar desativação"
      footer={
        <>
          <SheetButton variant="default" onClick={() => setDeactivateTarget(null)}>
            Cancelar
          </SheetButton>
          <SheetButton
            color="red"
            loading={deactivateMutation.isPending}
            onClick={() => deactivateTarget && deactivateMutation.mutate(deactivateTarget.id)}
          >
            Desativar
          </SheetButton>
        </>
      }
    >
      <Stack gap="md">
        <Text>
          Desativar o usuário &quot;{deactivateTarget?.username}&quot;? Ele deixará de conseguir fazer login, mas o histórico dele é
          preservado e a conta pode ser reativada depois (marque &quot;Conta ativa&quot; na edição).
        </Text>
        {deactivateMutation.isError ? (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
            {isForbidden(deactivateMutation.error)
              ? 'Ação restrita a administradores.'
              : errorMessage(deactivateMutation.error, 'Não foi possível desativar o usuário.')}
          </Alert>
        ) : null}
      </Stack>
    </BottomSheet>
  );

  // ---- Criar ----
  if (view.kind === 'create') {
    return (
      <>
        <ScreenHeader title="Novo usuário" />
        <form onSubmit={submitCreate}>
          <Stack gap="md" pb="md">
            <TextInput size="md" label="Username" placeholder="Ex.: joao.silva" value={createUsername} onChange={(e) => setCreateUsername(e.currentTarget.value)} required autoCapitalize="none" />
            <PasswordInput
              size="md"
              label={`Senha inicial (mínimo ${MIN_PASSWORD_LENGTH} caracteres)`}
              value={createPassword}
              onChange={(e) => setCreatePassword(e.currentTarget.value)}
              minLength={MIN_PASSWORD_LENGTH}
              required
            />
            <TextInput size="md" label="Nome completo" value={createFullName} onChange={(e) => setCreateFullName(e.currentTarget.value)} />
            <TextInput size="md" label="Email" type="email" value={createEmail} onChange={(e) => setCreateEmail(e.currentTarget.value)} />
            <Select size="md" label="Papel" data={ROLE_OPTIONS} value={createRole} onChange={(v) => setCreateRole((v as UserRole) || 'regular')} allowDeselect={false} required />
            {createMutation.isError ? (
              <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
                {isForbidden(createMutation.error)
                  ? 'Ação restrita a administradores.'
                  : errorMessage(createMutation.error, 'Não foi possível criar o usuário.')}
              </Alert>
            ) : null}
          </Stack>
        </form>
        <StickyAction>
          <Group wrap="nowrap" gap="sm">
            <Button variant="default" size="md" mih={48} onClick={() => setView({ kind: 'list' })}>
              Cancelar
            </Button>
            <Button size="md" color="brand" mih={48} style={{ flex: 1 }} disabled={!canSubmitCreate} loading={createMutation.isPending} onClick={() => submitCreate()}>
              Criar usuário
            </Button>
          </Group>
        </StickyAction>
      </>
    );
  }

  // ---- Editar ----
  if (view.kind === 'edit' && editTarget) {
    return (
      <>
        <ScreenHeader title={`Editar ${editTarget.username}`} />
        <Stack gap="md" pb="md">
          <Group gap="sm" align="center">
            <UserAvatar userId={editTarget.id} hasPhoto={liveEditTarget?.has_photo} version={photoVersions[editTarget.id] || 0} alt={editTarget.username} size={64} />
            <Stack gap={6}>
              <Button variant="light" color="brand" mih={44} leftSection={<IconUpload size={14} />} loading={uploadPhotoMutation.isPending} onClick={() => photoInputRef.current?.click()}>
                Trocar foto
              </Button>
              {liveEditTarget?.has_photo ? (
                <Button variant="light" color="red" mih={44} leftSection={<IconX size={14} />} loading={removePhotoMutation.isPending} onClick={() => removePhotoMutation.mutate(editTarget.id)}>
                  Remover foto
                </Button>
              ) : null}
              <input ref={photoInputRef} type="file" accept="image/*" hidden onChange={handlePhotoSelected} />
            </Stack>
          </Group>
          {uploadPhotoMutation.isError || removePhotoMutation.isError ? (
            <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
              {errorMessage(uploadPhotoMutation.error || removePhotoMutation.error, 'Não foi possível atualizar a foto.')}
            </Alert>
          ) : null}
          <TextInput size="md" label="Nome completo" value={editFullName} onChange={(e) => setEditFullName(e.currentTarget.value)} />
          <TextInput size="md" label="Email" type="email" value={editEmail} onChange={(e) => setEditEmail(e.currentTarget.value)} />
          <TextInput size="md" label="Data de nascimento" type="date" value={editBirthDate} onChange={(e) => setEditBirthDate(e.currentTarget.value)} />
          <Select size="md" label="Sexo" data={SEX_OPTIONS} value={editSex} onChange={setEditSex} clearable />
          <Select
            size="md"
            label="Papel"
            data={ROLE_OPTIONS}
            value={editRole}
            onChange={(v) => setEditRole((v as UserRole) || 'regular')}
            allowDeselect={false}
            disabled={isEditingSelf}
            description={isEditingSelf ? 'Você não pode alterar o próprio papel.' : undefined}
          />
          <Switch
            size="md"
            label="Conta ativa"
            checked={editActive}
            onChange={(e) => setEditActive(e.currentTarget.checked)}
            disabled={isEditingSelf}
            description={isEditingSelf ? 'Você não pode desativar a própria conta.' : undefined}
            color="brand"
          />
          {isEditingSelf ? (
            <Alert color="gray" variant="light" icon={<IconAlertTriangle size={16} />}>
              Para trocar a própria senha, use &quot;Meu perfil&quot;: o reset administrativo não vale para a própria conta.
            </Alert>
          ) : (
            <>
              <Button variant="light" color="brand" mih={48} leftSection={<IconKey size={16} />} onClick={() => openReset(editTarget)}>
                Resetar senha
              </Button>
              <Button variant="light" color="red" mih={48} leftSection={<IconTrash size={16} />} disabled={!editTarget.is_active} onClick={() => setDeactivateTarget(editTarget)}>
                {editTarget.is_active ? 'Desativar usuário' : 'Já desativado'}
              </Button>
            </>
          )}
          {updateMutation.isError ? (
            <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
              {isForbidden(updateMutation.error)
                ? 'Ação restrita a administradores.'
                : errorMessage(updateMutation.error, 'Não foi possível salvar o usuário.')}
            </Alert>
          ) : null}
        </Stack>
        <StickyAction>
          <Group wrap="nowrap" gap="sm">
            <Button variant="default" size="md" mih={48} onClick={() => setView({ kind: 'list' })}>
              Cancelar
            </Button>
            <Button size="md" color="brand" mih={48} style={{ flex: 1 }} loading={updateMutation.isPending} onClick={saveEdit}>
              Salvar
            </Button>
          </Group>
        </StickyAction>
        {resetSheet}
        {deactivateSheet}
      </>
    );
  }

  // ---- Lista ----
  return (
    <>
      <ScreenHeader title="Usuários" subtitle="Cadastro, papel, status e foto. Restrito a administradores." />
      <ResponsiveDataList
        aria-label="Usuários"
        items={usersQuery.data?.items}
        getKey={(u) => u.id}
        isLoading={usersQuery.isLoading}
        isError={usersQuery.isError}
        onRetry={() => void usersQuery.refetch()}
        errorTitle="Não foi possível carregar os usuários"
        emptyTitle="Nenhum usuário cadastrado ainda"
        renderItem={(user) => {
          const self = currentUser != null && user.id === currentUser.id;
          return (
            <ListRow
              title={
                <Group gap={6} wrap="nowrap">
                  <span>{user.username}</span>
                  {self ? (
                    <Badge size="xs" variant="light" color="gray">
                      você
                    </Badge>
                  ) : null}
                </Group>
              }
              subtitle={[user.full_name, user.email, user.is_active ? null : 'Desativado'].filter(Boolean).join(' · ') || undefined}
              value={roleLabel(user.role)}
              onClick={() => openEdit(user)}
            />
          );
        }}
      />
      <StickyAction>
        <Button fullWidth size="lg" color="brand" mih={48} leftSection={<IconPlus size={20} />} onClick={() => setView({ kind: 'create' })}>
          Novo usuário
        </Button>
      </StickyAction>
      {resetSheet}
      {deactivateSheet}
    </>
  );
}
