'use client';

import { Alert, Badge, Group, Loader, Modal, Rating, Stack, Text, Tooltip, UnstyledButton } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconAlertTriangle, IconCheck } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { apiFetch, ApiError } from '@/lib/api';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import { getAdvancedTechniqueLabel } from '@/lib/advanced-techniques';
import { MAX_PREFERENCE_STARS, starsLabel } from '@/lib/equipment-recommend';
import {
  equipmentOptionReasonLabel,
  planExerciseEquipmentOptionsPath,
  planExerciseSubstituteEquipmentPath,
  type PlanExerciseEquipmentOptionsResponse,
  type SubstituteEquipmentResponse,
  type WorkoutPlanExercise,
} from '@/lib/workout-plans-types';
import { BRAND } from '@/theme';

type Props = {
  opened: boolean;
  onClose: () => void;
  planId: number;
  // Linha SALVA da ficha (precisa de `workout_plan_exercise_id`).
  exercise: WorkoutPlanExercise | null;
  // Chamado com a linha já relida do servidor (prescrição recongelada).
  onApplied: (exercise: WorkoutPlanExercise) => void;
};

/**
 * M8: troca de EQUIPAMENTO de um exercício numa ficha já salva, na própria
 * tela da ficha — vale para qualquer origem (Modo A/B, legado, prescrição
 * NULL). Só equipamento: o exercício e o grupo muscular não mudam.
 *
 * A lista vem de `GET .../equipment-options` (só associações da academia da
 * ficha para o MESMO exercício, no ranking do usuário, com elegibilidade
 * D13a calculada no servidor). A escolha vai em `POST
 * .../substitute-equipment`, e o servidor grava o vínculo e recongela a
 * prescrição da linha — o cliente só reflete a linha devolvida.
 */
