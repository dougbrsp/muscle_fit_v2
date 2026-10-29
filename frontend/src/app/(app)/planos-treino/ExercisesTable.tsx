'use client';

import { ActionIcon, Autocomplete, Badge, Button, Checkbox, Group, Loader, NumberInput, Table, Text, Textarea, TextInput, Tooltip } from '@mantine/core';
import type { ComboboxItem, OptionsFilter } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { DragDropContext, Draggable, Droppable, type DropResult } from '@hello-pangea/dnd';
import { IconAdjustments, IconArrowDown, IconArrowUp, IconBarbell, IconGripVertical, IconLock, IconPlus, IconReplace, IconScale, IconTrash } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { toActivationPayloadItem } from '@/lib/activation-payload';
import { filterBySwapEligibility, swapEligibilityFor, type SwapEligibilityIndex } from '@/lib/exercise-swap-options';
import { apiFetch } from '@/lib/api';
import type { MuscleActivationBreakdownItem } from '@/lib/analytics-types';
import type { MappingCanonicalOptionsResponse } from '@/lib/mappings-types';
import { getAdvancedTechniqueLabel } from '@/lib/advanced-techniques';
import { useAdvancedTechniques } from '@/lib/use-advanced-techniques';
import {
  PRESCRIPTION_WARNING_COLORS,
  PRESCRIPTION_WARNING_LABELS,
  PRESCRIPTION_WARNING_TOOLTIPS,
  substitutionWarningPresentation,
  type PrescriptionWarning,
  type SuggestedProgressionResponse,
  type WorkoutPlanExercise,
} from '@/lib/workout-plans-types';
import { suggestedProgressionTargets } from '@/lib/suggested-progression';
import { DEFAULT_REPS_TARGET, DEFAULT_RPE_TARGET } from '@/lib/prescription-defaults';
import { BRAND } from '@/theme';
import { AdvancedConfigModal, type AdvancedConfigPatch } from './AdvancedConfigModal';

// Larguras mínimas (px) dos inputs curtos da tabela. Aplicadas no input E no
// cabeçalho: com `table-layout: auto`, a largura fixa (`w`) do <th> era
// espremida pelas colunas flexíveis e cortava os valores ("6-" em vez de
// "6-10", "10k" em vez de "10kg"). Dimensionadas para o pior caso de cada
// campo em fonte xs: Séries "20" + spinner, Reps "10-12", Carga "102,5 kg",
// RPE "9,5"/"10", Descanso "180" + spinner. Se a soma passar do container, o
// `Table.ScrollContainer` rola na horizontal — a página não.
const COL_WIDTH = {
  sets: 72,
  reps: 84,
  load: 100,
  rpe: 68,
  rest: 84,
} as const;

const NOWRAP = { whiteSpace: 'nowrap' } as const;

type Props = {
  exercises: WorkoutPlanExercise[];
  onChange: (exercises: WorkoutPlanExercise[]) => void;
  /**
   * Dispara `POST /workout-plans/{id}/refresh-prescription`. Só é passado
   * pela tela de detalhe quando a ficha está `planned` e com data de hoje em
   * diante — nas demais, o backend recusaria com 409 e o botão nem aparece.
   */
  onRefreshPrescription?: () => void;
  refreshingPrescription?: boolean;
  refreshPrescriptionError?: string | null;
  /**
   * M8: abre a troca de EQUIPAMENTO da linha (`EquipmentSwapModal`). Só é
   * passado pela tela de detalhe de uma ficha salva, `planned`, com data de
   * hoje em diante e academia definida — mesmas condições do backend. O botão
   * só aparece em linhas já persistidas e vinculadas ao catálogo.
   */
  onSwapEquipment?: (exercise: WorkoutPlanExercise) => void;
  /**
   * Ficha finalizada (o servidor diz em `plan.editable`): tudo somente
   * leitura, sem adicionar, remover ou reordenar.
   */
  readOnly?: boolean;
  /**
   * Exercícios elegíveis para a troca em cada linha SALVA, prontos do
   * servidor (`GET /workout-plans/{id}/exercise-swap-options`, mesma regra do
   * 422 `muscle_group_locked`). `'loading'` enquanto a leitura não chega;
   * ausente = tabela sem linhas salvas (rascunho), sem restrição.
   */
  swapOptions?: SwapEligibilityIndex | 'loading';
};

// Sem digitação (foco/clique num campo já preenchido) a lista mostra todas as
// opções da linha: o filtro por texto usaria o próprio nome salvo como busca e
// esconderia as demais.
const showAllOptions: OptionsFilter = ({ options }) => options;

