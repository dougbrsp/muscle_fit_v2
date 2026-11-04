'use client';

import { Switch } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { Suspense, useMemo, useState } from 'react';
import { MinTargetMaxBar } from '@/components/dashboard/MinTargetMaxBar';
import { PlannedVsRealizedBar } from '@/components/dashboard/PlannedVsRealizedBar';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { dashboardKeys, fetchMuscleDetail, fetchMuscles } from '@/lib/dashboard-api';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { EM_DASH, fmtDate, fmtInt, fmtKg, fmtRpe, fmtSets } from '@/lib/dashboard-format';
import {
  MUSCLE_SORTS,
  MUSCLE_SORT_LABELS,
  contributionsByExercise,
  countByStatus,
  groupsOutsideTemplatePlan,
  groupsWithGenericTarget,
  humanizeHours,
  isTemplatePlanItem,
  sessionsForExercise,
  sortMuscleItems,
  type MuscleSort,
} from '@/lib/dashboard-muscles';
import { STATUS_DOMAIN_ORDER, volumeStatusDomain } from '@/lib/dashboard-status';
import type { MuscleItem, MuscleSession } from '@/lib/dashboard-v3-types';
import { exerciseProgressHref } from '@/lib/exercise-progress';
import { isWorkingSetType } from '@/lib/set-types';
import {
  BottomSheet,
  DataCard,
  EmptyState,
  FilterSheet,
  ListBlock,
  ListGroup,
  ListRow,
  ResponsiveDataList,
  ScreenHeader,
} from '../../ui';
import styles from './evolucao.module.css';
import { MusculosExtras } from './MusculosExtras';
import { BlockSkeleton, SectionJump, LimitationsNote, metricLimitations, metricText, PartialNote, PeriodFilter, QueryGate } from './shared';

export function MusculosScreen() {
  return (
    <Suspense fallback={<BlockSkeleton rows={4} label="Carregando grupos musculares" />}>
      <MusculosContent />
    </Suspense>
  );
}

function TargetBar({ item }: { item: MuscleItem }) {
  if (isTemplatePlanItem(item)) {
    return <PlannedVsRealizedBar planned={item.targets.target.value} actual={item.realized.weekly_effective_sets.value} status={item.status} />;
  }
  return (
    <MinTargetMaxBar
      min={item.targets.min.value}
      target={item.targets.target.value}
      max={item.targets.max.value}
      actual={item.realized.weekly_effective_sets.value}
      projected={null}
      status={item.status}
    />
  );
}

