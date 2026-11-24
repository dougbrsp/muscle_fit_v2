'use client';

import { Alert, Badge, Group, List, Loader, Paper, Stack, Text } from '@mantine/core';
import { useEffect, useState } from 'react';
import {
  fetchRedundancyDiagnostic,
  type RedundancyDiagnostic,
  type RedundancyDiagnosticPair,
} from '@/lib/exercise-redundancy-api';
import { BRAND } from '@/theme';

export type DiagnosticState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ready'; data: RedundancyDiagnostic };

export type FetchDiagnostic = typeof fetchRedundancyDiagnostic;

/** Carrega sob demanda; erro vira estado (não derruba a tela). */
export async function loadDiagnostic(fetcher: FetchDiagnostic, templateId: number, signal?: AbortSignal): Promise<DiagnosticState | null> {
  try {
    return { status: 'ready', data: await fetcher(templateId, signal) };
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') return null;
    return { status: 'error' };
  }
}

// Usa o nome vindo da API; sem nome, identifica pelo mapeamento do catálogo.
const itemLabel = (name: string | null | undefined, itemId: number | null, mappingId: number) =>
  name?.trim() || `Exercício (mapeamento ${mappingId}${itemId != null ? `, item ${itemId}` : ''})`;

function List2({ title, values }: { title: string; values: string[] }) {
  if (values.length === 0) return null;
  return (
    <div>
      <Text size="xs" fw={600}>
        {title}
      </Text>
      <List size="xs" spacing={0}>
        {values.map((v) => (
          <List.Item key={v}>{v}</List.Item>
        ))}
      </List>
    </div>
  );
}

function PairRow({ pair }: { pair: RedundancyDiagnosticPair }) {
  return (
    <Paper withBorder p="xs">
      <Stack gap={4}>
        <Group gap="xs">
          <Badge size="sm" variant="light" color="yellow">
            {pair.level_label}
          </Badge>
          <Text size="sm">
            {itemLabel(pair.a_exercise_name, pair.a_item_id, pair.a_mapping_id)} e {itemLabel(pair.b_exercise_name, pair.b_item_id, pair.b_mapping_id)}
          </Text>
        </Group>
        {pair.reasons.length > 0 && (
          <Text size="xs" c={BRAND.textMuted}>
            {pair.reasons.map((r) => r.label).join(' · ')}
          </Text>
        )}
        <Group gap="lg" align="flex-start" wrap="wrap">
          <List2 title="Igual" values={pair.matching} />
          <List2 title="Muda" values={pair.diverging} />
        </Group>
      </Stack>
    </Paper>
  );
}

export function RedundancyDiagnosticView({ state }: { state: DiagnosticState }) {
  if (state.status === 'loading') {
    return (
      <Group justify="center" py="md" role="status">
        <Loader size="sm" />
      </Group>
    );
  }
  if (state.status === 'error') {
    return (
      <Alert color="red" variant="light" role="alert">
        Não foi possível carregar o diagnóstico de redundância.
      </Alert>
    );
  }
  const slots = state.data.slots.filter((s) => s.pairs.length > 0);
  if (slots.length === 0) {
    return (
      <Text size="sm" c={BRAND.textMuted} role="status">
        Nenhum par redundante encontrado. Aparecem os pares de redundância moderada ou maior. Moderada e alta exigem perfis de resistência revisados; exercícios repetidos no mesmo dia aparecem sempre.
      </Text>
    );
  }
  return (
    <Stack gap="md">
      {slots.map((s, i) => (
        <Stack key={s.slot_id ?? i} gap="xs">
          <Text fw={600}>{s.name || `Dia ${s.sequence ?? i + 1}`}</Text>
          {s.pairs.map((p, j) => (
            <PairRow key={`${p.a_mapping_id}-${p.b_mapping_id}-${j}`} pair={p} />
          ))}
        </Stack>
      ))}
    </Stack>
  );
}

/** Somente leitura: não altera o Modelo. Consulta a versão vigente salva, ao abrir. */
export function RedundancyDiagnosticPanel({
  templateId,
  fetcher = fetchRedundancyDiagnostic,
}: {
  templateId: number;
  fetcher?: FetchDiagnostic;
}) {
  const [state, setState] = useState<DiagnosticState>({ status: 'loading' });
  useEffect(() => {
    const ctrl = new AbortController();
    void loadDiagnostic(fetcher, templateId, ctrl.signal).then((s) => {
      if (s && !ctrl.signal.aborted) setState(s);
    });
    return () => ctrl.abort();
  }, [fetcher, templateId]);
  return (
    <Paper withBorder p="md">
      <Stack gap="sm">
        <Text fw={600}>Diagnóstico de redundância</Text>
        <Text size="xs" c={BRAND.textMuted}>
          Mostra a versão salva do Modelo; alterações ainda não salvas não entram.
        </Text>
        <RedundancyDiagnosticView state={state} />
      </Stack>
    </Paper>
  );
}
