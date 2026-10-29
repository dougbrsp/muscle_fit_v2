// Tipos e helpers da tela "Modelos de treino" (Fase 4, Etapa 15 da migração;
// modo `volume_mode='equipment_series'` adicionado na Etapa 27 — Fase 4,
// parte 3/3). CRUD de modelos de ficha de treino personalizados (slots
// cíclicos) + atribuição a usuários + preview de ativação semanal. Contratos
// confirmados lendo C:\muscle_fit\api\app\workout_plan_templates.py e
// models.py (WorkoutPlanTemplateIn / WorkoutPlanTemplateSlotIn /
// WorkoutPlanTemplateSlotItemIn / WorkoutPlanTemplateAssignIn) — a maior
// parte do router exige `require_admin_or_pt` (role 'admin' ou
// 'personal_trainer'); apenas `GET /workout-plan-templates/assigned-to-me`
// (me_router) é aberto a qualquer usuário autenticado.
//
// `volume_mode` (cabeçalho do template, decidido uma vez, não por slot):
// - 'percent_budget' (default, motor v2 por % de orçamento): itens
//   item_kind='muscle_group' (+ 'cardio').
// - 'equipment_series': itens item_kind='equipment' (grupo muscular +
//   equipamento + exercício + séries fixas, sem % de orçamento, sem IA
//   escolhendo nada — usuário define tudo) (+ 'cardio'). `item_kind`
//   'exercise' continua REMOVIDO do contrato (rejeitado por
//   `_validate_template_payload` em ambos os modos).

// M98 C0: campos novos da prescrição de cardio (colunas da 095; dono
// `domain/cardio_prescription.py`), opcionais até a API nova.
import type { CardioPrescriptionExtras } from './cardio-prescription';
import type { TemplateVersionRef } from './template-versions';

export type ApiListResponse<T> = { items: T[] };

// Item de slot — parte de GET /workout-plan-templates/{id}
// (workout_plan_templates.py::_get_template) e do payload de POST/PUT
// (WorkoutPlanTemplateSlotItemIn em models.py).
export type WorkoutPlanTemplateItemKind = 'muscle_group' | 'cardio' | 'equipment';

// Técnica avançada aplicada a UM único exercício/série do card
// item_kind='equipment': CÓDIGO do catálogo `advanced_techniques`
// (GET /advanced-techniques — ver lib/advanced-techniques.ts). O backend
// valida contra o catálogo (422 para código inexistente/inativo). Bi-set/
// tri-set/giant-set/pré-exaustão/pós-exaustão/super-série ficam fora de
// escopo (combinações de múltiplos exercícios, não suportadas).
export type AdvancedTechnique = string;

export type WorkoutPlanTemplateSlotItem = {
  id?: number;
  slot_id?: number;
  item_order: number;
  item_kind: WorkoutPlanTemplateItemKind;
  muscle_group_id: number | null;
  // FK para exercise_catalog(id). Null quando item_kind='cardio'; obrigatório
  // quando item_kind='equipment' (validado no backend); sempre null para
  // 'muscle_group' (item_kind 'exercise' legado removido do contrato).
  mapping_id: number | null;
  // FK para equipment(id) — só usado por item_kind='equipment'.
  equipment_id: number | null;
  sets_count: number | null;
  reps_target: string | null;
  rpe_target: string | null;
  prioritize: boolean;
  cardio_duration_minutes: number | null;
  // Motor de ficha v2: faixa percentual (0-100) da meta semanal do grupo
  // muscular que este item deve entregar neste slot. Só relevante para
  // item_kind='muscle_group'; ambos null = item não configurado para o v2.
  priority_min_pct: number | null;
  priority_max_pct: number | null;
  // Card item_kind='equipment' (modo 'equipment_series') — técnica avançada,
  // aquecimento, série de ajuste e pico de contração. Todos null/None quando
  // não configurados; rejeitados pelo backend em qualquer outro item_kind.
  advanced_technique: AdvancedTechnique | null;
  advanced_technique_applications: number | null; // Field(ge=1, le=20)
  advanced_technique_duration_seconds: number | null; // Field(ge=1, le=600)
  warmup_sets_count: number | null; // Field(ge=1, le=10)
  warmup_reps: number | null; // Field(ge=1, le=100)
  warmup_rest_seconds: number | null; // Field(ge=1, le=600)
  feeler_set_sets_count: number | null; // Field(ge=1, le=10)
  feeler_set_reps: number | null; // Field(ge=1, le=100)
  feeler_set_rest_seconds: number | null; // Field(ge=1, le=600)
  peak_contraction_seconds: number | null; // Field(ge=1, le=60)
  // Descanso entre as séries de TRABALHO deste exercício (Fase 7, migração
  // 058). null = "da geração" (ponto médio do intervalo escolhido ao gerar a
  // ficha); preenchido, sobrepõe o intervalo. Só em item_kind='equipment'.
  rest_seconds: number | null; // Field(ge=1, le=600)
  // Medida da série (Lote T2, migração 089; dono `domain/set_measures.py`,
  // espelho `lib/set-measures.ts`): `reps` | `duration` | `max_reps`. Opcionais
  // até a API nova; ausente = repetições.
  measure?: string | null;
  duration_seconds_target?: number | null;
  time_window_seconds?: number | null;
  // M97 F3 (coluna da 093): override do objetivo do Modelo neste item (valor de
  // `GET /training-goals`); null = herda. Só item de grupo muscular/equipamento.
  training_goal?: string | null;
} & CardioPrescriptionExtras;

