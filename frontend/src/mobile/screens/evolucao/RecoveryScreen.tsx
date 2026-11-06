'use client';

import { Badge } from '@mantine/core';
import { IconLock } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Suspense, useEffect, useMemo, useState } from 'react';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { MuscleBodyMapLazy as MuscleBodyMap, preloadMuscleBodyMap } from '@/components/MuscleBodyMapLazy';
import { dashboardKeys, fetchRecoverySignals } from '@/lib/dashboard-api';
import { useDashboardFilters, type DashboardWindow } from '@/lib/dashboard-filters';
import { EM_DASH } from '@/lib/dashboard-format';
import {
  adjustmentLabel,
  bodyMapEntries,
  bucketSignals,
  collectLimitations,
  countByReadiness,
  factorLines,
  isAdjustmentStale,
  readyAtLabel,
  reassessLabel,
  recommendationLabel,
  RECOVERY_BODY_MAP_COLORS,
  SIGNAL_BUCKET_HINTS,
  SIGNAL_BUCKET_LABELS,
  SIGNAL_BUCKET_ORDER,
  sortGroupsByUrgency,
  trainingVsRestLabel,
} from '@/lib/dashboard-recovery';
import type { RecoveryGroup, RecoverySignal, TrainingVsRest } from '@/lib/dashboard-recovery-types';
import { READINESS_ORDER, statusLegend } from '@/lib/dashboard-status';
import {
  BottomSheet,
  DataCard,
  EmptyState,
  ListBlock,
  ListGroup,
  ListRow,
  ResponsiveDataList,
  ScreenHeader,
} from '../../ui';
import styles from './evolucao.module.css';
import { plural } from './pure';
import { BlockSkeleton, LimitationsNote, metricLimitations, metricText, PartialNote, PeriodFilter, QueryGate } from './shared';
import { useIsAdmin } from '@/lib/use-is-admin';

const RECOVERY_WINDOWS = ['7d', 'cycle', '28d', 'custom'] as const satisfies readonly DashboardWindow[];
const BODY_MAP_LEGEND = statusLegend('readiness');

export function RecoveryScreen() {
  return (
    <Suspense fallback={<BlockSkeleton rows={4} label="Carregando recuperação" />}>
      <RecoveryContent />
    </Suspense>
  );
}