export function EquipmentSwapModal({ opened, onClose, planId, exercise, onApplied }: Props) {
  const isMobile = useMediaQuery('(max-width: 48em)');
  const exerciseId = exercise?.workout_plan_exercise_id ?? null;

  const optionsQuery = useQuery({
    queryKey: ['plan-exercise-equipment-options', planId, exerciseId],
    queryFn: () =>
      apiFetch<PlanExerciseEquipmentOptionsResponse>(planExerciseEquipmentOptionsPath(planId, exerciseId as number)),
    enabled: opened && exerciseId != null,
  });

  const swapMutation = useMutation({
    mutationFn: (gymEquipmentId: number) =>
      apiFetch<SubstituteEquipmentResponse>(planExerciseSubstituteEquipmentPath(planId, exerciseId as number), {
        method: 'POST',
        body: JSON.stringify({ gym_equipment_id: gymEquipmentId }),
      }),
    onSuccess: (result) => {
      if (result.exercise) onApplied(result.exercise);
      onClose();
    },
  });

  const data = optionsQuery.data;
  const items = data?.items ?? [];
  const techniqueName = data?.required_technique
    ? getAdvancedTechniqueLabel(data.required_technique) ?? data.required_technique
    : null;

  function handleClose() {
    if (swapMutation.isPending) return;
    swapMutation.reset();
    onClose();
  }

  return (
    <Modal
      opened={opened}
      onClose={handleClose}
      title={exercise ? `Trocar equipamento — ${exercise.exercise_name}` : 'Trocar equipamento'}
      size="md"
      centered={!isMobile}
      fullScreen={isMobile}
      radius={isMobile ? 0 : 'xl'}
    >
      <Stack gap="md">
        <Text size="sm" c={BRAND.textMuted}>
          Só o equipamento muda: o exercício e o grupo muscular continuam os mesmos. A prescrição desta linha é
          recalculada e congelada de novo pelo servidor com o equipamento escolhido: a carga e a progressão passam a
          vir do seu histórico nesse equipamento (sem histórico, a carga fica em branco).
        </Text>

        {techniqueName && (
          <Text size="xs" c={BRAND.textMuted}>
            Técnica prescrita nesta linha: <b>{techniqueName}</b> — equipamentos incompatíveis ficam indisponíveis.
          </Text>
        )}

        {optionsQuery.isLoading && <Loader size="sm" color="brand" />}
        {optionsQuery.isError && (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
            <Text size="sm">
              {optionsQuery.error instanceof ApiError
                ? optionsQuery.error.message
                : 'Não foi possível carregar os equipamentos desta academia.'}
            </Text>
          </Alert>
        )}

        {swapMutation.isError && (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
            <Text size="sm">
              {swapMutation.error instanceof ApiError ? swapMutation.error.message : 'Não foi possível trocar o equipamento.'}
            </Text>
          </Alert>
        )}

        {swapMutation.isPending && (
          <Group gap="xs">
            <Loader size="xs" color="brand" />
            <Text size="sm" c={BRAND.textMuted}>
              Recalculando a prescrição com o novo equipamento...
            </Text>
          </Group>
        )}

        <Stack gap="xs">
          {items.map((item) => {
            const disabled = item.is_current || !item.eligible || swapMutation.isPending;
            const reason = !item.eligible ? equipmentOptionReasonLabel(item.reason, data?.current_max_safe_rpe ?? null) : '';
            const button = (
              <UnstyledButton
                key={item.gym_equipment_id}
                p="sm"
                disabled={disabled}
                aria-label={`Trocar para ${item.equipment_name ?? 'equipamento'}`}
                style={{
                  borderRadius: 'var(--mantine-radius-xl)',
                  border: `1px solid ${item.is_current ? BRAND.orange : BRAND.border}`,
                  opacity: disabled && !item.is_current ? 0.5 : 1,
                  width: '100%',
                }}
                onClick={() => !disabled && swapMutation.mutate(item.gym_equipment_id)}
              >
                <Group gap="sm" wrap="nowrap" align="center">
                  <EquipmentPhoto photoUrl={item.photo_url} alt={item.equipment_name ?? ''} size={56} />
                  <div style={{ flex: 1 }}>
                    <Group gap={6}>
                      <Text fw={700} c={BRAND.textPrimary}>
                        {item.equipment_name}
                      </Text>
                      {item.is_current && (
                        <Badge size="xs" color="orange" variant="filled" leftSection={<IconCheck size={10} />}>
                          atual
                        </Badge>
                      )}
                      {!item.eligible && (
                        <Badge size="xs" color="yellow" variant="light">
                          indisponível
                        </Badge>
                      )}
                    </Group>
                    <Group gap={8} mt={2}>
                      {item.max_safe_rpe != null && (
                        <Text size="xs" c={BRAND.textMuted}>
                          Teto RPE {item.max_safe_rpe}
                          {item.failure_allowed ? ' (falha permitida)' : ''}
                        </Text>
                      )}
                      {item.user_preference_stars != null && (
                        <Group gap={4} wrap="nowrap" aria-label={`Sua nota: ${starsLabel(item.user_preference_stars)}`}>
                          <Rating value={item.user_preference_stars} count={MAX_PREFERENCE_STARS} size="xs" color="orange" readOnly />
                        </Group>
                      )}
                    </Group>
                    {reason && (
                      <Text size="xs" c="yellow" mt={2}>
                        {reason}
                      </Text>
                    )}
                  </div>
                </Group>
              </UnstyledButton>
            );
            return reason ? (
              <Tooltip key={item.gym_equipment_id} label={reason} multiline w={260}>
                {button}
              </Tooltip>
            ) : (
              button
            );
          })}
          {optionsQuery.isSuccess && items.length === 0 && (
            <Text size="sm" c={BRAND.textMuted} ta="center" py="md">
              Esta academia não tem nenhum equipamento cadastrado para este exercício.
            </Text>
          )}
        </Stack>
      </Stack>
    </Modal>
  );
}