export type WorkoutPlanTemplateSlotType = 'normal' | 'descanso';

export type WorkoutPlanTemplateSlot = {
  id?: number;
  template_id?: number;
  sequence: number;
  name: string;
  // 1=segunda..7=domingo; metadado informativo, não usado no cálculo do
  // ciclo (que segue a ordem de `sequence`).
  weekday: number | null;
  slot_type: WorkoutPlanTemplateSlotType;
  priority_min_pct: number | null;
  priority_max_pct: number | null;
  items: WorkoutPlanTemplateSlotItem[];
};

export type WorkoutPlanTemplateAssignment = {
  id: number;
  user_id: number;
  username: string;
  full_name: string | null;
  assigned_by_user_id: number;
  assigned_at: string;
  // Fim da vigência (M23). `null` = este usuário segue este modelo HOJE —
  // e, como só existe um modelo vigente por usuário, é o modelo que o motor
  // de geração e o dashboard usam para ele. Preenchido = vigência encerrada,
  // linha de histórico: ela fica para as evoluções feitas sob aquele modelo
  // continuarem explicáveis.
  unassigned_at: string | null;
  unassigned_by_user_id: number | null;
};

// Item da listagem — GET /workout-plan-templates
// (workout_plan_templates.py::_list_templates).
export type VolumeMode = 'percent_budget' | 'equipment_series';

export type WorkoutPlanTemplateListItem = {
  id: number;
  name: string;
  description: string | null;
  // Legado (M24): ainda vem da API (a coluna sai na F2.4), mas nenhuma tela
  // lê nem envia este campo.
  template_type: string;
  training_type_id: number | null;
  volume_mode: VolumeMode;
  /** Modo de contagem do volume (migração 073): valor de `VolumeCountingMode` do
   * motor. Opcional até o Lote 3 (editor); ausente = a API preserva/usa `primary_only`. */
  volume_counting_mode?: string;
  /** Objetivo de treino do Modelo (093; valor de `GET /training-goals`). Ausente = default. */
  training_goal?: string;
  is_active: boolean;
  created_by_user_id: number;
  created_by_username: string | null;
  created_at: string;
  updated_at: string;
  assignments_count: number;
  // Versão vigente (migração 065). null só em Modelo sem versão.
  current_version?: TemplateVersionRef | null;
  // Rascunho não publicado (migração 067) — badge "Rascunho" na lista.
  has_draft?: boolean;
  draft_updated_at?: string | null;
};

// Warning não-bloqueante (seção 11 do plano) — anexado pelo backend na
// resposta de POST/PUT quando o template tem training_type_id, comparando
// a soma de priority_min_pct/priority_max_pct de cada grupo muscular contra
// 90%/110% (template_volume_planner.validate_template_volume_coverage).
export type VolumeCoverageStatus = 'UNDER_ALLOCATED' | 'BALANCED' | 'OVER_ALLOCATED';

export type VolumeCoverageWarning = {
  muscle_group_id: number;
  muscle_group_name: string;
  sum_pct_min: number;
  sum_pct_max: number;
  status: VolumeCoverageStatus;
};

