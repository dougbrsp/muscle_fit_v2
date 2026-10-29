'use client';

import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  Paper,
  Progress,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconCheck, IconDeviceFloppy, IconPencil, IconSearch, IconShieldCheck } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import type { AdvancedTechniqueCatalogItem } from '@/lib/advanced-techniques';
import { apiFetch, ApiError } from '@/lib/api';
import type { EquipmentType } from '@/lib/exercise-catalog-admin-types';
import type { Equipment } from '@/lib/gyms-types';
import {
  DEFAULT_MAX_SAFE_RPE,
  LOAD_INCREMENT_HELP,
  MAX_SAFE_RPE_OPTIONS,
  MAX_SAFE_RPE_UNKNOWN_HELP,
  SAFETY_NOTES_MAX_LENGTH,
  buildBatchSafetyPayload,
  buildEquipmentSafetyPayload,
  describeEquipmentMaxSafeRpe,
  equipmentOfType,
  equipmentSafetyPath,
  equipmentSafetyQueryKey,
  equipmentSafetyToForm,
  formatLoadIncrement,
  isBlockedByFailure,
  parseLoadIncrement,
  runBatch,
  selectValueToRpe,
  type BatchOutcome,
  type BatchSafetyForm,
  type EquipmentSafety,
  type EquipmentSafetyForm,
  type EquipmentSafetyPayload,
} from '@/lib/training-safety';
import { useAdvancedTechniques } from '@/lib/use-advanced-techniques';
import { BRAND } from '@/theme';

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.status === 403) return 'Ação restrita a administradores.';
  return error instanceof ApiError ? error.message : fallback;
}

function putEquipmentSafety(equipmentId: number, payload: EquipmentSafetyPayload) {
  return apiFetch<EquipmentSafety>(equipmentSafetyPath(equipmentId), { method: 'PUT', body: JSON.stringify(payload) });
}

function TechniqueCheckboxes({
  techniques,
  checked,
  onToggle,
  effectiveMaxSafeRpe,
  disabled,
}: {
  techniques: readonly AdvancedTechniqueCatalogItem[];
  checked: ReadonlySet<string>;
  onToggle: (code: string, value: boolean) => void;
  effectiveMaxSafeRpe: number;
  disabled?: boolean;
}) {
  return (
    <Stack gap={6}>
      {techniques.map((technique) => {
        const blocked = checked.has(technique.code) && isBlockedByFailure(technique, effectiveMaxSafeRpe);
        return (
          <Checkbox
            key={technique.code}
            color="brand"
            label={technique.name}
            description={
              blocked
                ? `Exige falha: fica bloqueada enquanto o RPE máximo seguro for menor que 10 (hoje ${effectiveMaxSafeRpe}).`
                : undefined
            }
            checked={checked.has(technique.code)}
            disabled={disabled}
            onChange={(event) => onToggle(technique.code, event.currentTarget.checked)}
          />
        );
      })}
    </Stack>
  );
}

function toggled(set: ReadonlySet<string>, code: string, value: boolean): Set<string> {
  const next = new Set(set);
  if (value) next.add(code);
  else next.delete(code);
  return next;
}

// ---------------------------------------------------------------------------
// Edição de um equipamento
// ---------------------------------------------------------------------------

