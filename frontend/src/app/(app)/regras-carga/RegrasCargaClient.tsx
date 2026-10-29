'use client';

import { Alert, Badge, Button, Group, List, Loader, Modal, Paper, Stack, Text, Title } from '@mantine/core';
import { useDebouncedValue, useDisclosure } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle, IconArrowBackUp, IconDeviceFloppy, IconRestore, IconSend } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { ApiError, apiFetch } from '@/lib/api';
import { formatDateTimeOrDash } from '@/lib/format';
import {
  DRAFT_RULES_QUERY_KEY,
  MOTOR_DEFAULT_RULESET,
  NEXT_SET_LOAD_RULES_QUERY_KEY,
  PUBLISHED_RULES_QUERY_KEY,
  VERSIONS_RULES_QUERY_KEY,
  codeMessage,
  draftFromRuleSet,
  draftToPayload,
  isPublishConflict,
  ruleSetErrorDetail,
  ruleSetErrorMessages,
  sameRuleSet,
  shapeIssues,
  type RuleSetDraft,
} from '@/lib/next-set-load-rules';
import type {
  DescribeResponse,
  DraftPayload,
  DraftResponse,
  PublishPayload,
  PublishResponse,
  PublishedRuleSetResponse,
  VersionsResponse,
} from '@/lib/next-set-load-rules-types';
import type { UserAdminListResponse } from '@/lib/users-admin-types';
import { BRAND } from '@/theme';
import { GlobalParamsCard } from './components/GlobalParamsCard';
import { RuleSetTable } from './components/RuleSetTable';
import { SimulatePanel } from './components/SimulatePanel';
import { VersionsList } from './components/VersionsList';

const DESCRIBE_DEBOUNCE_MS = 500;

const fetchPublished = () => apiFetch<PublishedRuleSetResponse>('/next-set-load-rules/published');
const fetchDraft = () => apiFetch<DraftResponse>('/next-set-load-rules/draft');
const fetchVersions = () => apiFetch<VersionsResponse>('/next-set-load-rules/versions');

/** Base do editor: o rascunho salvo ou, sem rascunho, as regras vigentes. */
function editorBase(draft: DraftResponse, published: PublishedRuleSetResponse): RuleSetDraft {
  const stored = draft.draft;
  return stored ? draftFromRuleSet(stored.ruleset, stored.notes) : draftFromRuleSet(published.ruleset);
}

function queryError(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return error.status === 403 ? 'Ação restrita a administradores.' : error.message;
  return 'Erro ao carregar.';
}

function MessagesAlert({ color, title, messages }: { color: string; title: string; messages: string[] }) {
  if (messages.length === 0) return null;
  return (
    <Alert color={color} title={title} icon={<IconAlertTriangle size={16} />}>
      <List size="sm" spacing={2}>
        {messages.map((message) => (
          <List.Item key={message}>{message}</List.Item>
        ))}
      </List>
    </Alert>
  );
}

