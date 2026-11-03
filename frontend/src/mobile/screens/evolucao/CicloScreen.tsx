'use client';

import { useQuery } from '@tanstack/react-query';
import { MuscleStatusBadge } from '@/components/dashboard/MuscleStatusBadge';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { dashboardKeys, fetchCycle } from '@/lib/dashboard-api';
import { fmtSets } from '@/lib/dashboard-format';
import {
  DataCard,
  ListBlock,
  ListGroup,
  ListRow,
  ResponsiveDataList,
  ScreenHeader,
} from '../../ui';
import styles from './evolucao.module.css';
import { QueryGate } from './shared';

// 1 = segunda ... 7 = domingo, mesma convenção do builder de Modelos de treino.
const WEEKDAY_LABELS: Record<number, string> = {
  1: 'Segunda',
  2: 'Terça',
  3: 'Quarta',
  4: 'Quinta',
  5: 'Sexta',
  6: 'Sábado',
  7: 'Domingo',
};

/** `/dashboard/ciclo`: posição no ciclo do Modelo e cobertura por grupo (mesma consulta do desktop). */
export function CicloScreen() {
  const query = useQuery({ queryKey: dashboardKeys.cycle(), queryFn: fetchCycle });
  return (
    <>
      <ScreenHeader title="Ciclo de treino" subtitle="Posição no ciclo do Modelo atribuído e cobertura de volume por grupo." />
      <QueryGate query={query} title="ciclo de treino" rows={4}>
        {(data) => {
          if (!data.has_template) {
            return (
              <ListGroup>
                <ListBlock>
                  <p className={styles.body}>{data.message}</p>
                </ListBlock>
                <ListRow title="Ver planos de treino" href="/planos-treino" />
              </ListGroup>
            );
          }
          return (
            <>
              <ListGroup
                title={data.template_name ?? 'Modelo'}
                footer={`Ciclo de ${data.cycle_length_days} dias · ${data.slots.length} slot(s)${data.next_slot ? ` · próximo: ${data.next_slot.name}` : ''}`}
              >
                {data.slots.map((slot) => (
                  <ListRow
                    key={slot.sequence}
                    title={`#${slot.sequence} ${slot.name}`}
                    subtitle={slot.weekday != null ? (WEEKDAY_LABELS[slot.weekday] ?? `Dia ${slot.weekday}`) : 'Sem dia fixo'}
                    current={slot.status === 'next'}
                    trailing={<StatusBadge domain="cycleSlot" code={slot.status} />}
                  />
                ))}
              </ListGroup>

              {data.training_type_source === 'unavailable' ? (
                <ListGroup>
                  <ListBlock>
                    <p className={styles.body}>
                      Nenhuma Meta de Ativação resolvida para este modelo: a cobertura do ciclo por grupo muscular fica indisponível até que
                      uma Meta de Ativação seja vinculada ao modelo ou atribuída a você (configuração disponível no computador).
                    </p>
                  </ListBlock>
                </ListGroup>
              ) : (
                <>
                  <p className={styles.sectionTitle}>Cobertura do ciclo</p>
                  <p className={styles.note} style={{ marginBottom: 8 }}>
                    Realizado = últimos 7 dias. Planejado = semana do Modelo (7 dias a partir do 1º treino). Status = planejado × meta.
                  </p>
                  <ResponsiveDataList
                    aria-label="Cobertura do ciclo por grupo muscular"
                    variant="cards"
                    items={data.coverage}
                    getKey={(r) => r.muscle_group_id}
                    emptyTitle="Nenhum grupo muscular no ciclo"
                    emptyDescription="Nenhum grupo com meta, realizado ou projetado neste ciclo."
                    renderItem={(r) => (
                      <DataCard
                        title={r.muscle_group}
                        value={fmtSets(r.projected_weekly_sets)}
                        status={r.status ? <MuscleStatusBadge status={r.status} /> : undefined}
                        secondary={[
                          { label: 'Meta / semana', value: fmtSets(r.target_weekly_sets) },
                          { label: 'Realizado (7 dias)', value: fmtSets(r.actual_weekly_sets) },
                        ]}
                      />
                    )}
                  />
                  <p className={styles.note}>O valor principal é o planejado por semana.</p>
                </>
              )}
            </>
          );
        }}
      </QueryGate>
    </>
  );
}
