'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Code,
  CopyButton,
  Group,
  Loader,
  Modal,
  PasswordInput,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconKey,
  IconPencil,
  IconPlus,
  IconTrash,
  IconUpload,
  IconUserOff,
  IconX,
} from '@tabler/icons-react';
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
import { BRAND } from '@/theme';

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

/**
 * Tela "Admin / Usuários" (Fase 4, Etapa 23): listar, criar, editar,
 * desativar e gerenciar foto de perfil de qualquer usuário da plataforma.
 *
 * Admin-only estrito, mesmo padrão de `/metas-ativacao`: `role === "admin"`
 * via `/auth/me`, a query de listagem só dispara depois de saber o papel do
 * usuário (evita uma chamada fadada a 403), e o bloqueio de acesso mostra
 * mensagem clara em vez de renderizar a tela pela metade. A garantia real
 * continua sendo o 403 do backend (`require_admin`).
 *
 * DELETE /users/{id} é soft-delete (`is_active = 0`, não remove a linha) —
 * por isso os botões usam "Desativar", não "Excluir". O backend já rejeita
 * auto-exclusão (`identity.user_id == user_id` → 400); a UI reforça isso
 * escondendo a ação para a própria conta logada.
 */
export function UsuariosClient() {
  const qc = useQueryClient();
  const isMobile = useMediaQuery('(max-width: 48em)');

  // Mesma query key usada pelo AppShellLayout/outras telas — o React Query
  // dedupa, não gera uma chamada extra a /auth/me.
  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: false,
  });
  const currentUser = currentUserQuery.data;
  const isAdmin = currentUser?.role === 'admin';

  const usersQuery = useQuery({
    queryKey: ['users-admin'],
    queryFn: () => apiFetch<UserAdminListResponse>('/users'),
    // GET /users é admin-only no backend (403 para os demais) — só dispara
    // depois de saber o papel do usuário, para não gastar uma chamada
    // fadada a falhar e mostrar direto a mensagem de acesso restrito.
    enabled: currentUserQuery.isSuccess,
    retry: false,
  });

  const forbidden = usersQuery.error instanceof ApiError && usersQuery.error.status === 403;

  // Contador local incrementado após upload/remoção de foto de um usuário,
  // para invalidar o cache de `fetchUserPhotoUrl` (a tabela `users` não
  // expõe `updated_at` para basear o cache nele, diferente de gyms/equipment).
  const [photoVersions, setPhotoVersions] = useState<Record<number, number>>({});
  function bumpPhotoVersion(userId: number) {
    setPhotoVersions((current) => ({ ...current, [userId]: (current[userId] || 0) + 1 }));
  }

  // ---- Criar usuário (POST /users) -----------------------------------
  const [createOpen, { open: openCreate, close: closeCreateRaw }] = useDisclosure(false);
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

  function closeCreate() {
    closeCreateRaw();
    resetCreateForm();
  }

  const createMutation = useMutation({
    mutationFn: (payload: CreateUserPayload) =>
      apiFetch<UserAdminMutationResponse>('/users', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: async () => {
      closeCreate();
      await qc.invalidateQueries({ queryKey: ['users-admin'] });
    },
  });

  const canSubmitCreate = createUsername.trim().length > 0 && createPassword.length >= MIN_PASSWORD_LENGTH;

  function submitCreate(event: FormEvent) {
    event.preventDefault();
    if (!canSubmitCreate) return;
    createMutation.mutate({
      username: createUsername.trim(),
      password: createPassword,
      role: createRole,
      full_name: createFullName.trim() || null,
      email: createEmail.trim() || null,
    });
  }

  // ---- Editar usuário (PUT /users/{id}) -------------------------------
  const [editOpen, { open: openEditRaw, close: closeEditRaw }] = useDisclosure(false);
  const [editTarget, setEditTarget] = useState<UserAdminItem | null>(null);
  const [editFullName, setEditFullName] = useState('');
  const [editEmail, setEditEmail] = useState('');
  const [editBirthDate, setEditBirthDate] = useState('');
  const [editSex, setEditSex] = useState<string | null>(null);
  const [editRole, setEditRole] = useState<UserRole>('regular');
  const [editActive, setEditActive] = useState(true);

  function openEdit(user: UserAdminItem) {
    setEditTarget(user);
    setEditFullName(user.full_name || '');
    setEditEmail(user.email || '');
    setEditBirthDate(user.birth_date ? user.birth_date.slice(0, 10) : '');
    setEditSex(user.sex || null);
    setEditRole(isUserRole(user.role) ? user.role : 'regular');
    setEditActive(user.is_active);
    openEditRaw();
  }

  function closeEdit() {
    closeEditRaw();
    setEditTarget(null);
  }

  // Mantém o alvo em edição sincronizado com a lista mais recente (ex.: após
  // upload de foto), sem perder o formulário em andamento.
  const liveEditTarget = editTarget
    ? usersQuery.data?.items.find((item) => item.id === editTarget.id) ?? editTarget
    : null;

  const isEditingSelf = editTarget != null && currentUser != null && editTarget.id === currentUser.id;

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: UpdateUserPayload }) =>
      apiFetch<UserAdminMutationResponse>(`/users/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
    onSuccess: async () => {
      closeEdit();
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
        // Auto-desativação também é bloqueada pelo backend (400), mas a UI
        // já não deixa a própria conta logada desmarcar "ativo" (switch
        // desabilitado abaixo), então este valor nunca chega false para si
        // mesmo.
        is_active: editActive,
      },
    });
  }

  // ---- Foto de perfil (POST/DELETE /users/{id}/photo) -----------------
  const editPhotoInputRef = useRef<HTMLInputElement>(null);

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

  function handleEditPhotoSelected(event: ChangeEvent<HTMLInputElement>) {
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

  // ---- Resetar senha (POST /users/{id}/reset-password) -----------------
  // A senha é gerada pelo SERVIDOR e vem na resposta uma única vez (o banco
  // guarda só o hash bcrypt). Por isso ela fica no estado local até o admin
  // fechar o modal — não há como pedi-la de novo, só resetar outra vez.
  const [resetOpen, { open: openResetRaw, close: closeResetRaw }] = useDisclosure(false);
  const [resetTarget, setResetTarget] = useState<UserAdminItem | null>(null);

  const resetPasswordMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch<ResetPasswordResponse>(`/users/${id}/reset-password`, { method: 'POST' }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['users-admin'] });
    },
  });

  function openReset(user: UserAdminItem) {
    setResetTarget(user);
    resetPasswordMutation.reset();
    openResetRaw();
  }

  function closeReset() {
    closeResetRaw();
    setResetTarget(null);
    // Limpa a senha temporária do estado ao fechar.
    resetPasswordMutation.reset();
  }

  // ---- Desativar usuário (DELETE /users/{id}, soft-delete) -------------
  const [deactivateOpen, { open: openDeactivateRaw, close: closeDeactivate }] = useDisclosure(false);
  const [deactivateTarget, setDeactivateTarget] = useState<UserAdminItem | null>(null);

  function openDeactivate(user: UserAdminItem) {
    setDeactivateTarget(user);
    openDeactivateRaw();
  }

  const deactivateMutation = useMutation({
    mutationFn: (id: number) => apiFetch<{ status: string; id: number; is_active: boolean }>(`/users/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeDeactivate();
      setDeactivateTarget(null);
      await qc.invalidateQueries({ queryKey: ['users-admin'] });
    },
  });

  // ---- Guardas de acesso -----------------------------------------------
  if (currentUserQuery.isLoading) {
    return (
      <Group>
        <Loader size="sm" color="brand" />
        <Text c={BRAND.textMuted}>Carregando...</Text>
      </Group>
    );
  }

  if (!isAdmin || forbidden) {
    return (
      <Stack gap="md">
        <Title order={1} c={BRAND.textPrimary}>
          Usuários
        </Title>
        <Alert color="red" icon={<IconAlertTriangle size={16} />} radius="md">
          {forbidden
            ? errorMessage(usersQuery.error, 'Acesso restrito a administradores.')
            : 'Acesso restrito a administradores.'}
        </Alert>
      </Stack>
    );
  }

  const users = usersQuery.data?.items || [];

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Usuários
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Gestão de usuários da plataforma: cadastro, papel (role), status e foto de perfil. Restrito a
            administradores.
          </Text>
        </div>
        <Button color="brand" leftSection={<IconPlus size={16} />} onClick={openCreate}>
          Novo usuário
        </Button>
      </Group>

      {usersQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando usuários...</Text>
        </Group>
      )}

      {usersQuery.isError && !forbidden && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(usersQuery.error, 'Não foi possível carregar os usuários.')}
        </Alert>
      )}

      {usersQuery.isSuccess && (
        <Table.ScrollContainer minWidth={720}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th />
                <Table.Th>Usuário</Table.Th>
                <Table.Th>Nome completo</Table.Th>
                <Table.Th>Email</Table.Th>
                <Table.Th>Papel</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {users.map((user) => {
                const self = currentUser != null && user.id === currentUser.id;
                return (
                  <Table.Tr key={user.id}>
                    <Table.Td>
                      <UserAvatar
                        userId={user.id}
                        hasPhoto={user.has_photo}
                        version={photoVersions[user.id] || 0}
                        alt={user.username}
                        size={36}
                      />
                    </Table.Td>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Text fw={600} c={BRAND.textPrimary}>
                          {user.username}
                        </Text>
                        {self && (
                          <Badge size="xs" variant="light" color="gray">
                            você
                          </Badge>
                        )}
                      </Group>
                    </Table.Td>
                    <Table.Td c={BRAND.textMuted}>{user.full_name || '—'}</Table.Td>
                    <Table.Td c={BRAND.textMuted}>{user.email || '—'}</Table.Td>
                    <Table.Td>
                      <Badge variant="light" color={user.role === 'admin' ? 'brand' : 'gray'}>
                        {roleLabel(user.role)}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="light" color={user.is_active ? 'teal' : 'red'}>
                        {user.is_active ? 'Ativo' : 'Desativado'}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap" justify="flex-end">
                        <Tooltip label="Editar usuário">
                          <ActionIcon color="brand" variant="subtle" onClick={() => openEdit(user)}>
                            <IconPencil size={16} />
                          </ActionIcon>
                        </Tooltip>
                        {self ? (
                          <Tooltip label="Use 'Meu perfil' para trocar a própria senha">
                            <ActionIcon color="brand" variant="subtle" disabled>
                              <IconKey size={16} />
                            </ActionIcon>
                          </Tooltip>
                        ) : (
                          <Tooltip label="Resetar senha">
                            <ActionIcon color="brand" variant="subtle" onClick={() => openReset(user)}>
                              <IconKey size={16} />
                            </ActionIcon>
                          </Tooltip>
                        )}
                        {self ? (
                          <Tooltip label="Você não pode desativar a própria conta">
                            <ActionIcon color="red" variant="subtle" disabled>
                              <IconUserOff size={16} />
                            </ActionIcon>
                          </Tooltip>
                        ) : (
                          <Tooltip label={user.is_active ? 'Desativar usuário' : 'Já desativado'}>
                            <ActionIcon
                              color="red"
                              variant="subtle"
                              disabled={!user.is_active}
                              onClick={() => openDeactivate(user)}
                            >
                              <IconTrash size={16} />
                            </ActionIcon>
                          </Tooltip>
                        )}
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
              {users.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={7}>
                    <Text c={BRAND.textMuted} ta="center">
                      Nenhum usuário cadastrado ainda.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      {/* ---- Modal: novo usuário (POST /users) --------------------------- */}
      <Modal
        opened={createOpen}
        onClose={closeCreate}
        title="Novo usuário"
        size="md"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <form onSubmit={submitCreate}>
          <Stack gap="md">
            <TextInput
              label="Username"
              placeholder="Ex.: joao.silva"
              value={createUsername}
              onChange={(event) => setCreateUsername(event.currentTarget.value)}
              required
            />
            <PasswordInput
              label={`Senha inicial (mínimo ${MIN_PASSWORD_LENGTH} caracteres)`}
              value={createPassword}
              onChange={(event) => setCreatePassword(event.currentTarget.value)}
              minLength={MIN_PASSWORD_LENGTH}
              required
            />
            <TextInput
              label="Nome completo"
              value={createFullName}
              onChange={(event) => setCreateFullName(event.currentTarget.value)}
            />
            <TextInput
              label="Email"
              type="email"
              value={createEmail}
              onChange={(event) => setCreateEmail(event.currentTarget.value)}
            />
            <Select
              label="Papel"
              data={ROLE_OPTIONS}
              value={createRole}
              onChange={(value) => setCreateRole((value as UserRole) || 'regular')}
              allowDeselect={false}
              required
            />

            {createMutation.isError && (
              <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                {isForbidden(createMutation.error)
                  ? 'Ação restrita a administradores.'
                  : errorMessage(createMutation.error, 'Não foi possível criar o usuário.')}
              </Alert>
            )}

            <Group justify="flex-end">
              <Button variant="default" onClick={closeCreate}>
                Cancelar
              </Button>
              <Button type="submit" color="brand" disabled={!canSubmitCreate} loading={createMutation.isPending}>
                Criar usuário
              </Button>
            </Group>
          </Stack>
        </form>
      </Modal>

      {/* ---- Modal: editar usuário (PUT /users/{id}) + foto -------------- */}
      <Modal
        opened={editOpen}
        onClose={closeEdit}
        title={editTarget ? `Editar ${editTarget.username}` : 'Editar usuário'}
        size="md"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        {editTarget && (
          <Stack gap="md">
            <Group gap="sm" align="center">
              <UserAvatar
                userId={editTarget.id}
                hasPhoto={liveEditTarget?.has_photo}
                version={photoVersions[editTarget.id] || 0}
                alt={editTarget.username}
                size={64}
              />
              <Stack gap={6}>
                <Group gap="xs">
                  <Button
                    size="xs"
                    variant="light"
                    color="brand"
                    leftSection={<IconUpload size={14} />}
                    loading={uploadPhotoMutation.isPending}
                    onClick={() => editPhotoInputRef.current?.click()}
                  >
                    Trocar foto
                  </Button>
                  {liveEditTarget?.has_photo && (
                    <Button
                      size="xs"
                      variant="light"
                      color="red"
                      leftSection={<IconX size={14} />}
                      loading={removePhotoMutation.isPending}
                      onClick={() => removePhotoMutation.mutate(editTarget.id)}
                    >
                      Remover foto
                    </Button>
                  )}
                </Group>
                <input
                  ref={editPhotoInputRef}
                  type="file"
                  accept="image/*"
                  hidden
                  onChange={handleEditPhotoSelected}
                />
              </Stack>
            </Group>
            {(uploadPhotoMutation.isError || removePhotoMutation.isError) && (
              <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                {errorMessage(uploadPhotoMutation.error || removePhotoMutation.error, 'Não foi possível atualizar a foto.')}
              </Alert>
            )}

            <TextInput
              label="Nome completo"
              value={editFullName}
              onChange={(event) => setEditFullName(event.currentTarget.value)}
            />
            <TextInput
              label="Email"
              type="email"
              value={editEmail}
              onChange={(event) => setEditEmail(event.currentTarget.value)}
            />
            <TextInput
              label="Data de nascimento"
              type="date"
              value={editBirthDate}
              onChange={(event) => setEditBirthDate(event.currentTarget.value)}
            />
            <Select label="Sexo" data={SEX_OPTIONS} value={editSex} onChange={setEditSex} clearable />
            <Select
              label="Papel"
              data={ROLE_OPTIONS}
              value={editRole}
              onChange={(value) => setEditRole((value as UserRole) || 'regular')}
              allowDeselect={false}
              disabled={isEditingSelf}
              description={isEditingSelf ? 'Você não pode alterar o próprio papel.' : undefined}
            />
            <Switch
              label="Conta ativa"
              checked={editActive}
              onChange={(event) => setEditActive(event.currentTarget.checked)}
              disabled={isEditingSelf}
              description={isEditingSelf ? 'Você não pode desativar a própria conta.' : undefined}
              color="brand"
            />

            {isEditingSelf ? (
              <Alert color="gray" variant="light" icon={<IconAlertTriangle size={16} />}>
                Para trocar a própria senha, use &quot;Meu perfil&quot; — o reset administrativo não vale para a
                própria conta.
              </Alert>
            ) : (
              <Button
                variant="light"
                color="brand"
                leftSection={<IconKey size={16} />}
                onClick={() => {
                  closeEdit();
                  openReset(editTarget);
                }}
              >
                Resetar senha
              </Button>
            )}

            {updateMutation.isError && (
              <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                {isForbidden(updateMutation.error)
                  ? 'Ação restrita a administradores.'
                  : errorMessage(updateMutation.error, 'Não foi possível salvar o usuário.')}
              </Alert>
            )}

            <Group justify="flex-end">
              <Button variant="default" onClick={closeEdit}>
                Cancelar
              </Button>
              <Button color="brand" loading={updateMutation.isPending} onClick={saveEdit}>
                Salvar
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>

      {/* ---- Modal: resetar senha (POST /users/{id}/reset-password) ------ */}
      <Modal
        opened={resetOpen}
        onClose={closeReset}
        title={resetTarget ? `Resetar senha de ${resetTarget.username}` : 'Resetar senha'}
        size="md"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          {resetPasswordMutation.data ? (
            <>
              <Alert color="teal" variant="light">
                Senha redefinida. Ela aparece <strong>uma única vez</strong> — copie agora e entregue ao usuário por
                um canal seguro. O usuário será obrigado a trocá-la no próximo login, e todas as sessões abertas dele
                foram encerradas ({resetPasswordMutation.data.revoked_sessions}).
              </Alert>
              <Group gap="sm" wrap="nowrap">
                <Code style={{ fontSize: 16, padding: '8px 12px', flex: 1, wordBreak: 'break-all' }}>
                  {resetPasswordMutation.data.temporary_password}
                </Code>
                <CopyButton value={resetPasswordMutation.data.temporary_password}>
                  {({ copied, copy }) => (
                    <Button variant="light" color={copied ? 'teal' : 'brand'} onClick={copy}>
                      {copied ? 'Copiado' : 'Copiar'}
                    </Button>
                  )}
                </CopyButton>
              </Group>
              <Group justify="flex-end">
                <Button color="brand" onClick={closeReset}>
                  Fechar
                </Button>
              </Group>
            </>
          ) : (
            <>
              <Text c={BRAND.textPrimary}>
                Gerar uma senha temporária para &quot;{resetTarget?.username}&quot;? A senha atual deixa de funcionar
                imediatamente, todas as sessões abertas desse usuário são encerradas e ele terá de definir uma nova
                senha no próximo login.
              </Text>
              {resetPasswordMutation.isError && (
                <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                  {isForbidden(resetPasswordMutation.error)
                    ? 'Ação restrita a administradores.'
                    : errorMessage(resetPasswordMutation.error, 'Não foi possível resetar a senha.')}
                </Alert>
              )}
              <Group justify="flex-end">
                <Button variant="default" onClick={closeReset}>
                  Cancelar
                </Button>
                <Button
                  color="brand"
                  loading={resetPasswordMutation.isPending}
                  onClick={() => resetTarget && resetPasswordMutation.mutate(resetTarget.id)}
                >
                  Resetar senha
                </Button>
              </Group>
            </>
          )}
        </Stack>
      </Modal>

      {/* ---- Modal: confirmar desativação (DELETE /users/{id}) ----------- */}
      <Modal
        opened={deactivateOpen}
        onClose={closeDeactivate}
        title="Confirmar desativação"
        size="sm"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Desativar o usuário &quot;{deactivateTarget?.username}&quot;? Ele deixará de conseguir fazer login, mas
            o histórico dele é preservado e a conta pode ser reativada depois (marque &quot;Conta ativa&quot; na
            edição).
          </Text>
          {deactivateMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {isForbidden(deactivateMutation.error)
                ? 'Ação restrita a administradores.'
                : errorMessage(deactivateMutation.error, 'Não foi possível desativar o usuário.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeDeactivate}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={deactivateMutation.isPending}
              onClick={() => deactivateTarget && deactivateMutation.mutate(deactivateTarget.id)}
            >
              Desativar
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
