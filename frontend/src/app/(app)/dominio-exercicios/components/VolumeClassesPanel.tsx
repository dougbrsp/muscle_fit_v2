'use client';

import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  MultiSelect,
  NumberInput,
  Paper,
  SegmentedControl,
  Stack,
  Table,
  Text,
  Textarea,
  TextInput,
  Tooltip,
  type ButtonProps,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconCopy, IconDeviceFloppy, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import { statusVisual } from '@/lib/dashboard-status';
import type { MuscleGroupItem, MuscleGroupListResponse } from '@/lib/exercise-catalog-admin-types';
import type { MethodologyRangeSource } from '@/lib/methodology-volume-types';
import {
  FREQUENCY_BUCKET_LABEL,
  VOLUME_CLASSES_QUERY_KEY,
  buildRangesPayload,
  countingModeQuery,
  formatDecimalRange,
  groupOptionsFor,
  missingInMode,
  parseDraftNumber,
  rangeDrafts,
  sameRanges,
  sortVolumeClasses,
  toNumber,
  unassignedGroups,
  volumeClassesQueryKey,
  type DraftNumber,
  type VolumeClassRangeDraft,
} from '@/lib/volume-classes';
import {
  COUNTING_MODE_SELECT_DATA,
  countingModeOption,
  DEFAULT_COUNTING_MODE,
  isDefaultCountingMode,
  normalizeCountingMode,
  setsUnitOf,
  UNCLASSIFIED_VOLUME_CLASS_LABEL,
} from '@/lib/volume-counting-mode';
import type {
  VolumeClassItem,
  VolumeClassListResponse,
  VolumeClassResponse,
  VolumeClassUpsertPayload,
} from '@/lib/volume-classes-types';
import { BRAND } from '@/theme';
import { ConfirmDeleteModal } from './ConfirmDeleteModal';

/** Mesma chave do `MuscleGroupsPanel` — o React Query dedupa a leitura. */
export const MUSCLE_GROUPS_CRUD_QUERY_KEY = ['exercise-catalog-admin-muscle-groups-crud'] as const;

const ADMIN_ONLY = 'Ação restrita a administradores';

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.status === 403 ? `${ADMIN_ONLY}.` : error.message;
  return fallback;
}

/**
 * Depois de qualquer escrita: o cadastro, os grupos (coluna "Macro-grupo" da
 * aba ao lado e seletor do drawer) e TODA leitura da metodologia
 * (`workout-plan-template-methodology-volume*`: preview do builder e tela de
 * diagnóstico), porque as faixas avaliadas mudaram.
 */
export async function invalidateAfterVolumeClassWrite(qc: QueryClient): Promise<void> {
  await Promise.all([
    qc.invalidateQueries({ queryKey: VOLUME_CLASSES_QUERY_KEY }),
    qc.invalidateQueries({ queryKey: MUSCLE_GROUPS_CRUD_QUERY_KEY }),
    qc.invalidateQueries({ queryKey: ['exercise-catalog-admin-muscle-groups'] }),
    qc.invalidateQueries({
      predicate: (query) =>
        typeof query.queryKey[0] === 'string' &&
        query.queryKey[0].startsWith('workout-plan-template-methodology-volume'),
    }),
  ]);
}

/** Selo da faixa/meta copiada do modo default e ainda não calibrada (073, Lote 3). */
function UncalibratedBadge({ show }: { show: boolean | undefined }) {
  if (!show) return null;
  return (
    <Tooltip
      label={`Cópia de "${countingModeOption(DEFAULT_COUNTING_MODE).label}": grave um número diferente para calibrar este modo.`}
      multiline
      w={260}
      withArrow
    >
      <Badge size="xs" variant="outline" color="yellow" data-uncalibrated style={{ textTransform: 'none' }}>
        cópia — não calibrada
      </Badge>
    </Tooltip>
  );
}