// Linha já salva na ficha: o grupamento muscular dela é travado (o servidor
// recusa a mudança com 422). Linha nova escolhe o grupo livremente.
function isSavedRow(exercise: WorkoutPlanExercise): boolean {
  return exercise.workout_plan_exercise_id != null;
}

// Badges da prescrição CONGELADA (PR-11): o que foi prescrito, o que a
// segurança cortou e o que exige decisão do usuário. Nada aqui é editável —
// a prescrição é read-only e o servidor a preserva no PUT.
function prescriptionBadges(exercise: WorkoutPlanExercise) {
  const badges: { key: string; color: string; label: string; tooltip: string }[] = [];
  const prescription = exercise.prescription;
  const overlayClamps = exercise.safety_overlay?.clamps || [];
  const clamps = [...(prescription?.clamps || []), ...overlayClamps];

  if (!prescription) {
    if (exercise.prescription_status === 'LEGACY') {
      badges.push({
        key: 'legacy',
        color: 'yellow',
        label: 'Prescrição legada',
        tooltip:
          'Esta ficha foi criada antes do congelamento da prescrição. Ela é exibida como está, sem recálculo. Use "Atualizar prescrição" para gerar uma nova.',
      });
    }
    return badges;
  }

  if (prescription.effective_rpe_target !== null) {
    badges.push({
      key: 'rpe',
      color: 'blue',
      label: `RPE alvo ${prescription.effective_rpe_target}`,
      tooltip: `Teto seguro ${prescription.effective_rpe_ceiling ?? '?'}${
        prescription.failure_allowed ? ' (falha permitida)' : ' (falha não permitida)'
      }. Congelada em ${new Date(prescription.frozen_at).toLocaleString('pt-BR')}.`,
    });
  }
  if (clamps.length > 0) {
    badges.push({
      key: 'clamp',
      color: 'orange',
      label: 'RPE limitado por segurança',
      tooltip: clamps.map((c) => `${c.field}: ${c.from} → ${c.to} (${c.reason})`).join(' | '),
    });
  }
  if (prescription.phase) {
    badges.push({
      key: 'phase',
      color: 'grape',
      label: `Fase ${prescription.phase}`,
      tooltip: prescription.reason || `Decisão: ${prescription.decision ?? '—'}`,
    });
  }
  // Um badge por warning congelado, com rótulo e tooltip ESPECÍFICOS do
  // código (PR-12) — o texto genérico anterior não dizia o que tinha
  // acontecido nem o que o usuário deveria fazer. Códigos desconhecidos
  // (backend mais novo que o frontend) caem no próprio código como rótulo.
  for (const warning of prescription.warnings || []) {
    const code = warning as PrescriptionWarning;
    // M6: o mesmo código cobre a troca de EXERCÍCIO (outro do mesmo grupo)
    // e a de equipamento; `prescription.substitution.kind` diz qual foi.
    const substituted =
      code === 'EQUIPMENT_SUBSTITUTED' ? substitutionWarningPresentation(prescription.substitution) : null;
    badges.push({
      key: `warning-${warning}`,
      color: PRESCRIPTION_WARNING_COLORS[code] || 'red',
      label: substituted?.label || PRESCRIPTION_WARNING_LABELS[code] || warning,
      tooltip:
        substituted?.tooltip ||
        PRESCRIPTION_WARNING_TOOLTIPS[code] ||
        'Aviso registrado no momento em que a prescrição foi congelada.',
    });
  }
  if (exercise.requires_user_choice) {
    badges.push({
      key: 'choice',
      color: 'red',
      label: 'Exige sua escolha',
      tooltip:
        'Este exercício não pode ser iniciado no modo treino enquanto você não escolher o equipamento. Abra o modo treino e use "Escolher equipamento" no card deste exercício.',
    });
  }
  return badges;
}

function progressionBadge(progression?: WorkoutPlanExercise['progression']) {
  if (progression === 'increased') return <Badge color="green" variant="light">Carga subiu</Badge>;
  if (progression === 'decreased') return <Badge color="red" variant="light">Carga caiu</Badge>;
  if (progression === 'same') return <Badge color="gray" variant="light">Mantida</Badge>;
  return null;
}

