'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Checkbox,
  Divider,
  Group,
  NumberInput,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconInfoCircle, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { DashboardCardSkeleton, ErrorState } from '@/components/dashboard/states';
import { TablePager } from '@/components/TablePager';
import { ApiError } from '@/lib/api';
import {
  createBodyMeasurement,
  dashboardKeys,
  deleteBodyMeasurement,
  fetchBodyMeasurements,
} from '@/lib/dashboard-api';
import { MEASUREMENT_SITE_OPTIONS, type MeasurementSite } from '@/lib/body-measurement-sites';
import {
  emptyMeasurementForm,
  MEASUREMENT_DATE_MIN,
  measurementDateMax,
  toMeasurementInput,
  validateMeasurementForm,
  type MeasurementFormErrors,
  type MeasurementFormState,
} from '@/lib/dashboard-body';
import { EM_DASH, fmtDate, fmtDecimal } from '@/lib/dashboard-format';
import { usePagination } from '@/lib/pagination';
import { localTodayIso } from '@/lib/report-markdown';
import { BRAND } from '@/theme';

/** Quantas medidas a lista busca de uma vez; a tabela pagina no cliente. */
const LIST_LIMIT = 200;

/**
 * Registro de medida por fita (cintura, quadril, braços...) em
 * `POST /body-measurements`. Veio de `/dashboard/corpo`, que agora só exibe
 * as séries e aponta para cá. Gravar/apagar invalida `dashboardKeys.bodyAll()`,
 * então o painel do corpo se atualiza sozinho.
 *
 * Disponibilidade da tabela: a própria lista declara a limitação (antes da
 * migração 047 `GET /body-measurements` devolve `limitations` preenchido).
 */