function capitalize(text: string): string {
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}`;
}

function SourceBadge({ source, evidence }: { source: MethodologyRangeSource | null; evidence: string | null }) {
  if (!source) return <Text size="xs" c={BRAND.textMuted}>—</Text>;
  const visual = statusVisual('methodologyRangeSource', source);
  const Icon = visual.Icon;
  return (
    <Tooltip label={[visual.description, evidence].filter(Boolean).join(' ')} multiline w={300} withArrow>
      <Badge
        size="xs"
        variant="light"
        color={visual.color}
        leftSection={<Icon size={10} aria-hidden />}
        data-range-source={source}
        style={{ textTransform: 'none' }}
      >
        {visual.label}
      </Badge>
    </Tooltip>
  );
}

function AdminButton({
  isAdmin,
  disabledReason,
  children,
  ...props
}: ButtonProps & {
  isAdmin: boolean;
  disabledReason?: string | null;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  const reason = !isAdmin ? ADMIN_ONLY : disabledReason;
  if (reason) {
    return (
      <Tooltip label={reason} multiline w={260}>
        <Button {...props} disabled>
          {children}
        </Button>
      </Tooltip>
    );
  }
  return <Button {...props}>{children}</Button>;
}

type RangeField = 'sets_min' | 'sets_max' | 'exercises_min' | 'exercises_max';

type CardProps = {
  item: VolumeClassItem;
  groups: MuscleGroupItem[];
  classes: VolumeClassItem[];
  isAdmin: boolean;
  onDelete: (item: VolumeClassItem) => void;
  /** Modo de contagem das faixas e da meta semanal exibidas/editadas. */
  countingMode: string;
};

function VolumeClassCard({ item, groups, classes, isAdmin, onDelete, countingMode }: CardProps) {
  const modeQuery = countingModeQuery(countingMode);
  const setsUnit = setsUnitOf(countingMode).plural;
  const isDefaultMode = isDefaultCountingMode(countingMode);
  const missing = missingInMode(item);
  const qc = useQueryClient();
  const [name, setName] = useState(item.name);
  const [description, setDescription] = useState(item.description ?? '');
  const [weeklyMin, setWeeklyMin] = useState<DraftNumber>(toNumber(item.weekly_sets_min) ?? '');
  const [weeklyMax, setWeeklyMax] = useState<DraftNumber>(toNumber(item.weekly_sets_max) ?? '');
  const [ranges, setRanges] = useState<VolumeClassRangeDraft[]>(() => rangeDrafts(item.ranges));
  const [groupIds, setGroupIds] = useState<string[]>(() => item.muscle_group_ids.map(String));

  const groupOptions = useMemo(() => groupOptionsFor(item.id, groups, classes), [item.id, groups, classes]);
  const sourceByBucket = useMemo(
    () => new Map(item.ranges.map((range) => [range.frequency_bucket, range])),
    [item.ranges],
  );

  const fieldsDirty =
    name.trim() !== item.name ||
    (description.trim() || null) !== (item.description || null) ||
    parseDraftNumber(weeklyMin) !== toNumber(item.weekly_sets_min) ||
    parseDraftNumber(weeklyMax) !== toNumber(item.weekly_sets_max);
  const rangesDirty = !sameRanges(ranges, item.ranges);
  const nextGroupIds = groupIds.map(Number).sort((a, b) => a - b);
  const groupsDirty =
    JSON.stringify(nextGroupIds) !== JSON.stringify([...item.muscle_group_ids].sort((a, b) => a - b));
  const dirty = fieldsDirty || rangesDirty || groupsDirty;

  const saveMutation = useMutation({
    // Três chamadas, só as que mudaram, em sequência: se uma falha, as
    // anteriores já valeram e a mensagem do backend (422/409) aparece no card.
    mutationFn: async () => {
      if (fieldsDirty) {
        const payload: VolumeClassUpsertPayload = {
          name: name.trim(),
          description: description.trim() || null,
          weekly_sets_min: parseDraftNumber(weeklyMin),
          weekly_sets_max: parseDraftNumber(weeklyMax),
        };
        await apiFetch<VolumeClassResponse>(`/muscle-volume-classes/${item.id}${modeQuery}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        });
      }
      if (rangesDirty) {
        await apiFetch<VolumeClassResponse>(`/muscle-volume-classes/${item.id}/ranges${modeQuery}`, {
          method: 'PUT',
          body: JSON.stringify({ ranges: buildRangesPayload(ranges) }),
        });
      }
      if (groupsDirty) {
        await apiFetch<VolumeClassResponse>(`/muscle-volume-classes/${item.id}/groups`, {
          method: 'PUT',
          body: JSON.stringify({ muscle_group_ids: nextGroupIds }),
        });
      }
    },
    onSettled: () => invalidateAfterVolumeClassWrite(qc),
  });

  // Lote 3: ação EXPLÍCITA — só preenche, no modo ponderado, o que ainda não
  // existe (bucket sem faixa, meta ausente), copiando do modo default.
  const copyMutation = useMutation({
    mutationFn: () =>
      apiFetch<VolumeClassResponse>(`/muscle-volume-classes/${item.id}/copy-from-default${modeQuery}`, {
        method: 'POST',
      }),
    onSettled: () => invalidateAfterVolumeClassWrite(qc),
  });

  function updateRange(bucketIndex: number, field: RangeField, value: DraftNumber) {
    setRanges((current) => current.map((row, index) => (index === bucketIndex ? { ...row, [field]: value } : row)));
  }

  const readOnly = !isAdmin;
  const hasGroups = item.muscle_group_ids.length > 0;

  return (
    <Paper withBorder radius="md" p="md" data-volume-class={item.id} style={{ borderColor: BRAND.border }}>
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start" wrap="wrap">
          <Stack gap={4} style={{ flex: 1, minWidth: 240 }}>
            <TextInput
              label="Nome"
              value={name}
              onChange={(event) => setName(event.currentTarget.value)}
              disabled={readOnly}
              maxLength={80}
            />
            <Textarea
              label="Descrição"
              value={description}
              onChange={(event) => setDescription(event.currentTarget.value)}
              disabled={readOnly}
              autosize
              minRows={1}
              maxLength={500}
            />
          </Stack>
          <Stack gap={4}>
            <Group gap={6}>
              <Text size="sm" fw={500} c={BRAND.textPrimary}>
                Meta semanal ({setsUnit} por semana)
              </Text>
              <UncalibratedBadge show={!isDefaultMode && item.weekly_uncalibrated} />
            </Group>
            <Group gap="xs" align="center" wrap="nowrap">
              <NumberInput
                aria-label={`Meta semanal mínima de ${item.name}`}
                value={weeklyMin}
                onChange={setWeeklyMin}
                disabled={readOnly}
                decimalSeparator=","
                decimalScale={1}
                allowNegative={false}
                min={0}
                w={80}
                size="xs"
              />
              <Text size="xs" c={BRAND.textMuted}>
                a
              </Text>
              <NumberInput
                aria-label={`Meta semanal máxima de ${item.name}`}
                value={weeklyMax}
                onChange={setWeeklyMax}
                disabled={readOnly}
                decimalSeparator=","
                decimalScale={1}
                allowNegative={false}
                min={0}
                w={80}
                size="xs"
              />
              <SourceBadge source={item.weekly_source} evidence={item.weekly_evidence} />
            </Group>
            <Text size="xs" c={BRAND.textMuted}>
              Atual: {formatDecimalRange(item.weekly_sets_min, item.weekly_sets_max)} — vazio = sem meta semanal.
            </Text>
          </Stack>
        </Group>

        <Table.ScrollContainer minWidth={640}>
          <Table withTableBorder withColumnBorders verticalSpacing={4} horizontalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Frequência do grupo</Table.Th>
                <Table.Th>{capitalize(setsUnit)} por treino (mín–máx)</Table.Th>
                <Table.Th>Origem</Table.Th>
                <Table.Th>Exercícios por treino (mín–máx, opcional)</Table.Th>
                <Table.Th>Origem</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {ranges.map((row, index) => {
                const saved = sourceByBucket.get(row.frequency_bucket);
                const numberProps = { disabled: readOnly, allowNegative: false, min: 0, w: 72, size: 'xs' as const };
                return (
                  <Table.Tr key={row.frequency_bucket} data-frequency-bucket={row.frequency_bucket}>
                    <Table.Td>
                      <Text size="sm" c={BRAND.textPrimary}>
                        {FREQUENCY_BUCKET_LABEL[row.frequency_bucket]}
                      </Text>
                      {!saved && (
                        <Text size="xs" c={BRAND.textMuted}>
                          sem faixa (não avalia por treino)
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4} wrap="nowrap">
                        <NumberInput
                          {...numberProps}
                          aria-label={`Séries mínimas por treino em ${FREQUENCY_BUCKET_LABEL[row.frequency_bucket]}`}
                          value={row.sets_min}
                          onChange={(value) => updateRange(index, 'sets_min', value)}
                          decimalSeparator=","
                          decimalScale={1}
                        />
                        <Text size="xs" c={BRAND.textMuted}>
                          a
                        </Text>
                        <NumberInput
                          {...numberProps}
                          aria-label={`Séries máximas por treino em ${FREQUENCY_BUCKET_LABEL[row.frequency_bucket]}`}
                          value={row.sets_max}
                          onChange={(value) => updateRange(index, 'sets_max', value)}
                          decimalSeparator=","
                          decimalScale={1}
                        />
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4} wrap="nowrap">
                        <SourceBadge source={saved?.sets_source ?? null} evidence={saved?.sets_evidence ?? null} />
                        <UncalibratedBadge show={!isDefaultMode && saved?.uncalibrated} />
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4} wrap="nowrap">
                        <NumberInput
                          {...numberProps}
                          aria-label={`Exercícios mínimos por treino em ${FREQUENCY_BUCKET_LABEL[row.frequency_bucket]}`}
                          value={row.exercises_min}
                          onChange={(value) => updateRange(index, 'exercises_min', value)}
                          allowDecimal={false}
                        />
                        <Text size="xs" c={BRAND.textMuted}>
                          a
                        </Text>
                        <NumberInput
                          {...numberProps}
                          aria-label={`Exercícios máximos por treino em ${FREQUENCY_BUCKET_LABEL[row.frequency_bucket]}`}
                          value={row.exercises_max}
                          onChange={(value) => updateRange(index, 'exercises_max', value)}
                          allowDecimal={false}
                        />
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <SourceBadge
                        source={saved?.exercises_source ?? null}
                        evidence={saved?.exercises_evidence ?? null}
                      />
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        <Text size="xs" c={BRAND.textMuted}>
          Deixe uma frequência toda em branco para removê-la: grupos nessa frequência ficam sem avaliação por treino
          (a meta semanal continua). Número alterado passa a ter origem “Admin”; número mantido conserva a origem.
        </Text>

        <MultiSelect
          label="Grupos musculares associados"
          description="Só grupos que não são fatia (a fatia usa o macro-grupo do grupo pai). Um grupo marcado “(em …)” está em outro macro-grupo e será movido para este ao salvar."
          data={groupOptions}
          value={groupIds}
          onChange={setGroupIds}
          disabled={readOnly}
          searchable
          clearable
          placeholder={groupIds.length ? undefined : 'Nenhum grupo associado'}
        />

        {!isDefaultMode && (missing.buckets > 0 || missing.weekly) && (
          <Group gap="xs" align="center" wrap="wrap" data-copy-from-default>
            <Text size="xs" c={BRAND.textMuted}>
              Neste modo faltam{' '}
              {[
                missing.buckets > 0 ? `${missing.buckets} ${missing.buckets === 1 ? 'faixa' : 'faixas'}` : null,
                missing.weekly ? 'a meta semanal' : null,
              ]
                .filter(Boolean)
                .join(' e ')}
              .
            </Text>
            <AdminButton
              isAdmin={isAdmin}
              disabledReason={dirty ? 'Salve ou desfaça as alterações antes de copiar.' : null}
              variant="light"
              color="brand"
              size="xs"
              leftSection={<IconCopy size={14} />}
              loading={copyMutation.isPending}
              onClick={() => copyMutation.mutate()}
            >
              Copiar de {countingModeOption(DEFAULT_COUNTING_MODE).label}
            </AdminButton>
          </Group>
        )}

        {saveMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(saveMutation.error, 'Não foi possível salvar o macro-grupo.')}
          </Alert>
        )}
        {copyMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(copyMutation.error, 'Não foi possível copiar as faixas.')}
          </Alert>
        )}

        <Group justify="space-between">
          <AdminButton
            isAdmin={isAdmin}
            disabledReason={hasGroups ? 'Mova ou desassocie os grupos antes de excluir o macro-grupo.' : null}
            color="red"
            variant="subtle"
            size="xs"
            leftSection={<IconTrash size={14} />}
            onClick={() => onDelete(item)}
          >
            Excluir
          </AdminButton>
          <AdminButton
            isAdmin={isAdmin}
            disabledReason={dirty ? null : 'Nenhuma alteração para salvar.'}
            color="brand"
            size="xs"
            leftSection={<IconDeviceFloppy size={14} />}
            loading={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            Salvar
          </AdminButton>
        </Group>
      </Stack>
    </Paper>
  );
}

