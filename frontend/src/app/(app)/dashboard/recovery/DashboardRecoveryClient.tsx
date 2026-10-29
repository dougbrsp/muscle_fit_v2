'use client';

import {
  Alert,
  Badge,
  Box,
  Card,
  Divider,
  Group,
  List,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { IconInfoCircle, IconLock } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo } from 'react';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import { MetricValue } from '@/components/dashboard/MetricValue';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import {
  DashboardCardSkeleton,
  EmptyState,
  ErrorState,
  PartialState,
} from '@/components/dashboard/states';
import { MuscleBodyMapLazy as MuscleBodyMap, preloadMuscleBodyMap } from '@/components/MuscleBodyMapLazy';
import { dashboardKeys, fetchRecoverySignals } from '@/lib/dashboard-api';
import { useDashboardFilters, type DashboardWindow } from '@/lib/dashboard-filters';
import { EM_DASH, fmtDate } from '@/lib/dashboard-format';
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
import type {
  RecoveryGroup,
  RecoverySignal,
  RecoverySignalsResponse,
  TrainingVsRest,
} from '@/lib/dashboard-recovery-types';
import { READINESS_ORDER, statusLegend, statusVisual } from '@/lib/dashboard-status';
import { BRAND } from '@/theme';

/**
 * `/dashboard/recovery` — "estou recuperando bem?" (plano §7, §11; etapa F1.5).
 *
 * Reescrita sobre `GET /analytics/dashboard/recovery-signals`. A tela mostra
 * DUAS leituras lado a lado e nunca as funde num número só:
 *
 * - **Prontidão por grupo**, do modelo `fullbody-no-reps-v1` — o mesmo que a
 *   geração de ficha e o Modo Treino consultam. Nada é recalculado aqui:
 *   status, horas e a `explanation` vêm prontos do backend.
 * - **Painel de sinais** (sono, HRV, FC de repouso, TSB, dor, desempenho)
 *   comparados com a BASELINE PESSOAL. O que DIVERGE da prontidão aparece
 *   primeiro, e o que não tem baseline aparece como "sem baseline" — nunca
 *   escondido, nunca convertido em zero.
 *
 * Linguagem forte fica reservada ao que o modelo de fato classificou como
 * `NO_DIRECT_WORK`; o resto é informação.
 *
 * **F2.5-A (M33/M36)**: a F2.4 tinha verificado a paridade e decidido NÃO
 * aposentar `/recuperacao-muscular`, porque três coisas dela não existiam
 * aqui — mapa corporal, recomendação de séries e fatores do cálculo. As três
 * (mais o ajuste global de fadiga por TSB) passaram a sair de
 * `GET /analytics/dashboard/recovery-signals`, REPASSADAS do mesmo modelo:
 * nada é recalculado nesta tela. Junto veio o recorte "dia com treino × dia
 * sem treino" (M36), que morava em `/treinos` sob o rótulo de cardio, mas
 * sempre foi recuperação. Com isso o aviso que mandava o usuário de volta
 * para a tela legada SAIU — não há mais nada lá que não esteja aqui.
 *
 * **F2.4, 2ª passada**: `/recuperacao-muscular` FOI aposentada. A paridade foi
 * reverificada coluna a coluna (o inventário está no comentário de
 * `app/(app)/recuperacao-muscular/page.tsx`), a última pendência era a coluna
 * "RPE médio" da janela — que já vinha no payload como
 * `factors.avg_rpe_window` e passou a aparecer nos "Fatores". O caminho antigo
 * responde 301 para cá (CloudFront Function + `LegacyPathRedirect`).
 *
 * A rota v2 `/analytics/dashboard-recovery` continua existindo; esta tela não
 * a consome. `GET /analytics/muscle-recovery`, que a tela legada consumia,
 * NÃO entrou em telemetria de obsolescência. Os consumidores no servidor que
 * justificaram isso (`template_volume_planner` via HTTP, `engines_client`) já
 * não a chamam: o planner lê a recuperação direto por `user_id` (PR-09) e o
 * `engines_client` foi removido (SEG-04).
 */

/** Recuperação é uma pergunta sobre AGORA: janelas longas não fazem sentido. */
const RECOVERY_WINDOWS = ['7d', 'cycle', '28d', 'custom'] as const satisfies readonly DashboardWindow[];