// Aviso de SEGURANÇA por item do modelo (PR-12, decisão D14) — devolvido
// pelo GET do modelo e nas respostas de POST/PUT. É apenas um AVISO: itens
// legados não tocados continuam salvando normalmente. Itens NOVOS ou
// ALTERADOS com técnica/RPE incompatíveis são bloqueados pelo backend com
// HTTP 422 (`detail` já em português, identificando slot e item).
//
// `message` já vem pronta para exibir (português) — o frontend não remonta
// texto a partir de `code`/`technique`/`max_safe_rpe`, que ficam disponíveis
// só como contexto adicional.
export type TemplateSafetyWarningCode = 'MODEL_TECHNIQUE_INVALID' | 'RPE_ABOVE_SAFE_CEILING';

export type TemplateSafetyWarning = {
  // 1-based, igual ao `sequence` do slot no payload de save.
  slot_sequence: number;
  slot_name: string;
  // 1-based, igual ao `item_order` do item no payload de save.
  item_order: number;
  muscle_group_id: number | null;
  mapping_id: number | null;
  equipment_id: number | null;
  code: TemplateSafetyWarningCode;
  message: string;
  technique: string | null;
  max_safe_rpe: number | null;
  rpe_target: string | null;
};

export const TEMPLATE_SAFETY_WARNING_LABEL: Record<TemplateSafetyWarningCode, string> = {
  MODEL_TECHNIQUE_INVALID: 'Técnica incompatível',
  RPE_ABOVE_SAFE_CEILING: 'RPE acima do teto seguro',
};

// Detalhe completo — GET /workout-plan-templates/{id}, também devolvido por
// POST/PUT/duplicate.
export type WorkoutPlanTemplateDetail = {
  id: number;
  name: string;
  description: string | null;
  // Legado (M24): ainda vem da API (a coluna sai na F2.4), mas nenhuma tela
  // lê nem envia este campo.
  template_type: string;
  training_type_id: number | null;
  volume_mode: VolumeMode;
  /** Modo de contagem do volume (migração 073): valor de `VolumeCountingMode` do
   * motor. Opcional até o Lote 3 (editor); ausente = a API preserva/usa `primary_only`. */
  volume_counting_mode?: string;
  /** Objetivo de treino do Modelo (093; valor de `GET /training-goals`). Ausente = default. */
  training_goal?: string;
  is_active: boolean;
  created_by_user_id: number;
  created_at: string;
  updated_at: string;
  // Grade do editor (migração 062): linhas (1–2) x colunas (1–4), lida linha
  // por linha na ordem da `sequence`. NULL = automática — o builder usa
  // `defaultGrid(n)` de `lib/slot-grid.ts`.
  grid_rows: number | null;
  grid_cols: number | null;
  slots: WorkoutPlanTemplateSlot[];
  assignments: WorkoutPlanTemplateAssignment[];
  volume_coverage_warnings?: VolumeCoverageWarning[];
  safety_warnings?: TemplateSafetyWarning[];
  // Versionamento (PR1/PR4): versão vigente; `version_created` vem no PUT
  // (false = nada estrutural mudou e a versão foi mantida).
  current_version?: TemplateVersionRef | null;
  version_created?: boolean;
  // Rascunho não publicado (migração 067). Vem no GET/PUT/revert do Modelo;
  // o conteúdo do rascunho é lido em `GET /{id}/draft`.
  has_draft?: boolean;
  draft_updated_at?: string | null;
};

/** `GET/PUT /workout-plan-templates/{id}/draft` (migração 067). */
export type WorkoutPlanTemplateDraft = {
  template_id: number;
  // O mesmo corpo do PUT do Modelo, possivelmente incompleto.
  payload: WorkoutPlanTemplatePayload;
  updated_at: string;
  updated_by: { id: number | null; name: string | null };
};

/**
 * `GET /workout-plan-templates/{id}/versions/{vid}`: a árvore de UMA versão do
 * histórico, na mesma forma do detalhe (para abrir com os mesmos componentes
 * em modo somente leitura).
 */
