'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Divider,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Timeline,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCalendarEvent, IconInfoCircle, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { useMemo, useState } from 'react';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { DashboardCardSkeleton, EmptyState, ErrorState, PartialState } from '@/components/dashboard/states';
import { ApiError } from '@/lib/api';
import {
  createContextEvent,
  dashboardKeys,
  deleteContextEvent,
  fetchContext,
} from '@/lib/dashboard-api';
import {
  CONTEXT_EVENT_TYPE_OPTIONS,
  collectContextLimitations,
  contextEventLabel,
  emptyContextForm,
  eventDurationDays,
  eventPeriodLabel,
  groupByDayDescending,
  INTENSITY_MAX,
  INTENSITY_MIN,
  sourceBadge,
  toContextEventInput,
  validateContextForm,
  type ContextFormErrors,
  type ContextFormState,
} from '@/lib/dashboard-context';
import type {
  ContextEvent,
  ContextEventType,
  ContextResponse,
} from '@/lib/dashboard-context-types';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { EM_DASH, fmtDate, fmtInt } from '@/lib/dashboard-format';
import { BRAND } from '@/theme';

/**
 * `/dashboard/contexto` — "o que mais estava acontecendo?" (plano §7, §11;
 * etapa F2.1).
 *
 * Tudo vem pronto de `GET /analytics/dashboard/context`: a timeline já une os
 * eventos MANUAIS (`context_events`, migração 048) aos DERIVADOS do histórico
 * (troca de ficha e troca de academia), ordenados, rotulados e com a origem
 * declarada. Nada é recalculado aqui — nem mesmo a contagem por tipo.
 *
 * Duas coisas esta tela faz questão de deixar visíveis:
 * - **o que é registro e o que é dedução**: o selo "Derivado" marca o que veio
 *   do histórico, e esses itens não têm botão de apagar (apagá-los seria
 *   apagar o histórico, não o evento);
 * - **ausência não é ausência de contexto**: sem evento nenhum a tela diz
 *   "nada foi anotado", nunca "não aconteceu nada".
 *
 * Evento de contexto é ASSOCIAÇÃO, nunca causa: nenhum número do hub é
 * reclassificado por nada daqui. Quando um evento coincide com uma queda de
 * desempenho, quem diz isso — com confiança baixa e a ressalva escrita — é o
 * insight `PERFORMANCE_DROP_WITH_CONTEXT_EVENT`, em `/dashboard/insights`.
 *
 * Única entrada de dado: o formulário abaixo, que grava em
 * `POST /context-events` (api-core).
 */

const STALE_TIME = 60_000;

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

export function DashboardContextoClient() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();

  const consulta = useQuery({
    queryKey: dashboardKeys.context(windowQuery),
    queryFn: () => fetchContext(windowQuery),
    staleTime: STALE_TIME,
  });

  const payload = consulta.data;
  const limitacoes = useMemo(() => (payload ? collectContextLimitations(payload) : []), [payload]);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Contexto
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            O que estava acontecendo em volta dos números: álcool, viagem, doença, lesão, troca de ficha
            ou de academia.
          </Text>
        </div>
        <DashboardPeriodSelect filters={filters} onChange={setFilters} rangeError={rangeError} />
      </Group>

      {consulta.isLoading && <DashboardCardSkeleton rows={8} />}
      {consulta.isError && <ErrorState title="Não foi possível carregar o contexto." error={consulta.error} />}

      {payload && (
        <>
          <ResumoCard payload={payload} />

          <PartialState quality={payload.data_quality} limitations={limitacoes} />

          <TimelineCard payload={payload} />

          <Divider />

          <RegistroDeEvento tabelaDisponivel={payload.table_available} />

          <Alert color="gray" variant="light" icon={<IconInfoCircle size={18} />}>
            Um evento nunca explica um número. Quando ele coincide no tempo com uma queda de desempenho,
            a associação (com confiança baixa e a ressalva declarada) aparece em{' '}
            <Text component={Link} href="/dashboard/insights" fw={600} c={BRAND.orange} span>
              Insights
            </Text>
            .
          </Alert>
        </>
      )}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Cartões
// ---------------------------------------------------------------------------

function CardBase({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <Card withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Stack gap="sm">
        <div>
          <Title order={3} size="h4" c={BRAND.textPrimary}>
            {title}
          </Title>
          {hint && (
            <Text size="sm" c={BRAND.textMuted}>
              {hint}
            </Text>
          )}
        </div>
        {children}
      </Stack>
    </Card>
  );
}