function RecoveryContent() {
  const isAdmin = useIsAdmin();
  useEffect(preloadMuscleBodyMap, []);
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const [grupo, setGrupo] = useState<RecoveryGroup | null>(null);
  const [sinal, setSinal] = useState<RecoverySignal | null>(null);
  const query = useQuery({
    queryKey: dashboardKeys.recoverySignals(windowQuery),
    queryFn: () => fetchRecoverySignals(windowQuery),
  });
  const payload = query.data;
  const grupos = useMemo(() => sortGroupsByUrgency(payload?.groups ?? []), [payload?.groups]);
  const baldes = useMemo(() => bucketSignals(payload?.signals ?? []), [payload?.signals]);
  const limitacoes = useMemo(() => (payload ? collectLimitations(payload) : []), [payload]);
  const contagem = useMemo(() => countByReadiness(grupos), [grupos]);
  const itens = useMemo(() => bodyMapEntries(grupos), [grupos]);

  return (
    <>
      <ScreenHeader title="Recuperação" subtitle="Prontidão por grupo e os sinais que concordam ou divergem dela." />
      <PeriodFilter filters={filters} setFilters={setFilters} rangeError={rangeError} allowed={RECOVERY_WINDOWS} />
      <QueryGate query={query} title="recuperação" rows={5}>
        {(p) => (
          <>
            <ListGroup title="Avaliação combinada">
              <ListBlock>
                <div className={styles.badges}>
                  <StatusBadge domain="severity" code={p.assessment.level} />
                  {isAdmin && <StatusBadge domain="dataQuality" code={p.assessment.uncertainty} suffix="de certeza" />}
                  {!p.assessment.allow_intensification ? (
                    <Badge color="red" variant="light" leftSection={<IconLock size={14} />}>
                      Não é hora de intensificar
                    </Badge>
                  ) : null}
                </div>
                <p className={styles.headline}>{p.assessment.headline}</p>
                {!p.assessment.allow_intensification && p.assessment.intensification_blocked_reason ? (
                  <p className={styles.meta}>{p.assessment.intensification_blocked_reason}</p>
                ) : null}
                {p.assessment.explanation.map((l) => (
                  <p key={l} className={styles.meta}>
                    • {l}
                  </p>
                ))}
                <p className={styles.meta}>{reassessLabel(p.assessment.reassess_after_hours)}</p>
              </ListBlock>
              {READINESS_ORDER.map((code) => (
                <ListRow
                  key={code}
                  title={<StatusBadge domain="readiness" code={code} />}
                  value={`${plural(contagem[code] ?? 0, 'grupo', 'grupos')}`}
                />
              ))}
            </ListGroup>
            <PartialNote quality={p.data_quality} limitations={limitacoes} />

            <ListGroup
              title="Prontidão por grupo"
              footer={`Modelo ${p.readiness_model}, o mesmo usado pela geração de ficha e pelo Modo Treino.`}
            >
              <ListBlock>
                <p className={styles.meta} style={{ marginTop: 0 }}>
                  <strong>{isAdjustmentStale(p.global_readiness_adjustment) ? 'Ajuste global de fadiga (dado desatualizado): ' : 'Ajuste global de fadiga: '}</strong>
                  {adjustmentLabel(p.global_readiness_adjustment)}
                </p>
              </ListBlock>
            </ListGroup>
            <ResponsiveDataList
              aria-label="Prontidão por grupo muscular"
              variant="cards"
              items={grupos}
              getKey={(g) => g.muscle_group_id ?? g.muscle_group}
              emptyTitle="Nenhum grupo treinado"
              emptyDescription="Nenhum grupo muscular treinado no período selecionado."
              renderItem={(g) => (
                <DataCard
                  title={g.muscle_group}
                  value={metricText(g.recovery_pct, 0)}
                  status={<StatusBadge domain="readiness" code={g.readiness_status} size="xs" />}
                  secondary={[
                    { label: 'Falta', value: metricText(g.remaining_hours) },
                    { label: 'Bom para', value: readyAtLabel(g, p.window.timezone) },
                  ]}
                  onClick={() => setGrupo(g)}
                />
              )}
            />
            {grupos.length > 0 ? (
              <ListGroup title="Mapa corporal" footer="Toque em um grupo para ver o detalhe.">
                <div className={styles.chartBlock}>
                  <MuscleBodyMap items={itens} colors={RECOVERY_BODY_MAP_COLORS} legend={BODY_MAP_LEGEND} />
                </div>
              </ListGroup>
            ) : null}

            <p className={styles.sectionTitle}>Sinais objetivos</p>
            <p className={styles.note} style={{ marginBottom: 8 }}>
              Cada sinal é comparado com a SUA mediana dos {p.baseline_window_days} dias anteriores ao período. Nenhum deles, sozinho,
              caracteriza recuperação ruim.
            </p>
            {p.signals.length === 0 ? (
              <EmptyState title="Sem sinais" description="Nenhum sinal disponível no período." />
            ) : (
              SIGNAL_BUCKET_ORDER.filter((k) => baldes[k].length > 0).map((k) => (
                <ListGroup key={k} title={SIGNAL_BUCKET_LABELS[k]} footer={SIGNAL_BUCKET_HINTS[k]}>
                  {baldes[k].map((s) => (
                    <ListRow
                      key={s.code}
                      title={s.label}
                      subtitle={`Baseline ${metricText(s.baseline)}`}
                      value={metricText(s.current)}
                      trailing={<StatusBadge domain="signalReading" code={s.reading} size="xs" />}
                      onClick={() => setSinal(s)}
                    />
                  ))}
                </ListGroup>
              ))
            )}

            <TreinoVsDescanso bloco={p.training_vs_rest} />
          </>
        )}
      </QueryGate>

      <BottomSheet opened={grupo !== null} onClose={() => setGrupo(null)} title={grupo?.muscle_group ?? 'Grupo'}>
        {grupo && payload ? <GrupoDetalhe g={grupo} tz={payload.window.timezone} /> : null}
      </BottomSheet>
      <BottomSheet opened={sinal !== null} onClose={() => setSinal(null)} title={sinal?.label ?? 'Sinal'}>
        {sinal ? <SinalDetalhe s={sinal} /> : null}
      </BottomSheet>
    </>
  );
}