export type WorkoutPlanTemplateVersionSlotItem = WorkoutPlanTemplateSlotItem & {
  // Nomes resolvidos pelo backend só nesta rota (o resumo não carrega os
  // catálogos do editor).
  exercise_name?: string | null;
  equipment_name?: string | null;
  muscle_group_name?: string | null;
};

export type WorkoutPlanTemplateVersionSlot = Omit<WorkoutPlanTemplateSlot, 'items'> & {
  items: WorkoutPlanTemplateVersionSlotItem[];
};

export type WorkoutPlanTemplateVersionDetail = Pick<
  WorkoutPlanTemplateDetail,
  'id' | 'name' | 'description' | 'training_type_id' | 'volume_mode' | 'grid_rows' | 'grid_cols' | 'training_goal'
> & {
  slots: WorkoutPlanTemplateVersionSlot[];
  version: {
    id: number;
    number: number;
    effective_from: string | null;
    superseded_at: string | null;
    is_current: boolean;
    change_summary: string | null;
    author_name?: string | null;
    reverted_from_number?: number | null;
  };
};

// Payload de POST/PUT /workout-plan-templates (WorkoutPlanTemplateIn /
// WorkoutPlanTemplateSlotIn / WorkoutPlanTemplateSlotItemIn em models.py).
// Replace-all transacional dos slots a cada save.
export type WorkoutPlanTemplateSlotItemPayload = {
  item_order: number;
  item_kind: WorkoutPlanTemplateItemKind;
  muscle_group_id: number | null;
  mapping_id: number | null;
  equipment_id: number | null;
  sets_count: number | null;
  reps_target: string | null;
  rpe_target: string | null;
  prioritize: boolean;
  cardio_duration_minutes: number | null;
  priority_min_pct: number | null;
  priority_max_pct: number | null;
  advanced_technique: AdvancedTechnique | null;
  advanced_technique_applications: number | null;
  advanced_technique_duration_seconds: number | null;
  warmup_sets_count: number | null;
  warmup_reps: number | null;
  warmup_rest_seconds: number | null;
  feeler_set_sets_count: number | null;
  feeler_set_reps: number | null;
  feeler_set_rest_seconds: number | null;
  peak_contraction_seconds: number | null;
  rest_seconds: number | null;
  // Medida da série (Lote T2): só em item_kind='equipment'; ausente = repetições.
  measure?: string | null;
  duration_seconds_target?: number | null;
  time_window_seconds?: number | null;
  // M97 F3 (coluna da 093): override do objetivo do Modelo neste item (valor de
  // `GET /training-goals`); null = herda. Só item de grupo muscular/equipamento.
  training_goal?: string | null;
} & CardioPrescriptionExtras;

export type WorkoutPlanTemplateSlotPayload = {
  sequence: number;
  name: string;
  weekday: number | null;
  slot_type: WorkoutPlanTemplateSlotType;
  priority_min_pct: number | null;
  priority_max_pct: number | null;
  items: WorkoutPlanTemplateSlotItemPayload[];
};

// M24: `template_type` ("Tipo de modelo") saiu da tela e NÃO é mais enviado.
// A API aceita o campo ausente (edição preserva o valor gravado, criação usa
// 'full_body'); a coluna e as rotas legadas que leem `plan_type` saem na F2.4.
export type WorkoutPlanTemplatePayload = {
  name: string;
  description: string | null;
  training_type_id: number | null;
  volume_mode: VolumeMode;
  /** Modo de contagem do volume (migração 073): valor de `VolumeCountingMode` do
   * motor. Opcional até o Lote 3 (editor); ausente = a API preserva/usa `primary_only`. */
  volume_counting_mode?: string | null;
  /** Objetivo de treino do Modelo (093). Ausente = a API preserva o gravado (criação: default). */
  training_goal?: string | null;
  is_active: boolean;
  // Procedência (migração 038, PR-16): id da geração automática de onde este
  // modelo saiu. O cliente NÃO informa `origin` — informa de qual geração
  // veio, e o backend decide (`workout_plan_templates._resolve_origin`) depois
  // de conferir que a geração é do próprio usuário. Ausente/null = modelo
  // manual, que é o que todo modelo era antes do Motor de Periodização.
  // Enviá-lo é o que marca `origin='periodization'` e fecha a auditoria.
  generation_log_id?: number | null;
  // Grade do editor (migração 062). O builder sempre envia os dois; só um
  // Modelo legado com mais de 8 slots (que nenhuma grade 2x4 comporta) vai
  // com os dois nulos — ver `gridPayload` em `lib/slot-grid.ts`.
  grid_rows: number | null;
  grid_cols: number | null;
  slots: WorkoutPlanTemplateSlotPayload[];
};