// Escala do mapa corporal: intensidade cresce com a gravidade (pronto = 1 ...
// evitar = 3) e as cores seguem a ordem canônica — o pior estado vence quando
// grupos da mesma região divergem. Numeração e cores moram juntas em
// `lib/dashboard-recovery.ts` (`bodyMapEntries`/`RECOVERY_BODY_MAP_COLORS`).
const BODY_MAP_LEGEND = statusLegend('readiness');

export function DashboardRecoveryClient() {
  // COD-14: o mapa corporal vem sob demanda; começa a baixar já na montagem.
  useEffect(preloadMuscleBodyMap, []);
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();

  const consulta = useQuery({
    queryKey: dashboardKeys.recoverySignals(windowQuery),
    queryFn: () => fetchRecoverySignals(windowQuery),
  });

  const payload = consulta.data;
  const grupos = useMemo(() => sortGroupsByUrgency(payload?.groups ?? []), [payload?.groups]);
  const baldes = useMemo(() => bucketSignals(payload?.signals ?? []), [payload?.signals]);
  const limitacoes = useMemo(() => (payload ? collectLimitations(payload) : []), [payload]);
  const contagem = useMemo(() => countByReadiness(grupos), [grupos]);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Recuperação
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            Prontidão por grupo muscular e os sinais objetivos que concordam ou divergem dela.
          </Text>
        </div>
        <DashboardPeriodSelect
          filters={filters}
          onChange={setFilters}
          allowed={RECOVERY_WINDOWS}
          rangeError={rangeError}
        />
      </Group>

      {consulta.isLoading && <DashboardCardSkeleton rows={6} />}
      {consulta.isError && (
        <ErrorState title="Não foi possível carregar a recuperação." error={consulta.error} />
      )}

      {payload && (
        <>
          <AvaliacaoCombinada payload={payload} contagem={contagem} />

          <PartialState quality={payload.data_quality} limitations={limitacoes} />

          <Stack gap="xs">
            <Title order={3} size="h4" c={BRAND.textPrimary}>
              Sinais objetivos
            </Title>
            <Text size="sm" c={BRAND.textMuted}>
              Cada sinal é comparado com a SUA mediana dos {payload.baseline_window_days} dias
              anteriores ao período — não com a média de ninguém. Nenhum deles, sozinho, caracteriza
              recuperação ruim.
            </Text>
          </Stack>

          {payload.signals.length === 0 ? (
            <EmptyState description="Nenhum sinal disponível no período." />
          ) : (
            SIGNAL_BUCKET_ORDER.filter((key) => baldes[key].length > 0).map((key) => (
              <PainelDeSinais
                key={key}
                titulo={SIGNAL_BUCKET_LABELS[key]}
                dica={SIGNAL_BUCKET_HINTS[key]}
                sinais={baldes[key]}
              />
            ))
          )}

          <Divider />

          <Stack gap="xs">
            <Title order={3} size="h4" c={BRAND.textPrimary}>
              Prontidão por grupo
            </Title>
            <Text size="sm" c={BRAND.textMuted}>
              Modelo {payload.readiness_model}, o mesmo usado pela geração de ficha e pelo Modo
              Treino. Os números aqui e lá são sempre os mesmos.
            </Text>
          </Stack>

          <Alert
            color={isAdjustmentStale(payload.global_readiness_adjustment) ? 'yellow' : 'gray'}
            variant="light"
            icon={<IconInfoCircle size={18} />}
            title={
              isAdjustmentStale(payload.global_readiness_adjustment)
                ? 'Ajuste global de fadiga — dado desatualizado'
                : 'Ajuste global de fadiga'
            }
          >
            {adjustmentLabel(payload.global_readiness_adjustment)}
          </Alert>

          {grupos.length === 0 ? (
            <EmptyState description="Nenhum grupo muscular treinado no período selecionado." />
          ) : (
            <>
              <TabelaDeGrupos grupos={grupos} timeZone={payload.window.timezone} />
              <MapaCorporal grupos={grupos} />
            </>
          )}

          <Divider />

          <PainelTreinoVsDescanso bloco={payload.training_vs_rest} />
        </>
      )}
    </Stack>
  );
}