export function RegrasCargaClient() {
  const qc = useQueryClient();
  const publishedQuery = useQuery({ queryKey: PUBLISHED_RULES_QUERY_KEY, queryFn: fetchPublished });
  const draftQuery = useQuery({ queryKey: DRAFT_RULES_QUERY_KEY, queryFn: fetchDraft });
  const versionsQuery = useQuery({ queryKey: VERSIONS_RULES_QUERY_KEY, queryFn: fetchVersions });
  // Mesma chave da tela "Usuários": nome de quem publicou (falha = mostra o id).
  const usersQuery = useQuery({
    queryKey: ['users-admin'],
    queryFn: () => apiFetch<UserAdminListResponse>('/users'),
    staleTime: 60_000,
    retry: false,
  });

  const [editor, setEditor] = useState<RuleSetDraft | null>(null);
  const [base, setBase] = useState<RuleSetDraft | null>(null);
  const [restoredDefault, setRestoredDefault] = useState(false);
  const [saveErrors, setSaveErrors] = useState<string[]>([]);
  const [publishOpened, publishModal] = useDisclosure(false);

  function resetEditor(next: RuleSetDraft) {
    setBase(next);
    setEditor(next);
    setRestoredDefault(false);
    setSaveErrors([]);
  }

  useEffect(() => {
    if (editor === null && draftQuery.data && publishedQuery.data) {
      resetEditor(editorBase(draftQuery.data, publishedQuery.data));
    }
  }, [editor, draftQuery.data, publishedQuery.data]);

  async function reloadAll() {
    const [draft, published] = await Promise.all([
      qc.fetchQuery({ queryKey: DRAFT_RULES_QUERY_KEY, queryFn: fetchDraft, staleTime: 0 }),
      qc.fetchQuery({ queryKey: PUBLISHED_RULES_QUERY_KEY, queryFn: fetchPublished, staleTime: 0 }),
    ]);
    await qc.invalidateQueries({ queryKey: VERSIONS_RULES_QUERY_KEY });
    resetEditor(editorBase(draft, published));
  }

  const payload = useMemo(() => (editor ? draftToPayload(editor) : null), [editor]);
  const issues = useMemo(() => (editor ? shapeIssues(editor) : []), [editor]);
  const payloadJson = payload && issues.length === 0 ? JSON.stringify(payload) : null;
  const [debouncedJson] = useDebouncedValue(payloadJson, DESCRIBE_DEBOUNCE_MS);

  // Prévia ao vivo: textos, erros e avisos do DONO para o rascunho do editor.
  const describeQuery = useQuery({
    queryKey: [...NEXT_SET_LOAD_RULES_QUERY_KEY, 'describe', debouncedJson],
    queryFn: () =>
      apiFetch<DescribeResponse>('/next-set-load-rules/describe', {
        method: 'POST',
        body: JSON.stringify({ ruleset: JSON.parse(debouncedJson as string) }),
      }),
    enabled: debouncedJson !== null,
    staleTime: Infinity,
    retry: false,
  });
  const describe = describeQuery.data && debouncedJson === payloadJson ? describeQuery.data : null;
  const texts = (editor?.rules ?? []).map((rule, index) => describe?.texts[index] ?? rule.text);

  const dirty = editor !== null && base !== null && !sameRuleSet(editor, base);
  const storedDraft = draftQuery.data?.draft ?? null;
  const published = publishedQuery.data;

  const saveMutation = useMutation({
    mutationFn: (body: DraftPayload) =>
      apiFetch<DraftResponse>('/next-set-load-rules/draft', { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: async (data) => {
      qc.setQueryData(DRAFT_RULES_QUERY_KEY, data);
      await qc.invalidateQueries({ queryKey: VERSIONS_RULES_QUERY_KEY });
      if (data.draft) resetEditor(draftFromRuleSet(data.draft.ruleset, data.draft.notes));
      notifications.show({ color: 'green', message: 'Rascunho salvo. Publique para valer no Modo Treino.' });
      if (data.warnings.length > 0) {
        notifications.show({ color: 'yellow', title: 'Avisos do motor', message: data.warnings.map(codeMessage).join(' ') });
      }
    },
    onError: (error) => setSaveErrors(ruleSetErrorMessages(error, 'Erro ao salvar o rascunho.')),
  });

  const publishMutation = useMutation({
    mutationFn: (body: PublishPayload) =>
      apiFetch<PublishResponse>('/next-set-load-rules/publish', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: async (data) => {
      publishModal.close();
      notifications.show({ color: 'green', message: `Regras publicadas: v${data.published.version} vigente.` });
      await reloadAll();
    },
    onError: async (error) => {
      publishModal.close();
      if (isPublishConflict(error)) {
        const current = ruleSetErrorDetail(error)?.current_version;
        notifications.show({
          color: 'orange',
          title: 'Outra publicação aconteceu',
          message: `As regras vigentes mudaram${current != null ? ` (agora v${current})` : ''}. A tela foi recarregada: revise e publique de novo.`,
        });
        await reloadAll();
        return;
      }
      if (error instanceof ApiError && error.status === 404) {
        notifications.show({ color: 'orange', message: 'O rascunho não existe mais; a tela foi recarregada.' });
        await reloadAll();
        return;
      }
      setSaveErrors(ruleSetErrorMessages(error, 'Erro ao publicar.'));
    },
  });

  const loading = publishedQuery.isLoading || draftQuery.isLoading;
  const loadError = queryError(publishedQuery.error) ?? queryError(draftQuery.error);

  function userName(id: number | null): string {
    if (id == null) return '—';
    const user = usersQuery.data?.items.find((item) => item.id === id);
    return user ? user.full_name || user.username : `usuário #${id}`;
  }

  const publishBlocked = !storedDraft
    ? 'Salve um rascunho antes de publicar.'
    : dirty
      ? 'Há alterações não salvas: salve o rascunho antes de publicar.'
      : null;

  return (
    <Stack gap="lg">
      <div>
        <Title order={1} c={BRAND.textPrimary}>
          Regras de carga
        </Title>
        <Text c={BRAND.textMuted}>
          Regras que decidem a carga sugerida para a PRÓXIMA série no Modo Treino, a partir da série que acabou de ser
          registrada.
        </Text>
      </div>

      <Paper withBorder radius="md" p="md">
        <Stack gap={6}>
          <Group gap="xs">
            {published ? (
              published.source === 'published' ? (
                <Badge color="green" variant="light" style={{ textTransform: 'none' }} data-published-version>
                  Vigente: v{published.version}, publicada em {formatDateTimeOrDash(published.published_at)}
                </Badge>
              ) : (
                <Badge color="blue" variant="light" style={{ textTransform: 'none' }} data-published-version>
                  Vigente: padrão do sistema
                </Badge>
              )
            ) : (
              <Loader size="xs" color="brand" />
            )}
            {storedDraft ? (
              <Badge color="gray" variant="outline" style={{ textTransform: 'none' }}>
                Rascunho salvo em {formatDateTimeOrDash(storedDraft.updated_at)}
              </Badge>
            ) : null}
            {dirty && (
              <Badge color="orange" variant="light" style={{ textTransform: 'none' }}>
                Alterações não salvas
              </Badge>
            )}
          </Group>
          <List size="sm" spacing={2} c={BRAND.textMuted}>
            <List.Item>As regras valem só para a carga da próxima série, dentro da sessão.</List.Item>
            <List.Item>Dor articular ou dor muscular alta sempre reduz a carga, antes de qualquer regra.</List.Item>
            <List.Item>As regras são avaliadas em ordem: a primeira que casa vence; nenhuma casa = manter.</List.Item>
            <List.Item>Publicar vale na hora para todos os usuários.</List.Item>
          </List>
        </Stack>
      </Paper>

      {loadError && <Alert color="red">{loadError}</Alert>}
      {loading && (
        <Group justify="center" py="xl">
          <Loader color="brand" />
        </Group>
      )}

      {editor && (
        <>
          <GlobalParamsCard
            repsFloor={editor.reps_floor}
            repsCeiling={editor.reps_ceiling}
            notes={editor.notes}
            onChange={(patch) => setEditor((current) => (current ? { ...current, ...patch } : current))}
          />

          <Paper withBorder radius="md" p="md">
            <Stack gap="sm">
              <Title order={4}>Regras (arraste para mudar a ordem)</Title>
              <RuleSetTable
                rules={editor.rules}
                texts={texts}
                onChange={(rules) => setEditor((current) => (current ? { ...current, rules } : current))}
              />
              {restoredDefault && (
                <Alert color="blue" title="Padrão carregado no editor">
                  As regras padrão do sistema foram carregadas como rascunho. Ao salvar e publicar, a direção e a carga
                  sugeridas ficam iguais às do padrão, mas o texto do motivo passa a ser o da regra (como nas regras
                  cadastradas) e a versão vigente vira um número a partir de v1. Nada muda até publicar.
                </Alert>
              )}
              <MessagesAlert color="yellow" title="Revise os campos" messages={issues} />
              <MessagesAlert color="red" title="O motor recusa este conjunto" messages={(describe?.errors ?? []).map(codeMessage)} />
              <MessagesAlert color="yellow" title="Avisos do motor" messages={(describe?.warnings ?? []).map(codeMessage)} />
              <MessagesAlert color="red" title="Não foi possível concluir" messages={saveErrors} />
              <Group gap="sm">
                <Button
                  color="brand"
                  leftSection={<IconDeviceFloppy size={16} />}
                  loading={saveMutation.isPending}
                  disabled={issues.length > 0 || payload === null || (!dirty && storedDraft !== null)}
                  onClick={() => {
                    if (!payload) return;
                    setSaveErrors([]);
                    saveMutation.mutate({ ...payload, notes: editor.notes.trim() || null });
                  }}
                >
                  Salvar rascunho
                </Button>
                <Button
                  color="green"
                  leftSection={<IconSend size={16} />}
                  disabled={publishBlocked !== null || !published}
                  onClick={publishModal.open}
                >
                  Publicar
                </Button>
                <Button
                  variant="default"
                  leftSection={<IconArrowBackUp size={16} />}
                  disabled={!dirty}
                  onClick={() => {
                    if (base) setEditor(base);
                    setRestoredDefault(false);
                    setSaveErrors([]);
                  }}
                >
                  Descartar alterações
                </Button>
                <Button
                  variant="subtle"
                  leftSection={<IconRestore size={16} />}
                  title="Carrega as regras padrão do sistema no editor como rascunho; ainda é preciso salvar e publicar."
                  onClick={() => {
                    setEditor((current) => ({ ...draftFromRuleSet(MOTOR_DEFAULT_RULESET), notes: current?.notes ?? '' }));
                    setRestoredDefault(true);
                    setSaveErrors([]);
                  }}
                >
                  Restaurar padrão
                </Button>
              </Group>
              {publishBlocked && (
                <Text size="xs" c={BRAND.textMuted}>
                  {publishBlocked}
                </Text>
              )}
            </Stack>
          </Paper>

          <SimulatePanel draftPayload={issues.length === 0 ? payload : null} />
        </>
      )}

      <VersionsList
        versions={versionsQuery.data?.versions ?? []}
        isLoading={versionsQuery.isLoading}
        error={queryError(versionsQuery.error)}
        userName={userName}
      />

      <Modal opened={publishOpened} onClose={publishModal.close} title="Publicar regras de carga" centered>
        <Stack gap="sm">
          <Text size="sm">
            Publicar troca as regras vigentes{published ? ` (${published.source === 'published' ? `v${published.version}` : 'padrão do sistema'})` : ''}{' '}
            pelo rascunho salvo. A carga sugerida para a próxima série muda na hora para TODOS os usuários no Modo
            Treino.
          </Text>
          <Group justify="flex-end" gap="sm">
            <Button variant="default" onClick={publishModal.close}>
              Cancelar
            </Button>
            <Button
              color="green"
              loading={publishMutation.isPending}
              onClick={() => {
                if (!storedDraft || !published) return;
                publishMutation.mutate({ draft_id: storedDraft.id, expected_published_version: published.version });
              }}
            >
              Publicar agora
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