// GET /workout-plan-templates/users-lookup — lookup mínimo de usuários,
// evita expor GET /users (admin-only puro) a personal trainers.
export type UserLookupItem = {
  id: number;
  username: string;
  full_name: string | null;
};

// POST /workout-plan-templates/{id}/assignments
// (WorkoutPlanTemplateAssignIn em models.py).
export type WorkoutPlanTemplateAssignPayload = {
  user_id: number;
};

// GET /workout-plan-templates/assigned-to-me (me_router, aberto a qualquer
// usuário autenticado). Não consumido por esta tela — ver nota de gap no
// componente de listagem.
export type AssignedTemplateSummary = {
  id: number;
  name: string;
  description: string | null;
  // Legado (M24): ainda vem da API (a coluna sai na F2.4), mas nenhuma tela
  // lê nem envia este campo.
  template_type: string;
  slots: { sequence: number; name: string; slot_type: WorkoutPlanTemplateSlotType }[];
};

// POST /workout-plan-templates/preview-activation
// (WorkoutPlanTemplatePreviewIn em workout_plan_templates.py) — payload usa
// o MESMO formato de slots do template (sem persistir), permitindo prévia
// em tempo real durante a edição, antes de salvar.
export type PreviewActivationPayload = {
  slots: WorkoutPlanTemplateSlotPayload[];
  // Mesmo Literal de WorkoutPlanTemplateIn.volume_mode — omitido usa o
  // default do backend ('percent_budget').
  volume_mode?: VolumeMode;
  // Aditivo: tipo de treino do Modelo. Só vai no modo 'percent_budget' — o
  // backend projeta o orçamento (meta semanal x faixa %) dos itens de grupo
  // e devolve `item_budgets`. Ausente = comportamento anterior.
  training_type_id?: number;
};

export type PreviewActivationGroup = {
  muscle_group: string;
  primary_equivalent: number;
  secondary_equivalent: number;
  stabilizer_equivalent: number;
  total_equivalent: number;
};

export type PreviewActivationDay = {
  day: number;
  slot_sequence: number;
  slot_name: string;
  slot_type: WorkoutPlanTemplateSlotType;
  groups: PreviewActivationGroup[];
};

export type PreviewActivationResponse = {
  cycle_length: number;
  days: PreviewActivationDay[];
  // Tamanho da janela: 7 dias a partir do primeiro slot de treino (mesma regra
  // da metodologia de volume; o ciclo curto dá a volta e o longo é cortado no
  // 7º dia). 0 quando o ciclo só tem descanso. Opcional porque respostas
  // anteriores ao PR-13 não têm o campo.
  window_days?: number;
  message?: string;
  // M11 (aditivos): detalhamento por item e total por grupo na janela.
  // Opcionais — resposta antiga (ou falha no detalhamento) não os traz, e
  // então a linha de ativação por item simplesmente não aparece.
  items?: PreviewActivationItem[];
  group_totals?: PreviewActivationGroupTotal[];
  // Aditivo (modo 'percent_budget' com `training_type_id`): orçamento de TODOS
  // os itens de grupo do ciclo (não só da janela). Ausente sem tipo de treino,
  // no modo equipamento ou se as metas não puderam ser lidas.
  item_budgets?: PreviewActivationItemBudget[];
  // Aditivo: fadiga prévia de CADA item de CADA slot do ciclo, na ordem do
  // corpo (dono `api/app/domain/prior_fatigue.py`). Ausente em resposta antiga
  // ou se o cálculo falhar — o card então fica sem a faixa de cor.
  item_prior_fatigue?: PreviewItemPriorFatigue[];
};

export type PriorFatigueBand = 'rested' | 'pre_fatigued' | 'heavily_fatigued';

export type PriorFatigue = {
  muscle_group_id: number | null;
  muscle_group_name: string | null;
  // Séries equivalentes prévias no músculo principal (informativo).
  prior_sets: number;
  // 0 (menos fatigado) .. 1 (mais fatigado), já saturado pelo backend.
  level: number;
  band: PriorFatigueBand;
  // Rótulo da faixa pronto ("descansado", "pré-fatigado", "muito fatigado").
  band_label: string;
};

