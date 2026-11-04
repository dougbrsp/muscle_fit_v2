'use client';

import { Badge } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { Suspense, useMemo, useState } from 'react';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { dashboardKeys, fetchEnginePlan, fetchEngineRuns } from '@/lib/dashboard-api';
import {
  AUDIT_SOURCE_META,
  REASON_KIND_META,
  collectEngineLimitations,
  executedSummary,
  generatedSummary,
  plannedSummary,
  reasonText,
  runOptionLabel,
  runTimestampLabel,
} from '@/lib/dashboard-engine';
import type { EngineItem, EngineRun } from '@/lib/dashboard-engine-types';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { EM_DASH, fmtDecimal, fmtInt } from '@/lib/dashboard-format';
import { versionLabel } from '@/lib/template-versions';
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
import { BlockSkeleton, PartialNote, PeriodFilter, QueryGate } from './shared';

const STALE_TIME = 60_000;

/** Rótulo legível do modo de volume (apresentação; valor desconhecido aparece como veio). */
function volumeModeLabel(mode: string | null): string {
  if (!mode) return EM_DASH;
  if (mode === 'percent_budget') return 'Orçamento por grupo muscular';
  if (mode === 'equipment_series') return 'Séries por equipamento';
  return mode;
}

export function EngineScreen() {
  return (
    <Suspense fallback={<BlockSkeleton rows={4} label="Carregando motor de geração" />}>
      <EngineContent />
    </Suspense>
  );
}