function EquipmentSafetyModal({
  equipment,
  techniques,
  catalogIsFallback,
  onClose,
}: {
  equipment: Equipment;
  techniques: readonly AdvancedTechniqueCatalogItem[];
  catalogIsFallback: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const safetyQuery = useQuery({
    queryKey: equipmentSafetyQueryKey(equipment.id),
    queryFn: () => apiFetch<EquipmentSafety>(equipmentSafetyPath(equipment.id)),
  });

  const [form, setForm] = useState<EquipmentSafetyForm | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  useEffect(() => {
    if (safetyQuery.data) setForm(equipmentSafetyToForm(safetyQuery.data));
  }, [safetyQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: EquipmentSafetyPayload) => putEquipmentSafety(equipment.id, payload),
    onSuccess: async (saved) => {
      qc.setQueryData(equipmentSafetyQueryKey(equipment.id), saved);
      await qc.invalidateQueries({ queryKey: ['equipment-catalog'] });
      onClose();
    },
  });

  function save() {
    if (!form) return;
    const result = buildEquipmentSafetyPayload(form, techniques);
    setFormError(result.error);
    if (result.payload) saveMutation.mutate(result.payload);
  }

  const effectiveRpe = form ? (selectValueToRpe(form.maxSafeRpe) ?? DEFAULT_MAX_SAFE_RPE) : DEFAULT_MAX_SAFE_RPE;
  const incrementError = form ? parseLoadIncrement(form.loadIncrement).error : null;

  return (
    <Modal opened onClose={onClose} title={`Segurança — ${equipment.name}`} size="lg" centered>
      {safetyQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando...</Text>
        </Group>
      )}
      {safetyQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(safetyQuery.error, 'Não foi possível carregar a segurança do equipamento.')}
        </Alert>
      )}
      {form && (
        <Stack gap="md">
          <Select
            label="RPE máximo seguro"
            description={`10 = falha concêntrica permitida. ${MAX_SAFE_RPE_UNKNOWN_HELP}`}
            placeholder="Desconhecido (tratado como 9)"
            data={MAX_SAFE_RPE_OPTIONS}
            value={form.maxSafeRpe}
            onChange={(value) => setForm({ ...form, maxSafeRpe: value })}
            clearable
          />
          <TextInput
            label="Incremento mínimo de carga TOTAL (kg)"
            description={LOAD_INCREMENT_HELP}
            placeholder="ex.: 2,5"
            inputMode="decimal"
            value={form.loadIncrement}
            onChange={(event) => setForm({ ...form, loadIncrement: event.currentTarget.value })}
            error={incrementError}
          />
          <Textarea
            label="Notas de segurança"
            placeholder="ex.: sem trava de segurança; exige spotter acima de RPE 9"
            autosize
            minRows={2}
            maxLength={SAFETY_NOTES_MAX_LENGTH}
            value={form.safetyNotes}
            onChange={(event) => setForm({ ...form, safetyNotes: event.currentTarget.value })}
          />
          <Stack gap={6}>
            <Text size="sm" fw={500} c={BRAND.textPrimary}>
              Técnicas avançadas compatíveis
            </Text>
            <Text size="xs" c={BRAND.textMuted}>
              Técnica desmarcada = incompatível. Ao salvar, todas as técnicas ficam registradas como revisadas.
            </Text>
            {catalogIsFallback && (
              <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
                Catálogo de técnicas indisponível no momento; a lista abaixo é a padrão e o servidor valida os códigos ao salvar.
              </Alert>
            )}
            <TechniqueCheckboxes
              techniques={techniques}
              checked={form.checkedTechniques}
              effectiveMaxSafeRpe={effectiveRpe}
              onToggle={(code, value) => setForm({ ...form, checkedTechniques: toggled(form.checkedTechniques, code, value) })}
            />
          </Stack>

          {(formError || saveMutation.isError) && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {formError ?? errorMessage(saveMutation.error, 'Não foi possível salvar a segurança do equipamento.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={onClose}>
              Cancelar
            </Button>
            <Button color="brand" leftSection={<IconDeviceFloppy size={16} />} onClick={save} loading={saveMutation.isPending}>
              Salvar
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Edição em lote por tipo de equipamento
// ---------------------------------------------------------------------------

const EMPTY_BATCH_FORM: BatchSafetyForm = {
  applyMaxSafeRpe: false,
  maxSafeRpe: null,
  applyLoadIncrement: false,
  loadIncrement: '',
  applyTechniques: false,
  checkedTechniques: new Set(),
};

function BatchByTypePanel({
  equipment,
  equipmentTypes,
  techniques,
}: {
  equipment: readonly Equipment[];
  equipmentTypes: readonly EquipmentType[];
  techniques: readonly AdvancedTechniqueCatalogItem[];
}) {
  const qc = useQueryClient();
  const [typeId, setTypeId] = useState<string | null>(null);
  const [form, setForm] = useState<BatchSafetyForm>(EMPTY_BATCH_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [outcomes, setOutcomes] = useState<BatchOutcome[] | null>(null);

  const targets = useMemo(() => equipmentOfType(equipment, typeId ? Number(typeId) : null), [equipment, typeId]);
  const nameById = useMemo(() => new Map(equipment.map((item) => [item.id, item.name])), [equipment]);
  const typeOptions = useMemo(
    () =>
      equipmentTypes
        .map((type) => ({ value: String(type.id), label: `${type.name} (${equipmentOfType(equipment, type.id).length})` }))
        .sort((a, b) => a.label.localeCompare(b.label, 'pt-BR')),
    [equipmentTypes, equipment],
  );

  const batchMutation = useMutation({
    mutationFn: async (payload: EquipmentSafetyPayload) =>
      runBatch(
        targets.map((item) => item.id),
        (id) => putEquipmentSafety(id, payload),
        (done, total) => setProgress({ done, total }),
      ),
    onSuccess: async (result) => {
      setOutcomes(result);
      setProgress(null);
      await qc.invalidateQueries({ queryKey: ['equipment-catalog'] });
      await qc.invalidateQueries({ queryKey: ['equipment-safety'] });
    },
  });

  function apply() {
    const result = buildBatchSafetyPayload(form, techniques);
    setFormError(result.error);
    setOutcomes(null);
    if (result.payload) batchMutation.mutate(result.payload);
  }

  const failed = outcomes?.filter((outcome) => !outcome.ok) ?? [];
  const effectiveRpe = selectValueToRpe(form.maxSafeRpe) ?? DEFAULT_MAX_SAFE_RPE;

  return (
    <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Stack gap="md">
        <div>
          <Title order={4} c={BRAND.textPrimary}>
            Edição em lote por tipo de equipamento
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            Aplica os campos escolhidos a todos os equipamentos ativos do tipo. Campos não marcados e notas não são alterados.
          </Text>
        </div>
        <Select
          label="Tipo de equipamento"
          placeholder="Selecionar"
          data={typeOptions}
          value={typeId}
          onChange={(value) => {
            setTypeId(value);
            setOutcomes(null);
          }}
          searchable
          w={320}
        />

        <Stack gap="xs">
          <Switch
            color="brand"
            label="Aplicar RPE máximo seguro"
            checked={form.applyMaxSafeRpe}
            onChange={(event) => setForm({ ...form, applyMaxSafeRpe: event.currentTarget.checked })}
          />
          {form.applyMaxSafeRpe && (
            <Select
              description={`10 = falha concêntrica permitida. ${MAX_SAFE_RPE_UNKNOWN_HELP}`}
              placeholder="Desconhecido (tratado como 9)"
              data={MAX_SAFE_RPE_OPTIONS}
              value={form.maxSafeRpe}
              onChange={(value) => setForm({ ...form, maxSafeRpe: value })}
              clearable
              w={320}
            />
          )}
          <Switch
            color="brand"
            label="Aplicar incremento mínimo de carga TOTAL"
            checked={form.applyLoadIncrement}
            onChange={(event) => setForm({ ...form, applyLoadIncrement: event.currentTarget.checked })}
          />
          {form.applyLoadIncrement && (
            <TextInput
              description={LOAD_INCREMENT_HELP}
              placeholder="ex.: 2,5 (vazio = limpar)"
              inputMode="decimal"
              value={form.loadIncrement}
              onChange={(event) => setForm({ ...form, loadIncrement: event.currentTarget.value })}
              error={parseLoadIncrement(form.loadIncrement).error}
              w={320}
            />
          )}
          <Switch
            color="brand"
            label="Aplicar técnicas compatíveis"
            checked={form.applyTechniques}
            onChange={(event) => setForm({ ...form, applyTechniques: event.currentTarget.checked })}
          />
          {form.applyTechniques && (
            <TechniqueCheckboxes
              techniques={techniques}
              checked={form.checkedTechniques}
              effectiveMaxSafeRpe={form.applyMaxSafeRpe ? effectiveRpe : 10}
              onToggle={(code, value) => setForm({ ...form, checkedTechniques: toggled(form.checkedTechniques, code, value) })}
            />
          )}
        </Stack>

        {formError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {formError}
          </Alert>
        )}
        {batchMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(batchMutation.error, 'Não foi possível aplicar em lote.')}
          </Alert>
        )}
        {progress && <Progress value={(progress.done / Math.max(progress.total, 1)) * 100} color="brand" />}
        {outcomes && failed.length === 0 && (
          <Alert color="green" icon={<IconCheck size={16} />}>
            {outcomes.length} equipamento(s) atualizado(s).
          </Alert>
        )}
        {failed.length > 0 && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {outcomes!.length - failed.length} atualizado(s); {failed.length} falharam:{' '}
            {failed.map((outcome) => `${nameById.get(outcome.id) ?? outcome.id} (${outcome.error})`).join('; ')}
          </Alert>
        )}

        <Group>
          <Button
            color="brand"
            leftSection={<IconDeviceFloppy size={16} />}
            onClick={apply}
            disabled={targets.length === 0}
            loading={batchMutation.isPending}
          >
            Aplicar a {targets.length} equipamento(s)
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// Aba
// ---------------------------------------------------------------------------

export function EquipmentSafetyTab({
  equipment,
  equipmentTypes,
  isAdmin,
  loading,
}: {
  equipment: readonly Equipment[];
  equipmentTypes: readonly EquipmentType[];
  isAdmin: boolean;
  loading: boolean;
}) {
  const { items: catalog, isFallback } = useAdvancedTechniques();
  const techniques = useMemo(() => catalog.filter((item) => item.is_active), [catalog]);
  const [search, setSearch] = useState('');
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [editing, setEditing] = useState<Equipment | null>(null);

  const typeNameById = useMemo(() => new Map(equipmentTypes.map((type) => [type.id, type.name])), [equipmentTypes]);
  const missingCount = equipment.filter((item) => item.max_safe_rpe == null).length;
  const term = search.trim().toLocaleLowerCase('pt-BR');
  const visible = equipment.filter(
    (item) => (!term || item.name.toLocaleLowerCase('pt-BR').includes(term)) && (!onlyMissing || item.max_safe_rpe == null),
  );

  if (!isAdmin) {
    return (
      <Alert color="yellow" icon={<IconShieldCheck size={16} />}>
        O cadastro de segurança dos equipamentos é restrito a administradores.
      </Alert>
    );
  }

  return (
    <Stack gap="lg">
      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Stack gap="md">
          <div>
            <Title order={4} c={BRAND.textPrimary}>
              Segurança
            </Title>
            <Text size="sm" c={BRAND.textMuted}>
              RPE máximo seguro, incremento mínimo de carga e técnicas avançadas compatíveis por equipamento. Exceções por exercício ficam em
              Domínio de exercícios → equipamentos vinculados.
            </Text>
          </div>
          <Group align="flex-end" wrap="wrap">
            <TextInput
              label="Buscar equipamento"
              placeholder="Filtrar por nome..."
              leftSection={<IconSearch size={16} />}
              value={search}
              onChange={(event) => setSearch(event.currentTarget.value)}
              w={320}
            />
            <Switch
              color="brand"
              label={`Só sem RPE máximo cadastrado (${missingCount})`}
              checked={onlyMissing}
              onChange={(event) => setOnlyMissing(event.currentTarget.checked)}
            />
          </Group>

          {loading ? (
            <Group>
              <Loader size="sm" color="brand" />
              <Text c={BRAND.textMuted}>Carregando catálogo...</Text>
            </Group>
          ) : (
            <Table.ScrollContainer minWidth={520}>
              <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Equipamento</Table.Th>
                    <Table.Th>Tipo</Table.Th>
                    <Table.Th>RPE máximo seguro</Table.Th>
                    <Table.Th>Incremento total (kg)</Table.Th>
                    <Table.Th />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {visible.map((item) => (
                    <Table.Tr key={item.id}>
                      <Table.Td c={BRAND.textPrimary}>{item.name}</Table.Td>
                      <Table.Td c={BRAND.textMuted}>{item.equipment_type_id ? typeNameById.get(item.equipment_type_id) || '-' : '-'}</Table.Td>
                      <Table.Td>
                        {item.max_safe_rpe == null ? (
                          <Badge color="yellow" variant="light">
                            {describeEquipmentMaxSafeRpe(null)}
                          </Badge>
                        ) : (
                          <Badge color={item.max_safe_rpe === 10 ? 'green' : 'blue'} variant="light">
                            {item.max_safe_rpe === 10 ? '10 · falha permitida' : item.max_safe_rpe}
                          </Badge>
                        )}
                      </Table.Td>
                      <Table.Td c={BRAND.textMuted}>{formatLoadIncrement(item.load_increment_kg) || '-'}</Table.Td>
                      <Table.Td>
                        <Group justify="flex-end">
                          <Button size="xs" variant="light" color="brand" leftSection={<IconPencil size={14} />} onClick={() => setEditing(item)}>
                            Editar
                          </Button>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                  {visible.length === 0 && (
                    <Table.Tr>
                      <Table.Td colSpan={5}>
                        <Text size="sm" c={BRAND.textMuted}>
                          Nenhum equipamento encontrado.
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
        </Stack>
      </Paper>

      {equipmentTypes.length > 0 && <BatchByTypePanel equipment={equipment} equipmentTypes={equipmentTypes} techniques={techniques} />}

      {editing && (
        <EquipmentSafetyModal
          key={editing.id}
          equipment={editing}
          techniques={techniques}
          catalogIsFallback={isFallback}
          onClose={() => setEditing(null)}
        />
      )}
    </Stack>
  );
}