function GrupoDetalhe({ g, tz }: { g: RecoveryGroup; tz: string }) {
  const fatores = factorLines(g);
  return (
    <>
      <ListGroup>
        <ListBlock>
          <div className={styles.badges}>
            <StatusBadge domain="readiness" code={g.readiness_status} />
          </div>
          {g.explanation.map((l) => (
            <p key={l} className={styles.meta} style={{ marginTop: 0 }}>
              {l}
            </p>
          ))}
        </ListBlock>
        <ListRow title="Prontidão" value={metricText(g.recovery_pct, 0)} />
        <ListRow title="Recuperação estimada" value={metricText(g.estimated_recovery_hours)} />
        <ListRow title="Falta" value={metricText(g.remaining_hours)} />
        <ListRow title="Bom para" value={readyAtLabel(g, tz)} />
        <ListRow title="Desde o estímulo" value={metricText(g.hours_since_last_stimulus)} />
        <ListRow title="Recomendação de séries" subtitle={recommendationLabel(g)} />
      </ListGroup>
      <LimitationsNote
        items={metricLimitations([g.recovery_pct, g.estimated_recovery_hours, g.remaining_hours, g.hours_since_last_stimulus])}
      />
      <ListGroup title="Fatores do cálculo">
        <ListBlock>
          {fatores.length === 0 ? (
            <p className={styles.body}>{EM_DASH}</p>
          ) : (
            fatores.map((l, i) => (
              <p key={`${i}-${l}`} className={styles.body}>
                • {l}
              </p>
            ))
          )}
        </ListBlock>
      </ListGroup>
    </>
  );
}

function SinalDetalhe({ s }: { s: RecoverySignal }) {
  return (
    <>
      <ListGroup>
        <ListBlock>
          <div className={styles.badges}>
            <StatusBadge domain="signalReading" code={s.reading} />
            <StatusBadge domain="signalAgreement" code={s.agreement} size="sm" />
          </div>
          <p className={styles.body}>{s.description}</p>
        </ListBlock>
        <ListRow title="No período" subtitle={`${plural(s.observations, 'medida', 'medidas')}`} value={metricText(s.current)} />
        <ListRow
          title="Sua baseline"
          subtitle={s.baseline.value == null ? `Precisa de ${s.min_observations} pontos na janela de referência; há ${s.baseline_observations}.` : undefined}
          value={metricText(s.baseline)}
        />
      </ListGroup>
      <LimitationsNote items={[...s.limitations, ...metricLimitations([s.current, s.baseline])]} />
    </>
  );
}

function TreinoVsDescanso({ bloco }: { bloco: TrainingVsRest }) {
  return (
    <>
      <p className={styles.sectionTitle}>Dia com treino × dia sem treino</p>
      <p className={styles.note} style={{ marginBottom: 8 }}>
        {plural(bloco.training_days, 'dia', 'dias')} com treino e {bloco.rest_days} sem treino no período. Dois grupos observados não são um
        experimento: a diferença é associação, nunca causa.
      </p>
      {bloco.metrics.length === 0 ? (
        <EmptyState title="Sem recorte" description="Nenhuma métrica com recorte por dia de treino nesta janela." />
      ) : (
        bloco.metrics.map((m) => (
          <ListGroup key={m.code} title={m.label}>
            <ListBlock>
              <div className={styles.badges}>
                <StatusBadge domain="signalReading" code={m.reading} size="xs" />
              </div>
              <p className={styles.meta} style={{ marginTop: 0 }}>{trainingVsRestLabel(m)}</p>
            </ListBlock>
            <ListRow title="Dia com treino" subtitle={`${plural(m.training_observations, 'dia', 'dias')}`} value={metricText(m.training_days)} />
            <ListRow title="Dia sem treino" subtitle={`${plural(m.rest_observations, 'dia', 'dias')}`} value={metricText(m.rest_days)} />
            <LimitationsNote items={[...m.limitations, ...metricLimitations([m.training_days, m.rest_days])]} />
          </ListGroup>
        ))
      )}
    </>
  );
}
