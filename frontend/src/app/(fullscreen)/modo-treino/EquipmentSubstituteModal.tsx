'use client';

import { useEffect, useRef, useState } from 'react';
import { Alert, Badge, Button, Group, Loader, Modal, Rating, Stack, Text, TextInput, UnstyledButton } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconAlertTriangle, IconSearch, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  buildEquipmentPickerSections,
  currentLoadConvention,
  needsLoadConventionWarning,
  SECTION_TITLES,
  type EquipmentPickerItem,
} from '@/lib/equipment-picker';
import {
  buildCatalogSection,
  catalogItemState,
  CATALOG_ADDED_WITHOUT_LINK_MESSAGE,
  findAddedItem,
  findRecommendedItem,
  formatCatalogAddQuestion,
  type GymEquipmentCatalogAddResponse,
} from '@/lib/equipment-catalog-add';
import { SWAP_REMAINING_NOTE } from '@/lib/equipment-change-confirm';
import { loadConventionLabel, type EquipmentExerciseLink } from '@/lib/equipment-exercises-types';
import { apiFetch, ApiError } from '@/lib/api';
import { usePermissions } from '@/lib/use-permissions';
import { useOnlineStatus } from '@/mobile/ui/use-online-status';
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
import type { ApiList, GymEquipmentRecommendResponse } from '@/lib/workout-mode-types';
import { manufacturerLabel } from '@/lib/gym-equipment-manufacturer';
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
  // true na troca "séries restantes" com séries já lançadas: só explica o escopo.
  keepsLoggedSets?: boolean;
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
  keepsLoggedSets = false,
  onSelect,
  submitting = false,
}: Props) {
  const isMobile = useMediaQuery('(max-width: 48em)');
  const [search, setSearch] = useState('');
  // Opção cuja convenção de carga difere da atual, aguardando Confirmar/Voltar.
  const [pendingItem, setPendingItem] = useState<EquipmentPickerItem | null>(null);

  // Catálogo global: itens que a academia não tem. Só a ação de acrescentar exige a permissão.
  const online = useOnlineStatus();
  const canAddToGym = usePermissions().can('treino.academias', 'editar', 'todos');
  const [pendingAdd, setPendingAdd] = useState<EquipmentExerciseLink | null>(null);
  const [addMessage, setAddMessage] = useState<string | null>(null);

  // Geração de abertura: muda a cada abrir/fechar; o acréscimo que termina depois disso é descartado.
  const openGeneration = useRef(0);
  // Confirmar do aviso/confirmação: recebe foco ao aparecer (rola até ele no celular).
  const conventionConfirmRef = useRef<HTMLButtonElement>(null);
  const addConfirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    openGeneration.current += 1;
    if (!opened) {
      setSearch('');
      setPendingItem(null);
      setPendingAdd(null);
      setAddMessage(null);
    }
  }, [opened]);

  const catalogQuery = useQuery({
    queryKey: ['workout-mode-substitute-catalog', gymId, primaryGroup],
    queryFn: () =>
      apiFetch<ApiList<EquipmentPickerItem>>(
        `/gym-equipment?gym_id=${gymId}&primary_group=${encodeURIComponent(primaryGroup || '')}&active_only=true`,
      ),
    enabled: opened && Boolean(gymId && primaryGroup),
  });

  // Falha aqui só some com a seção: não derruba o seletor.
  const globalCatalogQuery = useQuery({
    queryKey: ['workout-mode-substitute-global-catalog', primaryGroup],
    queryFn: () =>
      apiFetch<{ items: EquipmentExerciseLink[] }>(
        `/equipment-exercises?primary_group=${encodeURIComponent(primaryGroup || '')}&active_only=true`,
      ),
    enabled: opened && Boolean(gymId && primaryGroup),
    retry: false,
  });

  const addMutation = useMutation({
    mutationFn: (pair: EquipmentExerciseLink) =>
      apiFetch<GymEquipmentCatalogAddResponse>('/gym-equipment-catalog', {
        method: 'POST',
        body: JSON.stringify({ gym_id: gymId, equipment_id: pair.equipment_id }),
      }),
    onSuccess: async (response, pair) => {
      const generation = openGeneration.current;
      const fresh = await catalogQuery.refetch();
      if (generation !== openGeneration.current) return; // fechou (ou reabriu) durante o acréscimo
      const found = findAddedItem(response.links, fresh.data?.items ?? [], pair);
      if (!found) {
        setAddMessage(CATALOG_ADDED_WITHOUT_LINK_MESSAGE);
        return;
      }
      chooseItem(found);
    },
    onMutate: () => ({ generation: openGeneration.current }),
    onError: (error, _pair, context) => {
      if (context?.generation !== openGeneration.current) return; // fechou (ou reabriu) antes da falha
      setAddMessage(error instanceof ApiError && error.message ? error.message : 'Não foi possível acrescentar o equipamento à academia.');
    },
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
  const allItems: EquipmentPickerItem[] = catalogQuery.data?.items || [];
  const sections = buildEquipmentPickerSections(allItems, mappingId, currentEquipmentLinkId, search);
  const currentConvention = currentLoadConvention(allItems, currentEquipmentLinkId);
  const catalogItems = gymId
    ? buildCatalogSection(
        globalCatalogQuery.data?.items,
        catalogQuery.isSuccess ? allItems : undefined,
        mappingId,
        search,
      )
    : [];
  const showEmpty = Boolean(gymId) && !catalogQuery.isLoading && !catalogQuery.isError;

  useEffect(() => {
    if (pendingItem) conventionConfirmRef.current?.focus();
  }, [pendingItem]);
  useEffect(() => {
    if (pendingAdd) addConfirmRef.current?.focus();
  }, [pendingAdd]);
  const gymName = allItems.find((item) => item.gym_name)?.gym_name;

  function choose(choice: EquipmentSubstituteChoice) {
    if (submitting) return;
    onSelect(choice);
  }

  function chooseItem(item: EquipmentPickerItem) {
    if (submitting) return;
    if (needsLoadConventionWarning(item.load_convention, currentConvention)) {
      setPendingItem(item);
      return;
    }
    choose(itemChoice(item));
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

        {keepsLoggedSets && (
          <Alert color="blue" variant="light" py={6} data-testid="swap-remaining-note">
            <Text size="xs">{SWAP_REMAINING_NOTE}</Text>
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
            onClick={() => {
              // Mesmo caminho dos demais (aviso de convenção); sem o item na lista, escolhe direto.
              const listed = findRecommendedItem(allItems, recommended.id);
              if (listed) {
                chooseItem(listed);
                return;
              }
              choose({
                mapping_id: recommended.mapping_id,
                equipment_link_id: recommended.id,
                exercise_name: recommended.mapping_exercise_name,
                equipment_name: recommended.equipment_name,
              });
            }}
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
                  {manufacturerLabel(recommended.manufacturer, recommended.manufacturer_source)
                    ? ` · ${manufacturerLabel(recommended.manufacturer, recommended.manufacturer_source)}`
                    : ''}
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

        <TextInput
          placeholder="Buscar equipamento, exercício ou fabricante"
          aria-label="Buscar equipamento"
          leftSection={<IconSearch size={16} />}
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
        />

        {pendingItem && (
          <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />} data-testid="equipment-convention-warning">
            <Text size="sm" mb="xs">
              Neste equipamento a carga é registrada: {loadConventionLabel(pendingItem.load_convention)}.
            </Text>
            <Group gap="xs">
              <Button
                size="md"
                radius="xl"
                h={44}
                ref={conventionConfirmRef}
                disabled={submitting}
                onClick={() => {
                  const chosen = pendingItem;
                  setPendingItem(null);
                  choose(itemChoice(chosen));
                }}
              >
                Confirmar
              </Button>
              <Button size="md" radius="xl" h={44} variant="default" onClick={() => setPendingItem(null)}>
                Voltar
              </Button>
            </Group>
          </Alert>
        )}

        {pendingAdd && (
          <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />} data-testid="equipment-catalog-add-confirm">
            <Text size="sm" mb="xs">
              {formatCatalogAddQuestion(pendingAdd.equipment_name, gymName)}
            </Text>
            <Group gap="xs">
              <Button
                size="md"
                radius="xl"
                h={44}
                ref={addConfirmRef}
                disabled={submitting || addMutation.isPending || !online}
                loading={addMutation.isPending}
                onClick={() => {
                  if (addMutation.isPending) return;
                  const pair = pendingAdd;
                  setPendingAdd(null);
                  setAddMessage(null);
                  addMutation.mutate(pair);
                }}
              >
                Confirmar
              </Button>
              <Button size="md" radius="xl" h={44} variant="default" onClick={() => setPendingAdd(null)}>
                Voltar
              </Button>
            </Group>
          </Alert>
        )}

        {addMessage && (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />} data-testid="equipment-catalog-add-message" role="alert">
            <Text size="sm">{addMessage}</Text>
          </Alert>
        )}

        <Stack gap="xs">
          {sections.map((section) => (
            <Stack key={section.key} gap="xs" data-testid={`equipment-section-${section.key}`}>
              <Text size="xs" fw={700} c={BRAND.textMuted} tt="uppercase">
                {section.title}
              </Text>
              {section.items.map((item) => (
                <UnstyledButton
                  key={item.id}
                  p="sm"
                  disabled={submitting}
                  style={{
                    borderRadius: 'var(--mantine-radius-xl)',
                    border: `1px solid ${BRAND.border}`,
                    opacity: submitting ? 0.6 : 1,
                  }}
                  onClick={() => chooseItem(item)}
                >
                  <Group gap="sm" wrap="nowrap" align="center">
                    <EquipmentPhoto photoUrl={item.photo_url} alt={item.equipment_name} size={56} />
                    <div>
                      <Text fw={700} c={BRAND.textPrimary}>
                        {item.mapping_exercise_name || item.equipment_name}
                      </Text>
                      <Text size="xs" c={BRAND.textMuted}>
                        {item.equipment_name}
                        {manufacturerLabel(item.manufacturer, item.manufacturer_source)
                          ? ` · ${manufacturerLabel(item.manufacturer, item.manufacturer_source)}`
                          : ''}
                      </Text>
                      {item.load_convention && (
                        <Text size="xs" c={BRAND.textMuted}>
                          Carga: {loadConventionLabel(item.load_convention)}
                        </Text>
                      )}
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
            </Stack>
          ))}
          {catalogItems.length > 0 && (
            <Stack gap="xs" data-testid="equipment-section-catalog">
              <Text size="xs" fw={700} c={BRAND.textMuted} tt="uppercase">
                {SECTION_TITLES.catalog}
              </Text>
              {catalogItems.map((pair) => {
                const state = catalogItemState(canAddToGym, online);
                const disabled = state.kind === 'disabled';
                return (
                  <div
                    key={pair.id}
                    data-testid="equipment-catalog-item"
                    aria-disabled={disabled || undefined}
                    style={{
                      padding: 'var(--mantine-spacing-sm)',
                      borderRadius: 'var(--mantine-radius-xl)',
                      border: `1px solid ${BRAND.border}`,
                      opacity: disabled ? 0.6 : 1,
                    }}
                  >
                    <Group gap="sm" wrap="nowrap" align="center">
                      <EquipmentPhoto photoUrl={pair.photo_url} alt={pair.equipment_name} size={56} />
                      <div>
                        <Text fw={700} c={BRAND.textPrimary}>
                          {pair.exercise_name || pair.equipment_name}
                        </Text>
                        <Text size="xs" c={BRAND.textMuted}>
                          {pair.equipment_name}
                        </Text>
                        <Text size="xs" c={BRAND.textMuted}>
                          Carga: {loadConventionLabel(pair.load_convention)}
                        </Text>
                        {state.notice && (
                          <Text size="xs" c={BRAND.textMuted} fs="italic">
                            {state.notice}
                          </Text>
                        )}
                      </div>
                    </Group>
                    {!disabled && (
                      <Button
                        mt="xs"
                        size="md"
                        radius="xl"
                        h={44}
                        variant="light"
                        fullWidth
                        disabled={state.kind !== 'add' || submitting || addMutation.isPending}
                        aria-label={`Acrescentar ${pair.equipment_name} à academia`}
                        onClick={() => {
                          setAddMessage(null);
                          setPendingAdd(pair);
                        }}
                      >
                        Acrescentar à academia
                      </Button>
                    )}
                  </div>
                );
              })}
            </Stack>
          )}
          {!gymId && (
            <Text size="sm" c={BRAND.textMuted} ta="center" py="md">
              Este treino não tem academia definida. Defina a academia do treino para escolher o equipamento.
            </Text>
          )}
          {showEmpty && sections.length === 0 && catalogItems.length === 0 && search.trim() !== '' && (
            <Stack gap="xs" align="center" py="md">
              <Text size="sm" c={BRAND.textMuted} ta="center">
                Nenhum equipamento encontrado
              </Text>
              <Button size="sm" variant="subtle" onClick={() => setSearch('')}>
                Limpar busca
              </Button>
            </Stack>
          )}
          {showEmpty && sections.length === 0 && catalogItems.length === 0 && search.trim() === '' && (
            <Text size="sm" c={BRAND.textMuted} ta="center" py="md">
              Nenhum outro equipamento cadastrado para este grupo muscular nesta academia.
            </Text>
          )}
        </Stack>
      </Stack>
    </Modal>
  );
}

function itemChoice(item: EquipmentPickerItem): EquipmentSubstituteChoice {
  return {
    mapping_id: item.mapping_id,
    equipment_link_id: item.id,
    exercise_name: item.mapping_exercise_name || item.equipment_name,
    equipment_name: item.equipment_name,
  };
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