function ResumoCard({ payload }: { payload: ContextResponse }) {
  const dias = payload.days_since_last_session;
  return (
    <CardBase
      title="Resumo do período"
      hint={`${fmtDate(payload.window.date_from)} a ${fmtDate(payload.window.date_to)}. Contagem de REGISTRO, não de ocorrência: "0 álcool" quer dizer "nada anotado".`}
    >
      <Group gap="sm" wrap="wrap">
        <Badge color="blue" variant="light" size="lg">
          {fmtInt(payload.manual_total)} registrado(s)
        </Badge>
        <Tooltip
          label="Troca de ficha e de academia, deduzidas do histórico. Não são editáveis."
          withArrow
          multiline
          w={280}
        >
          <Badge color="grape" variant="light" size="lg" style={{ cursor: 'help' }}>
            {fmtInt(payload.derived_total)} derivado(s)
          </Badge>
        </Tooltip>
        <StatusBadge domain="dataNature" code={payload.nature} size="sm" />
        <StatusBadge domain="dataQuality" code={payload.data_quality} size="sm" />
      </Group>

      <Group gap="xs" wrap="wrap">
        <Text size="sm" c={BRAND.textMuted}>
          Última sessão registrada:
        </Text>
        <Text size="sm" fw={600} c={BRAND.textPrimary}>
          {payload.last_session_local_date ? fmtDate(payload.last_session_local_date) : EM_DASH}
        </Text>
        {dias != null && (
          <Text size="sm" c={BRAND.textMuted}>
            ({fmtInt(dias)} dia(s) atrás)
          </Text>
        )}
      </Group>

      {payload.counts.length > 0 && (
        <Group gap="xs" wrap="wrap">
          {payload.counts.map((item) => (
            <Badge key={item.event_type} variant="outline" color="gray" size="sm">
              {item.event_type_label}: {fmtInt(item.count)}
            </Badge>
          ))}
        </Group>
      )}
    </CardBase>
  );
}

function EventoItem({ evento, onApagar, apagando }: {
  evento: ContextEvent;
  onApagar: (id: number) => void;
  apagando: boolean;
}) {
  const origem = sourceBadge(evento);
  const dias = eventDurationDays(evento);
  return (
    <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm">
      <Stack gap={2} style={{ flex: 1 }}>
        <Group gap="xs" wrap="wrap">
          <Text size="sm" fw={600} c={BRAND.textPrimary}>
            {contextEventLabel(evento)}
          </Text>
          <Badge size="xs" color={origem.color} variant="light">
            {origem.label}
          </Badge>
          {evento.intensity != null && (
            <Badge size="xs" color="orange" variant="light">
              intensidade {evento.intensity}/{INTENSITY_MAX}
            </Badge>
          )}
          {dias > 1 && (
            <Badge size="xs" color="gray" variant="outline">
              {dias} dias
            </Badge>
          )}
        </Group>
        <Text size="xs" c={BRAND.textMuted}>
          {eventPeriodLabel(evento)}
        </Text>
        {evento.notes && (
          <Text size="sm" c={BRAND.textMuted}>
            {evento.notes}
          </Text>
        )}
      </Stack>
      {evento.editable && evento.id != null ? (
        <ActionIcon
          variant="subtle"
          color="red"
          aria-label="Apagar evento"
          onClick={() => onApagar(evento.id as number)}
          loading={apagando}
        >
          <IconTrash size={16} />
        </ActionIcon>
      ) : (
        <Tooltip label="Derivado do histórico: some sozinho se a origem mudar." withArrow>
          <ActionIcon variant="subtle" color="gray" aria-label="Evento derivado" disabled>
            <IconCalendarEvent size={16} />
          </ActionIcon>
        </Tooltip>
      )}
    </Group>
  );
}

