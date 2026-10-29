'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Divider,
  Group,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconInfoCircle, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { DashboardCardSkeleton, EmptyState, ErrorState } from '@/components/dashboard/states';
import { ApiError } from '@/lib/api';
import {
  dashboardKeys,
  deleteDailyCheckin,
  fetchDailyCheckins,
  saveDailyCheckin,
} from '@/lib/dashboard-api';
import {
  answeredDays,
  checkinForDate,
  emptyCheckinForm,
  formFromCheckin,
  scaleHint,
  toCheckinInput,
  validateCheckinForm,
  type CheckinFormErrors,
  type CheckinFormState,
} from '@/lib/dashboard-checkin';
import {
  CHECKIN_SCORE_FIELDS,
  CHECKIN_SCORE_LABELS,
  CHECKIN_SCORE_MAX,
  CHECKIN_SCORE_MIN,
  type CheckinScoreField,
} from '@/lib/dashboard-checkin-types';
import { EM_DASH, fmtDate, fmtInt } from '@/lib/dashboard-format';
import { BRAND } from '@/theme';

/**
 * `/dashboard/checkin` — "como você está hoje?" (plano §5, §6 `recovery_signals`,
 * §11; etapa F2.2).
 *
 * Quatro escores de 1 a 5 (disposição, dor muscular global, estresse, humor) e
 * uma nota. Entrada de dado do usuário: grava em `POST /daily-checkins`
 * (api-core), e quem LÊ é a engines, que transforma cada escore num sinal de
 * recuperação contra a sua própria mediana dos 28 dias anteriores.
 *
 * Três decisões que esta tela faz questão de deixar visíveis:
 *
 * - **em branco é em branco.** Nenhum escore é obrigatório e não existe valor
 *   "neutro" pré-selecionado: responder só a dor é um check-in legítimo, e o que
 *   ficou em branco não entra em conta nenhuma (nunca vira 0 nem 3);
 * - **um por dia, corrigível.** O POST é upsert: reabrir a tela no mesmo dia
 *   mostra o que você respondeu e salvar de novo CORRIGE, não duplica;
 * - **relato não é veredito.** Os quatro escores aparecem como sinais em
 *   `/dashboard/recovery`, mas contam UMA vez na concordância — eles vêm do
 *   mesmo formulário, e um dia ruim marca os quatro de uma vez. Quem decide o
 *   nível da leitura é o backend, com os sinais objetivos ao lado.
 *
 * A escala é apresentada como `SegmentedControl` (cinco botões) e não como
 * campo numérico: a pergunta é subjetiva, e cinco opções com as pontas
 * nomeadas ("1 = sem dor, 5 = muita dor") respondem-se num toque, no celular.
 */

const DIAS_NA_LISTA = 14;
const STALE_TIME = 60_000;

/** Hoje na parede do usuário (o backend resolve a janela no fuso dele). */
function hoje(): string {
  return new Date().toISOString().slice(0, 10);
}

const OPCOES_ESCALA = Array.from(
  { length: CHECKIN_SCORE_MAX - CHECKIN_SCORE_MIN + 1 },
  (_, index) => {
    const valor = String(CHECKIN_SCORE_MIN + index);
    return { value: valor, label: valor };
  },
);

const SEM_RESPOSTA = '';

