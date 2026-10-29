'use client';

import { Alert, Badge, Button, Group, Loader, Modal, Rating, Stack, Text, UnstyledButton } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconAlertTriangle, IconSparkles } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import { getAdvancedTechniqueLabel } from '@/lib/advanced-techniques';
import {
  buildRecommendPath,
  EQUIPMENT_EXERCISE_PREFERENCES_PATH,
  EQUIPMENT_EXERCISE_PREFERENCES_QUERY_KEY,
  exclusionByLinkId,
  exclusionReasonLabel,
  MAX_PREFERENCE_STARS,
  pairKey,
  starsByPairKey,
  starsLabel,
  type EquipmentExercisePreferencesResponse,
} from '@/lib/equipment-recommend';
import type {
  ApiList,
  GymEquipmentLink,
  GymEquipmentRecommendResponse,
} from '@/lib/workout-mode-types';
import { BRAND } from '@/theme';

export type EquipmentSubstituteChoice = {
  mapping_id: number;
  equipment_link_id: number;
  exercise_name: string;
  equipment_name: string;
};

type Props = {
  opened: boolean;
  onClose: () => void;
  gymId?: number | null;
  mappingId?: number | null;
  primaryGroup?: string | null;
  // Id da ASSOCIAÇÃO atual (`gym_equipment.id`, o mesmo valor gravado em
  // `exercises.equipment_id`) a excluir das sugestões — não o id do
  // equipamento no catálogo.
  currentEquipmentLinkId?: number | null;
  // Técnica avançada PRESCRITA no exercício atual da ficha (código do
  // catálogo). Quando presente vai como `required_technique`: o backend tira
  // da sugestão os equipamentos incompatíveis e os devolve em `excluded[]`
  // com o motivo, exibido abaixo.
  requiredTechnique?: string | null;
  // true quando o exercício está TRAVADO por `requires_user_choice` (D13,
  // caminho b): o equipamento da ficha não pôde ser resolvido nesta academia
  // e a escolha aqui é obrigatória para poder iniciar o exercício. Só muda o
  // título e o texto de apoio — a mecânica de escolha é a mesma da troca.
  mandatoryChoice?: boolean;
  onSelect: (choice: EquipmentSubstituteChoice) => void;
  // true enquanto a escolha está sendo processada pelo chamador (ex.:
  // aguardando `GET /exercises/suggested-progression` do novo mapping_id) —
  // trava novos cliques para não disparar `onSelect` duas vezes.
  submitting?: boolean;
};

/**
 * Modal de troca de equipamento/exercício em pleno treino
 * (`PlanExecutionScreen`). Duas fontes, ambas restritas ao grupo muscular do
 * exercício atual para não desviar o estímulo da ficha:
 *
 * - "Sugerir substituto": `GET /gym-equipment/recommend` com `mapping_id` e
 *   `exclude_equipment_id` — deliberadamente NÃO `/recommend-all`, que não
 *   aceita exclusão do equipamento atual e traria todos os grupos (o próprio
 *   backend documenta `/recommend` como o endpoint do caso "trocar um
 *   equipamento ocupado durante o treino").
 * - lista completa dos equipamentos daquele grupo nesta academia
 *   (`GET /gym-equipment?gym_id&primary_group&active_only=true`), mesmo
 *   endpoint do passo 2 de `WorkoutDetailClient.tsx`.
 *
 * O recálculo da meta de séries (`GET /exercises/suggested-set-count-for-substitution`)
 * NÃO acontece aqui: fica em `PlanExecutionScreen`, que é quem conhece o
 * `sets_count` original do item da ficha e quantas séries já foram lançadas.
 */