function AvaliacaoCombinada({
  payload,
  contagem,
}: {
  payload: RecoverySignalsResponse;
  contagem: Record<string, number>;
}) {
  const { assessment } = payload;
  const visual = statusVisual('severity', assessment.level);

  return (
    <Card
      withBorder
      radius="lg"
      p="md"
      style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
    >
      <Stack gap="sm">
        <Group gap="sm" wrap="wrap">
          <StatusBadge domain="severity" code={assessment.level} />
          <StatusBadge
            domain="dataQuality"
            code={assessment.uncertainty}
            tooltip="Confiança da leitura combinada: quantos sinais tinham baseline pessoal para comparar."
            suffix="de certeza"
          />
          {!assessment.allow_intensification && (
            <Tooltip label={assessment.intensification_blocked_reason ?? ''} multiline w={280}>
              <Badge color="red" variant="light" leftSection={<IconLock size={14} />}>
                Não é hora de intensificar
              </Badge>
            </Tooltip>
          )}
        </Group>

        <Text fw={600} c={visual.color === 'gray' ? BRAND.textPrimary : undefined}>
          {assessment.headline}
        </Text>

        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="sm">
          {READINESS_ORDER.map((code) => (
            <Group key={code} gap="xs" wrap="nowrap">
              <StatusBadge domain="readiness" code={code} />
              <Text size="sm" c={BRAND.textMuted}>
                {contagem[code] ?? 0} grupo(s)
              </Text>
            </Group>
          ))}
        </SimpleGrid>

        {assessment.explanation.length > 0 && (
          <List size="sm" spacing={4} c={BRAND.textMuted}>
            {assessment.explanation.map((linha) => (
              <List.Item key={linha}>{linha}</List.Item>
            ))}
          </List>
        )}

        <Text size="sm" c={BRAND.textMuted}>
          {reassessLabel(assessment.reassess_after_hours)}
        </Text>
      </Stack>
    </Card>
  );
}

function PainelDeSinais({
  titulo,
  dica,
  sinais,
}: {
  titulo: string;
  dica: string;
  sinais: RecoverySignal[];
}) {
  return (
    <Paper
      withBorder
      radius="lg"
      p="md"
      style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
    >
      <Stack gap="sm">
        <div>
          <Text fw={600} c={BRAND.textPrimary}>
            {titulo}
          </Text>
          <Text size="xs" c={BRAND.textMuted}>
            {dica}
          </Text>
        </div>

        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="sm">
          {sinais.map((sinal) => (
            <Card key={sinal.code} withBorder radius="md" p="sm" style={{ borderColor: BRAND.border }}>
              <Stack gap={6}>
                <Group justify="space-between" gap="xs" wrap="nowrap">
                  <Text fw={600} size="sm">
                    {sinal.label}
                  </Text>
                  <StatusBadge domain="signalReading" code={sinal.reading} size="xs" />
                </Group>

                <Group gap="lg" wrap="wrap">
                  <div>
                    <Text size="xs" c={BRAND.textMuted}>
                      No período
                    </Text>
                    <MetricValue metric={sinal.current} showNature={false} />
                  </div>
                  <div>
                    <Text size="xs" c={BRAND.textMuted}>
                      Sua baseline
                    </Text>
                    <MetricValue
                      metric={sinal.baseline}
                      showNature={false}
                      emptyHint={`Precisa de ${sinal.min_observations} pontos na janela de referência; há ${sinal.baseline_observations}.`}
                    />
                  </div>
                </Group>

                <Group gap="xs" wrap="wrap">
                  <StatusBadge domain="signalAgreement" code={sinal.agreement} size="xs" />
                  <Text size="xs" c={BRAND.textMuted}>
                    {sinal.observations} medida(s) no período
                  </Text>
                </Group>

                <Text size="xs" c={BRAND.textMuted}>
                  {sinal.description}
                </Text>

                {sinal.limitations.map((limitacao) => (
                  <Text key={limitacao} size="xs" c={BRAND.textMuted} fs="italic">
                    {limitacao}
                  </Text>
                ))}
              </Stack>
            </Card>
          ))}
        </SimpleGrid>
      </Stack>
    </Paper>
  );
}

/**
 * Mapa corporal (M33). O componente resolve `muscle_group -> slugs` sozinho;
 * aqui só entra a prontidão que o modelo já decidiu — nenhuma cor é escolhida
 * nesta tela (`statusHexScale`/`statusLegend` são o ponto único).
 */
