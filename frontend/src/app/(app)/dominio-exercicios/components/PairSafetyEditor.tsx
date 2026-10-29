'use client';

import { Alert, Badge, Button, Group, Loader, Paper, SegmentedControl, Select, Stack, Text, Textarea } from '@mantine/core';
import { IconAlertTriangle, IconDeviceFloppy } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import {
  MAX_SAFE_RPE_OPTIONS,
  PAIR_TECHNIQUE_CHOICES,
  SAFETY_NOTES_MAX_LENGTH,
  buildPairSafetyPayload,
  describeInheritedTechnique,
  describePairMaxSafeRpe,
  equipmentExerciseSafetyPath,
  equipmentExerciseSafetyQueryKey,
  pairSafetyToForm,
  selectValueToRpe,
  type EquipmentExerciseSafety,
  type EquipmentExerciseSafetyPayload,
  type PairSafetyForm,
  type PairTechniqueChoice,
} from '@/lib/training-safety';
import { useAdvancedTechniques } from '@/lib/use-advanced-techniques';
import { BRAND } from '@/theme';

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError && error.status === 403) return 'Ação restrita a administradores.';
  return error instanceof ApiError ? error.message : fallback;
}

/**
 * Override de segurança de UM par exercício × equipamento
 * (`GET/PUT /equipment-exercises/{id}/safety`, admin). `linkId` é o id
 * canônico de `equipment_exercises`. Mostra sempre o valor herdado do
 * equipamento ao lado do sobrescrito; o "efetivo" vem do resolvedor da API.
 */
export function PairSafetyEditor({ linkId, onClose }: { linkId: number; onClose: () => void }) {
  const qc = useQueryClient();
  const { items: catalog } = useAdvancedTechniques();
  const techniques = useMemo(() => catalog.filter((item) => item.is_active), [catalog]);

  const safetyQuery = useQuery({
    queryKey: equipmentExerciseSafetyQueryKey(linkId),
    queryFn: () => apiFetch<EquipmentExerciseSafety>(equipmentExerciseSafetyPath(linkId)),
  });
  const saved = safetyQuery.data;

  const [form, setForm] = useState<PairSafetyForm | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  useEffect(() => {
    if (saved) setForm(pairSafetyToForm(saved, techniques));
  }, [saved, techniques]);

  const saveMutation = useMutation({
    mutationFn: (payload: EquipmentExerciseSafetyPayload) =>
      apiFetch<EquipmentExerciseSafety>(equipmentExerciseSafetyPath(linkId), { method: 'PUT', body: JSON.stringify(payload) }),
    onSuccess: (result) => qc.setQueryData(equipmentExerciseSafetyQueryKey(linkId), result),
  });

  function save() {
    if (!form || !saved) return;
    const result = buildPairSafetyPayload(form, saved);
    setFormError(result.error);
    if (result.payload) saveMutation.mutate(result.payload);
  }

  return (
    <Paper withBorder radius="md" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      {safetyQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text size="sm" c={BRAND.textMuted}>
            Carregando segurança do par...
          </Text>
        </Group>
      )}
      {safetyQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(safetyQuery.error, 'Não foi possível carregar a segurança deste par.')}
        </Alert>
      )}
      {form && saved && (
        <Stack gap="sm">
          <Group gap="xs" wrap="wrap">
            <Text size="sm" fw={500} c={BRAND.textPrimary}>
              Efetivo neste par:
            </Text>
            <Badge color={saved.resolved.failure_allowed ? 'green' : 'blue'} variant="light">
              RPE máx. {saved.resolved.max_safe_rpe}
              {saved.resolved.failure_allowed ? ' · falha permitida' : ' · sem falha'}
            </Badge>
            {saved.resolved.allowed_advanced_techniques.map((technique) => (
              <Badge key={technique.code} color="grape" variant="light">
                {techniques.find((item) => item.code === technique.code)?.name ?? technique.code}
              </Badge>
            ))}
            {saved.resolved.blocked_advanced_techniques.map((technique) => (
              <Badge key={technique.code} color="red" variant="light" title="Exige falha e este par não permite RPE 10">
                {techniques.find((item) => item.code === technique.code)?.name ?? technique.code} (bloqueada: exige falha)
              </Badge>
            ))}
          </Group>

          <Select
            label="RPE máximo seguro neste par"
            description={`Herdado: ${describePairMaxSafeRpe(null, saved.inherited.max_safe_rpe).label}. 10 = falha concêntrica permitida.`}
            placeholder="Herdar do equipamento"
            data={MAX_SAFE_RPE_OPTIONS}
            value={form.maxSafeRpeOverride}
            onChange={(value) => setForm({ ...form, maxSafeRpeOverride: value })}
            clearable
            w={320}
          />
          <Text size="xs" c={BRAND.textMuted}>
            Valor em uso: {describePairMaxSafeRpe(selectValueToRpe(form.maxSafeRpeOverride), saved.inherited.max_safe_rpe).label}
          </Text>

          <Stack gap={6}>
            <Text size="sm" fw={500} c={BRAND.textPrimary}>
              Técnicas avançadas neste par
            </Text>
            {techniques.map((technique) => (
              <Group key={technique.code} justify="space-between" wrap="wrap" gap="xs">
                <div>
                  <Text size="sm" c={BRAND.textPrimary}>
                    {technique.name}
                  </Text>
                  <Text size="xs" c={BRAND.textMuted}>
                    Herdado: {describeInheritedTechnique(technique.code, saved.inherited.techniques)}
                  </Text>
                </div>
                <SegmentedControl
                  size="xs"
                  color="brand"
                  data={PAIR_TECHNIQUE_CHOICES}
                  value={form.choices[technique.code] ?? 'inherit'}
                  onChange={(value) =>
                    setForm({ ...form, choices: { ...form.choices, [technique.code]: value as PairTechniqueChoice } })
                  }
                />
              </Group>
            ))}
          </Stack>

          <Textarea
            label="Notas de segurança do par"
            autosize
            minRows={2}
            maxLength={SAFETY_NOTES_MAX_LENGTH}
            value={form.safetyNotes}
            onChange={(event) => setForm({ ...form, safetyNotes: event.currentTarget.value })}
          />

          {(formError || saveMutation.isError) && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {formError ?? errorMessage(saveMutation.error, 'Não foi possível salvar o override deste par.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" size="xs" onClick={onClose}>
              Fechar
            </Button>
            <Button color="brand" size="xs" leftSection={<IconDeviceFloppy size={14} />} onClick={save} loading={saveMutation.isPending}>
              Salvar override
            </Button>
          </Group>
        </Stack>
      )}
    </Paper>
  );
}