/**
 * Aba "Macro-grupos de volume" (Fase 6, PR 3): CRUD de
 * `/muscle-volume-classes` (`api/app/muscle_volume_classes.py`).
 *
 * Um card por macro-grupo (ordem de `sort_order`) com meta semanal, faixas
 * por treino para 1x / 2x / 3x+ (com a ORIGEM de cada número) e os grupos
 * associados. Abaixo, "Sem macro-grupo": grupos não-fatia que a metodologia
 * não avalia. Leitura para qualquer autenticado; escrita só admin (o 403 do
 * backend é a garantia real).
 */
export function VolumeClassesPanel({
  isAdmin,
  initialCountingMode = DEFAULT_COUNTING_MODE,
}: {
  isAdmin: boolean;
  /** Modo de contagem inicial (testes de render); a tela começa no default. */
  initialCountingMode?: string;
}) {
  const qc = useQueryClient();
  // Modo de contagem (073, Lote 3): as faixas e a meta semanal exibidas e
  // editadas são as DESTE modo; nome, descrição e grupos valem para todos.
  const [countingMode, setCountingMode] = useState<string>(normalizeCountingMode(initialCountingMode));
  const counting = countingModeOption(countingMode);

  const classesQuery = useQuery({
    queryKey: volumeClassesQueryKey(countingMode),
    queryFn: () => apiFetch<VolumeClassListResponse>(`/muscle-volume-classes${countingModeQuery(countingMode)}`),
  });
  const groupsQuery = useQuery({
    queryKey: MUSCLE_GROUPS_CRUD_QUERY_KEY,
    queryFn: () => apiFetch<MuscleGroupListResponse>('/muscle-groups'),
  });
  const classes = useMemo(() => sortVolumeClasses(classesQuery.data?.items ?? []), [classesQuery.data]);
  const groups = useMemo(() => groupsQuery.data?.items ?? [], [groupsQuery.data]);
  const unassigned = useMemo(() => unassignedGroups(groups, classes), [groups, classes]);

  const [createOpen, { open: openCreate, close: closeCreateRaw }] = useDisclosure(false);
  const [newName, setNewName] = useState('');
  const [newDescription, setNewDescription] = useState('');
  const [deleting, setDeleting] = useState<VolumeClassItem | null>(null);

  const createMutation = useMutation({
    mutationFn: () =>
      apiFetch<VolumeClassResponse>('/muscle-volume-classes', {
        method: 'POST',
        body: JSON.stringify({
          name: newName.trim(),
          description: newDescription.trim() || null,
          // Novo macro-grupo entra no fim da lista.
          sort_order: classes.reduce((max, item) => Math.max(max, item.sort_order), 0) + 10,
        } satisfies VolumeClassUpsertPayload),
      }),
    onSuccess: async () => {
      closeCreate();
      await invalidateAfterVolumeClassWrite(qc);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (item: VolumeClassItem) =>
      apiFetch<{ status: 'ok'; deleted_id: number }>(`/muscle-volume-classes/${item.id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeDelete();
      await invalidateAfterVolumeClassWrite(qc);
    },
  });

  function startCreate() {
    setNewName('');
    setNewDescription('');
    createMutation.reset();
    openCreate();
  }

  function closeCreate() {
    closeCreateRaw();
    createMutation.reset();
  }

  function closeDelete() {
    setDeleting(null);
    deleteMutation.reset();
  }

  const loading = classesQuery.isLoading || groupsQuery.isLoading;
  const loadError = classesQuery.error ?? groupsQuery.error;

  return (
    <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Group justify="space-between" align="flex-end" mb="md" wrap="wrap">
        <div>
          <Text fw={600} c={BRAND.textPrimary}>
            Macro-grupos de volume
          </Text>
          <Text size="sm" c={BRAND.textMuted}>
            Grupos musculares que dividem as mesmas metas da metodologia de volume: séries (e exercícios) por treino
            conforme a frequência semanal do grupo, e a meta de séries por semana. É daqui que o diagnóstico de volume
            e a geração de Modelos por IA leem as faixas.
          </Text>
        </div>
        <AdminButton isAdmin={isAdmin} color="brand" leftSection={<IconPlus size={16} />} onClick={startCreate}>
          Novo macro-grupo
        </AdminButton>
      </Group>

      <Stack gap={4} mb="md" data-counting-mode-switch>
        <Text size="sm" fw={500} c={BRAND.textPrimary}>
          Contagem do volume
        </Text>
        <SegmentedControl
          data={COUNTING_MODE_SELECT_DATA as { value: string; label: string }[]}
          value={countingMode}
          onChange={(value) => setCountingMode(normalizeCountingMode(value))}
          size="xs"
          w="fit-content"
        />
        <Text size="xs" c={BRAND.textMuted}>
          {counting.description} Cada modo tem as próprias faixas e metas; o Modelo escolhe o modo no editor.
        </Text>
      </Stack>

      {loadError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
          {errorMessage(loadError, 'Não foi possível carregar os macro-grupos.')}
        </Alert>
      )}

      {loading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando macro-grupos...</Text>
        </Group>
      )}

      {!loading && !loadError && (
        <Stack gap="md">
          {classes.map((item) => (
            <VolumeClassCard
              // Remonta o card quando o servidor muda o item (salvar aqui ou
              // um grupo movido por outro card): o rascunho volta ao gravado.
              key={`${countingMode}:${item.id}:${item.updated_at ?? ''}:${item.muscle_group_ids.join(',')}:${JSON.stringify(
                item.ranges,
              )}:${String(item.weekly_sets_min)}:${String(item.weekly_sets_max)}`}
              item={item}
              groups={groups}
              classes={classes}
              isAdmin={isAdmin}
              onDelete={setDeleting}
              countingMode={countingMode}
            />
          ))}
          {classes.length === 0 && (
            <Text c={BRAND.textMuted} ta="center">
              Nenhum macro-grupo cadastrado — nenhum grupo é avaliado pela metodologia.
            </Text>
          )}

          <Paper withBorder radius="md" p="md" data-unassigned-groups style={{ borderColor: BRAND.border }}>
            <Text fw={600} c={BRAND.textPrimary}>
              {UNCLASSIFIED_VOLUME_CLASS_LABEL} ({unassigned.length})
            </Text>
            <Text size="xs" c={BRAND.textMuted} mb="xs">
              Grupos que não são fatia e não estão em nenhum macro-grupo: a metodologia não os avalia (aparecem como
              “Grupo sem macro-grupo”). Associe-os em um dos cards acima.
            </Text>
            {unassigned.length === 0 ? (
              <Text size="sm" c={BRAND.textMuted}>
                Todos os grupos estão em algum macro-grupo.
              </Text>
            ) : (
              <Group gap={6}>
                {unassigned.map((group) => (
                  <Badge key={group.id} variant="outline" color="gray" style={{ textTransform: 'none' }}>
                    {group.name}
                  </Badge>
                ))}
              </Group>
            )}
          </Paper>
        </Stack>
      )}

      <Modal opened={createOpen} onClose={closeCreate} title="Novo macro-grupo" centered>
        <Stack gap="sm">
          <TextInput
            label="Nome"
            placeholder="Ex.: Posteriores"
            value={newName}
            onChange={(event) => setNewName(event.currentTarget.value)}
            maxLength={80}
            required
          />
          <Textarea
            label="Descrição"
            value={newDescription}
            onChange={(event) => setNewDescription(event.currentTarget.value)}
            autosize
            minRows={2}
            maxLength={500}
          />
          <Text size="xs" c={BRAND.textMuted}>
            O macro-grupo nasce sem faixas nem meta semanal: preencha-as no card depois de criar.
          </Text>
          {createMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {errorMessage(createMutation.error, 'Não foi possível criar o macro-grupo.')}
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
              Criar
            </Button>
          </Group>
        </Stack>
      </Modal>

      <ConfirmDeleteModal
        opened={deleting != null}
        title="Excluir macro-grupo"
        message={
          <>
            Excluir o macro-grupo <b>{deleting?.name}</b> e as faixas dele? Esta ação não pode ser desfeita.
          </>
        }
        onClose={closeDelete}
        onConfirm={() => deleting && deleteMutation.mutate(deleting)}
        loading={deleteMutation.isPending}
        error={deleteMutation.error}
        fallbackErrorMessage="Não foi possível excluir o macro-grupo."
      />
    </Paper>
  );
}