function MapaCorporal({ grupos }: { grupos: RecoveryGroup[] }) {
  const itens = useMemo(() => bodyMapEntries(grupos), [grupos]);
  return (
    <Paper
      withBorder
      radius="lg"
      p="md"
      style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
    >
      <Stack gap="sm">
        <Group justify="space-between" wrap="wrap">
          <Text fw={600} c={BRAND.textPrimary}>
            Mapa corporal
          </Text>
          <Badge color="brand" variant="light">
            Toque em um grupo para ver o detalhe
          </Badge>
        </Group>
        <MuscleBodyMap items={itens} colors={RECOVERY_BODY_MAP_COLORS} legend={BODY_MAP_LEGEND} />
      </Stack>
    </Paper>
  );
}

/**
 * Sono, HRV e FC de repouso em dia COM treino × dia SEM treino (M36).
 *
 * Fica separado do painel de sinais de propósito: ali a comparação é com a
 * BASELINE PESSOAL e conta para a avaliação; aqui é um recorte descritivo que
 * não entra em nível nenhum. Lado sem medida aparece como ausência — nunca
 * como zero.
 */
function PainelTreinoVsDescanso({ bloco }: { bloco: TrainingVsRest }) {
  return (
    <Stack gap="sm">
      <Stack gap="xs">
        <Title order={3} size="h4" c={BRAND.textPrimary}>
          Dia com treino × dia sem treino
        </Title>
        <Text size="sm" c={BRAND.textMuted}>
          {bloco.training_days} dia(s) com treino e {bloco.rest_days} sem treino no período. Dois
          grupos observados não são um experimento: a diferença é associação, nunca causa.
        </Text>
      </Stack>

      {bloco.metrics.length === 0 ? (
        <EmptyState description="Nenhuma métrica com recorte por dia de treino nesta janela." />
      ) : (
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          {bloco.metrics.map((metrica) => (
            <Card
              key={metrica.code}
              withBorder
              radius="md"
              p="sm"
              style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
            >
              <Stack gap={6}>
                <Group justify="space-between" gap="xs" wrap="nowrap">
                  <Text fw={600} size="sm">
                    {metrica.label}
                  </Text>
                  <StatusBadge domain="signalReading" code={metrica.reading} size="xs" />
                </Group>

                <Group gap="lg" wrap="wrap">
                  <div>
                    <Text size="xs" c={BRAND.textMuted}>
                      Dia com treino
                    </Text>
                    <MetricValue metric={metrica.training_days} showNature={false} />
                    <Text size="xs" c={BRAND.textMuted}>
                      {metrica.training_observations} dia(s)
                    </Text>
                  </div>
                  <div>
                    <Text size="xs" c={BRAND.textMuted}>
                      Dia sem treino
                    </Text>
                    <MetricValue metric={metrica.rest_days} showNature={false} />
                    <Text size="xs" c={BRAND.textMuted}>
                      {metrica.rest_observations} dia(s)
                    </Text>
                  </div>
                </Group>

                <Text size="xs" c={BRAND.textMuted}>
                  {trainingVsRestLabel(metrica)}
                </Text>

                {metrica.limitations.map((limitacao) => (
                  <Text key={limitacao} size="xs" c={BRAND.textMuted} fs="italic">
                    {limitacao}
                  </Text>
                ))}
              </Stack>
            </Card>
          ))}
        </SimpleGrid>
      )}
    </Stack>
  );
}

/** Ícone de "de onde saiu este número" (M33). Sem fatores, não há ícone. */
function FatoresDoCalculo({ grupo }: { grupo: RecoveryGroup }) {
  const linhas = factorLines(grupo);
  if (linhas.length === 0) {
    return (
      <Text c={BRAND.textMuted} size="sm">
        {EM_DASH}
      </Text>
    );
  }
  return (
    <Tooltip label={linhas.join('\n')} multiline w={340} withArrow>
      <IconInfoCircle size={18} style={{ cursor: 'help', opacity: 0.7 }} color={BRAND.textMuted} />
    </Tooltip>
  );
}

/** Dica do cabeçalho "Bom para": separa a pergunta dela da pergunta de "Falta". */
const BOM_PARA_DICA =
  'Quando o modelo projeta o grupo como Pronto, o mesmo estado da coluna Estado. "Falta" é o tempo até a recuperação completa.';

/**
 * Tabela larga em fonte `xs` e sem quebra de linha: cada grupo cabe numa linha
 * só. Em tela estreita o `Table.ScrollContainer` rola na horizontal, sem
 * estourar a página. A "Recomendação" de séries saiu da tabela para abrir
 * espaço para "Bom para" e continua no cartão do celular.
 */
