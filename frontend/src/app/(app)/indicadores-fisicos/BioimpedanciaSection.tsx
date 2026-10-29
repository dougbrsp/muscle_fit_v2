'use client';

import dynamic from 'next/dynamic';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import {
  ActionIcon,
  Alert,
  Button,
  Grid,
  Group,
  Loader,
  Modal,
  NumberInput,
  Paper,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import { IconAlertTriangle, IconEdit, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import {
  ApiListResponse,
  BodyCompositionEntry,
  BodyCompositionFormState,
  emptyBodyCompositionForm,
  formatDateBR,
  formatNumberOrDash,
} from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico.
const MassChart = dynamic(() => import('./BioimpedanciaCharts').then((m) => m.MassChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={260} />,
});
const PercentChart = dynamic(() => import('./BioimpedanciaCharts').then((m) => m.PercentChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={260} />,
});

function BioimpedanciaForm({
  initial,
  onSubmit,
  loading,
}: {
  initial: BodyCompositionFormState;
  onSubmit: (data: BodyCompositionFormState) => void;
  loading: boolean;
}) {
  const [form, setForm] = useState<BodyCompositionFormState>(initial);

  function num(field: keyof BodyCompositionFormState) {
    const value = form[field] as number | string | null | undefined;
    return {
      value: value ?? undefined,
      onChange: (val: number | string) =>
        setForm((prev) => ({ ...prev, [field]: val === '' || val === undefined ? null : Number(val) })),
    };
  }

  return (
    <Stack gap="sm">
      <TextInput
        label="Data"
        required
        type="date"
        value={form.local_date ?? ''}
        onChange={(event) => {
          const dateVal = event.currentTarget.value;
          setForm((prev) => ({ ...prev, local_date: dateVal }));
        }}
      />

      <Group grow>
        <NumberInput label="Peso (kg)" decimalScale={2} step={0.1} {...num('weight_kg')} />
        <NumberInput label="IMC" decimalScale={2} step={0.1} {...num('bmi')} />
      </Group>

      <Group grow>
        <NumberInput label="Gordura corporal (%)" decimalScale={2} step={0.1} {...num('body_fat_percent')} />
        <NumberInput
          label="Gordura subcutânea (%)"
          decimalScale={2}
          step={0.1}
          {...num('subcutaneous_fat_percent')}
        />
      </Group>

      <Group grow>
        <NumberInput label="Gordura visceral (pts)" decimalScale={1} step={0.5} {...num('visceral_fat_rating')} />
        <NumberInput label="Água corporal (%)" decimalScale={2} step={0.1} {...num('body_water_percent')} />
      </Group>

      <Group grow>
        <NumberInput
          label="Musc. esquelética (%)"
          decimalScale={2}
          step={0.1}
          {...num('skeletal_muscle_percent')}
        />
        <NumberInput label="Massa muscular (kg)" decimalScale={2} step={0.1} {...num('muscle_mass_kg')} />
      </Group>

      <Group grow>
        <NumberInput label="Massa livre de gordura (kg)" decimalScale={2} step={0.1} {...num('fat_free_mass_kg')} />
        <NumberInput label="Massa de gordura (kg)" decimalScale={2} step={0.1} {...num('fat_mass_kg')} />
      </Group>

      <Group grow>
        <NumberInput label="Massa óssea (kg)" decimalScale={2} step={0.05} {...num('bone_mass_kg')} />
        <NumberInput label="TMB (kcal)" decimalScale={0} step={1} {...num('bmr_kcal')} />
      </Group>

      <Group grow>
        <NumberInput label="Proteínas (%)" decimalScale={2} step={0.1} {...num('protein_percent')} />
        <NumberInput label="Idade metabólica" decimalScale={0} step={1} {...num('metabolic_age')} />
      </Group>

      <TextInput
        label="Observações"
        value={form.notes ?? ''}
        onChange={(event) => {
          const noteVal = event.currentTarget.value;
          setForm((prev) => ({ ...prev, notes: noteVal || null }));
        }}
      />

      <Button mt="sm" color="brand" loading={loading} onClick={() => onSubmit(form)}>
        Salvar
      </Button>
    </Stack>
  );
}

export function BioimpedanciaSection() {
  const qc = useQueryClient();
  const isMobile = useMediaQuery('(max-width: 48em)');
  const [modalOpened, { open: openModal, close: closeModal }] = useDisclosure(false);
  const [editing, setEditing] = useState<BodyCompositionEntry | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<BodyCompositionEntry | null>(null);
  const [deleteOpened, { open: openDelete, close: closeDelete }] = useDisclosure(false);

  const listQuery = useQuery({
    queryKey: ['body-composition'],
    queryFn: () => apiFetch<ApiListResponse<BodyCompositionEntry>>('/body-composition'),
  });

  const saveMutation = useMutation({
    mutationFn: (data: { id?: number; form: BodyCompositionFormState }) =>
      data.id
        ? apiFetch(`/body-composition/${data.id}`, { method: 'PUT', body: JSON.stringify(data.form) })
        : apiFetch('/body-composition', { method: 'POST', body: JSON.stringify(data.form) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['body-composition'] });
      closeModal();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/body-composition/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['body-composition'] });
      closeDelete();
      setDeleteTarget(null);
    },
  });

  function handleOpenCreate() {
    setEditing(null);
    openModal();
  }

  function handleOpenEdit(entry: BodyCompositionEntry) {
    setEditing(entry);
    openModal();
  }

  function handleOpenDelete(entry: BodyCompositionEntry) {
    setDeleteTarget(entry);
    openDelete();
  }

  function handleSave(form: BodyCompositionFormState) {
    saveMutation.mutate({ id: editing?.id, form });
  }

  const items = listQuery.data?.items || [];

  // Duas séries por gráfico, sempre no mesmo eixo/unidade (regra de eixo
  // único do dataviz, mesma decisão de /evolucao-corporal): peso e massa
  // muscular em kg; gordura e água em %. Gráfico usa ordem cronológica
  // (API retorna mais recente primeiro).
  const massChartData = useMemo(
    () =>
      [...items]
        .reverse()
        .map((item) => ({
          data: formatDateBR(item.local_date),
          peso: item.weight_kg != null ? Number(item.weight_kg) : null,
          massaMuscular: item.muscle_mass_kg != null ? Number(item.muscle_mass_kg) : null,
        })),
    [items],
  );

  const percentChartData = useMemo(
    () =>
      [...items]
        .reverse()
        .map((item) => ({
          data: formatDateBR(item.local_date),
          gordura: item.body_fat_percent != null ? Number(item.body_fat_percent) : null,
          agua: item.body_water_percent != null ? Number(item.body_water_percent) : null,
        })),
    [items],
  );

  return (
    <Stack gap="md">
      <Group justify="space-between" align="center">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Bioimpedância
          </Title>
          <Text c={BRAND.textMuted} size="sm">
            Medições manuais de composição corporal.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} color="brand" onClick={handleOpenCreate}>
          Novo lançamento
        </Button>
      </Group>

      {listQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando bioimpedância...</Text>
        </Group>
      )}

      {listQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar dados">
          {listQuery.error instanceof ApiError ? listQuery.error.message : 'Não foi possível consultar a Fitness API.'}
        </Alert>
      )}

      {!listQuery.isLoading && !listQuery.isError && items.length > 0 && (
        <Grid>
          <Grid.Col span={{ base: 12, md: 6 }}>
            <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Title order={4} mb="sm" c={BRAND.textPrimary}>
                Peso e massa muscular (kg)
              </Title>
              <MassChart massChartData={massChartData} />
            </Paper>
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 6 }}>
            <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Title order={4} mb="sm" c={BRAND.textPrimary}>
                Gordura e água (%)
              </Title>
              <PercentChart percentChartData={percentChartData} />
            </Paper>
          </Grid.Col>
        </Grid>
      )}

      {!listQuery.isLoading && !listQuery.isError && (
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Table.ScrollContainer minWidth={1000}>
            <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Data</Table.Th>
                  <Table.Th>Peso</Table.Th>
                  <Table.Th>IMC</Table.Th>
                  <Table.Th>Gordura %</Table.Th>
                  <Table.Th>G. subcut. %</Table.Th>
                  <Table.Th>Água %</Table.Th>
                  <Table.Th>Musc. esq. %</Table.Th>
                  <Table.Th>Musc. (kg)</Table.Th>
                  <Table.Th>Gord. visc.</Table.Th>
                  <Table.Th>Massa gord. (kg)</Table.Th>
                  <Table.Th>Massa livre gord. (kg)</Table.Th>
                  <Table.Th>Óssea (kg)</Table.Th>
                  <Table.Th>TMB (kcal)</Table.Th>
                  <Table.Th>Proteínas %</Table.Th>
                  <Table.Th>Idade met.</Table.Th>
                  <Table.Th w={80}>Ações</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {items.map((entry) => (
                  <Table.Tr key={entry.id}>
                    <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateBR(entry.local_date)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.weight_kg)} kg</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.bmi)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.body_fat_percent)}%</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.subcutaneous_fat_percent)}%</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.body_water_percent)}%</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.skeletal_muscle_percent)}%</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.muscle_mass_kg)} kg</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.visceral_fat_rating, 0)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.fat_mass_kg)} kg</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.fat_free_mass_kg)} kg</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.bone_mass_kg)} kg</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.bmr_kcal, 0)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.protein_percent)}%</Table.Td>
                    <Table.Td>{entry.metabolic_age ?? '-'}</Table.Td>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap">
                        <Tooltip label="Editar">
                          <ActionIcon variant="subtle" color="brand" onClick={() => handleOpenEdit(entry)}>
                            <IconEdit size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Excluir">
                          <ActionIcon variant="subtle" color="red" onClick={() => handleOpenDelete(entry)}>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
                {items.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={16}>
                      <Text c={BRAND.textMuted} ta="center" py="md">
                        Nenhum lançamento encontrado. Clique em &quot;Novo lançamento&quot; para começar.
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Paper>
      )}

      <Modal
        opened={modalOpened}
        onClose={closeModal}
        title={editing ? 'Editar lançamento' : 'Novo lançamento'}
        size="lg"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <BioimpedanciaForm
          key={editing?.id ?? 'new'}
          initial={
            editing
              ? {
                  local_date: editing.local_date,
                  measured_at: editing.measured_at,
                  weight_kg: editing.weight_kg ?? null,
                  bmi: editing.bmi ?? null,
                  body_fat_percent: editing.body_fat_percent ?? null,
                  fat_mass_kg: editing.fat_mass_kg ?? null,
                  fat_free_mass_kg: editing.fat_free_mass_kg ?? null,
                  muscle_mass_kg: editing.muscle_mass_kg ?? null,
                  skeletal_muscle_percent: editing.skeletal_muscle_percent ?? null,
                  body_water_percent: editing.body_water_percent ?? null,
                  visceral_fat_rating: editing.visceral_fat_rating ?? null,
                  bone_mass_kg: editing.bone_mass_kg ?? null,
                  bmr_kcal: editing.bmr_kcal ?? null,
                  subcutaneous_fat_percent: editing.subcutaneous_fat_percent ?? null,
                  protein_percent: editing.protein_percent ?? null,
                  metabolic_age: editing.metabolic_age ?? null,
                  notes: editing.notes ?? null,
                }
              : emptyBodyCompositionForm()
          }
          onSubmit={handleSave}
          loading={saveMutation.isPending}
        />
        {saveMutation.isError && (
          <Alert mt="sm" color="red" icon={<IconAlertTriangle />}>
            {saveMutation.error instanceof ApiError ? saveMutation.error.message : 'Não foi possível salvar.'}
          </Alert>
        )}
      </Modal>

      <Modal
        opened={deleteOpened}
        onClose={closeDelete}
        title="Confirmar exclusão"
        size="sm"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Excluir o lançamento de{' '}
            <Text component="span" fw={700}>
              {deleteTarget ? formatDateBR(deleteTarget.local_date) : ''}
            </Text>
            ? Esta ação não pode ser desfeita.
          </Text>

          {deleteMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao excluir">
              {deleteMutation.error instanceof ApiError ? deleteMutation.error.message : 'Não foi possível excluir.'}
            </Alert>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={closeDelete} disabled={deleteMutation.isPending}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
            >
              Excluir
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