function MusculosContent() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const [sort, setSort] = useState<MuscleSort>('status');
  const [showReadiness, setShowReadiness] = useState(true);
  const [sheet, { open: openSheet, close: closeSheet }] = useDisclosure(false);
  const selectedId = filters.muscleGroupId;

  const musclesQuery = useQuery({
    queryKey: dashboardKeys.muscles(windowQuery, false, true),
    queryFn: () => fetchMuscles(windowQuery, false, true),
  });
  const readinessQuery = useQuery({
    queryKey: dashboardKeys.muscles(windowQuery, true),
    queryFn: () => fetchMuscles(windowQuery, true),
    enabled: showReadiness,
  });

  const payload = musclesQuery.data;
  const readinessById = useMemo(() => {
    const map = new Map<number, MuscleItem['readiness']>();
    for (const i of readinessQuery.data?.items ?? []) map.set(i.muscle_group_id, i.readiness);
    return map;
  }, [readinessQuery.data]);
  const items = useMemo(() => sortMuscleItems(payload?.items ?? [], sort), [payload?.items, sort]);
  const counts = useMemo(() => countByStatus(items), [items]);
  const genericTargets = useMemo(() => groupsWithGenericTarget(items), [items]);
  const outsideModel = useMemo(() => groupsOutsideTemplatePlan(items), [items]);
  const statusDomain = volumeStatusDomain(payload?.target_source);
  const selected = items.find((i) => i.muscle_group_id === selectedId) ?? null;
  const extraFilters = (sort !== 'status' ? 1 : 0) + (showReadiness ? 0 : 1);

  return (
    <>
      <ScreenHeader
        title="Grupos musculares"
        subtitle="Volume realizado contra a meta semanal. Planejado e projetado aparecem à parte."
      />
      <PeriodFilter filters={filters} setFilters={setFilters} rangeError={rangeError} />
      <SectionJump
        items={[
          { id: 'sec-lista', label: 'Grupos' },
          { id: 'sec-sessao', label: 'Últimas sessões' },
          { id: 'sec-ativacao', label: 'Ativação 7 dias' },
          { id: 'sec-tendencia', label: 'Tendência' },
        ]}
      />
      <div id="sec-lista" />
      <ListGroup>
        <ListRow
          title="Ordenar e exibir"
          subtitle={`${MUSCLE_SORT_LABELS[sort]} · prontidão ${showReadiness ? 'ligada' : 'desligada'}`}
          onClick={openSheet}
        />
      </ListGroup>

      <FilterSheet
        opened={sheet}
        onClose={closeSheet}
        activeCount={extraFilters}
        onClear={() => {
          setSort('status');
          setShowReadiness(true);
        }}
      >
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="mob-ordem">
            Ordenar por
          </label>
          <select id="mob-ordem" className={styles.dateInput} value={sort} onChange={(e) => setSort(e.currentTarget.value as MuscleSort)}>
            {MUSCLE_SORTS.map((v) => (
              <option key={v} value={v}>
                {MUSCLE_SORT_LABELS[v]}
              </option>
            ))}
          </select>
        </div>
        <Switch
          checked={showReadiness}
          onChange={(e) => setShowReadiness(e.currentTarget.checked)}
          label="Mostrar prontidão"
          description="Consulta separada, mais lenta"
        />
      </FilterSheet>

      <QueryGate query={musclesQuery} title="grupos musculares" rows={5}>
        {(data) => (
          <>
            {items.length > 0 ? (
              <div className={styles.badges}>
                {STATUS_DOMAIN_ORDER[statusDomain]
                  .filter((code) => counts[code])
                  .map((code) => (
                    <StatusBadge key={code} domain={statusDomain} code={code} size="sm" suffix={` · ${counts[code]}`} />
                  ))}
              </div>
            ) : null}
            <PartialNote
              quality={data.data_quality}
              limitations={[
                ...data.limitations,
                genericTargets.length > 0
                  ? `Sem meta cadastrada para: ${genericTargets.join(', ')}. Para esses grupos, "abaixo" e "acima" usam limiares genéricos (Metas de Ativação: disponível no computador).`
                  : '',
                outsideModel.length > 0
                  ? `Fora do seu Modelo: ${outsideModel.join(', ')}. O volume realizado nesses grupos é trabalho extra em relação ao plano, não déficit.`
                  : '',
              ].filter(Boolean)}
              fixHref={genericTargets.length > 0 ? null : '/dashboard/data-quality'}
              fixLabel="Ver qualidade dos dados"
            />
            {items.length === 0 ? (
              <EmptyState
                title="Nenhum grupo com meta nem volume no período"
                description="Registre um treino ou cadastre uma Meta de Ativação (no computador) para comparar o volume com um alvo."
              />
            ) : (
              <ResponsiveDataList
                aria-label="Grupos musculares"
                variant="cards"
                items={items}
                getKey={(i) => i.muscle_group_id}
                renderItem={(i) => {
                  const r = readinessById.get(i.muscle_group_id) ?? null;
                  return (
                    <DataCard
                      title={i.muscle_group}
                      value={metricText(i.realized.weekly_effective_sets)}
                      status={<StatusBadge domain={volumeStatusDomain(i.targets.source)} code={i.status} size="xs" />}
                      secondary={[
                        { label: 'Último estímulo', value: fmtDate(i.last_stimulus_local_date) },
                        {
                          label: 'Prontidão',
                          value: !showReadiness ? EM_DASH : r ? <StatusBadge domain="readiness" code={r.status} size="xs" /> : readinessQuery.isPending ? '…' : EM_DASH,
                        },
                      ]}
                      onClick={() => setFilters({ muscleGroupId: i.muscle_group_id })}
                    />
                  );
                }}
              />
            )}
            {data.comparatives ? <MusculosExtras comparatives={data.comparatives} /> : <MusculosExtras comparatives={null} />}
            <BottomSheet
              opened={selectedId != null}
              onClose={() => setFilters({ muscleGroupId: null })}
              title={selected?.muscle_group ?? 'Grupo'}
            >
              {selected ? (
                <GrupoDetalhe item={selected} readiness={readinessById.get(selected.muscle_group_id) ?? null} showReadiness={showReadiness} />
              ) : selectedId != null ? (
                <GrupoPorId id={selectedId} showReadiness={showReadiness} />
              ) : null}
            </BottomSheet>
          </>
        )}
      </QueryGate>
    </>
  );
}