export function MedidasFitaSection() {
  const qc = useQueryClient();
  const [form, setForm] = useState<MeasurementFormState>(() => emptyMeasurementForm(localTodayIso()));
  const [errors, setErrors] = useState<MeasurementFormErrors>({});

  const lista = useQuery({
    queryKey: dashboardKeys.bodyMeasurements(null, LIST_LIMIT),
    queryFn: () => fetchBodyMeasurements(null, LIST_LIMIT),
  });

  const itens = lista.data?.items ?? [];
  const pagination = usePagination(itens);
  // Enquanto carrega (ou se a lista falhar) o formulário fica habilitado: o
  // POST devolve o erro real. Só a limitação declarada desabilita.
  const tabelaDisponivel = !lista.data || lista.data.limitations.length === 0;

  const invalidar = () => qc.invalidateQueries({ queryKey: dashboardKeys.bodyAll() });

  const salvar = useMutation({
    mutationFn: () => createBodyMeasurement(toMeasurementInput(form)),
    onSuccess: (resposta) => {
      notifications.show({
        color: 'green',
        message:
          resposta.status === 'updated'
            ? `${resposta.item.site_label} de ${fmtDate(resposta.item.local_date)} atualizada.`
            : `${resposta.item.site_label} de ${fmtDate(resposta.item.local_date)} registrada.`,
      });
      setForm((prev) => ({ ...prev, value_cm: '', notes: '' }));
      invalidar();
    },
    onError: (error) => {
      notifications.show({
        color: 'red',
        message: error instanceof ApiError ? error.message : 'Não foi possível registrar a medida.',
      });
    },
  });

  const apagar = useMutation({
    mutationFn: (id: number) => deleteBodyMeasurement(id),
    onSuccess: () => {
      notifications.show({ color: 'green', message: 'Medida apagada.' });
      invalidar();
    },
    onError: (error) => {
      notifications.show({
        color: 'red',
        message: error instanceof ApiError ? error.message : 'Não foi possível apagar a medida.',
      });
    },
  });

  function submit() {
    const found = validateMeasurementForm(form);
    setErrors(found);
    if (Object.keys(found).length === 0) salvar.mutate();
  }

  return (
    <Stack gap="md">
      <div>
        <Title order={2} c={BRAND.textPrimary}>
          Medidas por fita
        </Title>
        <Text c={BRAND.textMuted} size="sm">
          Data, local, valor em centímetros e se foi medida no protocolo (mesmo horário, mesmo ponto, em jejum).
          Repetir o mesmo local no mesmo dia corrige a medida anterior. As séries aparecem em Dashboard › Corpo.
        </Text>
      </div>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Stack gap="sm">
          {!tabelaDisponivel && (
            <Alert color="yellow" variant="light" icon={<IconInfoCircle size={18} />}>
              O registro de medidas ainda não está disponível neste ambiente (migração 047 pendente).
            </Alert>
          )}
          <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }} spacing="sm">
            <TextInput
              label="Data"
              type="date"
              min={MEASUREMENT_DATE_MIN}
              max={measurementDateMax(localTodayIso())}
              value={form.local_date}
              error={errors.local_date}
              onChange={(e) => {
                const value = e.currentTarget.value;
                setForm((prev) => ({ ...prev, local_date: value }));
              }}
              disabled={!tabelaDisponivel}
            />
            <Select
              label="Local"
              data={MEASUREMENT_SITE_OPTIONS}
              value={form.site}
              error={errors.site}
              allowDeselect={false}
              onChange={(value) => value && setForm((prev) => ({ ...prev, site: value as MeasurementSite }))}
              disabled={!tabelaDisponivel}
            />
            <NumberInput
              label="Valor (cm)"
              decimalScale={1}
              step={0.5}
              min={10}
              max={300}
              value={form.value_cm}
              error={errors.value_cm}
              onChange={(value) => setForm((prev) => ({ ...prev, value_cm: value === '' ? '' : Number(value) }))}
              disabled={!tabelaDisponivel}
            />
            <TextInput
              label="Nota (opcional)"
              value={form.notes}
              error={errors.notes}
              maxLength={255}
              onChange={(e) => {
                const value = e.currentTarget.value;
                setForm((prev) => ({ ...prev, notes: value }));
              }}
              disabled={!tabelaDisponivel}
            />
          </SimpleGrid>
          <Group justify="space-between" wrap="wrap">
            <Checkbox
              label="Medida no protocolo"
              description="Fora do protocolo a medida entra na série com confiança baixa e não alimenta o sinal de recomposição."
              checked={form.protocol_ok}
              onChange={(e) => {
                const checked = e.currentTarget.checked;
                setForm((prev) => ({ ...prev, protocol_ok: checked }));
              }}
              disabled={!tabelaDisponivel}
            />
            <Button
              leftSection={<IconPlus size={16} />}
              color="brand"
              onClick={submit}
              loading={salvar.isPending}
              disabled={!tabelaDisponivel}
            >
              Registrar
            </Button>
          </Group>

          {tabelaDisponivel && (
            <>
              <Divider label="Últimas medidas" labelPosition="left" />
              {lista.isLoading && <DashboardCardSkeleton rows={3} />}
              {lista.isError && <ErrorState title="Não foi possível listar as medidas." error={lista.error} />}
              {lista.data && itens.length === 0 && (
                <Text size="sm" c={BRAND.textMuted}>
                  Nenhuma medida registrada ainda.
                </Text>
              )}
              {itens.length > 0 && (
                <>
                  <Table.ScrollContainer minWidth={480}>
                    <Table striped highlightOnHover withTableBorder={false} verticalSpacing="xs">
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th>Data</Table.Th>
                          <Table.Th>Local</Table.Th>
                          <Table.Th>cm</Table.Th>
                          <Table.Th>Protocolo</Table.Th>
                          <Table.Th>Nota</Table.Th>
                          <Table.Th />
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {pagination.pageItems.map((item) => (
                          <Table.Tr key={item.id}>
                            <Table.Td>{fmtDate(item.local_date)}</Table.Td>
                            <Table.Td>{item.site_label}</Table.Td>
                            <Table.Td>{fmtDecimal(item.value_cm, 1)}</Table.Td>
                            <Table.Td>
                              <Badge size="xs" color={item.protocol_ok ? 'green' : 'gray'} variant="light">
                                {item.protocol_ok ? 'Sim' : 'Fora'}
                              </Badge>
                            </Table.Td>
                            <Table.Td>{item.notes ?? EM_DASH}</Table.Td>
                            <Table.Td>
                              <ActionIcon
                                variant="subtle"
                                color="red"
                                aria-label="Apagar medida"
                                onClick={() => apagar.mutate(item.id)}
                                loading={apagar.isPending && apagar.variables === item.id}
                              >
                                <IconTrash size={16} />
                              </ActionIcon>
                            </Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  </Table.ScrollContainer>
                  <TablePager
                    page={pagination.page}
                    totalPages={pagination.totalPages}
                    onChange={pagination.setPage}
                    visible={pagination.showPager}
                  />
                </>
              )}
            </>
          )}
        </Stack>
      </Paper>
    </Stack>
  );
}