function TabelaDeGrupos({ grupos, timeZone }: { grupos: RecoveryGroup[]; timeZone: string }) {
  return (
    <>
      {/* Mobile: cartões empilhados (tabela larga não cabe em tela de telefone). */}
      <Stack gap="sm" hiddenFrom="sm">
        {grupos.map((grupo) => (
          <Card
            key={grupo.muscle_group_id ?? grupo.muscle_group}
            withBorder
            radius="md"
            p="sm"
            style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
          >
            <Stack gap={6}>
              <Group justify="space-between" wrap="nowrap">
                <Text fw={600}>{grupo.muscle_group}</Text>
                <StatusBadge domain="readiness" code={grupo.readiness_status} size="xs" />
              </Group>
              <Group gap="lg" wrap="wrap">
                <div>
                  <Text size="xs" c={BRAND.textMuted}>
                    Prontidão
                  </Text>
                  <MetricValue metric={grupo.recovery_pct} digits={0} />
                </div>
                <div>
                  <Text size="xs" c={BRAND.textMuted}>
                    Falta
                  </Text>
                  <MetricValue metric={grupo.remaining_hours} />
                </div>
                <div>
                  <Text size="xs" c={BRAND.textMuted}>
                    Bom para
                  </Text>
                  <Text size="sm">{readyAtLabel(grupo, timeZone)}</Text>
                </div>
              </Group>
              <div>
                <Text size="xs" c={BRAND.textMuted}>
                  Recomendação de séries
                </Text>
                <Text size="sm">{recommendationLabel(grupo)}</Text>
              </div>
              <Explicacao linhas={factorLines(grupo)} />
            </Stack>
          </Card>
        ))}
      </Stack>

      <Paper
        withBorder
        radius="lg"
        p="md"
        visibleFrom="sm"
        style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
      >
        <Table.ScrollContainer minWidth={960}>
          <Table highlightOnHover verticalSpacing="sm" fz="xs" style={{ whiteSpace: 'nowrap' }}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Grupo</Table.Th>
                <Table.Th>Prontidão</Table.Th>
                <Table.Th>Recuperação estimada</Table.Th>
                <Table.Th>Falta</Table.Th>
                <Table.Th title={BOM_PARA_DICA}>Bom para</Table.Th>
                <Table.Th>Desde o estímulo</Table.Th>
                <Table.Th>Última sessão</Table.Th>
                <Table.Th>Estado</Table.Th>
                <Table.Th>Fatores</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {grupos.map((grupo) => (
                <Table.Tr key={grupo.muscle_group_id ?? grupo.muscle_group}>
                  <Table.Td fw={600}>{grupo.muscle_group}</Table.Td>
                  <Table.Td>
                    <MetricValue metric={grupo.recovery_pct} digits={0} size="xs" />
                  </Table.Td>
                  <Table.Td>
                    <MetricValue metric={grupo.estimated_recovery_hours} size="xs" />
                  </Table.Td>
                  <Table.Td>
                    <MetricValue metric={grupo.remaining_hours} size="xs" />
                  </Table.Td>
                  <Table.Td>{readyAtLabel(grupo, timeZone)}</Table.Td>
                  <Table.Td>
                    <MetricValue metric={grupo.hours_since_last_stimulus} size="xs" />
                  </Table.Td>
                  <Table.Td>{grupo.last_session_at ? fmtDate(grupo.last_session_at) : EM_DASH}</Table.Td>
                  <Table.Td>
                    {/* O selo quebra linha de propósito (`BADGE_QUEBRA_LINHA`); aqui a
                        largura do conteúdo impede a tabela de espremê-lo: ela rola. */}
                    <Box w="max-content">
                      <StatusBadge
                        domain="readiness"
                        code={grupo.readiness_status}
                        tooltip={grupo.explanation.join('\n') || null}
                      />
                    </Box>
                  </Table.Td>
                  <Table.Td>
                    <FatoresDoCalculo grupo={grupo} />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>
    </>
  );
}

function Explicacao({ linhas }: { linhas: string[] }) {
  if (linhas.length === 0) return null;
  // Chave por posição: as linhas de `factorLines` podem repetir texto (duas
  // penalidades iguais, por exemplo), e a ordem é estável por construção.
  return (
    <List size="xs" spacing={2} c={BRAND.textMuted}>
      {linhas.map((linha, indice) => (
        <List.Item key={`${indice}-${linha}`}>{linha}</List.Item>
      ))}
    </List>
  );
}
