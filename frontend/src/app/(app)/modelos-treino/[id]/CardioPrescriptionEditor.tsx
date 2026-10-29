'use client';

import { Box, Group, SegmentedControl, Select, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'next/navigation';
import { apiFetch } from '@/lib/api';
import {
  CARDIO_FIELD_LABELS,
  CARDIO_LIMITS,
  CARDIO_MODE_OPTIONS,
  cardioDescribe,
  cardioMode,
  cardioModeChangePatch,
  cardioModeFields,
  cardioTotalSeconds,
  isCardioZoneField,
  pickCardioExtras,
  type CardioLimitedField,
  type CardioMode,
} from '@/lib/cardio-prescription';
import { hrZoneSelectData, hrZonesHint, useTemplateHrZones } from '@/lib/hr-zones';
import { formatRestSeconds } from '@/lib/template-time-estimate';
import { useDynamicRouteParam } from '@/lib/use-dynamic-route-param';
import type { ItemState } from '@/lib/workout-plan-template-form';
import { BRAND } from '@/theme';
import { BlurCommitNumberInput } from './BlurCommitInputs';
import { FIELD_GRID_STYLE } from './fieldGridStyle';

// Prescrição do item de cardio do Modelo (M98 C1): seletor Normal / HIIT e os
// campos do modo. Tudo vem do dono `domain/cardio_prescription.py`:
// - campos, rótulos (com a unidade), mínimos, tetos e padrões: JSON gerado;
// - tempo total e texto: espelho `lib/cardio-prescription.ts` (paridade);
// - validação: `POST /workout-plan-templates/cardio-prescription-check`, a
//   mesma regra do save, com a mensagem do dono no card;
// - faixa de bpm de cada zona: `GET /workout-plan-templates/hr-zones` (dono
//   `hr_zones`); de QUEM são as zonas (PT -> aluno do Modelo) é o backend que
//   decide.
// Sem equipamento.

type CardioPrescriptionEditorProps = {
  item: ItemState;
  onUpdate: (patch: Partial<ItemState>) => void;
};

/** Os campos da prescrição como o backend valida (o que vai no PUT). */
function prescriptionOf(item: ItemState) {
  return { cardio_duration_minutes: item.cardio_duration_minutes, ...pickCardioExtras(item) };
}

export function CardioPrescriptionEditor({ item, onUpdate }: CardioPrescriptionEditorProps) {
  const mode = cardioMode(item);
  const params = useParams<{ id: string }>();
  const routeId = useDynamicRouteParam(params?.id);
  const templateId = routeId && /^\d+$/.test(routeId) ? Number(routeId) : null;
  const zonesQuery = useTemplateHrZones(templateId, routeId !== null);
  const zones = zonesQuery.data?.zones ?? [];
  const zonesHint = zonesQuery.isSuccess ? hrZonesHint(zonesQuery.data) : null;
  const zoneData = hrZoneSelectData(zones);

  const prescription = prescriptionOf(item);
  const checkQuery = useQuery({
    queryKey: ['cardio-prescription-check', prescription],
    queryFn: () =>
      apiFetch<{ error: string | null }>('/workout-plan-templates/cardio-prescription-check', {
        method: 'POST',
        body: JSON.stringify(prescription),
      }),
    staleTime: Infinity,
    retry: false,
  });
  const ownerError = checkQuery.data?.error ?? null;

  function changeMode(next: string) {
    if (next === mode) return;
    onUpdate(cardioModeChangePatch(next as CardioMode));
  }

  function renderField(field: CardioLimitedField) {
    const label = CARDIO_FIELD_LABELS[field];
    const value = item[field];
    if (isCardioZoneField(field)) {
      return (
        <Select
          key={field}
          size="xs"
          label={label}
          placeholder="—"
          data={zoneData}
          value={value != null ? String(value) : null}
          onChange={(v) => onUpdate({ [field]: v ? Number(v) : null })}
          clearable={field === 'cardio_target_zone'}
          allowDeselect={false}
        />
      );
    }
    return (
      <BlurCommitNumberInput
        key={field}
        size="xs"
        label={label}
        value={value ?? undefined}
        onCommit={(v) => onUpdate({ [field]: typeof v === 'number' ? v : null })}
        min={CARDIO_LIMITS[field].min}
        max={CARDIO_LIMITS[field].max}
        allowDecimal={false}
        allowNegative={false}
      />
    );
  }

  const totalSeconds = Math.round(cardioTotalSeconds(item));
  const text = cardioDescribe(item);

  return (
    <Stack gap="xs">
      <SegmentedControl
        size="xs"
        data={CARDIO_MODE_OPTIONS}
        value={mode}
        onChange={changeMode}
        aria-label="Tipo de cardio"
        w="fit-content"
      />
      <Box style={FIELD_GRID_STYLE}>{cardioModeFields(mode).map(renderField)}</Box>
      <Group gap="xs" wrap="wrap">
        <Text size="xs" c={BRAND.textMuted}>
          Tempo total:{' '}
          <Text span fw={700} c={BRAND.textPrimary}>
            {formatRestSeconds(totalSeconds)}
          </Text>
        </Text>
        {text && (
          <Text size="xs" c={BRAND.textMuted}>
            · {text}
          </Text>
        )}
      </Group>
      {zonesHint && (
        <Text size="xs" c={BRAND.textMuted}>
          {zonesHint}
        </Text>
      )}
      {ownerError && (
        <Text size="xs" c="red" role="alert">
          {ownerError}
        </Text>
      )}
    </Stack>
  );
}
