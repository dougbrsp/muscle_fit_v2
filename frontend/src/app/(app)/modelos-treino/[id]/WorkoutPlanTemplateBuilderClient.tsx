'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Grid,
  Group,
  Loader,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type CollisionDetection,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import { SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates } from '@dnd-kit/sortable';
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconAppWindow,
  IconChartBar,
  IconChevronsDown,
  IconChevronsUp,
  IconInfoCircle,
  IconPlus,
  IconTrash,
  IconUsers,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import { useParams, useRouter } from 'next/navigation';
import { useEffect, useMemo, useRef, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import { useDynamicRouteParam } from '@/lib/use-dynamic-route-param';
import type { ApiListResponse as TrainingTypesApiListResponse, MuscleGroupOption, TrainingTypeListItem } from '@/lib/training-types-types';
import { clearPeriodizationSeed, hasPeriodizationOriginFlag, readPeriodizationSeed } from '@/lib/periodization';
import type { PeriodizationSeed } from '@/lib/periodization-types';
import {
  hydrateCollapsedItems,
  readCollapsedItemIds,
  serializeCollapsedItems,
  setItemsCollapsed,
  toggleCollapsedItem,
  writeCollapsedItemIds,
} from '@/lib/item-collapse';
import { applyBatchPctToItems, type BatchPctDraft } from '@/lib/slot-batch-pct';
import {
  GRID_MAX_COLS,
  GRID_MAX_ROWS,
  MAX_TEMPLATE_SLOTS,
  canAddSlot,
  canResizeGrid,
  defaultGrid,
  gridCapacity,
  gridPayload,
  hydrateCollapsed,
  isLegacyOverflow,
  readCollapsedPositions,
  reorderSlots,
  resolveGrid,
  serializeCollapsed,
  writeCollapsedPositions,
  type SlotGrid,
} from '@/lib/slot-grid';
import {
  VOLUME_COVERAGE_STATUS_COLOR,
  VOLUME_COVERAGE_STATUS_LABEL,
  VOLUME_MODE_DESCRIPTION,
  VOLUME_MODE_OPTIONS,
  type ApiListResponse,
  type TemplateSafetyWarning,
  type VolumeCoverageWarning,
  type VolumeMode,
  type WorkoutPlanTemplateDetail,
  type WorkoutPlanTemplateDraft,
  type WorkoutPlanTemplateItemKind,
  type WorkoutPlanTemplatePayload,
} from '@/lib/workout-plan-templates-types';
import {
  countSlotSafetyWarnings,
  hydrateSlots,
  hydrateSlotsWithItemIds,
  mapSavedItemServerIds,
  newItem,
  newSlot,
  slotsToPayload,
  validateSlots,
  type ItemState,
  type SlotState,
} from '@/lib/workout-plan-template-form';
import { BRAND } from '@/theme';
import { AssignTemplateModal } from '../AssignTemplateModal';
import { IntensityDensityFloatingPanel, INTENSITY_DENSITY_PANEL_ID } from '../IntensityDensityFloatingPanel';
import { IntensityDensityTable } from '../IntensityDensityTable';
import { RoleActivationFloatingPanel, ROLE_ACTIVATION_PANEL_ID } from '../RoleActivationFloatingPanel';
import { MethodologyVolumePanel } from '../MethodologyVolumePanel';
import { useActivationPreviewQuery } from '../useActivationPreviewQuery';
import { useFloatingPanels } from '../useFloatingPanels';
import { useMethodologyVolumeQuery } from '../useMethodologyVolumeQuery';
import { WeeklySetsFloatingPanel, WEEKLY_SETS_PANEL_ID } from '../WeeklySetsFloatingPanel';
import { WeeklyActivationPreviewTable } from '../WeeklyActivationPreviewTable';
import { buildIntensityDensityByGroup } from '@/lib/intensity-density';
import { buildItemActivationLookup } from '@/lib/item-activation-summary';
import { buildItemDirectShareLookup } from '@/lib/methodology-card-share';
import {
  COUNTING_MODE_SELECT_DATA,
  countingModeOption,
  DEFAULT_COUNTING_MODE,
  normalizeCountingMode,
} from '@/lib/volume-counting-mode';
import { buildItemPriorFatigueLookup } from '@/lib/prior-fatigue';
import { useAdvancedTechniques } from '@/lib/use-advanced-techniques';
import type { MethodologyIntentKind } from '@/lib/methodology-volume-types';
import type { SessionUser } from '@/lib/auth';
import { versionLabel } from '@/lib/template-versions';
import {
  goalOption,
  goalSelectData,
  goalValue,
  trainingGoalDescription,
  useTrainingGoals,
} from '@/lib/training-goals';
import { PUBLISH_LABEL, SAVE_DRAFT_LABEL, draftSavedMessage, publishNotice } from '@/lib/template-drafts';
import { BlurCommitTextInput, BlurCommitTextarea } from './BlurCommitInputs';
import { TemplateDraftBanner } from './TemplateDraftBanner';
import { TemplateVersionHistoryPanel } from './TemplateVersionHistory';
import { PeriodizationDraftPanel } from './PeriodizationDraftPanel';
import { SlotDragPreview, SlotEditor } from './SlotEditor';
import type { SortableData } from './sortable';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

const GRID_ROW_OPTIONS = Array.from({ length: GRID_MAX_ROWS }, (_, i) => String(i + 1));
const GRID_COL_OPTIONS = Array.from({ length: GRID_MAX_COLS }, (_, i) => String(i + 1));

function sortableData(data: { current?: unknown } | null | undefined): SortableData | undefined {
  return data?.current as SortableData | undefined;
}

// Colisão filtrada pelo tipo do que está sendo arrastado: slot só enxerga
// slot; item só enxerga itens do MESMO slot (soltar em outro slot é ignorado
// — mover entre slots não é escopo).
const slotAwareCollision: CollisionDetection = (args) => {
  const active = sortableData(args.active.data);
  const droppableContainers = args.droppableContainers.filter((container) => {
    const data = sortableData(container.data);
    if (!active || !data) return false;
    if (active.type === 'slot') return data.type === 'slot';
    return data.type === 'item' && data.slotLocalId === active.slotLocalId;
  });
  return closestCenter({ ...args, droppableContainers });
};

/**
 * Builder de "Modelo de treino" (Fase 4, Etapa 15) — CRUD de slots cíclicos
 * (grupo muscular/cardio, sem exercício específico — `item_kind='exercise'`
 * foi removido do contrato atual), preview de ativação semanal e atribuição
 * a usuários. Mesmo padrão estrutural de TrainingTypeBuilderClient.tsx
 * (Etapa 14): `useParams()` resolve "novo" vs. id numérico em runtime
 * (compatível com `output: 'export'`).
 */
export function WorkoutPlanTemplateBuilderClient() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  // Em `output: 'export'` o `useParams()` devolve o slug do build
  // ("placeholder"), não o id da barra de endereços — ver
  // `useDynamicRouteParam`. `routeId` é `null` só no primeiro render.
  const routeId = useDynamicRouteParam(params.id);
  const isNew = routeId === 'novo';
  const numericId = routeId && !isNew ? Number(routeId) : null;

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [trainingTypeId, setTrainingTypeId] = useState<string | null>(null);
  const [volumeMode, setVolumeMode] = useState<VolumeMode>('percent_budget');
  // Modo de contagem do volume (073, Lote 3): valor de `VolumeCountingMode`
  // do motor; hidratado do Modelo/rascunho e enviado no PUT/POST, no
  // rascunho e no corpo do preview da metodologia.
  const [countingMode, setCountingMode] = useState<string>(DEFAULT_COUNTING_MODE);
  // M97 F3: objetivo do Modelo (valor de `GET /training-goals`). null = ainda
  // não informado: o PUT sem o campo preserva o gravado e a criação usa o default.
  const [trainingGoal, setTrainingGoal] = useState<string | null>(null);
  const trainingGoalsQuery = useTrainingGoals();
  const trainingGoals = trainingGoalsQuery.data;
  const [isActive, setIsActive] = useState(true);
  const [slots, setSlots] = useState<SlotState[]>([]);
  // M10: payload dos previews memoizado por identidade de `slots` (antes era
  // recalculado a cada render, 2x). Como os campos de texto/número só promovem
  // o valor ao estado no blur, `slots` não muda a cada tecla.
  const previewSlots = useMemo(() => slotsToPayload(slots), [slots]);
  // M11: linha de ativação por item. MESMA query/queryKey das tabelas de
  // preview (o React Query deduplica — nenhuma requisição extra por linha nem
  // por slot); o cliente só casa `items[]` da resposta com as linhas.
  // Modo 'percent_budget': o tipo de treino entra no preview (orçamento
  // projetado dos itens de grupo + `item_budgets` do resumo recolhido).
  const previewTrainingTypeId = trainingTypeId ? Number(trainingTypeId) : null;
  const { query: activationPreviewQuery, hasAnyFilledSlot: activationHasAnyFilledSlot } = useActivationPreviewQuery(
    previewSlots,
    volumeMode,
    previewTrainingTypeId,
  );
  const itemActivationByKey = useMemo(
    () => buildItemActivationLookup(previewSlots, activationPreviewQuery.data),
    [previewSlots, activationPreviewQuery.data],
  );
  // Fadiga prévia por item (faixa de cor do card): MESMA query; reordenar ou
  // editar séries muda o corpo e o preview devolve a fadiga já recalculada.
  const itemPriorFatigueByKey = useMemo(
    () => buildItemPriorFatigueLookup(previewSlots, activationPreviewQuery.data),
    [previewSlots, activationPreviewQuery.data],
  );
  // "Intensidade × Densidade": UM cálculo só, lido pela janela flutuante e pela
  // tabela. Pesos vêm da MESMA query do preview; RPE/reps/descanso/pico/técnica
  // vêm de `previewSlots` (não vão no corpo do preview — editá-los recalcula
  // aqui sem nova requisição).
  const { items: techniqueCatalog } = useAdvancedTechniques();
  const intensityDensityRows = useMemo(
    () => buildIntensityDensityByGroup(previewSlots, activationPreviewQuery.data, techniqueCatalog),
    [previewSlots, activationPreviewQuery.data, techniqueCatalog],
  );
  // M43: a MESMA query do painel "Metodologia de volume por grupo" alimenta a
  // linha de séries diretas de cada card do Modo B. A intenção declarada (que
  // entra no corpo/queryKey) mora aqui para painel e cards lerem a mesma
  // avaliação — nenhuma requisição extra por card. Falha do preview só some
  // com a linha; o builder segue.
  const [methodologyIntent, setMethodologyIntent] = useState<MethodologyIntentKind>('standard');
  const [methodologyTargetGroupIds, setMethodologyTargetGroupIds] = useState<string[]>([]);
  const methodologyNumericTargets = useMemo(
    () => methodologyTargetGroupIds.map(Number).filter((id) => Number.isFinite(id)),
    [methodologyTargetGroupIds],
  );
  const { query: methodologyVolumeQuery, hasAnyFilledSlot: methodologyHasAnyFilledSlot } = useMethodologyVolumeQuery(
    previewSlots,
    volumeMode,
    methodologyIntent,
    methodologyNumericTargets,
    countingMode,
  );
  // Lote 3: fatia de cada card por bloco, pronta do backend (`item_shares`).
  const itemDirectShareByKey = useMemo(
    () => buildItemDirectShareLookup(previewSlots, methodologyVolumeQuery.data),
    [previewSlots, methodologyVolumeQuery.data],
  );
  // Fase 7 (Frente A): janelas flutuantes só no desktop. Leem as MESMAS
  // queries acima (nenhuma requisição nova); "Séries por grupo" só existe no
  // Modo B, "Ativação por participação" nos dois modos.
  const isDesktop = useMediaQuery('(min-width: 62em)');
  const floatingPanels = useFloatingPanels();
  const availablePanelIds = useMemo(
    () =>
      volumeMode === 'equipment_series'
        ? [WEEKLY_SETS_PANEL_ID, ROLE_ACTIVATION_PANEL_ID, INTENSITY_DENSITY_PANEL_ID]
        : [ROLE_ACTIVATION_PANEL_ID, INTENSITY_DENSITY_PANEL_ID],
    [volumeMode],
  );
  const closedPanelIds = availablePanelIds.filter((id) => floatingPanels.isClosed(id));
  // Sempre `false`: no primeiro render `routeId` ainda é `null`. Quem liga o
  // flag no caso "novo" é o efeito de hidratação abaixo.
  const [initialized, setInitialized] = useState(false);
  const [warnings, setWarnings] = useState<VolumeCoverageWarning[]>([]);
  // Avisos de segurança por item (PR-12) — vêm do GET do modelo e são
  // atualizados a cada save bem-sucedido. Não bloqueiam a edição.
  const [safetyWarnings, setSafetyWarnings] = useState<TemplateSafetyWarning[]>([]);
  // Rascunho gerado pelo Motor de Periodização (PR-17). Preenchido só quando a
  // tela foi aberta pelo modal de geração; `null` em qualquer outro caso —
  // inclusive num "Novo modelo" comum. Enquanto ele existe, NADA foi gravado:
  // o modelo só passa a existir no "Salvar", que leva o `generation_log_id`
  // junto para o backend marcar `origin='periodization'`.
  const [periodizationSeed, setPeriodizationSeed] = useState<PeriodizationSeed | null>(null);
  // Cards de item_kind='equipment' expandidos (seção "Configurações
  // avançadas" — técnica/aquecimento/ajuste/pico de contração), por localId.
  const [expandedItemIds, setExpandedItemIds] = useState<Set<string>>(new Set());
  // Grade de slots (linhas x colunas, lida linha por linha = ordem do ciclo).
  // Vem do Modelo (migração 062) e é sempre enviada no save. Um "Novo modelo"
  // comum começa com uma linha de 4 (a grade padrão de 4 slots), para não
  // obrigar a mexer na grade antes do 2º slot.
  const [grid, setGrid] = useState<SlotGrid>(() => defaultGrid(GRID_MAX_COLS));
  // Teto de slots aceito no save: 8, ou a quantidade já gravada num Modelo
  // legado acima disso (o PUT do backend aceita até ela).
  const [savedSlotCount, setSavedSlotCount] = useState(0);
  // Slots recolhidos, por `localId`. Só no navegador: persistido como lista de
  // posições em `localStorage` por Modelo (nunca no modelo novo, sem id).
  // Independente de `expandedItemIds` (configurações avançadas do card).
  const [collapsedSlotIds, setCollapsedSlotIds] = useState<Set<string>>(new Set());
  // Cards de item recolhidos, por `localId` (o id do `SortableItem`, que
  // sobrevive à reordenação). Começam abertos. Persistidos em `localStorage`
  // por Modelo pelo `id` do item no servidor (`itemServerIds`), porque o
  // `localId` muda a cada carregamento e a posição muda ao arrastar.
  const [collapsedItemIds, setCollapsedItemIds] = useState<Set<string>>(new Set());
  // `localId` -> `id` do item salvo: da hidratação e refeito a cada save (a
  // versão nova grava itens com ids novos). Item ainda não salvo não está aqui.
  const [itemServerIds, setItemServerIds] = useState<Map<string, number>>(new Map());
  // Foto dos slots enviados no último save, para casar com os ids da resposta.
  const savedSlotsSnapshot = useRef<SlotState[] | null>(null);
  // Rascunho do Modelo (migração 067): `updated_at` do rascunho que está no
  // editor, ou `null` quando o editor mostra a versão publicada.
  const [draftUpdatedAt, setDraftUpdatedAt] = useState<string | null>(null);
  // Itens vindos do rascunho não têm `id` no servidor: enquanto o editor
  // mostra o rascunho, a lista de cards recolhidos (por id) não é regravada,
  // para não apagar a da versão publicada.
  const [itemIdsFromDraft, setItemIdsFromDraft] = useState(false);
  const [activeDragSlotId, setActiveDragSlotId] = useState<string | null>(null);
  const dndSensors = useSensors(
    // Distância mínima: clique na alça e foco nos campos não viram arraste.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  function toggleSlotCollapsed(slotLocalId: string) {
    setCollapsedSlotIds((prev) => {
      const next = new Set(prev);
      if (next.has(slotLocalId)) next.delete(slotLocalId);
      else next.add(slotLocalId);
      return next;
    });
  }

  function setAllSlotsCollapsed(collapsed: boolean) {
    setCollapsedSlotIds(collapsed ? new Set(slots.map((s) => s.localId)) : new Set());
  }

  function toggleItemCollapsed(itemLocalId: string) {
    setCollapsedItemIds((prev) => toggleCollapsedItem(prev, itemLocalId));
  }

  function setSlotItemsCollapsed(itemLocalIds: string[], collapsed: boolean) {
    setCollapsedItemIds((prev) => setItemsCollapsed(prev, itemLocalIds, collapsed));
  }

  function toggleItemExpanded(itemLocalId: string) {
    setExpandedItemIds((prev) => {
      const next = new Set(prev);
      if (next.has(itemLocalId)) next.delete(itemLocalId);
      else next.add(itemLocalId);
      return next;
    });
  }

  const [assignOpened, { open: openAssign, close: closeAssign }] = useDisclosure(false);

  const templateQuery = useQuery({
    queryKey: ['workout-plan-template', numericId],
    queryFn: () => apiFetch<WorkoutPlanTemplateDetail>(`/workout-plan-templates/${numericId}`),
    enabled: !isNew && !!numericId,
    retry: false,
  });

  // Rascunho (067): só quando o GET do Modelo diz que existe. Ao abrir, o
  // editor carrega o RASCUNHO, não a vigente.
  const hasDraft = Boolean(templateQuery.data?.has_draft);
  const draftQuery = useQuery({
    queryKey: ['workout-plan-template-draft', numericId],
    queryFn: () => apiFetch<WorkoutPlanTemplateDraft>(`/workout-plan-templates/${numericId}/draft`),
    enabled: !isNew && !!numericId && hasDraft,
    retry: false,
  });

  // Mesma query key do AppShellLayout/lista de Modelos — o React Query dedupa.
  // Só admin/PT veem as ações do histórico (reverter).
  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: () => apiFetch<SessionUser>('/auth/me'),
    staleTime: 60_000,
    retry: false,
  });
  const canManageVersions =
    currentUserQuery.data?.role === 'admin' || currentUserQuery.data?.role === 'personal_trainer';

  const muscleGroupsQuery = useQuery({
    queryKey: ['muscle-groups-catalog'],
    queryFn: () => apiFetch<ApiListResponse<MuscleGroupOption>>('/muscle-groups'),
  });

  const trainingTypesQuery = useQuery({
    queryKey: ['training-types-for-templates'],
    queryFn: () => apiFetch<TrainingTypesApiListResponse<TrainingTypeListItem>>('/training-types'),
  });

  // Hidrata o estado do builder a partir do detalhe salvo (só uma vez) ou, em
  // `/modelos-treino/novo?origem=periodizacao`, do rascunho que o
  // `PeriodizationModal` deixou na sessão (PR-17).
  useEffect(() => {
    if (initialized) return;
    if (isNew) {
      // A marca na URL é obrigatória: sem ela um "Novo modelo" comum jamais
      // herda um rascunho que tenha sobrado da sessão.
      const fromPeriodization =
        typeof window !== 'undefined' && hasPeriodizationOriginFlag(window.location.search);
      const seed = fromPeriodization ? readPeriodizationSeed() : null;
      if (seed) {
        const seedSlots = hydrateSlots(seed.slots);
        setName(seed.header.name);
        setDescription(seed.header.description);
        setTrainingTypeId(seed.header.trainingTypeId);
        setVolumeMode(seed.header.volumeMode);
        setCountingMode(normalizeCountingMode(seed.header.countingMode));
        // M97 F5: objetivo do Modelo pedido na geração (os overrides por dia já
        // vêm nos itens do rascunho).
        setTrainingGoal(seed.header.trainingGoal ?? null);
        setIsActive(seed.header.isActive);
        setSlots(seedSlots);
        setGrid(resolveGrid(seed.grid?.rows, seed.grid?.cols, seedSlots.length));
        setPeriodizationSeed(seed);
      }
      setInitialized(true);
      return;
    }
    if (!templateQuery.data) return;

    const detail = templateQuery.data;
    // Com rascunho, espera a leitura dele; se ela falhar (ex.: descartado em
    // outra aba), cai na vigente.
    if (detail.has_draft && draftQuery.isPending) return;
    const draft = detail.has_draft && draftQuery.data ? draftQuery.data : null;
    const source = draft ? draft.payload : detail;
    setName(source.name ?? '');
    setDescription(source.description || '');
    setTrainingTypeId(source.training_type_id != null ? String(source.training_type_id) : null);
    setVolumeMode(source.volume_mode || 'percent_budget');
    setCountingMode(normalizeCountingMode(source.volume_counting_mode));
    setTrainingGoal(source.training_goal ?? null);
    setIsActive(source.is_active ?? true);
    const { slots: hydratedSlots, itemServerIds: hydratedItemServerIds } = hydrateSlotsWithItemIds(
      source.slots ?? [],
    );
    setSlots(hydratedSlots);
    setItemServerIds(hydratedItemServerIds);
    setItemIdsFromDraft(Boolean(draft));
    setCollapsedItemIds(
      hydrateCollapsedItems(hydratedSlots, hydratedItemServerIds, readCollapsedItemIds(detail.id)),
    );
    setGrid(resolveGrid(source.grid_rows, source.grid_cols, hydratedSlots.length));
    // Teto de slots do legado: o que a versão PUBLICADA tem.
    setSavedSlotCount((detail.slots || []).length);
    setCollapsedSlotIds(hydrateCollapsed(hydratedSlots, readCollapsedPositions(detail.id)));
    // Avisos são da versão publicada e casam itens por posição: com o
    // rascunho no editor eles apontariam para os itens errados. A publicação
    // recalcula tudo.
    setWarnings(draft ? [] : detail.volume_coverage_warnings || []);
    setSafetyWarnings(draft ? [] : detail.safety_warnings || []);
    setDraftUpdatedAt(draft ? draft.updated_at : null);
    setInitialized(true);
  }, [isNew, initialized, templateQuery.data, draftQuery.isPending, draftQuery.data]);

  // Persiste os slots recolhidos por POSIÇÃO (o `localId` muda a cada
  // carregamento). Reordenar slots move a posição junto. Modelo novo não grava.
  useEffect(() => {
    if (!initialized || !numericId) return;
    writeCollapsedPositions(numericId, serializeCollapsed(slots, collapsedSlotIds));
  }, [initialized, numericId, slots, collapsedSlotIds]);

  // Persiste os cards recolhidos pelo `id` do item no servidor. Ids de itens
  // removidos somem da lista; item ainda não salvo só entra após o save.
  useEffect(() => {
    if (!initialized || !numericId || itemIdsFromDraft) return;
    writeCollapsedItemIds(numericId, serializeCollapsedItems(slots, collapsedItemIds, itemServerIds));
  }, [initialized, numericId, slots, collapsedItemIds, itemServerIds, itemIdsFromDraft]);

  const muscleGroupOptions = (muscleGroupsQuery.data?.items || []).map((g) => ({ value: String(g.id), label: g.name }));
  const muscleGroupNameById = useMemo(() => {
    const map = new Map<number, string>();
    for (const g of muscleGroupsQuery.data?.items || []) map.set(g.id, g.name);
    return map;
  }, [muscleGroupsQuery.data]);
  // Agregação da metodologia: o resumo do slot recolhido soma subgrupos no
  // grupo principal (raiz da cadeia `methodology_parent_group_id`).
  const muscleGroupParentById = useMemo(() => {
    const map = new Map<number, number | null>();
    for (const g of muscleGroupsQuery.data?.items || []) map.set(g.id, g.methodology_parent_group_id ?? null);
    return map;
  }, [muscleGroupsQuery.data]);
  const trainingTypeOptions = (trainingTypesQuery.data?.items || []).map((t) => ({ value: String(t.id), label: t.name }));

  // Legado com mais de 8 slots: exibido inteiro numa grade de 4 colunas que
  // cresce além de 2 linhas, sem permitir adicionar (só proteção).
  const legacyOverflow = isLegacyOverflow(slots.length);
  const slotAddAllowed = canAddSlot(grid, slots.length);
  const addSlotBlockedReason =
    slots.length >= MAX_TEMPLATE_SLOTS
      ? `Um modelo pode ter no máximo ${MAX_TEMPLATE_SLOTS} slots.`
      : `A grade ${grid.rows}x${grid.cols} está cheia — aumente as linhas ou colunas para adicionar um slot.`;
  // No celular (1 coluna) basta uma célula livre; no desktop, todas as da grade.
  const freeCells = legacyOverflow ? 0 : Math.max(gridCapacity(grid) - slots.length, 0);
  const emptyCellCount = isDesktop ? freeCells : Math.min(freeCells, 1);

  function addSlot() {
    if (!canAddSlot(grid, slots.length)) return;
    setSlots((prev) => [...prev, newSlot(prev.length + 1)]);
  }

  // Reduzir a grade abaixo da quantidade de slots é bloqueado — nunca apaga
  // slot sozinho.
  function resizeGrid(patch: Partial<SlotGrid>) {
    const next = { ...grid, ...patch };
    if (!canResizeGrid(next, slots.length)) {
      notifications.show({
        color: 'yellow',
        title: 'Grade pequena demais',
        message: `A grade ${next.rows}x${next.cols} comporta ${gridCapacity(next)} slots, mas o modelo tem ${slots.length}. Remova slots antes de reduzir a grade.`,
      });
      return;
    }
    setGrid(next);
  }

  function removeSlot(localId: string) {
    setSlots((prev) => prev.filter((s) => s.localId !== localId));
  }

  function moveSlot(localId: string, direction: -1 | 1) {
    setSlots((prev) => {
      const index = prev.findIndex((s) => s.localId === localId);
      if (index < 0) return prev;
      return reorderSlots(prev, index, index + direction);
    });
  }

  function updateSlot(localId: string, patch: Partial<SlotState>) {
    setSlots((prev) => prev.map((s) => (s.localId === localId ? { ...s, ...patch } : s)));
  }

  function addItem(slotLocalId: string, kind: WorkoutPlanTemplateItemKind) {
    setSlots((prev) =>
      prev.map((s) => (s.localId === slotLocalId ? { ...s, items: [...s.items, newItem(kind)] } : s)),
    );
  }

  // Muda o modo do template — decisão única por template (não por slot).
  // Itens incompatíveis com o novo modo ('muscle_group' ao ir para
  // 'equipment_series', ou 'equipment' ao voltar para 'percent_budget') são
  // removidos, com aviso, para não deixar o payload num estado que o backend
  // rejeitaria (_validate_template_payload).
  function handleVolumeModeChange(next: VolumeMode) {
    if (next === volumeMode) return;
    const incompatibleKind: WorkoutPlanTemplateItemKind = next === 'equipment_series' ? 'muscle_group' : 'equipment';
    let removedCount = 0;
    setSlots((prev) =>
      prev.map((s) => {
        const kept = s.items.filter((it) => it.item_kind !== incompatibleKind);
        removedCount += s.items.length - kept.length;
        return { ...s, items: kept };
      }),
    );
    if (removedCount > 0) {
      notifications.show({
        color: 'yellow',
        title: 'Itens removidos',
        message: `${removedCount} item(ns) incompatível(is) com o novo modo foram removidos dos slots.`,
      });
    }
    setVolumeMode(next);
    // Motor 'equipment_series' ignora training_type_id (só o motor v2 de
    // 'percent_budget' usa a meta semanal da meta de ativação). Ao trocar
    // para 'equipment_series' o campo é ocultado e zerado; ao voltar para
    // 'percent_budget' o Select reaparece vazio — não restaura o valor
    // anterior automaticamente.
    if (next === 'equipment_series') {
      setTrainingTypeId(null);
    }
  }

  function updateItem(slotLocalId: string, itemLocalId: string, patch: Partial<ItemState>) {
    setSlots((prev) =>
      prev.map((s) =>
        s.localId === slotLocalId
          ? { ...s, items: s.items.map((it) => (it.localId === itemLocalId ? { ...it, ...patch } : it)) }
          : s,
      ),
    );
  }

  function handleDragStart(event: DragStartEvent) {
    const data = sortableData(event.active.data);
    setActiveDragSlotId(data?.type === 'slot' ? String(event.active.id) : null);
  }

  // Um só `DndContext` para slots e itens (dnd-kit), distinguidos pelo `data`
  // do `useSortable`:
  //   - slot sobre slot: reordena o ciclo (a posição na grade É o `sequence`);
  //   - item sobre item do MESMO slot (M9): reordena só o array em memória —
  //     `item_order` é recalculado como `itemIndex + 1` no save. Item sobre
  //     outro slot é ignorado (mover entre slots não é escopo).
  // Os ids são sempre `localId` (chaves estáveis, nunca o índice).
  function handleDragEnd(event: DragEndEvent) {
    setActiveDragSlotId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const activeData = sortableData(active.data);
    const overData = sortableData(over.data);
    if (!activeData || !overData) return;
    const activeId = String(active.id);
    const overId = String(over.id);

    if (activeData.type === 'slot') {
      if (overData.type !== 'slot') return;
      setSlots((prev) => {
        const from = prev.findIndex((s) => s.localId === activeId);
        const to = prev.findIndex((s) => s.localId === overId);
        return reorderSlots(prev, from, to);
      });
      return;
    }

    if (overData.type !== 'item' || overData.slotLocalId !== activeData.slotLocalId) return;
    setSlots((prev) =>
      prev.map((s) => {
        if (s.localId !== activeData.slotLocalId) return s;
        const from = s.items.findIndex((it) => it.localId === activeId);
        const to = s.items.findIndex((it) => it.localId === overId);
        if (from < 0 || to < 0) return s;
        return { ...s, items: arrayMove(s.items, from, to) };
      }),
    );
  }

  function removeItem(slotLocalId: string, itemLocalId: string) {
    setSlots((prev) =>
      prev.map((s) =>
        s.localId === slotLocalId ? { ...s, items: s.items.filter((it) => it.localId !== itemLocalId) } : s,
      ),
    );
  }

  // Atalho de UI (não persistido, não enviado ao backend): copia o valor de
  // Min %/Max % informado no header do slot para priority_min_pct/
  // priority_max_pct de TODOS os itens item_kind='muscle_group' desse slot
  // especificamente, sobrescrevendo qualquer valor já preenchido item a
  // item. Não altera outros slots nem trava a edição individual depois — o
  // usuário continua podendo sobrescrever item a item após aplicar o lote.
  function applyBatchPctToSlot(slotLocalId: string, draft: BatchPctDraft) {
    setSlots((prev) =>
      prev.map((s) => (s.localId === slotLocalId ? { ...s, items: applyBatchPctToItems(s.items, draft) } : s)),
    );
  }

  // Corpo do PUT do Modelo — o MESMO para publicar e para o rascunho (067).
  function buildPayload(): WorkoutPlanTemplatePayload {
    return {
      name,
      description: description || null,
      training_type_id: trainingTypeId ? Number(trainingTypeId) : null,
      volume_mode: volumeMode,
      volume_counting_mode: countingMode,
      ...(trainingGoal != null ? { training_goal: trainingGoal } : {}),
      is_active: isActive,
      // Só num rascunho de periodização ainda não salvo. É isto que marca
      // `origin='periodization'` no backend e fecha a auditoria da geração;
      // sem enviar, o modelo nasceria `manual` e o log ficaria órfão.
      ...(isNew && periodizationSeed?.generationLogId != null
        ? { generation_log_id: periodizationSeed.generationLogId }
        : {}),
      // Sempre os dois campos (só o legado com mais de 8 slots vai sem grade).
      ...gridPayload(grid, slots.length),
      slots: slotsToPayload(slots),
    };
  }

  // "Salvar rascunho" (067): grava o estado do editor como está — mesmo
  // incompleto ou inválido —, sem criar versão. Só para Modelo já publicado.
  const saveDraftMutation = useMutation({
    mutationFn: () =>
      apiFetch<WorkoutPlanTemplateDraft>(`/workout-plan-templates/${numericId}/draft`, {
        method: 'PUT',
        body: JSON.stringify(buildPayload()),
      }),
    onSuccess: async (draft) => {
      setDraftUpdatedAt(draft.updated_at);
      qc.setQueryData(['workout-plan-template-draft', numericId], draft);
      qc.setQueryData<WorkoutPlanTemplateDetail>(['workout-plan-template', numericId], (old) =>
        old ? { ...old, has_draft: true, draft_updated_at: draft.updated_at } : old,
      );
      await qc.invalidateQueries({ queryKey: ['workout-plan-templates'] });
      notifications.show({ color: 'green', message: draftSavedMessage(draft.updated_at) });
    },
    onError: (error: unknown) => {
      notifications.show({
        color: 'red',
        title: 'Erro ao salvar rascunho',
        message: errorMessage(error, 'Não foi possível salvar o rascunho.'),
        withCloseButton: true,
      });
    },
  });

  // "Descartar rascunho": apaga no servidor e recarrega a versão publicada.
  const discardDraftMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string }>(`/workout-plan-templates/${numericId}/draft`, { method: 'DELETE' }),
    onSettled: async (_data, error) => {
      // 404 = já não havia rascunho (descartado em outra aba): mesmo efeito.
      if (error && !(error instanceof ApiError && error.status === 404)) {
        notifications.show({
          color: 'red',
          message: errorMessage(error, 'Não foi possível descartar o rascunho.'),
        });
        return;
      }
      forgetDraft();
      setInitialized(false);
      await qc.invalidateQueries({ queryKey: ['workout-plan-templates'] });
      notifications.show({ color: 'gray', message: 'Rascunho descartado. O editor voltou para a versão publicada.' });
    },
  });

  // O rascunho deixou de existir no servidor (publicado, revertido ou
  // descartado): o cache e o aviso do topo acompanham.
  function forgetDraft() {
    setDraftUpdatedAt(null);
    setItemIdsFromDraft(false);
    qc.removeQueries({ queryKey: ['workout-plan-template-draft', numericId] });
    qc.setQueryData<WorkoutPlanTemplateDetail>(['workout-plan-template', numericId], (old) =>
      old ? { ...old, has_draft: false, draft_updated_at: null } : old,
    );
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload = buildPayload();
      savedSlotsSnapshot.current = slots;
      const path = isNew ? '/workout-plan-templates' : `/workout-plan-templates/${numericId}`;
      const method = isNew ? 'POST' : 'PUT';
      return apiFetch<WorkoutPlanTemplateDetail>(path, { method, body: JSON.stringify(payload) });
    },
    onSuccess: async (data) => {
      await qc.invalidateQueries({ queryKey: ['workout-plan-templates'] });
      setWarnings(data.volume_coverage_warnings || []);
      setSafetyWarnings(data.safety_warnings || []);
      // Versão nova = itens com ids novos: refaz o mapa para os cards
      // recolhidos continuarem lembrados depois de recarregar.
      if (savedSlotsSnapshot.current && Array.isArray(data.slots)) {
        setItemServerIds(mapSavedItemServerIds(savedSlotsSnapshot.current, data.slots));
      }
      savedSlotsSnapshot.current = null;
      // PR4 + 067: o PUT diz se gerou versão nova ("Publicado como vN") ou
      // não ("Modelo atualizado (sem nova versão)"). Criação = v1.
      notifications.show({
        color: 'green',
        message: isNew ? publishNotice({ version_created: true, current_version: data.current_version }) : publishNotice(data),
      });
      if (!isNew) forgetDraft();
      if (isNew) {
        // O rascunho virou modelo: não deve sobrar nada na sessão para
        // reabrir depois.
        clearPeriodizationSeed();
        setPeriodizationSeed(null);
        router.replace(`/modelos-treino/${data.id}`);
      } else {
        await qc.invalidateQueries({ queryKey: ['workout-plan-template', numericId] });
        await qc.invalidateQueries({ queryKey: ['workout-plan-template-versions', numericId] });
      }
    },
    onError: (error: unknown) => {
      // 422 (PR-12/D14): item NOVO ou ALTERADO com técnica/RPE incompatível
      // com o teto de segurança. O `detail` já vem em português
      // identificando slot e item — é exibido literalmente, sem fechar
      // sozinho, para o usuário conseguir ler e corrigir o item citado.
      const blockedBySafety = error instanceof ApiError && error.status === 422;
      notifications.show({
        color: 'red',
        title: blockedBySafety ? 'Bloqueado por segurança' : 'Erro ao salvar',
        message: errorMessage(error, 'Erro ao salvar modelo de treino.'),
        autoClose: blockedBySafety ? false : undefined,
        withCloseButton: true,
      });
    },
  });

  function handleSave() {
    if (!name.trim()) {
      notifications.show({ color: 'red', message: 'Informe um nome para o modelo de treino.' });
      return;
    }
    const payloadGrid = gridPayload(grid, slots.length);
    const validationError = validateSlots(slots, volumeMode, trainingTypeId, {
      grid: payloadGrid.grid_rows != null && payloadGrid.grid_cols != null ? grid : null,
      maxSlots: Math.max(MAX_TEMPLATE_SLOTS, savedSlotCount),
    });
    if (validationError) {
      notifications.show({ color: 'red', title: 'Modelo inválido', message: validationError });
      return;
    }
    saveMutation.mutate();
  }

  /**
   * Descarta o rascunho gerado. Não há nada a apagar em banco (nenhum INSERT
   * aconteceu): basta limpar a sessão e voltar para a listagem — o `DELETE` de
   * modelo não é nem chamado, porque não existe modelo.
   */
  function discardDraft() {
    clearPeriodizationSeed();
    setPeriodizationSeed(null);
    notifications.show({ color: 'gray', message: 'Rascunho descartado. Nenhum modelo foi criado.' });
    router.push('/modelos-treino');
  }

  const forbidden = templateQuery.error instanceof ApiError && templateQuery.error.status === 403;

  if (!isNew && templateQuery.isError) {
    return (
      <Stack gap="md">
        <Title order={1} c={BRAND.textPrimary}>
          Modelo de treino
        </Title>
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {forbidden
            ? 'Acesso restrito a administradores e personal trainers.'
            : errorMessage(
                templateQuery.error,
                'Não foi possível carregar este modelo de treino (talvez não exista mais).',
              )}
        </Alert>
        <Button
          variant="light"
          color="brand"
          leftSection={<IconArrowLeft size={16} />}
          onClick={() => router.push('/modelos-treino')}
        >
          Voltar para modelos de treino
        </Button>
      </Stack>
    );
  }

  if (!initialized) {
    return (
      <Group justify="center" py="xl">
        <Loader color="brand" />
      </Group>
    );
  }

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <Group gap="sm">
          <ActionIcon
            variant="light"
            color="brand"
            onClick={() => router.push('/modelos-treino')}
            aria-label="Voltar"
          >
            <IconArrowLeft size={18} />
          </ActionIcon>
          <div>
            <Title order={1} c={BRAND.textPrimary}>
              {periodizationSeed
                ? 'Rascunho gerado (não salvo)'
                : isNew
                  ? 'Novo modelo de treino'
                  : `Editando: ${name}`}
              {!isNew && versionLabel(templateQuery.data?.current_version?.number) && (
                <Badge ml="sm" variant="outline" color="gray" size="lg" style={{ verticalAlign: 'middle' }}>
                  {versionLabel(templateQuery.data?.current_version?.number)}
                </Badge>
              )}
            </Title>
            <Text c={BRAND.textMuted} size="md">
              Monte os slots cíclicos (grupo muscular/cardio) que o motor de geração de ficha usará para este modelo.
            </Text>
          </div>
        </Group>
        <Group>
          {periodizationSeed && (
            <Button variant="light" color="red" leftSection={<IconTrash size={16} />} onClick={discardDraft}>
              Descartar rascunho
            </Button>
          )}
          {/* Fase 4 (PR G): rota DE TOPO própria — nunca /modelos-treino/<id>/…, que a
              CloudFront Function reescreveria para este mesmo builder. Só Modelo salvo. */}
          {isDesktop && (
            <Tooltip
              label={closedPanelIds.length > 0 ? 'Reabrir as janelas fechadas' : 'Todas as janelas estão abertas'}
              openDelay={400}
            >
              <Button
                variant="light"
                color="brand"
                leftSection={<IconAppWindow size={16} />}
                onClick={() => floatingPanels.reopen(closedPanelIds)}
                disabled={closedPanelIds.length === 0}
              >
                Janelas
              </Button>
            </Tooltip>
          )}
          <Button
            variant="light"
            color="brand"
            leftSection={<IconChartBar size={16} />}
            onClick={() => numericId && router.push(`/diagnostico-volume/${numericId}`)}
            disabled={isNew || !numericId}
          >
            Diagnóstico de volume
          </Button>
          <Button
            variant="light"
            color="brand"
            leftSection={<IconUsers size={16} />}
            onClick={openAssign}
            disabled={isNew}
          >
            Atribuir usuários
          </Button>
          {/* Modelo novo: só "Publicar modelo" (cria a v1) — nunca existe Modelo sem versão vigente. */}
          {!isNew && (
            <Button
              variant="default"
              onClick={() => saveDraftMutation.mutate()}
              loading={saveDraftMutation.isPending}
              disabled={saveMutation.isPending}
            >
              {SAVE_DRAFT_LABEL}
            </Button>
          )}
          <Button
            color="brand"
            onClick={handleSave}
            loading={saveMutation.isPending}
            disabled={saveDraftMutation.isPending}
          >
            {PUBLISH_LABEL}
          </Button>
        </Group>
      </Group>

      {!isNew && draftUpdatedAt && (
        <TemplateDraftBanner
          updatedAt={draftUpdatedAt}
          currentVersionNumber={templateQuery.data?.current_version?.number}
          discarding={discardDraftMutation.isPending}
          onDiscard={() => discardDraftMutation.mutate()}
        />
      )}

      {periodizationSeed && (
        <Alert color="violet" icon={<IconInfoCircle size={16} />} title="Rascunho ainda não salvo">
          <Text size="sm" c={BRAND.textPrimary}>
            Este modelo foi gerado automaticamente e <b>não existe no banco de dados</b>. Revise e edite o que quiser:
            ele só é criado quando você clicar em &quot;Publicar modelo&quot;. &quot;Descartar rascunho&quot; não deixa
            resíduo — não há o que apagar.
          </Text>
        </Alert>
      )}

      {isNew && !periodizationSeed && (
        <Text size="xs" c={BRAND.textMuted}>
          Publique o modelo primeiro para poder atribuí-lo a usuários.
        </Text>
      )}

      <Grid gutter="xl" align="stretch">
      <Grid.Col span={{ base: 12, md: periodizationSeed ? 7 : 12 }}>
      <Paper
        withBorder
        radius="lg"
        p="lg"
        h="100%"
        style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
      >
        <Group grow align="flex-start">
          <BlurCommitTextInput label="Nome" value={name} onCommit={(v) => setName(v)} required />
          <Switch
            color="brand"
            label={isActive ? 'Ativo' : 'Inativo'}
            checked={isActive}
            onChange={(e) => setIsActive(e.currentTarget.checked)}
            mt={28}
          />
        </Group>
        <BlurCommitTextarea
          label="Descrição"
          mt="sm"
          value={description}
          onCommit={(v) => setDescription(v)}
          autosize
          minRows={1}
        />
        {volumeMode === 'percent_budget' && (
          <Select
            label="Meta de ativação associada"
            description="Meta semanal de volume por grupo muscular usada pelo motor de ficha v2 para avaliar a cobertura dos slots."
            mt="sm"
            placeholder="Nenhuma"
            clearable
            searchable
            data={trainingTypeOptions}
            value={trainingTypeId}
            onChange={setTrainingTypeId}
            disabled={trainingTypesQuery.isLoading}
          />
        )}
        <Select
          label="Modo de volume"
          description={VOLUME_MODE_DESCRIPTION[volumeMode]}
          mt="sm"
          data={VOLUME_MODE_OPTIONS}
          value={volumeMode}
          onChange={(v) => handleVolumeModeChange((v as VolumeMode) || 'percent_budget')}
          allowDeselect={false}
        />
        {volumeMode === 'equipment_series' && (
          <Select
            label="Contagem do volume"
            description={countingModeOption(countingMode).description}
            mt="sm"
            data={COUNTING_MODE_SELECT_DATA as { value: string; label: string }[]}
            value={countingMode}
            onChange={(v) => setCountingMode(normalizeCountingMode(v))}
            allowDeselect={false}
            data-testid="volume-counting-mode-select"
          />
        )}
        {/* M97 F3: objetivo do Modelo; cada item pode sobrepor (card) e o slot
            tem o atalho "Objetivo do dia". Rótulos e defaults vêm da API. */}
        <Select
          label="Objetivo"
          description={trainingGoalDescription(goalOption(trainingGoals, trainingGoal))}
          mt="sm"
          data={goalSelectData(trainingGoals)}
          value={goalValue(trainingGoals, trainingGoal)}
          onChange={(v) => v && setTrainingGoal(v)}
          allowDeselect={false}
          disabled={!trainingGoals}
          placeholder={trainingGoalsQuery.isError ? 'Não foi possível carregar os objetivos' : undefined}
          data-testid="training-goal-select"
        />
      </Paper>
      </Grid.Col>
      {periodizationSeed && (
        <Grid.Col span={{ base: 12, md: 5 }}>
          <PeriodizationDraftPanel seed={periodizationSeed} />
        </Grid.Col>
      )}
      </Grid>

      {safetyWarnings.length > 0 && (
        <Alert
          color="red"
          icon={<IconAlertTriangle size={16} />}
          title={`Este modelo tem ${safetyWarnings.length} ${
            safetyWarnings.length === 1 ? 'item a revisar' : 'itens a revisar'
          } por segurança`}
        >
          <Text size="sm" c={BRAND.textPrimary}>
            Os avisos aparecem no card de cada item. A edição continua liberada: itens antigos que você não alterar
            seguem sendo salvos normalmente — mas um item novo ou alterado com técnica/RPE incompatível será recusado
            ao salvar.
          </Text>
        </Alert>
      )}

      {warnings.length > 0 && (
        <Alert color="yellow" icon={<IconAlertTriangle size={16} />} title="Cobertura de volume (não bloqueante)">
          <Stack gap={4}>
            {warnings.map((w) => (
              <Group key={w.muscle_group_id} gap="xs">
                <Badge color={VOLUME_COVERAGE_STATUS_COLOR[w.status]} variant="light">
                  {VOLUME_COVERAGE_STATUS_LABEL[w.status]}
                </Badge>
                <Text size="sm" c={BRAND.textPrimary}>
                  {w.muscle_group_name}: {w.sum_pct_min}%–{w.sum_pct_max}% da meta semanal cobertos pelos slots.
                </Text>
              </Group>
            ))}
          </Stack>
        </Alert>
      )}

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="sm" align="flex-end" wrap="wrap">
          <div>
            <Title order={3} c={BRAND.textPrimary}>
              Slots do ciclo
            </Title>
            <Text size="xs" c={BRAND.textMuted}>
              A grade é lida linha por linha: a 1ª posição é o Dia 1 do ciclo. Arraste um slot pela alça para mudar a
              ordem.
            </Text>
          </div>
          <Group gap="xs" align="flex-end" wrap="wrap">
            <Select
              size="xs"
              label="Linhas"
              data={GRID_ROW_OPTIONS}
              value={String(grid.rows)}
              onChange={(v) => v && resizeGrid({ rows: Number(v) })}
              allowDeselect={false}
              disabled={legacyOverflow}
              w={80}
            />
            <Select
              size="xs"
              label="Colunas"
              data={GRID_COL_OPTIONS}
              value={String(grid.cols)}
              onChange={(v) => v && resizeGrid({ cols: Number(v) })}
              allowDeselect={false}
              disabled={legacyOverflow}
              w={80}
            />
            <Button
              size="xs"
              variant="subtle"
              color="brand"
              leftSection={<IconChevronsUp size={14} />}
              onClick={() => setAllSlotsCollapsed(true)}
              disabled={slots.length === 0}
            >
              Recolher todos
            </Button>
            <Button
              size="xs"
              variant="subtle"
              color="brand"
              leftSection={<IconChevronsDown size={14} />}
              onClick={() => setAllSlotsCollapsed(false)}
              disabled={collapsedSlotIds.size === 0}
            >
              Expandir todos
            </Button>
            <Tooltip label={addSlotBlockedReason} disabled={slotAddAllowed} multiline w={260}>
              {/* span: Tooltip precisa de um alvo que receba eventos com o botão desabilitado. */}
              <span>
                <Button
                  size="xs"
                  variant="light"
                  color="brand"
                  leftSection={<IconPlus size={14} />}
                  onClick={addSlot}
                  disabled={!slotAddAllowed}
                >
                  Adicionar slot
                </Button>
              </span>
            </Tooltip>
          </Group>
        </Group>

        {legacyOverflow && (
          <Alert color="yellow" icon={<IconAlertTriangle size={16} />} mb="sm">
            Este modelo tem {slots.length} slots, acima do máximo de {MAX_TEMPLATE_SLOTS}. Ele continua editável, mas não
            é possível adicionar slots nem mudar a grade.
          </Alert>
        )}

        {muscleGroupsQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="sm">
            {errorMessage(muscleGroupsQuery.error, 'Não foi possível carregar os grupos musculares.')}
          </Alert>
        )}

        <DndContext
          sensors={dndSensors}
          collisionDetection={slotAwareCollision}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
          onDragCancel={() => setActiveDragSlotId(null)}
        >
          <SortableContext items={slots.map((s) => s.localId)} strategy={rectSortingStrategy}>
            {/* Em 1 coluna (celular) a ordem de leitura é a mesma da grade. */}
            <SimpleGrid
              cols={isDesktop ? (legacyOverflow ? GRID_MAX_COLS : grid.cols) : 1}
              spacing="md"
              verticalSpacing="md"
              style={{ alignItems: 'start' }}
            >
              {slots.map((slot, slotIndex) => (
                <SlotEditor
                  key={slot.localId}
                  slot={slot}
                  slotIndex={slotIndex}
                  slotCount={slots.length}
                  volumeMode={volumeMode}
                  trainingTypeId={trainingTypeId}
                  trainingGoals={trainingGoals}
                  templateGoal={trainingGoal}
                  safetyWarnings={safetyWarnings}
                  itemActivationByKey={itemActivationByKey}
                  itemPriorFatigueByKey={itemPriorFatigueByKey}
                  itemDirectShareByKey={itemDirectShareByKey}
                  itemBudgets={activationPreviewQuery.data?.item_budgets}
                  methodologyEvaluation={methodologyVolumeQuery.data}
                  methodologyPending={methodologyHasAnyFilledSlot && methodologyVolumeQuery.isPending}
                  muscleGroupOptions={muscleGroupOptions}
                  muscleGroupNameById={muscleGroupNameById}
                  muscleGroupParentById={muscleGroupParentById}
                  expandedItemIds={expandedItemIds}
                  collapsed={collapsedSlotIds.has(slot.localId)}
                  onToggleCollapsed={() => toggleSlotCollapsed(slot.localId)}
                  onToggleItemExpanded={toggleItemExpanded}
                  collapsedItemIds={collapsedItemIds}
                  onToggleItemCollapsed={toggleItemCollapsed}
                  onSetItemsCollapsed={setSlotItemsCollapsed}
                  onChangeSlot={(patch) => updateSlot(slot.localId, patch)}
                  onMoveSlot={(direction) => moveSlot(slot.localId, direction)}
                  onRemoveSlot={() => removeSlot(slot.localId)}
                  onAddItem={(kind) => addItem(slot.localId, kind)}
                  onChangeItem={(itemLocalId, patch) => updateItem(slot.localId, itemLocalId, patch)}
                  onRemoveItem={(itemLocalId) => removeItem(slot.localId, itemLocalId)}
                  onApplyBatchPct={(draft) => applyBatchPctToSlot(slot.localId, draft)}
                />
              ))}

              {/* Células livres só no fim da grade (nunca no meio). */}
              {Array.from({ length: emptyCellCount }, (_, i) => (
                <Paper
                  key={`empty-${i}`}
                  radius="md"
                  p="md"
                  style={{ border: `1px dashed ${BRAND.border}`, background: 'transparent' }}
                >
                  <Stack gap={4} align="center">
                    <Text size="xs" c={BRAND.textMuted}>
                      Dia {slots.length + i + 1} (livre)
                    </Text>
                    <Button
                      size="xs"
                      variant="subtle"
                      color="brand"
                      leftSection={<IconPlus size={12} />}
                      onClick={addSlot}
                      disabled={!slotAddAllowed}
                    >
                      Adicionar slot
                    </Button>
                  </Stack>
                </Paper>
              ))}
            </SimpleGrid>
          </SortableContext>

          <DragOverlay>
            {activeDragSlotId
              ? (() => {
                  const index = slots.findIndex((s) => s.localId === activeDragSlotId);
                  const dragged = slots[index];
                  return dragged ? (
                    <SlotDragPreview
                      slot={dragged}
                      slotIndex={index}
                      safetyWarningCount={countSlotSafetyWarnings(safetyWarnings, index, dragged)}
                    />
                  ) : null;
                })()
              : null}
          </DragOverlay>
        </DndContext>

        {slots.length === 0 && (
          <Text c={BRAND.textMuted} ta="center" py="md">
            Nenhum slot configurado ainda. Use &quot;Adicionar slot&quot; para começar o ciclo.
          </Text>
        )}

        <Tooltip label="No máximo 3 itens por slot podem ser marcados como priorizados" position="top-start">
          <Text size="xs" c={BRAND.textMuted} mt="sm">
            <IconInfoCircle size={12} style={{ verticalAlign: 'middle' }} /> Dica: slots do tipo &quot;descanso&quot;
            só podem ter itens de cardio.
          </Text>
        </Tooltip>
      </Paper>

      {/* Fase 4 (PR F): duas leituras do MESMO Modelo em unidades DIFERENTES —
          cada cabeçalho escreve a sua. Fase 7: uma embaixo da outra, em
          largura total. Primeiro a metodologia (séries diretas e exercícios
          por sessão, faixa por macro-grupo × frequência); abaixo, a ativação
          ponderada (séries equivalentes, meta do tipo de treino). Nunca
          comparar um número de um painel com o outro. */}
      <Stack gap="md">
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Title order={3} c={BRAND.textPrimary}>
            Metodologia de volume por grupo
          </Title>
          <Text size="sm" c={BRAND.textMuted} mb="sm">
            Unidade: séries diretas e exercícios por sessão (metodologia) — outra unidade, não compare com a
            ativação ponderada abaixo.
          </Text>
          <MethodologyVolumePanel
            query={methodologyVolumeQuery}
            hasAnyFilledSlot={methodologyHasAnyFilledSlot}
            intent={methodologyIntent}
            onIntentChange={setMethodologyIntent}
            targetGroupIds={methodologyTargetGroupIds}
            onTargetGroupIdsChange={setMethodologyTargetGroupIds}
            muscleGroupOptions={muscleGroupOptions}
          />
        </Paper>

        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Title order={3} c={BRAND.textPrimary}>
            Intensidade × Densidade por grupo
          </Title>
          <Text size="sm" c={BRAND.textMuted} mb="sm">
            Direção de 0% (Densidade/Volume, vermelho) a 100% (Intensidade, verde), ponderada pelas séries
            equivalentes do grupo (principal + secundário) na janela de 7 dias. Intensidade: RPE 9–10 e técnica
            de falha; Densidade/Volume: reps, pico de contração, descanso curto e séries.
          </Text>
          <IntensityDensityTable
            rows={intensityDensityRows}
            hasAnyFilledSlot={activationHasAnyFilledSlot}
            isPending={activationPreviewQuery.isPending}
            isError={activationPreviewQuery.isError}
            error={activationPreviewQuery.error}
          />
        </Paper>

        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Title order={3} c={BRAND.textPrimary}>
            Preview de ativação semanal (grupos primários)
          </Title>
          <Text size="sm" c={BRAND.textMuted} mb="sm">
            Unidade: séries equivalentes ponderadas (meta do tipo de treino).
          </Text>
          <WeeklyActivationPreviewTable
            slots={previewSlots}
            volumeMode={volumeMode}
            trainingTypeId={previewTrainingTypeId}
            metric="primary_equivalent"
            totalColumnPrefix="Total primário"
          />
        </Paper>
      </Stack>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Title order={3} c={BRAND.textPrimary}>
          Preview de ativação semanal
        </Title>
        <Text size="sm" c={BRAND.textMuted} mb="sm">
          Unidade: séries equivalentes ponderadas (meta do tipo de treino), primário + secundário + estabilizador.
        </Text>
        <WeeklyActivationPreviewTable
          slots={previewSlots}
          volumeMode={volumeMode}
          trainingTypeId={previewTrainingTypeId}
        />
      </Paper>

      {isDesktop && volumeMode === 'equipment_series' && !floatingPanels.isClosed(WEEKLY_SETS_PANEL_ID) && (
        <WeeklySetsFloatingPanel
          index={0}
          controls={floatingPanels.controls(WEEKLY_SETS_PANEL_ID)}
          evaluation={methodologyVolumeQuery.data}
          isPending={methodologyHasAnyFilledSlot && methodologyVolumeQuery.isPending}
          isError={methodologyVolumeQuery.isError}
        />
      )}
      {isDesktop && !floatingPanels.isClosed(ROLE_ACTIVATION_PANEL_ID) && (
        <RoleActivationFloatingPanel
          index={volumeMode === 'equipment_series' ? 1 : 0}
          controls={floatingPanels.controls(ROLE_ACTIVATION_PANEL_ID)}
          response={activationPreviewQuery.data}
          isPending={activationPreviewQuery.isFetching && !activationPreviewQuery.data}
          isError={activationPreviewQuery.isError}
        />
      )}
      {isDesktop && !floatingPanels.isClosed(INTENSITY_DENSITY_PANEL_ID) && (
        <IntensityDensityFloatingPanel
          index={volumeMode === 'equipment_series' ? 2 : 1}
          controls={floatingPanels.controls(INTENSITY_DENSITY_PANEL_ID)}
          rows={intensityDensityRows}
          isPending={activationPreviewQuery.isFetching && !activationPreviewQuery.data}
          isError={activationPreviewQuery.isError}
        />
      )}

      {!isNew && numericId && (
        <TemplateVersionHistoryPanel
          templateId={numericId}
          canManage={canManageVersions}
          hasDraft={draftUpdatedAt != null}
          onReverted={async (detail) => {
            // O Modelo voltou ao conteúdo da versão escolhida: recarrega o
            // builder a partir do detalhe novo (a hidratação roda de novo).
            // Reverter também descartou o rascunho no servidor (067).
            forgetDraft();
            qc.setQueryData(['workout-plan-template', numericId], detail);
            setInitialized(false);
            await qc.invalidateQueries({ queryKey: ['workout-plan-templates'] });
            notifications.show({
              color: 'green',
              message: `Modelo revertido. Versão vigente: ${versionLabel(detail.current_version?.number) ?? '—'}.`,
            });
          }}
        />
      )}

      {!isNew && numericId && (
        <AssignTemplateModal
          templateId={numericId}
          opened={assignOpened}
          onClose={closeAssign}
          onAssigned={() => qc.invalidateQueries({ queryKey: ['workout-plan-template', numericId] })}
        />
      )}
    </Stack>
  );
}
