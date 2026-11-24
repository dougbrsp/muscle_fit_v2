'use client';

import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  MultiSelect,
  Pagination,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { ApiError, apiFetch } from '@/lib/api';
import {
  COMPONENT_FIELDS,
  fetchCoverage,
  fetchHistory,
  fetchProfiles,
  fetchVocabulary,
  payloadWithStatus,
  putProfile,
  resistanceKeys,
  suggestProfiles,
  type ComponentField,
  type ResistanceFilters,
  type ResistanceProfile,
  type ResistanceProfilePayload,
  type ResistanceVocabulary,
  type VocabularyMap,
} from '@/lib/resistance-profiles-api';

const PAGE_SIZE = 25;
const FIELD_TITLES: Record<ComponentField, string> = {
  source: 'Fonte',
  mechanism: 'Mecanismo',
  trajectory: 'Trajetória',
  profile: 'Perfil',
  line: 'Linha de força',
  direction_changes: 'Muda de direção',
};

export type ProfileForm = {
  components: Record<ComponentField, string>;
  flags: string[];
  confidence: string | null;
  status: string;
  rationale: string;
  evidence: string;
  reason: string;
};

/** Erro da API sem derrubar a tela; o 422 tipado do dono (`code` + `field`) vira texto legível. */
export function resistanceErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  if (error.status === 403) return 'Ação restrita a quem pode editar o catálogo.';
  const d = error.detail as { code?: string; field?: string } | null | undefined;
  if (error.status === 422 && d && typeof d === 'object' && d.code) {
    return `Valor recusado pela API (${d.field ?? 'campo'}: ${d.code}).`;
  }
  return error.message || fallback;
}

export function profileToForm(p: ResistanceProfile, defaultStatus: string): ProfileForm {
  const components = Object.fromEntries(COMPONENT_FIELDS.map((f) => [f, p[f]])) as Record<ComponentField, string>;
  return {
    components,
    flags: p.flags,
    confidence: p.confidence,
    status: p.status ?? defaultStatus,
    rationale: p.rationale ?? '',
    evidence: p.evidence ?? '',
    reason: '',
  };
}

export function formToPayload(form: ProfileForm): ResistanceProfilePayload {
  return {
    ...form.components,
    flags: form.flags,
    confidence: form.confidence,
    status: form.status,
    rationale: form.rationale.trim() || null,
    evidence: form.evidence.trim() || null,
    reason: form.reason.trim() || null,
  };
}

/** Campos que mudaram entre dois snapshots do histórico (chaves vindas da API). */
export function changedFields(before: Record<string, unknown> | null, after: Record<string, unknown> | null): string[] {
  const keys = new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]);
  return [...keys].filter((k) => JSON.stringify(before?.[k] ?? null) !== JSON.stringify(after?.[k] ?? null));
}

function options(map: VocabularyMap | undefined) {
  return Object.entries(map ?? {}).map(([value, label]) => ({ value, label }));
}

function label(map: VocabularyMap | undefined, code: string | null | undefined): string {
  return code ? (map?.[code] ?? code) : '—';
}

// ---------------------------------------------------------------------------