/** Id da URL fora da lista (como no desktop): o detalhe vem da própria resposta do drill-down. */
function GrupoPorId({ id, showReadiness }: { id: number; showReadiness: boolean }) {
  const { windowQuery } = useDashboardFilters();
  const query = useQuery({
    queryKey: dashboardKeys.muscleDetail(id, windowQuery),
    queryFn: () => fetchMuscleDetail(id, windowQuery),
  });
  return (
    <QueryGate query={query} title="grupo muscular" rows={3}>
      {(d) => <GrupoDetalhe item={d.item} readiness={d.item.readiness} showReadiness={showReadiness} />}
    </QueryGate>
  );
}

function GrupoDetalhe({
  item,
  readiness,
  showReadiness,
}: {
  item: MuscleItem;
  readiness: MuscleItem['readiness'];
  showReadiness: boolean;
}) {
  const { filters, windowQuery } = useDashboardFilters();
  const detailQuery = useQuery({
    queryKey: dashboardKeys.muscleDetail(item.muscle_group_id, windowQuery),
    queryFn: () => fetchMuscleDetail(item.muscle_group_id, windowQuery),
  });
  const [exercise, setExercise] = useState<{ id: number | null; name: string } | null>(null);
  const horas = humanizeHours(item.hours_since_last_stimulus.value);
  const metrics = [
    item.realized.weekly_effective_sets,
    item.realized.effective_sets_stimulus,
    item.realized.direct_sets,
    item.realized.indirect_sets,
    item.realized.stabilizer_sets,
    item.frequency.direct_sessions,
    item.frequency.median_interval_hours,
    item.planned.effective_sets,
    item.projected.remaining_sets,
  ];
  return (
    <>
      <ListGroup>
        <ListBlock>
          <div className={styles.badges}>
            <StatusBadge domain={volumeStatusDomain(item.targets.source)} code={item.status} size="sm" />
            {item.targets.source === 'fallback_generic' || item.targets.source === 'template_plan' ? (
              <StatusBadge domain="targetSource" code={item.targets.source} size="xs" />
            ) : null}
          </div>
          <TargetBar item={item} />
          <p className={styles.meta}>
            Meta: mín {metricText(item.targets.min)} · alvo {metricText(item.targets.target)} · máx {metricText(item.targets.max)}
          </p>
        </ListBlock>
        <ListRow title="Realizado (semana)" value={metricText(item.realized.weekly_effective_sets)} />
        <ListRow title="Efetivas no período" value={metricText(item.realized.effective_sets_stimulus)} />
        <ListRow
          title="Direto · indireto · estabilizador"
          value={`${metricText({ ...item.realized.direct_sets, unit: null }, 0)} · ${metricText({ ...item.realized.indirect_sets, unit: null }, 0)} · ${metricText(item.realized.stabilizer_sets, 0)}`}
        />
        <ListRow
          title="Frequência"
          subtitle={`a cada ${metricText(item.frequency.median_interval_hours, 0)}`}
          value={`${metricText(item.frequency.direct_sessions, 0)} diretas`}
        />
        <ListRow title="Planejado" value={metricText(item.planned.effective_sets)} />
        <ListRow title="Projetado (ciclo)" value={metricText(item.projected.remaining_sets)} />
        <ListRow title="Último estímulo" subtitle={horas ? `há ${horas}` : undefined} value={fmtDate(item.last_stimulus_local_date)} />
        {showReadiness ? (
          <ListRow
            title="Prontidão"
            subtitle={readiness?.explanation.join(' · ') || undefined}
            trailing={readiness ? <StatusBadge domain="readiness" code={readiness.status} size="xs" /> : <span>{EM_DASH}</span>}
          />
        ) : null}
      </ListGroup>
      <LimitationsNote items={metricLimitations(metrics)} />

      <p className={styles.sectionTitle}>De onde veio o estímulo</p>
      <QueryGate query={detailQuery} title="estímulo do grupo" rows={3}>
        {(d) => {
          if (d.sessions.length === 0) {
            return <EmptyState title="Nenhuma série neste período" description="Nada foi registrado que gerasse estímulo para este grupo na janela selecionada." />;
          }
          return (
            <>
              <PartialNote quality={d.data_quality} limitations={d.limitations} />
              {d.sets_truncated ? <p className={styles.note}>Mostrando as {d.sets_limit} séries mais recentes do período.</p> : null}
              <Contribuicoes sessions={d.sessions} onPick={(id, name) => setExercise({ id, name })} />
              <BottomSheet opened={exercise !== null} onClose={() => setExercise(null)} title={exercise?.name ?? 'Exercício'}>
                {exercise ? (
                  <ExercicioSeries
                    sessions={d.sessions}
                    exerciseId={exercise.id}
                    name={exercise.name}
                    href={exercise.id != null ? exerciseProgressHref(exercise.id, filters) : null}
                  />
                ) : null}
              </BottomSheet>
            </>
          );
        }}
      </QueryGate>
    </>
  );
}