export function EquipmentSubstituteModal({
  opened,
  onClose,
  gymId,
  mappingId,
  primaryGroup,
  currentEquipmentLinkId,
  requiredTechnique = null,
  mandatoryChoice = false,
  onSelect,
  submitting = false,
}: Props) {
  const isMobile = useMediaQuery('(max-width: 48em)');

  const catalogQuery = useQuery({
    queryKey: ['workout-mode-substitute-catalog', gymId, primaryGroup],
    queryFn: () =>
      apiFetch<ApiList<GymEquipmentLink>>(
        `/gym-equipment?gym_id=${gymId}&primary_group=${encodeURIComponent(primaryGroup || '')}&active_only=true`,
      ),
    enabled: opened && Boolean(gymId && primaryGroup),
  });

  // Sob demanda (`enabled: false` + `refetch()`): a recomendação é uma ação
  // explícita do usuário, não deve custar uma chamada a cada abertura do modal.
  const recommendQuery = useQuery({
    queryKey: ['workout-mode-substitute-recommend', gymId, mappingId, currentEquipmentLinkId, requiredTechnique],
    queryFn: () =>
      apiFetch<GymEquipmentRecommendResponse>(
        buildRecommendPath({
          gymId: gymId as number,
          mappingId,
          excludeEquipmentLinkId: currentEquipmentLinkId,
          requiredTechnique,
        }),
      ),
    enabled: false,
  });

  // Estrelas do usuário para o PAR exercício+equipamento (mesma query do
  // Preferências de Treino, onde a nota é dada): só exibição — a ORDEM da sugestão já vem do backend. Falha
  // aqui não atrapalha a troca: a lista aparece sem estrelas.
  const preferencesQuery = useQuery({
    queryKey: EQUIPMENT_EXERCISE_PREFERENCES_QUERY_KEY,
    queryFn: () => apiFetch<EquipmentExercisePreferencesResponse>(EQUIPMENT_EXERCISE_PREFERENCES_PATH),
    enabled: opened,
    staleTime: 60_000,
    retry: false,
  });
  const stars = starsByPairKey(preferencesQuery.data?.items);

  const recommended = recommendQuery.data?.recommended ?? null;
  const excluded = recommendQuery.data?.excluded ?? [];
  const excludedByLinkId = exclusionByLinkId(excluded);
  const techniqueName = getAdvancedTechniqueLabel(requiredTechnique) ?? requiredTechnique;
  const items = (catalogQuery.data?.items || []).filter((item) => item.id !== currentEquipmentLinkId);

  function choose(choice: EquipmentSubstituteChoice) {
    if (submitting) return;
    onSelect(choice);
  }

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={mandatoryChoice ? 'Escolher equipamento' : 'Trocar equipamento/exercício'}
      size="md"
      centered={!isMobile}
      fullScreen={isMobile}
      radius={isMobile ? 0 : 'xl'}
    >
      <Stack gap="md">
        {mandatoryChoice && (
          <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />}>
            <Text size="sm">
              A ficha não definiu um equipamento desta academia para este exercício. Escolha um abaixo para poder
              iniciar — a escolha vale só para este treino.
            </Text>
          </Alert>
        )}

        {submitting && (
          <Group gap="xs">
            <Loader size="xs" color="brand" />
            <Text size="sm" c={BRAND.textMuted}>
              Recalculando meta de séries e carga para o novo equipamento...
            </Text>
          </Group>
        )}

        <Button
          size="md"
          radius="xl"
          h={48}
          variant="light"
          leftSection={<IconSparkles size={18} />}
          loading={recommendQuery.isFetching}
          disabled={!gymId || !mappingId || submitting}
          onClick={() => recommendQuery.refetch()}
        >
          Sugerir substituto
        </Button>

        {recommendQuery.isError && (
          <Alert color="gray" variant="light" icon={<IconAlertTriangle size={16} />} py={6}>
            <Text size="xs" c={BRAND.textMuted}>
              Não foi possível calcular uma sugestão agora. Escolha manualmente na lista abaixo.
            </Text>
          </Alert>
        )}

        {recommendQuery.data && !recommendQuery.data.has_equipment_for_group && (
          <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />}>
            <Text size="sm">Esta academia não tem equipamento cadastrado para este grupo muscular.</Text>
          </Alert>
        )}

        {recommendQuery.data && recommendQuery.data.has_equipment_for_group && !recommended && (
          <Alert color="gray" variant="light" icon={<IconAlertTriangle size={16} />} py={6}>
            <Text size="xs" c={BRAND.textMuted}>
              Nenhuma alternativa além do equipamento atual está cadastrada para este grupo nesta academia.
            </Text>
          </Alert>
        )}

        {recommended && (
          <UnstyledButton
            p="sm"
            disabled={submitting}
            style={{
              borderRadius: 'var(--mantine-radius-xl)',
              border: `1px solid ${BRAND.orange}`,
              opacity: submitting ? 0.6 : 1,
            }}
            onClick={() =>
              choose({
                mapping_id: recommended.mapping_id,
                equipment_link_id: recommended.id,
                exercise_name: recommended.mapping_exercise_name,
                equipment_name: recommended.equipment_name,
              })
            }
          >
            <Group gap="sm" wrap="nowrap" align="center">
              <EquipmentPhoto photoUrl={recommended.photo_url} alt={recommended.equipment_name} size={56} />
              <div>
                <Badge size="sm" color="orange" variant="filled" mb={4}>
                  Recomendado
                </Badge>
                <Text fw={800} c={BRAND.textPrimary}>
                  {recommended.mapping_exercise_name}
                </Text>
                <Text size="sm" c={BRAND.textMuted}>
                  {recommended.equipment_name}
                </Text>
                <StarsLine stars={recommended.user_preference_stars ?? null} />
              </div>
            </Group>
          </UnstyledButton>
        )}

        {excluded.length > 0 && (
          <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />}>
            <Text size="sm" fw={700} mb={4}>
              Fora da sugestão por causa da técnica {techniqueName}:
            </Text>
            <Stack gap={4}>
              {excluded.map((entry) => (
                <Text size="xs" key={entry.gym_equipment_id}>
                  <b>{entry.equipment_name}</b> — {exclusionReasonLabel(entry.reason, techniqueName)}
                </Text>
              ))}
            </Stack>
          </Alert>
        )}

        <Text size="sm" fw={700} c={BRAND.textMuted}>
          Ou escolha da lista desta academia
        </Text>

        {catalogQuery.isLoading && <Loader size="sm" color="brand" />}
        {catalogQuery.isError && (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
            <Text size="sm">Não foi possível carregar o catálogo de equipamentos.</Text>
          </Alert>
        )}

        <Stack gap="xs">
          {items.map((item) => (
            <UnstyledButton
              key={item.id}
              p="sm"
              disabled={submitting}
              style={{
                borderRadius: 'var(--mantine-radius-xl)',
                border: `1px solid ${BRAND.border}`,
                opacity: submitting ? 0.6 : 1,
              }}
              onClick={() =>
                choose({
                  mapping_id: item.mapping_id,
                  equipment_link_id: item.id,
                  exercise_name: item.mapping_exercise_name || item.equipment_name,
                  equipment_name: item.equipment_name,
                })
              }
            >
              <Group gap="sm" wrap="nowrap" align="center">
                <EquipmentPhoto photoUrl={item.photo_url} alt={item.equipment_name} size={56} />
                <div>
                  <Text fw={700} c={BRAND.textPrimary}>
                    {item.mapping_exercise_name || item.equipment_name}
                  </Text>
                  <Text size="xs" c={BRAND.textMuted}>
                    {item.equipment_name}
                  </Text>
                  <StarsLine stars={stars.get(pairKey(item.equipment_id, item.mapping_id)) ?? null} />
                  {excludedByLinkId.has(item.id) && (
                    <Badge size="xs" color="yellow" variant="light" mt={4}>
                      incompatível com {techniqueName}
                    </Badge>
                  )}
                </div>
              </Group>
            </UnstyledButton>
          ))}
          {!catalogQuery.isLoading && items.length === 0 && (
            <Text size="sm" c={BRAND.textMuted} ta="center" py="md">
              Nenhum outro equipamento cadastrado para este grupo muscular nesta academia.
            </Text>
          )}
        </Stack>
      </Stack>
    </Modal>
  );
}

// Estrelas do usuário para o equipamento; sem avaliação não mostra nada (o
// neutro = 3 é regra do ranking, não uma nota dada por ele).
function StarsLine({ stars }: { stars: number | null }) {
  if (stars == null) return null;
  return (
    <Group gap={6} mt={2} wrap="nowrap" aria-label={`Sua nota: ${starsLabel(stars)}`}>
      <Rating value={stars} count={MAX_PREFERENCE_STARS} size="xs" color="orange" readOnly />
    </Group>
  );
}