function EngineContent() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const planId = filters.planId;
  const [picker, { open, close }] = useDisclosure(false);
  const [item, setItem] = useState<EngineItem | null>(null);
  const [run, setRun] = useState<EngineRun | null>(null);

  const runsQuery = useQuery({
    queryKey: dashboardKeys.engineRuns(windowQuery),
    queryFn: () => fetchEngineRuns(windowQuery),
    staleTime: STALE_TIME,
  });
  const planQuery = useQuery({
    queryKey: dashboardKeys.enginePlan(planId),
    queryFn: () => fetchEnginePlan(planId),
    enabled: planId != null,
    staleTime: STALE_TIME,
  });
  const runs = runsQuery.data?.items;
  // Só as gerações que viraram ficha entram no seletor (o detalhe é por plan_id).
  const options = useMemo(
    () => (runs ?? []).filter((r) => r.workout_plan_id != null).map((r) => ({ value: r.workout_plan_id as number, label: runOptionLabel(r) })),
    [runs],
  );
  const payload = planQuery.data;
  const limitacoes = useMemo(() => (payload ? collectEngineLimitations(payload) : []), [payload]);
  // Escolher uma geração leva direto à auditoria (ela abre abaixo da lista).
  const pick = (id: number) => {
    setFilters({ planId: id });
    window.setTimeout(() => document.getElementById('sec-auditoria')?.scrollIntoView({ block: 'start' }), 400);
  };

  return (
    <>
      <ScreenHeader
        title="Motor de geração"
        subtitle="Por que a sua ficha saiu assim: planejado, gerado (e por quê) e executado."
      />
      <PeriodFilter filters={filters} setFilters={setFilters} rangeError={rangeError} />

      <QueryGate query={runsQuery} title="gerações do período" rows={3}>
        {(data) => (
          <>
            <PartialNote quality={data.data_quality} limitations={data.limitations} title="Histórico de gerações" fixHref={null} />
            <ListGroup>
              <ListRow
                title="Ficha gerada"
                subtitle={options.length === 0 ? 'Nenhuma ficha gerada no período' : 'Escolha uma ficha para ver a auditoria'}
                value={planId != null ? (options.find((o) => o.value === planId)?.label ?? `Ficha ${planId}`) : 'Selecionar'}
                onClick={options.length === 0 ? undefined : open}
              />
            </ListGroup>
            <ResponsiveDataList
              aria-label="Gerações no período"
              items={data.items}
              getKey={(r) => `${r.source}-${r.run_id ?? r.workout_plan_id}`}
              emptyTitle="Nenhuma geração neste período"
              emptyDescription="A tabela generation_runs passou a registrar cada geração em 2026-09-26; antes disso só existe o que as fichas salvas guardaram no próprio payload."
              renderItem={(r) => (
                <ListRow
                  title={r.workout_plan_id == null ? 'Rascunho não salvo' : (r.plan_name ?? `Ficha ${r.workout_plan_id}`)}
                  subtitle={runTimestampLabel(r)}
                  value={r.workout_plan_id == null ? 'sem ficha' : undefined}
                  onClick={() => setRun(r)}
                />
              )}
            />
          </>
        )}
      </QueryGate>

      {planId != null ? (
        <QueryGate query={planQuery} title="auditoria da ficha" rows={4}>
          {(p) => {
            const meta = AUDIT_SOURCE_META[p.run.source];
            const c = p.item_status_counts;
            return (
              <>
                <div id="sec-auditoria" />
                <ListGroup title={p.plan_name ?? `Ficha ${p.plan_id}`}>
                  <ListBlock>
                    <div className={styles.badges}>
                      {p.run.engine_version ? <Badge variant="light" color="gray" radius="sm">Motor {p.run.engine_version}</Badge> : null}
                      <Badge variant="light" color={meta.color} radius="sm">{meta.label}</Badge>
                    </div>
                    <p className={styles.meta} style={{ marginTop: 0 }}>{meta.hint}</p>
                    <p className={styles.body} style={{ marginTop: 8 }}>
                      Modelo: {p.template_name ?? EM_DASH}
                      {versionLabel(p.template_version_number) ? ` ${versionLabel(p.template_version_number)}` : ''}
                      {p.template_slot_name ? ` · slot ${p.template_slot_name}` : ''}
                    </p>
                    <p className={styles.meta}>Modo de volume: {volumeModeLabel(p.volume_mode)}</p>
                    <p className={styles.meta}>Gerada em: {runTimestampLabel(p.run)} · {plural(p.sessions_count, 'sessão', 'sessões')} registrada(s)</p>
                    <p className={styles.meta}>
                      Itens: {c.completed} concluído(s) · {c.interrupted} interrompido(s) · {c.skipped} pulado(s) · {c.substituted} substituído(s) · {c.extra} extra(s)
                    </p>
                    {p.plan_level_reasons.map((reason, i) => (
                      <p key={`${reason.code}-${i}`} className={styles.meta}>• {reasonText(reason)}</p>
                    ))}
                  </ListBlock>
                </ListGroup>
                <PartialNote quality={p.data_quality} limitations={limitacoes} fixHref={null} />

                <p className={styles.sectionTitle}>Planejado × gerado × executado</p>
                {p.items.length === 0 ? (
                  <EmptyState title="Esta ficha não tem itens" description="Nada a comparar." />
                ) : (
                  <ResponsiveDataList
                    aria-label="Itens da ficha"
                    items={p.items}
                    getKey={(it) => it.plan_exercise_id ?? `extra-${it.plan_sequence}-${it.generated_name}`}
                    renderItem={(it) => (
                      <ListRow
                        title={`${it.plan_sequence ?? EM_DASH}. ${it.generated_name ?? EM_DASH}`}
                        subtitle={executedSummary(it)}
                        trailing={<StatusBadge domain="planItemStatus" code={it.status} size="xs" />}
                        onClick={() => setItem(it)}
                      />
                    )}
                  />
                )}

                {p.group_budgets.length > 0 ? (
                  <ListGroup title="Orçamento por grupo muscular">
                    {p.group_budgets.map((row, i) => (
                      <ListRow
                        key={`${row.muscle_group_id ?? 'sem-grupo'}-${i}`}
                        title={row.muscle_group_name ?? EM_DASH}
                        subtitle={`Meta/sem ${fmtDecimal(row.target_weekly_sets)} · faixa ${row.target_min_sets != null || row.target_max_sets != null ? `${fmtDecimal(row.target_min_sets)} – ${fmtDecimal(row.target_max_sets)}` : EM_DASH} · desvio ${row.deviation_status ?? EM_DASH} · recuperação ${row.recovery_status ?? EM_DASH}`}
                        value={`${fmtDecimal(row.generated_sets)} geradas`}
                      />
                    ))}
                  </ListGroup>
                ) : null}

                {p.rejections.length > 0 ? (
                  <ListGroup title="Exercícios avaliados e não escolhidos">
                    {p.rejections.map((row, i) => (
                      <ListRow
                        key={`${row.exercise_id ?? 'sem-id'}-${i}`}
                        title={row.exercise_name ?? (row.exercise_id != null ? `#${row.exercise_id}` : EM_DASH)}
                        subtitle={reasonText({ kind: 'selection', code: row.reason, detail: null, data: row.data })}
                        value={row.rank != null ? `ordem ${row.rank}` : undefined}
                      />
                    ))}
                  </ListGroup>
                ) : null}
              </>
            );
          }}
        </QueryGate>
      ) : runs && runs.length > 0 ? (
        <p className={styles.note}>Selecione uma ficha para ver planejado × gerado × executado, item a item.</p>
      ) : null}

      <BottomSheet
        opened={run !== null}
        onClose={() => setRun(null)}
        title={run ? (run.workout_plan_id == null ? 'Rascunho não salvo' : (run.plan_name ?? `Ficha ${run.workout_plan_id}`)) : 'Geração'}
      >
        {run ? (
          <>
            <ListGroup footer={AUDIT_SOURCE_META[run.source].hint}>
              <ListRow title="Gerada em" value={runTimestampLabel(run)} />
              <ListRow title="Origem do registro" value={AUDIT_SOURCE_META[run.source].label} />
              <ListRow title="Motor" value={run.engine_version ?? EM_DASH} />
              <ListRow title="IA usada" value={run.llm_used == null ? EM_DASH : run.llm_used ? 'Sim' : 'Não'} />
              <ListRow title="Modo de volume" value={volumeModeLabel(run.volume_mode)} />
              <ListRow title="Exercícios selecionados" value={run.selection_count == null ? EM_DASH : fmtInt(run.selection_count)} />
              <ListRow title="Limites de segurança aplicados" value={run.safety_clamp_count == null ? EM_DASH : fmtInt(run.safety_clamp_count)} />
              <ListRow title="Trocas" value={run.substitution_count == null ? EM_DASH : fmtInt(run.substitution_count)} />
              <ListRow title="Caminhos alternativos" value={run.fallback_count == null ? EM_DASH : fmtInt(run.fallback_count)} />
              {run.workout_plan_id == null ? <ListRow title="Esta geração não virou ficha" subtitle="Só existe o registro da geração" /> : null}
            </ListGroup>
            {run.limitations.length > 0 ? (
              <ListGroup title="Ressalvas">
                <ListBlock>
                  {run.limitations.map((l) => (
                    <p key={l} className={styles.meta} style={{ marginTop: 0 }}>{l}</p>
                  ))}
                </ListBlock>
              </ListGroup>
            ) : null}
            {run.workout_plan_id != null ? (
              <ListGroup>
                <ListRow
                  title="Ver auditoria da ficha"
                  subtitle="Planejado × gerado × executado, item a item"
                  onClick={() => {
                    const id = run.workout_plan_id as number;
                    setRun(null);
                    pick(id);
                  }}
                />
              </ListGroup>
            ) : null}
          </>
        ) : null}
      </BottomSheet>

      <BottomSheet opened={picker} onClose={close} title="Ficha gerada">
        <ListGroup>
          {planId != null ? <ListRow title="Limpar seleção" onClick={() => { setFilters({ planId: null }); close(); }} /> : null}
          {options.map((o) => (
            <ListRow
              key={o.value}
              title={o.label}
              current={o.value === planId}
              onClick={() => {
                pick(o.value);
                close();
              }}
            />
          ))}
        </ListGroup>
      </BottomSheet>

      <BottomSheet opened={item !== null} onClose={() => setItem(null)} title={item ? (item.generated_name ?? 'Item') : 'Item'}>
        {item ? (
          <>
            <ListGroup>
              <ListRow title="O Modelo planejou" subtitle={item.planned.source === 'muscle_group_budget' ? 'O Modelo pediu volume para o grupo, não este exercício' : undefined} value={plannedSummary(item.planned)} />
              <ListRow title="O motor gerou" value={generatedSummary(item)} />
              <ListRow
                title="Você executou"
                subtitle={[
                  item.missing_sets != null && item.missing_sets > 0 ? `faltaram ${plural(item.missing_sets, 'série', 'séries')} para o prescrito` : null,
                  item.substitution ? `no lugar de ${item.substitution.planned_name ?? EM_DASH}` : null,
                ]
                  .filter(Boolean)
                  .join(' · ') || undefined}
                value={executedSummary(item)}
              />
              <ListRow title="Status" trailing={<StatusBadge domain="planItemStatus" code={item.status} />} />
            </ListGroup>
            <ListGroup title="Por quê">
              {item.reasons.length === 0 ? (
                <ListBlock><p className={styles.body}>{EM_DASH}</p></ListBlock>
              ) : (
                item.reasons.map((reason, i) => (
                  <ListRow
                    key={`${reason.kind}-${reason.code}-${i}`}
                    title={REASON_KIND_META[reason.kind]?.label ?? reason.kind}
                    subtitle={reasonText(reason)}
                  />
                ))
              )}
            </ListGroup>
            {item.limitations.length > 0 ? (
              <ListGroup title="Ressalvas">
                <ListBlock>
                  {item.limitations.map((l) => (
                    <p key={l} className={styles.meta} style={{ marginTop: 0 }}>{l}</p>
                  ))}
                </ListBlock>
              </ListGroup>
            ) : null}
          </>
        ) : null}
      </BottomSheet>
    </>
  );
}
