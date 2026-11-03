'use client';

import { Badge, Button } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { IconPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Suspense, useMemo, useState } from 'react';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { ApiError } from '@/lib/api';
import { createContextEvent, dashboardKeys, deleteContextEvent, fetchContext } from '@/lib/dashboard-api';
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
import type { ContextEvent, ContextEventType } from '@/lib/dashboard-context-types';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { EM_DASH, fmtDate, fmtInt } from '@/lib/dashboard-format';
import {
  BottomSheet,
  EmptyState,
  ListBlock,
  ListGroup,
  ListRow,
  ScreenHeader,
  StickyAction,
} from '../../ui';
import styles from './evolucao.module.css';
import { BlockSkeleton, PartialNote, PeriodFilter, QueryGate } from './shared';

const STALE_TIME = 60_000;
const today = () => new Date().toISOString().slice(0, 10);

export function ContextoScreen() {
  return (
    <Suspense fallback={<BlockSkeleton rows={4} label="Carregando contexto" />}>
      <ContextoContent />
    </Suspense>
  );
}

function ContextoContent() {
  const qc = useQueryClient();
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const [sheet, { open, close }] = useDisclosure(false);
  const [evento, setEvento] = useState<ContextEvent | null>(null);
  const query = useQuery({
    queryKey: dashboardKeys.context(windowQuery),
    queryFn: () => fetchContext(windowQuery),
    staleTime: STALE_TIME,
  });
  const payload = query.data;
  const limitacoes = useMemo(() => (payload ? collectContextLimitations(payload) : []), [payload]);
  const dias = useMemo(() => groupByDayDescending(payload?.items ?? []), [payload?.items]);

  const apagar = useMutation({
    mutationFn: (id: number) => deleteContextEvent(id),
    onSuccess: () => {
      notifications.show({ color: 'green', message: 'Evento apagado.' });
      qc.invalidateQueries({ queryKey: dashboardKeys.contextAll() });
      setEvento(null);
    },
    onError: (error) =>
      notifications.show({
        color: 'red',
        message: error instanceof ApiError ? error.message : 'Não foi possível apagar o evento.',
      }),
  });

  return (
    <>
      <ScreenHeader title="Contexto" subtitle="O que estava acontecendo em volta dos números." />
      <PeriodFilter filters={filters} setFilters={setFilters} rangeError={rangeError} />
      <QueryGate query={query} title="contexto" rows={4}>
        {(p) => (
          <>
            <ListGroup
              title="Resumo do período"
              footer={`${fmtDate(p.window.date_from)} a ${fmtDate(p.window.date_to)}. Contagem de REGISTRO, não de ocorrência: "0 álcool" quer dizer "nada anotado".`}
            >
              <ListBlock>
                <div className={styles.badges}>
                  <Badge color="blue" variant="light" size="lg">{fmtInt(p.manual_total)} registrado(s)</Badge>
                  <Badge color="grape" variant="light" size="lg">{fmtInt(p.derived_total)} derivado(s)</Badge>
                  <StatusBadge domain="dataNature" code={p.nature} size="sm" />
                  <StatusBadge domain="dataQuality" code={p.data_quality} size="sm" />
                </div>
                <p className={styles.meta} style={{ marginTop: 0 }}>
                  Derivado: troca de ficha e de academia, deduzidas do histórico. Não é editável.
                </p>
                <p className={styles.body} style={{ marginTop: 8 }}>
                  Última sessão registrada: {p.last_session_local_date ? fmtDate(p.last_session_local_date) : EM_DASH}
                  {p.days_since_last_session != null ? ` (${fmtInt(p.days_since_last_session)} dia(s) atrás)` : ''}
                </p>
                {p.counts.length > 0 ? (
                  <div className={styles.chips} style={{ marginTop: 8 }}>
                    {p.counts.map((c) => (
                      <Badge key={c.event_type} variant="outline" color="gray" size="sm">
                        {c.event_type_label}: {fmtInt(c.count)}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </ListBlock>
            </ListGroup>
            <PartialNote quality={p.data_quality} limitations={limitacoes} />

            {dias.length === 0 ? (
              <EmptyState
                title="Nada anotado neste período"
                description="Ausência de evento não é ausência de contexto: só quer dizer que nada foi registrado. Toque em Registrar evento."
              />
            ) : (
              dias.map((dia) => (
                <ListGroup key={dia.local_date} title={fmtDate(dia.local_date)}>
                  {dia.events.map((ev) => {
                    const origem = sourceBadge(ev);
                    const n = eventDurationDays(ev);
                    return (
                      <ListRow
                        key={`${ev.source}-${ev.id ?? ev.event_type}-${ev.local_date}`}
                        title={contextEventLabel(ev)}
                        subtitle={`${eventPeriodLabel(ev)}${ev.intensity != null ? ` · intensidade ${ev.intensity}/${INTENSITY_MAX}` : ''}${n > 1 ? ` · ${n} dias` : ''}`}
                        trailing={<Badge size="xs" color={origem.color} variant="light">{origem.label}</Badge>}
                        onClick={() => setEvento(ev)}
                      />
                    );
                  })}
                </ListGroup>
              ))
            )}

            <ListGroup footer="Um evento nunca explica um número. Quando ele coincide com uma queda de desempenho, a associação (com confiança baixa e a ressalva declarada) aparece em Insights.">
              <ListRow title="Abrir Insights" href="/dashboard/insights" />
            </ListGroup>

            <BottomSheet
              opened={evento !== null}
              onClose={() => setEvento(null)}
              title={evento ? contextEventLabel(evento) : 'Evento'}
              footer={
                evento?.editable && evento.id != null ? (
                  <Button
                    color="red"
                    variant="light"
                    fullWidth
                    mih={44}
                    loading={apagar.isPending}
                    onClick={() => apagar.mutate(evento.id as number)}
                  >
                    Apagar evento
                  </Button>
                ) : undefined
              }
            >
              {evento ? (
                <ListGroup>
                  <ListBlock>
                    <p className={styles.body}>{eventPeriodLabel(evento)}</p>
                    {evento.notes ? <p className={styles.meta}>{evento.notes}</p> : null}
                    {!evento.editable ? (
                      <p className={styles.meta}>Derivado do histórico: some sozinho se a origem mudar. Não pode ser apagado.</p>
                    ) : null}
                  </ListBlock>
                </ListGroup>
              ) : null}
            </BottomSheet>

            <RegistroSheet opened={sheet} onClose={close} tabelaDisponivel={p.table_available} />
          </>
        )}
      </QueryGate>
      <StickyAction>
        <Button fullWidth size="lg" color="brand" mih={48} leftSection={<IconPlus size={18} />} onClick={open}>
          Registrar evento
        </Button>
      </StickyAction>
    </>
  );
}

function RegistroSheet({
  opened,
  onClose,
  tabelaDisponivel,
}: {
  opened: boolean;
  onClose: () => void;
  tabelaDisponivel: boolean;
}) {
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
      onClose();
    },
    onError: (error) =>
      notifications.show({
        color: 'red',
        message: error instanceof ApiError ? error.message : 'Não foi possível registrar o evento.',
      }),
  });
  function submit() {
    const found = validateContextForm(form);
    setErrors(found);
    if (Object.keys(found).length === 0) salvar.mutate();
  }
  return (
    <BottomSheet
      opened={opened}
      onClose={onClose}
      title="Registrar evento de contexto"
      footer={
        <Button fullWidth color="brand" mih={44} loading={salvar.isPending} disabled={!tabelaDisponivel} onClick={submit}>
          Registrar
        </Button>
      }
    >
      <div className={styles.stack}>
        {!tabelaDisponivel ? (
          <p className={styles.err}>
            O registro de eventos ainda não está disponível neste ambiente (migração 048 pendente). As trocas de ficha e de academia
            continuam na linha do tempo.
          </p>
        ) : null}
        <p className={styles.meta} style={{ margin: 0 }}>
          Registrar o mesmo tipo duas vezes no mesmo dia cria dois eventos: os dois podem ter acontecido.
        </p>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="mob-ctx-tipo">Tipo</label>
          <select
            id="mob-ctx-tipo"
            className={styles.dateInput}
            value={form.event_type}
            disabled={!tabelaDisponivel}
            onChange={(e) => setForm((prev) => ({ ...prev, event_type: e.currentTarget.value as ContextEventType }))}
          >
            {CONTEXT_EVENT_TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          {errors.event_type ? <p className={styles.err}>{errors.event_type}</p> : null}
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="mob-ctx-data">Data</label>
          <input id="mob-ctx-data" type="date" className={styles.dateInput} value={form.local_date} disabled={!tabelaDisponivel}
            onChange={(e) => setForm((prev) => ({ ...prev, local_date: e.currentTarget.value }))} />
          {errors.local_date ? <p className={styles.err}>{errors.local_date}</p> : null}
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="mob-ctx-fim">Data final (opcional)</label>
          <input id="mob-ctx-fim" type="date" className={styles.dateInput} value={form.end_local_date} disabled={!tabelaDisponivel}
            onChange={(e) => setForm((prev) => ({ ...prev, end_local_date: e.currentTarget.value }))} />
          {errors.end_local_date ? <p className={styles.err}>{errors.end_local_date}</p> : null}
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="mob-ctx-int">Intensidade (opcional, {INTENSITY_MIN} a {INTENSITY_MAX})</label>
          <input
            id="mob-ctx-int"
            type="number"
            inputMode="numeric"
            min={INTENSITY_MIN}
            max={INTENSITY_MAX}
            step={1}
            className={styles.dateInput}
            value={form.intensity}
            disabled={!tabelaDisponivel}
            onChange={(e) => setForm((prev) => ({ ...prev, intensity: e.currentTarget.value === '' ? '' : Number(e.currentTarget.value) }))}
          />
          {errors.intensity ? <p className={styles.err}>{errors.intensity}</p> : null}
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="mob-ctx-nota">Nota (opcional)</label>
          <input id="mob-ctx-nota" type="text" className={styles.dateInput} maxLength={255} value={form.notes} disabled={!tabelaDisponivel}
            onChange={(e) => setForm((prev) => ({ ...prev, notes: e.currentTarget.value }))} />
          {errors.notes ? <p className={styles.err}>{errors.notes}</p> : null}
        </div>
      </div>
    </BottomSheet>
  );
}