function TimelineCard({ payload }: { payload: ContextResponse }) {
  const qc = useQueryClient();
  const dias = useMemo(() => groupByDayDescending(payload.items), [payload.items]);

  const apagar = useMutation({
    mutationFn: (id: number) => deleteContextEvent(id),
    onSuccess: () => {
      notifications.show({ color: 'green', message: 'Evento apagado.' });
      qc.invalidateQueries({ queryKey: dashboardKeys.contextAll() });
    },
    onError: (error) => {
      notifications.show({
        color: 'red',
        message: error instanceof ApiError ? error.message : 'Não foi possível apagar o evento.',
      });
    },
  });

  return (
    <CardBase title="Linha do tempo" hint="Mais recente primeiro. Evento com duração mostra o intervalo.">
      {dias.length === 0 ? (
        <EmptyState
          title="Nada anotado neste período"
          description="Ausência de evento não é ausência de contexto: só quer dizer que nada foi registrado. Use o formulário abaixo."
        />
      ) : (
        <Timeline active={dias.length} bulletSize={16} lineWidth={2} color="brand">
          {dias.map((dia) => (
            <Timeline.Item key={dia.local_date} title={fmtDate(dia.local_date)}>
              <Stack gap="sm" mt={6}>
                {dia.events.map((evento) => (
                  <EventoItem
                    key={`${evento.source}-${evento.id ?? evento.event_type}-${evento.local_date}`}
                    evento={evento}
                    onApagar={(id) => apagar.mutate(id)}
                    apagando={apagar.isPending && apagar.variables === evento.id}
                  />
                ))}
              </Stack>
            </Timeline.Item>
          ))}
        </Timeline>
      )}
    </CardBase>
  );
}

// ---------------------------------------------------------------------------
// Registro manual
// ---------------------------------------------------------------------------

function RegistroDeEvento({ tabelaDisponivel }: { tabelaDisponivel: boolean }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<ContextFormState>(() => emptyContextForm(today()));
  const [errors, setErrors] = useState<ContextFormErrors>({});

  const salvar = useMutation({
    mutationFn: () => createContextEvent(toContextEventInput(form)),
    onSuccess: (resposta) => {
      notifications.show({
        color: 'green',
        message: `${resposta.item.event_type_label} de ${fmtDate(resposta.item.local_date)} registrado.`,
      });
      setForm((prev) => ({ ...prev, notes: '', intensity: '', end_local_date: '' }));
      qc.invalidateQueries({ queryKey: dashboardKeys.contextAll() });
    },
    onError: (error) => {
      notifications.show({
        color: 'red',
        message: error instanceof ApiError ? error.message : 'Não foi possível registrar o evento.',
      });
    },
  });

  function submit() {
    const found = validateContextForm(form);
    setErrors(found);
    if (Object.keys(found).length === 0) salvar.mutate();
  }

  return (
    <CardBase
      title="Registrar evento de contexto"
      hint="Tipo, data (e data final, se durou mais de um dia), intensidade opcional e uma nota. Registrar o mesmo tipo duas vezes no mesmo dia cria dois eventos — os dois podem ter acontecido."
    >
      {!tabelaDisponivel && (
        <Alert color="yellow" variant="light" icon={<IconInfoCircle size={18} />}>
          O registro de eventos ainda não está disponível neste ambiente (migração 048 pendente). As trocas
          de ficha e de academia, que são derivadas do histórico, continuam aparecendo na linha do tempo.
        </Alert>
      )}
      <SimpleGrid cols={{ base: 1, sm: 2, md: 5 }} spacing="sm">
        <Select
          label="Tipo"
          data={CONTEXT_EVENT_TYPE_OPTIONS}
          value={form.event_type}
          error={errors.event_type}
          allowDeselect={false}
          onChange={(value) =>
            value && setForm((prev) => ({ ...prev, event_type: value as ContextEventType }))
          }
          disabled={!tabelaDisponivel}
        />
        <TextInput
          label="Data"
          type="date"
          value={form.local_date}
          error={errors.local_date}
          onChange={(e) => setForm((prev) => ({ ...prev, local_date: e.currentTarget.value }))}
          disabled={!tabelaDisponivel}
        />
        <TextInput
          label="Data final (opcional)"
          type="date"
          value={form.end_local_date}
          error={errors.end_local_date}
          onChange={(e) => setForm((prev) => ({ ...prev, end_local_date: e.currentTarget.value }))}
          disabled={!tabelaDisponivel}
        />
        <NumberInput
          label="Intensidade (opcional)"
          description="1 a 5. Em branco = sem gradação."
          min={INTENSITY_MIN}
          max={INTENSITY_MAX}
          step={1}
          allowDecimal={false}
          value={form.intensity}
          error={errors.intensity}
          onChange={(value) => setForm((prev) => ({ ...prev, intensity: value === '' ? '' : Number(value) }))}
          disabled={!tabelaDisponivel}
        />
        <TextInput
          label="Nota (opcional)"
          value={form.notes}
          error={errors.notes}
          maxLength={255}
          onChange={(e) => setForm((prev) => ({ ...prev, notes: e.currentTarget.value }))}
          disabled={!tabelaDisponivel}
        />
      </SimpleGrid>
      <Group justify="flex-end">
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
    </CardBase>
  );
}