function Contribuicoes({ sessions, onPick }: { sessions: MuscleSession[]; onPick: (id: number | null, name: string) => void }) {
  const contribs = useMemo(() => contributionsByExercise(sessions), [sessions]);
  return (
    <ListGroup>
      {contribs.map((c) => (
        <ListRow
          key={`${c.exerciseId ?? c.name}`}
          title={c.name}
          subtitle={`${fmtSets(c.stimulus, 1)} efetivas${c.share != null ? ` · ${Math.round(c.share * 100)}% do grupo` : ''}`}
          trailing={
            <span className={styles.chips}>
              {c.roles.map((role) => (
                <StatusBadge key={role} domain="muscleRole" code={role} size="xs" />
              ))}
            </span>
          }
          onClick={() => onPick(c.exerciseId, c.name)}
        />
      ))}
    </ListGroup>
  );
}

function ExercicioSeries({
  sessions,
  exerciseId,
  name,
  href,
}: {
  sessions: MuscleSession[];
  exerciseId: number | null;
  name: string;
  href: string | null;
}) {
  const sessoes = sessionsForExercise(sessions, exerciseId, name);
  return (
    <>
      {href ? (
        <ListGroup>
          <ListRow title="Estou evoluindo neste exercício?" href={href} />
        </ListGroup>
      ) : null}
      {sessoes.map((s) => (
        <ListGroup
          key={s.activity_id}
          title={`${fmtDate(s.local_date)} · ${s.activity_name}`}
          footer={!s.has_direct_work ? 'Sem trabalho direto nesta sessão.' : undefined}
        >
          {s.exposures.flatMap((e) =>
            e.sets.map((set) => (
              <ListRow
                key={set.set_id}
                title={`Série ${fmtInt(set.set_number)}${!isWorkingSetType(set.set_type) ? ` (${set.set_type})` : ''}`}
                subtitle={`${fmtInt(set.reps)} reps · ${fmtKg(set.weight_kg)} · RPE ${fmtRpe(set.rpe)}`}
                value={`${fmtSets(set.effective_sets_stimulus, 2)} efetiva`}
              />
            )),
          )}
        </ListGroup>
      ))}
    </>
  );
}
