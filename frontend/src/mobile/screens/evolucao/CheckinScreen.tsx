'use client';

import { Button } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { ApiError } from '@/lib/api';
import { dashboardKeys, deleteDailyCheckin, fetchDailyCheckins, saveDailyCheckin } from '@/lib/dashboard-api';
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
  type DailyCheckin,
} from '@/lib/dashboard-checkin-types';
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
import { MOBILE_TOKENS } from '../../tokens';
import styles from './evolucao.module.css';
import { QueryGate } from './shared';

const DIAS_NA_LISTA = 14;
const STALE_TIME = 60_000;
const ESCALA = Array.from({ length: CHECKIN_SCORE_MAX - CHECKIN_SCORE_MIN + 1 }, (_, i) => CHECKIN_SCORE_MIN + i);

function hoje(): string {
  return new Date().toISOString().slice(0, 10);
}

/** `/dashboard/checkin`: mesmo formulário, mesmas consultas e mesmas mutações do desktop. */
export function CheckinScreen() {
  const qc = useQueryClient();
  const [dia, setDia] = useState<string>(() => hoje());
  const [form, setForm] = useState<CheckinFormState>(() => emptyCheckinForm(hoje()));
  const [errors, setErrors] = useState<CheckinFormErrors>({});
  const [apagar, setApagar] = useState<DailyCheckin | null>(null);

  const query = useQuery({
    queryKey: dashboardKeys.checkins(null, DIAS_NA_LISTA),
    queryFn: () => fetchDailyCheckins(null, DIAS_NA_LISTA),
    staleTime: STALE_TIME,
  });
  const itens = useMemo(() => query.data?.items ?? [], [query.data]);
  const doDia = useMemo(() => checkinForDate(itens, dia), [itens, dia]);

  useEffect(() => {
    setErrors({});
    setForm(doDia ? formFromCheckin(doDia) : emptyCheckinForm(dia));
  }, [dia, doDia]);

  const tabelaDisponivel = query.data?.table_available !== false;

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
      qc.invalidateQueries({ queryKey: ['dashboard', 'recovery-signals'] });
      qc.invalidateQueries({ queryKey: ['dashboard', 'overview'] });
    },
    onError: (error) =>
      notifications.show({
        color: 'red',
        message: error instanceof ApiError ? error.message : 'Não foi possível salvar o check-in.',
      }),
  });
  const remover = useMutation({
    mutationFn: (id: number) => deleteDailyCheckin(id),
    onSuccess: () => {
      notifications.show({ color: 'green', message: 'Check-in apagado.' });
      qc.invalidateQueries({ queryKey: dashboardKeys.checkinAll() });
      setApagar(null);
    },
    onError: (error) =>
      notifications.show({
        color: 'red',
        message: error instanceof ApiError ? error.message : 'Não foi possível apagar o check-in.',
      }),
  });

  function submit() {
    const found = validateCheckinForm(form);
    setErrors(found);
    if (Object.keys(found).length === 0) salvar.mutate();
  }

  return (
    <>
      <ScreenHeader
        title="Check-in diário"
        subtitle="Disposição, dor muscular, estresse e humor, de 1 a 5. Leva dez segundos."
      />
      <QueryGate query={query} title="check-ins" rows={4}>
        {() => (
          <>
            {!tabelaDisponivel ? (
              <ListGroup>
                <ListBlock>
                  <p className={styles.body}>O check-in diário ainda não está disponível neste ambiente (migração 050 pendente).</p>
                </ListBlock>
              </ListGroup>
            ) : null}

            <ListGroup
              title={doDia ? 'Corrigir o check-in do dia' : 'Registrar o check-in do dia'}
              footer="Nenhum item é obrigatório. O que você deixar em branco fica em branco: não entra em conta nenhuma."
            >
              <ListBlock>
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor="mob-checkin-dia">
                    Dia
                  </label>
                  <input
                    id="mob-checkin-dia"
                    type="date"
                    className={styles.dateInput}
                    max={hoje()}
                    value={dia}
                    disabled={!tabelaDisponivel}
                    onChange={(e) => setDia(e.currentTarget.value || hoje())}
                  />
                  {errors.local_date ? <p className={styles.err}>{errors.local_date}</p> : null}
                </div>
              </ListBlock>
              {CHECKIN_SCORE_FIELDS.map((field) => (
                <ListBlock key={field}>
                  <Escala
                    field={field}
                    value={form[field]}
                    error={errors[field]}
                    disabled={!tabelaDisponivel}
                    onChange={(v) => setForm((prev) => ({ ...prev, [field]: v }))}
                  />
                </ListBlock>
              ))}
              <ListBlock>
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor="mob-checkin-nota">
                    Nota (opcional)
                  </label>
                  <input
                    id="mob-checkin-nota"
                    type="text"
                    className={styles.dateInput}
                    placeholder="o que mais vale registrar sobre hoje"
                    value={form.notes}
                    maxLength={255}
                    disabled={!tabelaDisponivel}
                    onChange={(e) => setForm((prev) => ({ ...prev, notes: e.currentTarget.value }))}
                  />
                  {errors.notes ? <p className={styles.err}>{errors.notes}</p> : null}
                </div>
              </ListBlock>
            </ListGroup>

            {itens.length === 0 ? (
              <EmptyState
                title="Nenhum check-in registrado ainda"
                description="Ausência de check-in não é ausência de sintoma: só quer dizer que nada foi respondido. Os sinais subjetivos precisam de 14 dias de histórico para ganharem uma baseline sua."
              />
            ) : (
              <>
                <ListGroup
                  title="Últimos dias"
                  footer={CHECKIN_SCORE_FIELDS.map((f) => `${CHECKIN_SCORE_LABELS[f]}: ${fmtInt(answeredDays(itens, f))} dia(s)`).join(' · ')}
                >
                  {itens.map((item) => (
                    <ListRow
                      key={item.id}
                      title={fmtDate(item.local_date)}
                      subtitle={`${CHECKIN_SCORE_FIELDS.map((f) => `${CHECKIN_SCORE_LABELS[f]} ${item[f] ?? EM_DASH}`).join(' · ')}${item.notes ? ` · ${item.notes}` : ''}`}
                      onClick={() => setDia(item.local_date)}
                    />
                  ))}
                </ListGroup>
                <p className={styles.note}>
                  Toque num dia para corrigir. Um traço é um item não respondido, nunca um zero.
                </p>
                {doDia ? (
                  <ListGroup>
                    <ListRow title={`Apagar o check-in de ${fmtDate(doDia.local_date)}`} destructive onClick={() => setApagar(doDia)} />
                  </ListGroup>
                ) : null}
              </>
            )}

            <ListGroup footer="Os quatro escores aparecem como sinais em Recuperação, comparados com a SUA mediana dos 28 dias anteriores. Contam uma vez na concordância: vêm do mesmo formulário, e quatro respostas correlacionadas não são quatro observações independentes.">
              <ListRow title="Abrir Recuperação" href="/dashboard/recovery" />
            </ListGroup>
          </>
        )}
      </QueryGate>

      <BottomSheet
        opened={apagar !== null}
        onClose={() => setApagar(null)}
        title="Apagar check-in?"
        footer={
          <>
            <Button variant="default" style={{ flex: 1, minHeight: 44 }} onClick={() => setApagar(null)}>
              Cancelar
            </Button>
            <Button
              color="red"
              style={{ flex: 1, minHeight: 44 }}
              loading={remover.isPending}
              onClick={() => apagar && remover.mutate(apagar.id)}
            >
              Apagar
            </Button>
          </>
        }
      >
        <p className={styles.body}>{apagar ? `O check-in de ${fmtDate(apagar.local_date)} será removido.` : ''}</p>
      </BottomSheet>

      <StickyAction>
        <Button
          fullWidth
          size="lg"
          color="brand"
          mih={48}
          leftSection={<IconCheck size={18} />}
          loading={salvar.isPending}
          disabled={!tabelaDisponivel}
          onClick={submit}
        >
          {doDia ? 'Salvar correção' : 'Registrar'}
        </Button>
      </StickyAction>
    </>
  );
}

function Escala({
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
  const label = CHECKIN_SCORE_LABELS[field];
  return (
    <div role="radiogroup" aria-label={label}>
      <p className={styles.headline}>{label}</p>
      <p className={styles.meta} style={{ marginTop: 2 }}>{scaleHint(field)}</p>
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        {ESCALA.map((n) => {
          const on = value === n;
          return (
            <button
              key={n}
              type="button"
              role="radio"
              aria-checked={on}
              disabled={disabled}
              onClick={() => onChange(on ? '' : n)}
              style={{
                flex: 1,
                minHeight: 44,
                borderRadius: 10,
                border: `1px solid ${on ? MOBILE_TOKENS.accent : MOBILE_TOKENS.separator}`,
                background: on ? MOBILE_TOKENS.accent : 'transparent',
                color: on ? '#fff' : MOBILE_TOKENS.text,
                font: 'inherit',
                fontSize: 17,
                fontWeight: 600,
              }}
            >
              {n}
            </button>
          );
        })}
      </div>
      <p className={styles.meta}>{value === '' ? 'Em branco (não respondido). Toque de novo para limpar.' : 'Toque no mesmo número para deixar em branco.'}</p>
      {error ? <p className={styles.err}>{error}</p> : null}
    </div>
  );
}