export type PreviewItemPriorFatigue = {
  slot_sequence: number;
  // Posição do item em `slots[].items` do CORPO enviado (como em `items`).
  item_index: number;
  item_kind: WorkoutPlanTemplateItemKind;
  mapping_id: number | null;
  muscle_group_id: number | null;
  // null = item sem músculo principal (sem contribuição cadastrada).
  prior_fatigue: PriorFatigue | null;
};

export type PreviewActivationItemBudget = {
  slot_sequence: number;
  // Posição do item em `slots[].items` do CORPO enviado (como em `items`).
  item_index: number;
  item_order: number;
  muscle_group_id: number;
  // Faixa % efetiva: a configurada ou a divisão igual do que sobra até 100%.
  pct_min: number;
  pct_max: number;
  // Séries projetadas: meta semanal do tipo de treino x faixa %. `null` sem meta.
  sets_min: number | null;
  sets_max: number | null;
  has_target: boolean;
};

export type ActivationRole = 'primary' | 'secondary' | 'stabilizer';

export type PreviewActivationItemContribution = {
  muscle_group_id: number | null;
  muscle_group: string;
  role: ActivationRole;
  // Séries equivalentes (séries × stimulus_weight) do item para o grupo na
  // JANELA do preview (por sessão × vezes que o slot aparece na janela) —
  // mesma unidade da tabela; as linhas de um grupo somam o total da tabela.
  value: number;
};

export type PreviewActivationItem = {
  slot_sequence: number;
  // Posição do item em `slots[].items` do CORPO enviado (já filtrado por
  // `buildActivationPreviewPayload`), não do estado do builder.
  item_index: number;
  item_order: number;
  item_kind: WorkoutPlanTemplateItemKind;
  mapping_id: number | null;
  muscle_group_id: number | null;
  occurrences: number;
  contributions: PreviewActivationItemContribution[];
};

export type PreviewActivationGroupTotal = {
  muscle_group_id: number | null;
  muscle_group: string;
  // Soma de `days[].groups[].total_equivalent` do grupo — o total da tabela.
  total_equivalent: number;
};

export const SLOT_TYPE_OPTIONS: { value: WorkoutPlanTemplateSlotType; label: string }[] = [
  { value: 'normal', label: 'Normal' },
  { value: 'descanso', label: 'Descanso' },
];

export const ITEM_KIND_OPTIONS: { value: WorkoutPlanTemplateItemKind; label: string }[] = [
  { value: 'muscle_group', label: 'Grupo muscular' },
  { value: 'cardio', label: 'Cardio' },
];

export const VOLUME_MODE_OPTIONS: { value: VolumeMode; label: string }[] = [
  { value: 'percent_budget', label: '% de orçamento (motor v2)' },
  { value: 'equipment_series', label: 'Equipamento + séries fixas' },
];

export const VOLUME_MODE_DESCRIPTION: Record<VolumeMode, string> = {
  percent_budget:
    'Itens de grupo muscular com faixa % da meta semanal — a IA/motor v2 escolhe o exercício e o equipamento ao gerar a ficha.',
  equipment_series:
    'Você escolhe diretamente grupo muscular, equipamento, exercício e número de séries de cada item — sem IA decidindo nada.',
};

export const WEEKDAY_OPTIONS = [
  { value: '1', label: 'Segunda' },
  { value: '2', label: 'Terça' },
  { value: '3', label: 'Quarta' },
  { value: '4', label: 'Quinta' },
  { value: '5', label: 'Sexta' },
  { value: '6', label: 'Sábado' },
  { value: '7', label: 'Domingo' },
];

export const VOLUME_COVERAGE_STATUS_LABEL: Record<VolumeCoverageStatus, string> = {
  UNDER_ALLOCATED: 'Abaixo da meta',
  BALANCED: 'Equilibrado',
  OVER_ALLOCATED: 'Acima da meta',
};

export const VOLUME_COVERAGE_STATUS_COLOR: Record<VolumeCoverageStatus, string> = {
  UNDER_ALLOCATED: 'gray',
  BALANCED: 'green',
  OVER_ALLOCATED: 'orange',
};