function EditModal({
  profile,
  vocabulary,
  onClose,
}: {
  profile: ResistanceProfile;
  vocabulary: ResistanceVocabulary;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<ProfileForm>(() => profileToForm(profile, vocabulary.default_status));
  const save = useMutation({
    mutationFn: () => putProfile(profile.equipment_exercise_id, formToPayload(form)),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: resistanceKeys.all });
      onClose();
    },
  });
  const set = (patch: Partial<ProfileForm>) => setForm((f) => ({ ...f, ...patch }));

  return (
    <Modal opened onClose={onClose} size="lg" title={`${profile.exercise_name} — ${profile.equipment_name}`}>
      <Stack>
        {save.isError && (
          <Alert color="red" role="alert">
            {resistanceErrorMessage(save.error, 'Não foi possível salvar o perfil.')}
          </Alert>
        )}
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          {COMPONENT_FIELDS.map((f) => (
            <Select
              key={f}
              label={FIELD_TITLES[f]}
              data={options(vocabulary[f])}
              value={form.components[f]}
              onChange={(v) => v && set({ components: { ...form.components, [f]: v } })}
              allowDeselect={false}
            />
          ))}
        </SimpleGrid>
        <MultiSelect
          label="Sinalizadores de sensibilidade"
          data={options(vocabulary.flags)}
          value={form.flags}
          onChange={(flags) => set({ flags })}
        />
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <Select
            label="Confiança"
            data={options(vocabulary.confidence)}
            value={form.confidence}
            onChange={(confidence) => set({ confidence })}
            clearable
          />
          <Select
            label="Estado"
            data={options(vocabulary.status)}
            value={form.status}
            onChange={(v) => v && set({ status: v })}
            allowDeselect={false}
          />
        </SimpleGrid>
        <Textarea label="Justificativa" autosize minRows={2} value={form.rationale} onChange={(e) => set({ rationale: e.currentTarget.value })} />
        <Textarea label="Evidência (texto puro)" autosize minRows={2} value={form.evidence} onChange={(e) => set({ evidence: e.currentTarget.value })} />
        <Textarea label="Motivo da alteração" autosize minRows={1} value={form.reason} onChange={(e) => set({ reason: e.currentTarget.value })} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancelar
          </Button>
          <Button color="brand" loading={save.isPending} onClick={() => save.mutate()}>
            Salvar
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function HistoryModal({ profile, vocabulary, onClose }: { profile: ResistanceProfile; vocabulary: ResistanceVocabulary; onClose: () => void }) {
  const q = useQuery({
    queryKey: resistanceKeys.history(profile.equipment_exercise_id),
    queryFn: () => fetchHistory(profile.equipment_exercise_id),
  });
  const items = q.data?.items ?? [];
  return (
    <Modal opened onClose={onClose} size="lg" title={`Histórico — ${profile.exercise_name} / ${profile.equipment_name}`}>
      {q.isLoading ? (
        <Loader size="sm" />
      ) : q.isError ? (
        <Alert color="red" role="alert">
          {resistanceErrorMessage(q.error, 'Não foi possível carregar o histórico.')}
        </Alert>
      ) : items.length === 0 ? (
        <Text c="dimmed">Sem alterações registradas para este par.</Text>
      ) : (
        <Stack gap="sm">
          {items.map((h) => (
            <Paper key={h.id} withBorder p="sm">
              <Group gap="xs">
                <Text size="sm" fw={600}>
                  {new Date(h.created_at).toLocaleString('pt-BR')}
                </Text>
                <Badge variant="light">{label(vocabulary.origin, h.origin)}</Badge>
              </Group>
              {h.reason && <Text size="sm">Motivo: {h.reason}</Text>}
              <Text size="xs" c="dimmed">
                Alterou:{' '}
                {changedFields(h.before, h.after)
                  .map((k) => FIELD_TITLES[k as ComponentField] ?? k)
                  .join(', ') || 'nada'}
              </Text>
            </Paper>
          ))}
        </Stack>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------

export function ResistanceProfilesTab({
  equipment,
  canView,
  canEdit,
}: {
  equipment: { id: number; name: string }[];
  canView: boolean;
  canEdit: boolean;
}) {
  const qc = useQueryClient();
  const [filters, setFilters] = useState<ResistanceFilters>({});
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<ResistanceProfile | null>(null);
  const [history, setHistory] = useState<ResistanceProfile | null>(null);
  const [notice, setNotice] = useState<{ color: string; text: string } | null>(null);
  const [confirm, setConfirm] = useState<{ p: ResistanceProfile; kind: 'approve' | 'reject' } | null>(null);
  const [confirmReason, setConfirmReason] = useState('');

  const vocabQ = useQuery({ queryKey: resistanceKeys.vocabulary, queryFn: fetchVocabulary, enabled: canView, staleTime: 300_000 });
  const coverageQ = useQuery({ queryKey: resistanceKeys.coverage, queryFn: fetchCoverage, enabled: canView });
  const query = { ...filters, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE };
  const listQ = useQuery({ queryKey: resistanceKeys.list(query), queryFn: () => fetchProfiles(query), enabled: canView });

  const vocabulary = vocabQ.data;
  const actions = vocabulary?.actions;
  const maxPairs = vocabulary?.suggest_max_pairs;
  const overLimit = maxPairs != null && selected.size > maxPairs;
  // Mesma consulta da aba de associações (cache compartilhado): fonte das opções do filtro por exercício.
  const linksQ = useQuery({
    queryKey: ['equipment-exercises', null, null],
    queryFn: () => apiFetch<{ items: { exercise_id: number; exercise_name: string | null }[] }>('/equipment-exercises?active_only=true'),
    enabled: canView,
  });
  const exerciseOptions = [
    ...new Map((linksQ.data?.items ?? []).map((l) => [l.exercise_id, l.exercise_name ?? `#${l.exercise_id}`])).entries(),
  ]
    .map(([id, name]) => ({ value: String(id), label: name }))
    .sort((a, b) => a.label.localeCompare(b.label));

  const quickMut = useMutation({
    mutationFn: ({ p, status, reason }: { p: ResistanceProfile; status: string; reason: string | null }) =>
      putProfile(p.equipment_exercise_id, payloadWithStatus(p, status, reason)),
    onSuccess: () => qc.invalidateQueries({ queryKey: resistanceKeys.all }),
    onError: (e) => setNotice({ color: 'red', text: resistanceErrorMessage(e, 'Não foi possível alterar o estado.') }),
  });

  const suggestMut = useMutation({
    mutationFn: () => suggestProfiles(selected.size > 0 ? [...selected] : null),
    onSuccess: async (r) => {
      setNotice(
        r
          ? {
              color: r.failed.length > 0 ? 'yellow' : 'green',
              text: `Sugeridos: ${r.suggested}. Pulados: ${r.skipped}. Falhas: ${r.failed.length}${
                r.failed.length > 0 ? ` (${r.failed.map((f) => `par ${f.id}: ${f.code}`).join('; ')})` : ''
              }.`,
            }
          : { color: 'blue', text: 'Nenhum par pendente para sugerir.' },
      );
      setSelected(new Set());
    },
    onError: (e) => setNotice({ color: 'red', text: resistanceErrorMessage(e, 'Falha ao pedir a sugestão por IA.') }),
    // Atualiza a lista também quando o job falha ou expira (parte dos pares pode ter sido gravada).
    onSettled: () => qc.invalidateQueries({ queryKey: resistanceKeys.all }),
  });

  const totalForPage = listQ.data?.total;
  const lastPage = Math.max(1, Math.ceil((totalForPage ?? 0) / PAGE_SIZE));
  useEffect(() => {
    if (totalForPage != null && page > lastPage) setPage(lastPage);
  }, [totalForPage, page, lastPage]);

  if (!canView) return <Alert color="yellow">Sem permissão para ver os perfis de resistência.</Alert>;

  const setFilter = (patch: Partial<ResistanceFilters>) => {
    setFilters((f) => ({ ...f, ...patch }));
    setPage(1);
    setSelected(new Set());
  };
  const items = listQ.data?.items ?? [];
  const total = listQ.data?.total ?? 0;
  const cov = coverageQ.data;
  const openConfirm = (p: ResistanceProfile, kind: 'approve' | 'reject') => {
    setConfirmReason('');
    setConfirm({ p, kind });
  };
  const toggle = (id: number) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <Stack>
      <Paper withBorder p="md">
        <Title order={4} mb="xs">
          Cobertura
        </Title>
        {coverageQ.isLoading ? (
          <Loader size="sm" />
        ) : coverageQ.isError || !cov ? (
          <Alert color="red" role="alert">
            {resistanceErrorMessage(coverageQ.error, 'Não foi possível carregar a cobertura.')}
          </Alert>
        ) : (
          <Group gap="lg">
            <Text size="sm">Pares ativos: {cov.active_pairs}</Text>
            <Text size="sm">Com perfil: {cov.with_profile}</Text>
            <Text size="sm">Sem perfil: {cov.without_profile}</Text>
            <Text size="sm">Desconhecidos: {cov.unknown}</Text>
            {Object.entries(cov.by_status).map(([s, n]) => (
              <Badge key={s} variant="light">
                {label(vocabulary?.status, s)}: {n}
              </Badge>
            ))}
          </Group>
        )}
      </Paper>

      {notice && (
        <Alert color={notice.color} withCloseButton onClose={() => setNotice(null)} role="status">
          {notice.text}
        </Alert>
      )}
      {vocabQ.isError && (
        <Alert color="red" role="alert">
          {resistanceErrorMessage(vocabQ.error, 'Não foi possível carregar o vocabulário.')}
        </Alert>
      )}

      <Paper withBorder p="md">
        <Group align="flex-end" gap="sm" wrap="wrap">
          <Select
            label="Estado"
            data={options(vocabulary?.status)}
            value={filters.status ?? null}
            onChange={(v) => setFilter({ status: v })}
            clearable
            w={160}
          />
          <Select
            label="Confiança"
            data={options(vocabulary?.confidence)}
            value={filters.confidence ?? null}
            onChange={(v) => setFilter({ confidence: v })}
            clearable
            w={140}
          />
          <Select
            label="Equipamento"
            data={equipment.map((e) => ({ value: String(e.id), label: e.name }))}
            value={filters.equipment_id ?? null}
            onChange={(v) => setFilter({ equipment_id: v })}
            searchable
            clearable
            w={220}
          />
          <Select
            label="Exercício"
            data={exerciseOptions}
            value={filters.exercise_id ?? null}
            onChange={(v) => setFilter({ exercise_id: v })}
            searchable
            clearable
            w={220}
          />
          <Switch label="Só pendentes" checked={!!filters.pending} onChange={(e) => setFilter({ pending: e.currentTarget.checked })} />
          {canEdit && (
            <Button color="brand" loading={suggestMut.isPending} disabled={overLimit} onClick={() => suggestMut.mutate()}>
              {selected.size > 0 ? `Sugerir por IA (${selected.size} selecionados)` : 'Sugerir por IA (pendentes)'}
            </Button>
          )}
          {canEdit && selected.size > 0 && (
            <Group gap="xs">
              <Text size="sm">{selected.size} selecionados</Text>
              <Button size="compact-sm" variant="subtle" onClick={() => setSelected(new Set())}>
                Limpar seleção
              </Button>
            </Group>
          )}
        </Group>
        {canEdit && maxPairs != null && (
          <Text size="xs" c={overLimit ? 'red' : 'dimmed'} mt="xs" role={overLimit ? 'alert' : undefined}>
            {overLimit
              ? `Selecione no máximo ${maxPairs} pares por pedido (${selected.size} selecionados).`
              : `Cada pedido de sugestão por IA aceita até ${maxPairs} pares.`}
          </Text>
        )}
      </Paper>

      {listQ.isLoading ? (
        <Loader size="sm" />
      ) : listQ.isError ? (
        <Alert color="red" role="alert">
          {resistanceErrorMessage(listQ.error, 'Não foi possível carregar os perfis.')}
        </Alert>
      ) : items.length === 0 ? (
        <Text c="dimmed">Nenhum par encontrado com esses filtros.</Text>
      ) : (
        <Table.ScrollContainer minWidth={760}>
          <Table striped verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                {canEdit && <Table.Th w={36} aria-label="Selecionar" />}
                <Table.Th>Par</Table.Th>
                <Table.Th>Assinatura</Table.Th>
                <Table.Th>Estado</Table.Th>
                <Table.Th>Confiança</Table.Th>
                <Table.Th>Origem</Table.Th>
                <Table.Th>Ações</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((p) => (
                <Table.Tr key={p.equipment_exercise_id}>
                  {canEdit && (
                    <Table.Td>
                      <Checkbox
                        aria-label={`Selecionar ${p.exercise_name} / ${p.equipment_name}`}
                        checked={selected.has(p.equipment_exercise_id)}
                        onChange={() => toggle(p.equipment_exercise_id)}
                      />
                    </Table.Td>
                  )}
                  <Table.Td>
                    <Text size="sm" fw={600}>
                      {p.exercise_name}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {p.equipment_name}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{p.signature_label ?? '—'}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light">{p.has_profile ? label(vocabulary?.status, p.status) : 'Sem perfil'}</Badge>
                  </Table.Td>
                  <Table.Td>{label(vocabulary?.confidence, p.confidence)}</Table.Td>
                  <Table.Td>{label(vocabulary?.origin, p.origin)}</Table.Td>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      {canEdit && p.has_profile && actions && (
                        <>
                          <Button size="compact-xs" variant="light" color="green" disabled={quickMut.isPending}
                            aria-label={`Aprovar ${p.exercise_name} / ${p.equipment_name}`}
                            onClick={() => openConfirm(p, 'approve')}>
                            Aprovar
                          </Button>
                          <Button size="compact-xs" variant="light" color="red" disabled={quickMut.isPending}
                            aria-label={`Rejeitar ${p.exercise_name} / ${p.equipment_name}`}
                            onClick={() => openConfirm(p, 'reject')}>
                            Rejeitar
                          </Button>
                        </>
                      )}
                      {canEdit && vocabulary && (
                        <Button size="compact-xs" variant="default" aria-label={`Editar ${p.exercise_name} / ${p.equipment_name}`}
                          onClick={() => setEditing(p)}>
                          Editar
                        </Button>
                      )}
                      <Button size="compact-xs" variant="subtle" aria-label={`Histórico ${p.exercise_name} / ${p.equipment_name}`}
                        onClick={() => setHistory(p)}>
                        Histórico
                      </Button>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      {(total > PAGE_SIZE || page > 1) && <Pagination value={page} onChange={setPage} total={lastPage} />}

      {confirm && actions && (
        <Modal
          opened
          onClose={() => setConfirm(null)}
          title={`${confirm.kind === 'approve' ? 'Aprovar' : 'Rejeitar'} ${confirm.p.exercise_name} / ${confirm.p.equipment_name}`}
        >
          <Stack>
            <Textarea label="Motivo (opcional)" autosize minRows={2} value={confirmReason} onChange={(e) => setConfirmReason(e.currentTarget.value)} />
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setConfirm(null)}>
                Cancelar
              </Button>
              <Button
                color={confirm.kind === 'approve' ? 'green' : 'red'}
                loading={quickMut.isPending}
                onClick={() => {
                  quickMut.mutate({ p: confirm.p, status: actions[confirm.kind], reason: confirmReason.trim() || null });
                  setConfirm(null);
                }}
              >
                Confirmar
              </Button>
            </Group>
          </Stack>
        </Modal>
      )}

      {editing && vocabulary && <EditModal key={editing.equipment_exercise_id} profile={editing} vocabulary={vocabulary} onClose={() => setEditing(null)} />}
      {history && vocabulary && <HistoryModal profile={history} vocabulary={vocabulary} onClose={() => setHistory(null)} />}
    </Stack>
  );
}