// Badges informativos (somente leitura) dos campos de técnica avançada/
// aquecimento/série de ajuste/pico de contração — configurados no builder de
// modelos (item_kind='equipment', modo 'equipment_series') e persistidos em
// workout_plan_exercises, mas até agora invisíveis na ficha salva.
function advancedConfigBadges(exercise: WorkoutPlanExercise) {
  const badges: { key: string; color: string; label: string; tooltip: string }[] = [];

  if (exercise.warmup_sets_count) {
    badges.push({
      key: 'warmup',
      color: 'blue',
      label: 'Aquecimento',
      tooltip: `${exercise.warmup_sets_count} série(s) x ${exercise.warmup_reps ?? '?'} reps, ${exercise.warmup_rest_seconds ?? '?'}s de descanso`,
    });
  }
  if (exercise.feeler_set_sets_count) {
    badges.push({
      key: 'feeler',
      color: 'grape',
      label: 'Série de ajuste',
      tooltip: `${exercise.feeler_set_sets_count} série(s) x ${exercise.feeler_set_reps ?? '?'} reps, ${exercise.feeler_set_rest_seconds ?? '?'}s de descanso`,
    });
  }
  if (exercise.advanced_technique) {
    badges.push({
      key: 'technique',
      color: 'orange',
      label: getAdvancedTechniqueLabel(exercise.advanced_technique) || exercise.advanced_technique,
      tooltip: `${exercise.advanced_technique_applications ?? '?'} aplicação(ões), ${exercise.advanced_technique_duration_seconds ?? '?'}s de pausa`,
    });
  }
  if (exercise.peak_contraction_seconds) {
    badges.push({
      key: 'peak',
      color: 'teal',
      label: `Pico de contração (${exercise.peak_contraction_seconds}s)`,
      tooltip: `Segurar a contração na última repetição da série de trabalho por ${exercise.peak_contraction_seconds}s`,
    });
  }

  return badges;
}

function formatActivationList(items: { muscle_group_name: string; activation: number }[]): string {
  return items
    .map((item) => `${item.muscle_group_name} (${item.activation.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })})`)
    .join(', ');
}

/**
 * Tabela editável de exercícios de uma ficha — usada tanto no rascunho
 * gerado (antes de salvar) quanto na edição de uma ficha já salva. Edição é
 * sempre local (o array inteiro é reescrito via `onChange`); quem usa este
 * componente decide quando persistir (POST/PUT /workout-plans).
 *
 * Reordenação via drag-and-drop (`@hello-pangea/dnd`, mesma lib/versão do
 * onpremise — `WorkoutPlansClient.tsx`), com os botões subir/descer mantidos
 * como fallback acessível (teclado/leitor de tela, sem depender de
 * drag-and-drop). Ambos os caminhos convergem para `moveExercise`, que
 * reindexa `sequence` de 1..N depois de mover.
 */
