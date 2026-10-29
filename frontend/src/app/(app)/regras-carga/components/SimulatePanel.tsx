'use client';

import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  NumberInput,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconFlask } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { apiFetch } from '@/lib/api';
import {
  EMPTY_SIMULATE_FORM,
  FATIGUE_BAND_OPTIONS,
  RPE_VALUE_BOUNDS,
  ruleSetErrorMessages,
  simulateFacts,
  simulateMissing,
  toDraftNumber,
  type DraftNumber,
  type SimulateForm,
} from '@/lib/next-set-load-rules';
import type { RuleSetInput, SimulatePayload, SimulateResponse } from '@/lib/next-set-load-rules-types';
import { nextSetLoadView } from '@/lib/set-engine-status';
import { BRAND } from '@/theme';

type Target = 'draft' | 'published';

function simulatedWith(data: SimulateResponse | undefined): string {
  if (!data) return '';
  if (data.source === 'draft') return 'Simulado com o rascunho do editor.';
  if (data.source === 'default') return 'Simulado com as regras padrão do sistema.';
  return `Simulado com as regras vigentes (v${data.ruleset_version}).`;
}

function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  decimal = false,
  description,
}: {
  label: string;
  value: DraftNumber;
  onChange: (value: DraftNumber) => void;
  min?: number;
  max?: number;
  decimal?: boolean;
  description?: string;
}) {
  return (
    <NumberInput
      label={label}
      description={description}
      min={min}
      max={max}
      decimalScale={decimal ? 2 : 0}
      decimalSeparator=","
      value={value}
      onChange={(raw) => onChange(toDraftNumber(raw))}
    />
  );
}

/**
 * "Simular uma série": manda os fatos e o conjunto (rascunho do editor ou o
 * vigente) para `POST /simulate`; o resultado é o do dono, nada é avaliado aqui.
 */
export function SimulatePanel({ draftPayload }: { draftPayload: RuleSetInput | null }) {
  const [form, setForm] = useState<SimulateForm>(EMPTY_SIMULATE_FORM);
  const [target, setTarget] = useState<Target>('draft');
  const set = (patch: Partial<SimulateForm>) => setForm((current) => ({ ...current, ...patch }));

  const mutation = useMutation({
    mutationFn: (payload: SimulatePayload) =>
      apiFetch<SimulateResponse>('/next-set-load-rules/simulate', { method: 'POST', body: JSON.stringify(payload) }),
  });

  const missing = simulateMissing(form);
  const draftUnavailable = target === 'draft' && draftPayload === null;
  const result = mutation.data?.result ?? null;
  const view = nextSetLoadView(result);

  return (
    <Paper withBorder radius="md" p="md">
      <Stack gap="sm">
        <div>
          <Title order={4}>Simular uma série</Title>
          <Text size="sm" c={BRAND.textMuted}>
            Informe uma série de trabalho e veja a carga que o motor sugeriria para a próxima.
          </Text>
        </div>
        <SegmentedControl
          w="fit-content"
          size="xs"
          value={target}
          onChange={(value) => setTarget(value as Target)}
          data={[
            { value: 'draft', label: 'Com o rascunho do editor' },
            { value: 'published', label: 'Com as regras vigentes' },
          ]}
        />
        <SimpleGrid cols={{ base: 2, sm: 3, md: 5 }} spacing="sm">
          <NumberField
            label="Série nº"
            description="entre as de trabalho"
            min={1}
            value={form.working_set_index}
            onChange={(working_set_index) => set({ working_set_index })}
          />
          <Select
            label="Fadiga ao começar o exercício"
            data={FATIGUE_BAND_OPTIONS}
            value={form.fatigue_band}
            allowDeselect={false}
            onChange={(value) => value && set({ fatigue_band: value })}
          />
          <NumberField label="Reps" min={0} value={form.reps} onChange={(reps) => set({ reps })} />
          <NumberField
            label="RPE"
            min={RPE_VALUE_BOUNDS.min}
            max={RPE_VALUE_BOUNDS.max}
            decimal
            value={form.rpe}
            onChange={(rpe) => set({ rpe })}
          />
          <NumberField
            label="Teto de RPE do dia"
            min={RPE_VALUE_BOUNDS.min}
            max={RPE_VALUE_BOUNDS.max}
            decimal
            value={form.rpe_ceiling}
            onChange={(rpe_ceiling) => set({ rpe_ceiling })}
          />
          <NumberField
            label="RPE planejado (opcional)"
            description="rampa da ficha"
            min={RPE_VALUE_BOUNDS.min}
            max={RPE_VALUE_BOUNDS.max}
            decimal
            value={form.planned_rpe}
            onChange={(planned_rpe) => set({ planned_rpe })}
          />
          <NumberField label="Carga (kg)" min={0} decimal value={form.weight_kg} onChange={(weight_kg) => set({ weight_kg })} />
          <NumberField
            label="Passo do equipamento (kg)"
            min={0}
            decimal
            value={form.step_kg}
            onChange={(step_kg) => set({ step_kg })}
          />
          <NumberField
            label="Dor muscular (0-10)"
            min={0}
            max={10}
            value={form.muscle_pain}
            onChange={(muscle_pain) => set({ muscle_pain })}
          />
          <Checkbox
            mt="xl"
            label="Dor articular"
            checked={form.joint_pain}
            onChange={(event) => set({ joint_pain: event.currentTarget.checked })}
          />
        </SimpleGrid>
        <Group gap="sm">
          <Button
            leftSection={<IconFlask size={16} />}
            color="brand"
            loading={mutation.isPending}
            disabled={missing.length > 0 || draftUnavailable}
            onClick={() =>
              mutation.mutate({ ruleset: target === 'draft' ? draftPayload : null, facts: simulateFacts(form) })
            }
          >
            Simular
          </Button>
          {missing.length > 0 && (
            <Text size="xs" c={BRAND.textMuted}>
              Preencha: {missing.join(', ')}.
            </Text>
          )}
          {draftUnavailable && missing.length === 0 && (
            <Text size="xs" c={BRAND.textMuted}>
              Complete os campos do rascunho para simular com ele.
            </Text>
          )}
        </Group>
        {mutation.isError && (
          <Alert color="red" title="Não foi possível simular">
            {ruleSetErrorMessages(mutation.error, 'Erro ao simular.').map((message) => (
              <Text key={message} size="sm">
                {message}
              </Text>
            ))}
          </Alert>
        )}
        {mutation.isSuccess && (
          <Paper withBorder radius="sm" p="sm" data-simulate-result>
            {view && result ? (
              <Stack gap={4}>
                <Group gap="xs">
                  <Badge color={view.badge.color} variant="light" style={{ textTransform: 'none' }}>
                    {view.badge.label}
                  </Badge>
                  <Text size="sm" fw={600}>
                    {view.text}
                  </Text>
                </Group>
                <Text size="sm">
                  {result.rule_position != null
                    ? `Regra que casou: nº ${result.rule_position}. ${result.rule_text ?? ''}`
                    : `Nenhuma regra casou: ${view.reason ?? ''}`}
                </Text>
                <Text size="xs" c={BRAND.textMuted}>
                  {simulatedWith(mutation.data)}
                </Text>
              </Stack>
            ) : (
              <Text size="sm">
                Fora da regra: o motor não sugere carga para esta série (sem carga ou passo, ou reps inválidas).
              </Text>
            )}
          </Paper>
        )}
      </Stack>
    </Paper>
  );
}