export function DashboardCheckinClient() {
  const qc = useQueryClient();
  const [dia, setDia] = useState<string>(() => hoje());
  const [form, setForm] = useState<CheckinFormState>(() => emptyCheckinForm(hoje()));
  const [errors, setErrors] = useState<CheckinFormErrors>({});

  const consulta = useQuery({
    queryKey: dashboardKeys.checkins(null, DIAS_NA_LISTA),
    queryFn: () => fetchDailyCheckins(null, DIAS_NA_LISTA),
    staleTime: STALE_TIME,
  });

  const itens = consulta.data?.items ?? [];
  const doDia = useMemo(() => checkinForDate(itens, dia), [itens, dia]);

  // Trocar de dia (ou chegar o dado do servidor) pré-carrega o que já foi
  // respondido: o formulário CORRIGE o dia, nunca começa do zero por cima de
  // uma resposta existente.
  useEffect(() => {
    setErrors({});
    setForm(doDia ? formFromCheckin(doDia) : emptyCheckinForm(dia));
  }, [dia, doDia]);

  const tabelaDisponivel = consulta.data?.table_available !== false;

  const salvar = useMutation({
    mutationFn: () => saveDailyCheckin(toCheckinInput(form)),
    onSuccess: (resposta) => {
      notifications.show({
        color: 'green',
        message:
          resposta.status === 'updated'
            ? `Check-in de ${fmtDate(resposta.item.local_date)} corrigido.`
            : `Check-in de ${fmtDate(resposta.item.local_date)} registrado.`,
      });
      qc.invalidateQueries({ queryKey: dashboardKeys.checkinAll() });
      // O check-in alimenta os sinais de recuperação e as regras de insight.
      qc.invalidateQueries({ queryKey: ['dashboard', 'recovery-signals'] });
      qc.invalidateQueries({ queryKey: ['dashboard', 'overview'] });
    },
    onError: (error) => {
      notifications.show({
        color: 'red',
        message: error instanceof ApiError ? error.message : 'Não foi possível salvar o check-in.',
      });
    },
  });

  const apagar = useMutation({
    mutationFn: (id: number) => deleteDailyCheckin(id),
    onSuccess: () => {
      notifications.show({ color: 'green', message: 'Check-in apagado.' });
      qc.invalidateQueries({ queryKey: dashboardKeys.checkinAll() });
    },
    onError: (error) => {
      notifications.show({
        color: 'red',
        message: error instanceof ApiError ? error.message : 'Não foi possível apagar o check-in.',
      });
    },
  });

  function submit() {
    const found = validateCheckinForm(form);
    setErrors(found);
    if (Object.keys(found).length === 0) salvar.mutate();
  }

  return (
    <Stack gap="lg">
      <div>
        <Title order={2} c={BRAND.textPrimary}>
          Check-in diário
        </Title>
        <Text size="sm" c={BRAND.textMuted}>
          Como você está hoje: disposição, dor muscular, estresse e humor, de 1 a 5. Leva dez segundos e é
          o único jeito de o hub saber o que nenhum sensor mede.
        </Text>
      </div>

      {consulta.isLoading && <DashboardCardSkeleton rows={6} />}
      {consulta.isError && (
        <ErrorState title="Não foi possível carregar os check-ins." error={consulta.error} />
      )}

      {consulta.data && (
        <>
          {!tabelaDisponivel && (
            <Alert color="yellow" variant="light" icon={<IconInfoCircle size={18} />}>
              O check-in diário ainda não está disponível neste ambiente (migração 050 pendente).
            </Alert>
          )}

          <Card
            withBorder
            radius="lg"
            p="md"
            style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
          >
            <Stack gap="sm">
              <Group justify="space-between" align="flex-end" wrap="wrap">
                <div>
                  <Title order={3} size="h4" c={BRAND.textPrimary}>
                    {doDia ? 'Corrigir o check-in do dia' : 'Registrar o check-in do dia'}
                  </Title>
                  <Text size="sm" c={BRAND.textMuted}>
                    Nenhum item é obrigatório. O que você deixar em branco fica em branco — não entra em
                    conta nenhuma.
                  </Text>
                </div>
                <TextInput
                  label="Dia"
                  type="date"
                  max={hoje()}
                  value={dia}
                  error={errors.local_date}
                  onChange={(e) => setDia(e.currentTarget.value || hoje())}
                  disabled={!tabelaDisponivel}
                />
              </Group>

              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                {CHECKIN_SCORE_FIELDS.map((field) => (
                  <EscalaCampo
                    key={field}
                    field={field}
                    value={form[field]}
                    error={errors[field]}
                    disabled={!tabelaDisponivel}
                    onChange={(valor) => setForm((prev) => ({ ...prev, [field]: valor }))}
                  />
                ))}
              </SimpleGrid>

              <TextInput
                label="Nota (opcional)"
                placeholder="o que mais vale registrar sobre hoje"
                value={form.notes}
                error={errors.notes}
                maxLength={255}
                onChange={(e) => setForm((prev) => ({ ...prev, notes: e.currentTarget.value }))}
                disabled={!tabelaDisponivel}
              />

              <Group justify="flex-end">
                <Button
                  leftSection={<IconCheck size={16} />}
                  color="brand"
                  onClick={submit}
                  loading={salvar.isPending}
                  disabled={!tabelaDisponivel}
                >
                  {doDia ? 'Salvar correção' : 'Registrar'}
                </Button>
              </Group>
            </Stack>
          </Card>

          <Divider />

          <UltimosDias
            itens={itens}
            onApagar={(id) => apagar.mutate(id)}
            apagandoId={apagar.isPending ? (apagar.variables as number) : null}
            onSelecionar={setDia}
          />

          <Alert color="gray" variant="light" icon={<IconInfoCircle size={18} />}>
            Os quatro escores aparecem como sinais em{' '}
            <Text component={Link} href="/dashboard/recovery" fw={600} c={BRAND.orange} span>
              Recuperação
            </Text>
            , comparados com a SUA mediana dos 28 dias anteriores. Eles contam uma vez na concordância:
            vêm do mesmo formulário, e um dia ruim marca os quatro de uma vez — quatro respostas
            correlacionadas não são quatro observações independentes.
          </Alert>
        </>
      )}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Campos
// ---------------------------------------------------------------------------

function EscalaCampo({
  field,
  value,
  error,
  disabled,
  onChange,
}: {
  field: CheckinScoreField;
  value: number | '';
  error?: string;
  disabled: boolean;
  onChange: (value: number | '') => void;
}) {
  return (
    <Stack gap={4}>
      <Group gap="xs" align="baseline">
        <Text size="sm" fw={600} c={BRAND.textPrimary}>
          {CHECKIN_SCORE_LABELS[field]}
        </Text>
        <Text size="xs" c={BRAND.textMuted}>
          {scaleHint(field)}
        </Text>
      </Group>
      <Group gap="xs" wrap="nowrap">
        <SegmentedControl
          value={value === '' ? SEM_RESPOSTA : String(value)}
          onChange={(valor) => onChange(valor === SEM_RESPOSTA ? '' : Number(valor))}
          data={OPCOES_ESCALA}
          color="brand"
          size="sm"
          disabled={disabled}
          style={{ flex: 1 }}
        />
        {value !== '' && (
          <Tooltip label="Deixar em branco (não respondido)" withArrow>
            <ActionIcon
              variant="subtle"
              color="gray"
              aria-label={`Limpar ${CHECKIN_SCORE_LABELS[field]}`}
              onClick={() => onChange('')}
              disabled={disabled}
            >
              <IconTrash size={16} />
            </ActionIcon>
          </Tooltip>
        )}
      </Group>
      {error && (
        <Text size="xs" c="red">
          {error}
        </Text>
      )}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Últimos dias
// ---------------------------------------------------------------------------

function UltimosDias({
  itens,
  onApagar,
  apagandoId,
  onSelecionar,
}: {
  itens: readonly import('@/lib/dashboard-checkin-types').DailyCheckin[];
  onApagar: (id: number) => void;
  apagandoId: number | null;
  onSelecionar: (localDate: string) => void;
}) {
  if (itens.length === 0) {
    return (
      <EmptyState
        title="Nenhum check-in registrado ainda"
        description="Ausência de check-in não é ausência de sintoma: só quer dizer que nada foi respondido. Os sinais subjetivos precisam de 14 dias de histórico para ganharem uma baseline sua."
      />
    );
  }

  return (
    <Card withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Stack gap="sm">
        <div>
          <Title order={3} size="h4" c={BRAND.textPrimary}>
            Últimos dias
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            Clique numa data para corrigir aquele dia. Um traço é um item não respondido, nunca um zero.
          </Text>
        </div>

        <Group gap="xs" wrap="wrap">
          {CHECKIN_SCORE_FIELDS.map((field) => (
            <Badge key={field} variant="outline" color="gray" size="sm">
              {CHECKIN_SCORE_LABELS[field]}: {fmtInt(answeredDays(itens, field))} dia(s)
            </Badge>
          ))}
        </Group>

        <Table.ScrollContainer minWidth={520}>
          <Table striped highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Dia</Table.Th>
                {CHECKIN_SCORE_FIELDS.map((field) => (
                  <Table.Th key={field}>{CHECKIN_SCORE_LABELS[field]}</Table.Th>
                ))}
                <Table.Th>Nota</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {itens.map((item) => (
                <Table.Tr key={item.id}>
                  <Table.Td>
                    <Text
                      size="sm"
                      fw={600}
                      c={BRAND.orange}
                      style={{ cursor: 'pointer' }}
                      onClick={() => onSelecionar(item.local_date)}
                    >
                      {fmtDate(item.local_date)}
                    </Text>
                  </Table.Td>
                  {CHECKIN_SCORE_FIELDS.map((field) => (
                    <Table.Td key={field}>
                      <Text size="sm" c={item[field] == null ? BRAND.textMuted : BRAND.textPrimary}>
                        {item[field] ?? EM_DASH}
                      </Text>
                    </Table.Td>
                  ))}
                  <Table.Td>
                    <Text size="xs" c={BRAND.textMuted}>
                      {item.notes || EM_DASH}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <ActionIcon
                      variant="subtle"
                      color="red"
                      aria-label={`Apagar check-in de ${fmtDate(item.local_date)}`}
                      onClick={() => onApagar(item.id)}
                      loading={apagandoId === item.id}
                    >
                      <IconTrash size={16} />
                    </ActionIcon>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Stack>
    </Card>
  );
}