export function ExercisesTable({
  exercises,
  onChange,
  onRefreshPrescription,
  refreshingPrescription = false,
  refreshPrescriptionError = null,
  onSwapEquipment,
  readOnly = false,
  swapOptions,
}: Props) {
  // Nomes das técnicas vêm do catálogo (GET /advanced-techniques); o hook
  // dispara a carga e re-renderiza os badges quando ela chega.
  useAdvancedTechniques();

  // Índice da linha aguardando a sugestão de reps/carga/RPE (GET
  // /exercises/suggested-progression), disparada ao vincular um exercício do
  // catálogo canônico (onOptionSubmit do Autocomplete) — mesmo padrão do
  // onpremise (WorkoutPlansClient.tsx::applySuggestedProgression). null =
  // nenhuma em andamento.
  const [suggestionLoadingIndex, setSuggestionLoadingIndex] = useState<number | null>(null);
  // Linha com a configuração avançada aberta (`AdvancedConfigModal`).
  const [advancedIndex, setAdvancedIndex] = useState<number | null>(null);
  // Linha cujo autocomplete o usuário está DIGITANDO: só nela a lista filtra
  // pelo texto. Foco/clique zera, para a lista abrir com todas as opções.
  const [typingIndex, setTypingIndex] = useState<number | null>(null);

  // Sempre a versão mais recente de `exercises` — usado só dentro de
  // `applySuggestedProgression` (callback assíncrono) para nunca rebasear a
  // atualização por cima de um snapshot de props já desatualizado (o `await`
  // do fetch atravessa pelo menos um re-render).
  const exercisesRef = useRef(exercises);
  useEffect(() => {
    exercisesRef.current = exercises;
  }, [exercises]);

  // Catálogo canônico para o autocomplete de exercício (Fase 4, Etapa 29).
  // GET /mapping-canonical/options sem `q` já devolve o catálogo inteiro
  // (mesma fonte usada em EquipamentosExerciciosClient.tsx), pequeno o
  // suficiente para carregar uma única vez e filtrar no cliente em cada
  // linha da tabela — evita 1 chamada à API por tecla por linha.
  const exerciseCatalogQuery = useQuery({
    queryKey: ['mapping-canonical-options', 'all'],
    queryFn: () => apiFetch<MappingCanonicalOptionsResponse>('/mapping-canonical/options'),
    staleTime: 5 * 60_000,
  });

  const exerciseCatalogItems = useMemo(
    () => exerciseCatalogQuery.data?.items || [],
    [exerciseCatalogQuery.data],
  );

  // `value` e `label` são o próprio nome canônico (texto puro): o
  // Mantine Autocomplete, ao confirmar uma seleção, reescreve o input com
  // `label` logo em seguida de disparar `onOptionSubmit` — se `label`
  // trouxesse anotação (Amazfit/grupo), o texto final em `exercise_name`
  // ficaria sujo. Amazfit/grupo aparecem só no dropdown, via `renderOption`
  // + `filter` customizados abaixo, sem entrar em `label`.
  const exerciseAutocompleteData = useMemo(
    () =>
      exerciseCatalogItems
        .filter((item) => item.exercise_name)
        .map((item) => ({
          value: item.exercise_name as string,
          label: item.exercise_name as string,
          mappingId: item.id,
          amazfitName: item.amazfit_name,
          primaryGroup: item.primary_group,
        })),
    [exerciseCatalogItems],
  );

  // Nome do grupo primário de cada exercício do catálogo: só preenche o
  // texto do grupo de uma linha NOVA. A troca na linha salva usa os ids
  // elegíveis do servidor (`swapOptions`).
  const primaryGroupByMappingId = useMemo(() => {
    const map = new Map<number, string>();
    for (const item of exerciseCatalogItems) {
      if (item.primary_group) map.set(item.id, item.primary_group);
    }
    return map;
  }, [exerciseCatalogItems]);

  // Autocomplete só devolve o `value` escolhido no callback de seleção —
  // mapa nome -> mapping_id para preencher `mapping_id` automaticamente.
  const mappingIdByExerciseName = useMemo(() => {
    const map = new Map<string, number>();
    for (const item of exerciseCatalogItems) {
      if (item.exercise_name) map.set(item.exercise_name, item.id);
    }
    return map;
  }, [exerciseCatalogItems]);

  // Busca considera nome canônico, nome Amazfit e grupo muscular primário
  // (mesmos campos usados pelo backend em GET /mapping-canonical/options?q=),
  // mesmo com `label` mostrando só o nome puro.
  const exerciseOptionsFilter: OptionsFilter = ({ options, search }) => {
    const needle = search.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => {
      if ('group' in option) return true;
      const item = option as ComboboxItem & { amazfitName?: string | null; primaryGroup?: string | null };
      return (
        item.label.toLowerCase().includes(needle) ||
        (item.amazfitName || '').toLowerCase().includes(needle) ||
        (item.primaryGroup || '').toLowerCase().includes(needle)
      );
    });
  };

  // Ativação muscular POR EXERCÍCIO (não agregada por grupo, como
  // MuscleActivationPreview) — POST /analytics/muscle-activation-breakdown,
  // mesmo contrato de /analytics/muscle-activation-preview mas devolvendo um
  // item por linha, NA MESMA ORDEM/TAMANHO de `exercises` (inclusive linhas
  // sem mapping_id ainda, que voltam com ativação zerada) — permite parear
  // por índice de volta à linha original, sem depender de mapping_id ser
  // único na ficha. Técnica avançada da linha vai junto (payload único, M76
  // E3): o backend soma o equivalente dela às séries.
  const breakdownPayload = useMemo(
    () => ({
      exercises: exercises.map(toActivationPayloadItem),
    }),
    [exercises],
  );
  const breakdownPayloadSerialized = JSON.stringify(breakdownPayload);
  const [debouncedBreakdownPayload] = useDebouncedValue(breakdownPayloadSerialized, 500);
  const hasAnyMappedExercise = exercises.some((ex) => ex.mapping_id != null && (ex.sets_count ?? 0) > 0);

  const activationBreakdownQuery = useQuery({
    queryKey: ['muscle-activation-breakdown', debouncedBreakdownPayload],
    queryFn: () =>
      apiFetch<{ items: MuscleActivationBreakdownItem[] }>('/analytics/muscle-activation-breakdown', {
        method: 'POST',
        body: debouncedBreakdownPayload,
      }),
    enabled: hasAnyMappedExercise,
  });
  const activationBreakdown = activationBreakdownQuery.data?.items || [];

  function updateExercise(index: number, patch: Partial<WorkoutPlanExercise>) {
    const next = exercises.slice();
    next[index] = { ...next[index], ...patch };
    onChange(next);
  }

  // Busca reps/carga/RPE sugeridos com base no histórico real do exercício
  // (mesma lógica determinística de progressão do template ABC fixo,
  // services.suggested_progression_for_mapping) e preenche esses campos da
  // linha — disparada ao vincular um exercício do catálogo canônico.
  // Sobrescreve reps/carga/RPE mesmo se já preenchidos (o vínculo a um novo
  // exercício invalida os valores anteriores, que eram do exercício trocado);
  // falha de rede não quebra a tela: os campos simplesmente continuam como
  // estavam.
  async function applySuggestedProgression(index: number, mappingId: number) {
    setSuggestionLoadingIndex(index);
    try {
      const suggestion = await apiFetch<SuggestedProgressionResponse>(
        `/exercises/suggested-progression?mapping_id=${mappingId}`,
      );
      const current = exercisesRef.current;
      if (index >= current.length) return; // linha removida enquanto a sugestão carregava
      const next = current.slice();
      // Sem faixa do motor (grupo sem trabalho direto -> SKIP) o campo fica
      // vazio, nunca null nem faixa inventada; a nota do motor explica o porquê.
      const targets = suggestedProgressionTargets(suggestion);
      next[index] = {
        ...next[index],
        reps_target: targets.reps_target,
        load_target: targets.load_target,
        rpe_target: targets.rpe_target,
        progression_note: targets.progression_note,
        progression: suggestion.progression ?? null,
      };
      onChange(next);
    } catch {
      // silencioso: mantém os campos como estavam.
    } finally {
      setSuggestionLoadingIndex((current) => (current === index ? null : current));
    }
  }

  function removeExercise(index: number) {
    const next = exercises.filter((_, i) => i !== index);
    next.forEach((ex, i) => {
      ex.sequence = i + 1;
    });
    onChange(next);
  }

  // Reordenação genérica (fromIndex -> toIndex), reindexando `sequence` de
  // 1..N — usada tanto pelos botões subir/descer (toIndex = index ± 1)
  // quanto pelo drag-and-drop (toIndex = destino solto pelo usuário).
  function moveExercise(fromIndex: number, toIndex: number) {
    if (toIndex < 0 || toIndex >= exercises.length || toIndex === fromIndex) return;
    const next = exercises.slice();
    const [moved] = next.splice(fromIndex, 1);
    next.splice(toIndex, 0, moved);
    next.forEach((ex, i) => {
      ex.sequence = i + 1;
    });
    onChange(next);
  }

  function handleDragEnd(result: DropResult) {
    const { source, destination } = result;
    if (!destination || destination.index === source.index) return;
    moveExercise(source.index, destination.index);
  }

  function addExercise() {
    const next = exercises.concat({
      sequence: exercises.length + 1,
      exercise_name: '',
      muscle_primary: '',
      sets_count: 3,
      reps_target: '',
      warmup: false,
    });
    onChange(next);
  }

  const hasLegacyPrescription = exercises.some((ex) => ex.prescription_status === 'LEGACY');

  return (
    <>
      {onRefreshPrescription && !readOnly && (
        <Group justify="space-between" align="center" mb="sm" wrap="wrap">
          <Text size="xs" c={BRAND.textMuted}>
            {hasLegacyPrescription
              ? 'Esta ficha tem exercícios com prescrição legada, de antes do congelamento. Atualizar recalcula a prescrição de todos eles.'
              : 'A prescrição desta ficha está congelada. Atualizar recalcula tudo com o histórico e a segurança de hoje.'}
          </Text>
          <Button
            variant="light"
            color="brand"
            size="xs"
            loading={refreshingPrescription}
            onClick={onRefreshPrescription}
          >
            Atualizar prescrição
          </Button>
        </Group>
      )}
      {refreshPrescriptionError && (
        <Text size="xs" c="red" mb="sm">
          {refreshPrescriptionError}
        </Text>
      )}
      <Table.ScrollContainer minWidth={1500}>
        <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={70} style={NOWRAP}>Ordem</Table.Th>
              <Table.Th miw={200} style={NOWRAP}>Exercício</Table.Th>
              <Table.Th miw={140} style={NOWRAP}>Grupo primário</Table.Th>
              <Table.Th miw={COL_WIDTH.sets} style={NOWRAP}>Séries</Table.Th>
              <Table.Th miw={COL_WIDTH.reps} style={NOWRAP}>Reps</Table.Th>
              <Table.Th miw={COL_WIDTH.load} style={NOWRAP}>Carga</Table.Th>
              <Table.Th miw={COL_WIDTH.rpe} style={NOWRAP}>RPE</Table.Th>
              <Table.Th miw={COL_WIDTH.rest} style={NOWRAP}>Descanso(s)</Table.Th>
              <Table.Th w={70} style={NOWRAP}>Aquec.</Table.Th>
              <Table.Th miw={170} style={NOWRAP}>Config. avançada</Table.Th>
              <Table.Th miw={160}>Notas</Table.Th>
              <Table.Th w={110} style={NOWRAP}>Progressão</Table.Th>
              <Table.Th miw={190}>Prescrição</Table.Th>
              <Table.Th w={onSwapEquipment ? 110 : 90} style={NOWRAP}>Ações</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <DragDropContext onDragEnd={handleDragEnd}>
            <Droppable droppableId="exercises-table">
              {(droppableProvided) => (
          <Table.Tbody ref={droppableProvided.innerRef} {...droppableProvided.droppableProps}>
            {exercises.map((exercise, index) => {
              const configBadges = advancedConfigBadges(exercise);
              const swapEligibility = swapEligibilityFor(exercise.workout_plan_exercise_id, swapOptions);
              const rowOptions = filterBySwapEligibility(exerciseAutocompleteData, swapEligibility);
              const prescBadges = prescriptionBadges(exercise);
              const breakdown = activationBreakdown[index];
              const hasBreakdown =
                !!breakdown &&
                (breakdown.primary_activation > 0 || breakdown.secondary.length > 0 || breakdown.stabilizer.length > 0);
              return (
              <Fragment key={index}>
              <Draggable draggableId={`exercise-${index}`} index={index} isDragDisabled={readOnly}>
                {(draggableProvided) => (
              <Table.Tr ref={draggableProvided.innerRef} {...draggableProvided.draggableProps}>
                <Table.Td>
                  <Group gap={2} wrap="nowrap">
                    <ActionIcon
                      variant="subtle"
                      color="gray"
                      size="sm"
                      {...draggableProvided.dragHandleProps}
                      aria-label="Arrastar para reordenar"
                    >
                      <IconGripVertical size={14} />
                    </ActionIcon>
                    <Text size="sm" c={BRAND.textMuted}>
                      {exercise.sequence}
                    </Text>
                    <ActionIcon variant="subtle" size="sm" disabled={readOnly || index === 0} onClick={() => moveExercise(index, index - 1)} aria-label="Mover para cima">
                      <IconArrowUp size={14} />
                    </ActionIcon>
                    <ActionIcon
                      variant="subtle"
                      size="sm"
                      disabled={readOnly || index === exercises.length - 1}
                      onClick={() => moveExercise(index, index + 1)}
                      aria-label="Mover para baixo"
                    >
                      <IconArrowDown size={14} />
                    </ActionIcon>
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Autocomplete
                    size="xs"
                    disabled={readOnly}
                    data={rowOptions}
                    filter={typingIndex === index ? exerciseOptionsFilter : showAllOptions}
                    onFocus={() => setTypingIndex(null)}
                    onClick={() => setTypingIndex(null)}
                    rightSection={swapEligibility.kind === 'loading' ? <Loader size="xs" /> : undefined}
                    value={exercise.exercise_name}
                    onChange={(value) => {
                      setTypingIndex(index);
                      // Digitação livre: mantém o texto do jeito que o
                      // usuário digitou, sem travar em opções do catálogo
                      // (o backend resolve mapping_id pelo nome ao salvar).
                      // Limpa mapping_id quando o texto deixa de bater com
                      // o nome canônico vinculado — evita ficar com um
                      // vínculo stale após uma edição manual.
                      const matchesLinkedMapping =
                        exercise.mapping_id != null && mappingIdByExerciseName.get(value) === exercise.mapping_id;
                      updateExercise(index, {
                        exercise_name: value,
                        mapping_id: matchesLinkedMapping ? exercise.mapping_id : null,
                      });
                    }}
                    onOptionSubmit={(value) => {
                      const mappingId = mappingIdByExerciseName.get(value) ?? null;
                      // Linha nova sem grupo: herda o grupo primário do
                      // catálogo. Linha salva nunca muda de grupo.
                      const catalogGroup = mappingId != null ? primaryGroupByMappingId.get(mappingId) : undefined;
                      const groupPatch =
                        !isSavedRow(exercise) && !exercise.muscle_primary && catalogGroup
                          ? { muscle_primary: catalogGroup }
                          : {};
                      updateExercise(index, { exercise_name: value, mapping_id: mappingId, ...groupPatch });
                      // Troca de exercício (inclusive substituindo um já
                      // preenchido) invalida reps/carga/RPE anteriores — busca
                      // a sugestão determinística de progressão para o novo
                      // exercício, mesmo comportamento do onpremise.
                      if (mappingId != null) {
                        applySuggestedProgression(index, mappingId);
                      }
                    }}
                    renderOption={({ option }) => {
                      const item = option as ComboboxItem & { amazfitName?: string | null; primaryGroup?: string | null };
                      return (
                        <div>
                          <Text size="sm">{item.label}</Text>
                          {(item.amazfitName || item.primaryGroup) && (
                            <Text size="xs" c={BRAND.textMuted}>
                              {[item.amazfitName, item.primaryGroup].filter(Boolean).join(' — ')}
                            </Text>
                          )}
                        </div>
                      );
                    }}
                    placeholder="Nome do exercício"
                    // Linha salva: a lista elegível (mesmo grupamento) cabe
                    // inteira; linha livre: o catálogo todo, então limita.
                    limit={swapEligibility.kind === 'restricted' ? undefined : 20}
                  />
                  {exercise.equipment_name && (
                    <Text size="xs" c={BRAND.textMuted} mt={2}>
                      {exercise.equipment_name}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  {isSavedRow(exercise) ? (
                    <Tooltip label="O grupamento muscular não pode ser alterado na edição da ficha" withArrow>
                      <Group gap={4} wrap="nowrap" style={{ cursor: 'help' }}>
                        <IconLock size={12} color={BRAND.textMuted} />
                        <Text size="xs" c={BRAND.textPrimary}>
                          {exercise.muscle_primary || '—'}
                        </Text>
                      </Group>
                    </Tooltip>
                  ) : (
                    <TextInput
                      size="xs"
                      disabled={readOnly}
                      value={exercise.muscle_primary || ''}
                      onChange={(e) => updateExercise(index, { muscle_primary: e.currentTarget.value })}
                      placeholder="Grupo muscular"
                    />
                  )}
                </Table.Td>
                <Table.Td>
                  <NumberInput
                    size="xs"
                    miw={COL_WIDTH.sets}
                    disabled={readOnly}
                    value={exercise.sets_count ?? undefined}
                    onChange={(v) => updateExercise(index, { sets_count: typeof v === 'number' ? v : null })}
                    min={1}
                    max={20}
                  />
                </Table.Td>
                <Table.Td>
                  <TextInput
                    size="xs"
                    miw={COL_WIDTH.reps}
                    disabled={readOnly}
                    value={exercise.reps_target || ''}
                    onChange={(e) => updateExercise(index, { reps_target: e.currentTarget.value })}
                    placeholder={suggestionLoadingIndex === index ? 'Calculando...' : DEFAULT_REPS_TARGET}
                    rightSection={suggestionLoadingIndex === index ? <Loader size="xs" /> : undefined}
                  />
                </Table.Td>
                <Table.Td>
                  <TextInput
                    size="xs"
                    miw={COL_WIDTH.load}
                    disabled={readOnly}
                    value={exercise.load_target || ''}
                    onChange={(e) => updateExercise(index, { load_target: e.currentTarget.value })}
                    placeholder={
                      suggestionLoadingIndex === index
                        ? 'Calculando...'
                        : exercise.suggested_load_kg
                          ? `${exercise.suggested_load_kg} kg (sugerido)`
                          : ''
                    }
                    rightSection={suggestionLoadingIndex === index ? <Loader size="xs" /> : undefined}
                  />
                </Table.Td>
                <Table.Td>
                  <TextInput
                    size="xs"
                    miw={COL_WIDTH.rpe}
                    disabled={readOnly}
                    value={exercise.rpe_target || ''}
                    onChange={(e) => updateExercise(index, { rpe_target: e.currentTarget.value })}
                    placeholder={suggestionLoadingIndex === index ? 'Calculando...' : DEFAULT_RPE_TARGET}
                    rightSection={suggestionLoadingIndex === index ? <Loader size="xs" /> : undefined}
                  />
                </Table.Td>
                <Table.Td>
                  <NumberInput
                    size="xs"
                    miw={COL_WIDTH.rest}
                    disabled={readOnly}
                    value={exercise.rest_seconds ?? undefined}
                    onChange={(v) => updateExercise(index, { rest_seconds: typeof v === 'number' ? v : null })}
                    min={15}
                    max={240}
                  />
                </Table.Td>
                <Table.Td>
                  <Checkbox
                    disabled={readOnly}
                    checked={!!exercise.warmup}
                    onChange={(e) => updateExercise(index, { warmup: e.currentTarget.checked })}
                  />
                </Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="wrap">
                    {configBadges.map((badge) => (
                      <Tooltip key={badge.key} label={badge.tooltip} multiline w={220}>
                        <Badge size="xs" color={badge.color} variant="light" style={{ cursor: 'help' }}>
                          {badge.label}
                        </Badge>
                      </Tooltip>
                    ))}
                    {configBadges.length === 0 && (
                      <Text size="xs" c={BRAND.textMuted}>
                        —
                      </Text>
                    )}
                    {!readOnly && (
                      <Tooltip label="Editar técnica avançada, aquecimento, série de ajuste e pico de contração" withArrow>
                        <ActionIcon
                          size="sm"
                          variant="subtle"
                          color="brand"
                          onClick={() => setAdvancedIndex(index)}
                          aria-label="Editar configuração avançada"
                        >
                          <IconAdjustments size={14} />
                        </ActionIcon>
                      </Tooltip>
                    )}
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Textarea
                    size="xs"
                    autosize
                    minRows={1}
                    disabled={readOnly}
                    value={exercise.notes || ''}
                    onChange={(e) => updateExercise(index, { notes: e.currentTarget.value })}
                  />
                </Table.Td>
                <Table.Td>
                  {progressionBadge(exercise.progression)}
                  {exercise.progression_note && (
                    <Tooltip label={exercise.progression_note} multiline w={260}>
                      <Text size="xs" c={BRAND.textMuted} style={{ cursor: 'help' }}>
                        nota
                      </Text>
                    </Tooltip>
                  )}
                </Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="wrap">
                    {prescBadges.map((badge) => (
                      <Tooltip key={badge.key} label={badge.tooltip} multiline w={260}>
                        <Badge size="xs" color={badge.color} variant="light" style={{ cursor: 'help' }}>
                          {badge.label}
                        </Badge>
                      </Tooltip>
                    ))}
                    {prescBadges.length === 0 && (
                      <Text size="xs" c={BRAND.textMuted}>
                        —
                      </Text>
                    )}
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Group gap={2} wrap="nowrap">
                    {!readOnly && onSwapEquipment && exercise.workout_plan_exercise_id != null && exercise.mapping_id != null && (
                      <Tooltip label="Trocar equipamento (só o equipamento; o exercício não muda)" withArrow>
                        <ActionIcon
                          color="brand"
                          variant="subtle"
                          onClick={() => onSwapEquipment(exercise)}
                          aria-label="Trocar equipamento"
                        >
                          <IconReplace size={16} />
                        </ActionIcon>
                      </Tooltip>
                    )}
                    {!readOnly && (
                      <ActionIcon color="red" variant="subtle" onClick={() => removeExercise(index)} aria-label="Remover exercício">
                        <IconTrash size={16} />
                      </ActionIcon>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
                )}
              </Draggable>
              {hasBreakdown && (
                <Table.Tr>
                  <Table.Td colSpan={14}>
                    <Group gap="xs" wrap="wrap">
                      <IconBarbell size={14} color="var(--mantine-color-green-6)" />
                      <Text size="xs" c={BRAND.textMuted}>
                        Ativação primária: {breakdown.primary_activation.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}
                      </Text>
                      {breakdown.secondary.length > 0 && (
                        <>
                          <Text size="xs" c={BRAND.textMuted}>|</Text>
                          <IconBarbell size={14} color="var(--mantine-color-yellow-6)" />
                          <Text size="xs" c={BRAND.textMuted}>
                            Secundários: {formatActivationList(breakdown.secondary)}
                          </Text>
                        </>
                      )}
                      {breakdown.stabilizer.length > 0 && (
                        <>
                          <Text size="xs" c={BRAND.textMuted}>|</Text>
                          <IconScale size={14} color="var(--mantine-color-blue-6)" />
                          <Text size="xs" c={BRAND.textMuted}>
                            Estabilizadores: {formatActivationList(breakdown.stabilizer)}
                          </Text>
                        </>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              )}
              </Fragment>
              );
            })}
            {droppableProvided.placeholder ? (
              <Table.Tr>
                <Table.Td colSpan={14} p={0} style={{ border: 'none' }}>
                  {droppableProvided.placeholder}
                </Table.Td>
              </Table.Tr>
            ) : null}
            {exercises.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={14}>
                  <Text c={BRAND.textMuted} ta="center">
                    Nenhum exercício nesta ficha ainda.
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
              )}
            </Droppable>
          </DragDropContext>
        </Table>
      </Table.ScrollContainer>
      {!readOnly && (
        <Button variant="light" color="brand" size="xs" mt="sm" leftSection={<IconPlus size={14} />} onClick={addExercise}>
          Adicionar exercício
        </Button>
      )}
      <AdvancedConfigModal
        exercise={advancedIndex != null ? exercises[advancedIndex] ?? null : null}
        onClose={() => setAdvancedIndex(null)}
        onApply={(patch: AdvancedConfigPatch) => {
          if (advancedIndex != null) updateExercise(advancedIndex, patch);
        }}
      />
    </>
  );
}
