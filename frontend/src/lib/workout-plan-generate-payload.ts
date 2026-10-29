// Montagem do payload de POST /workout-plans/generate (PR-18).
//
// Por que uma função em vez de montar o objeto inline em cada tela: as duas
// telas que geram ficha (`planos-treino/GenerateDraftModal.tsx` e
// `modo-treino/ModeSelectScreen.tsx`) precisam das MESMAS regras — modelo
// obrigatório, slot '__auto__' vira null, campos vazios viram null — e antes
// do PR-18 elas divergiam (o modo treino nem mandava `template_id`).
import type { WorkoutPlanGenerateInput } from './workout-plans-types';
import { DEFAULT_MAX_REST_SECONDS, DEFAULT_MIN_REST_SECONDS } from './prescription-defaults';

/** Valor do Select de slot que significa "próximo slot do ciclo". */
export const AUTO_SLOT_VALUE = '__auto__';

export type GenerateFormState = {
  /** Valor do Select de modelo ('' = nenhum selecionado). */
  templateId: string | null;
  /** Valor do Select de slot ('__auto__' = automático). */
  templateSlotSequence?: string | null;
  gymId?: string | null;
  gymName?: string | null;
  planDate?: string | null;
  planTime?: string | null;
  goal?: string | null;
  minRestSeconds?: number | null;
  maxRestSeconds?: number | null;
};

/**
 * `true` quando o formulário tem um Modelo de Treino escolhido — é o que
 * habilita o botão de gerar. Sem modelo não existe geração desde o PR-18, e
 * o botão desabilitado evita um 422 previsível vindo do backend.
 */
export function canGenerateDraft(templateId: string | null | undefined): boolean {
  return !!templateId && !Number.isNaN(Number(templateId));
}

/**
 * Payload de geração. Lança quando não há modelo — a UI já impede esse
 * caminho (botão desabilitado), então um erro aqui é bug de tela, não
 * entrada de usuário.
 */
export function buildGeneratePayload(form: GenerateFormState): WorkoutPlanGenerateInput {
  if (!canGenerateDraft(form.templateId)) {
    throw new Error('É necessário um Modelo de Treino atribuído para gerar a ficha.');
  }
  const slot = form.templateSlotSequence;
  return {
    gym_id: form.gymId ? Number(form.gymId) : null,
    gym_name: form.gymName || null,
    plan_date: form.planDate || null,
    plan_time: form.planTime || null,
    goal: form.goal || null,
    min_rest_seconds: form.minRestSeconds ?? DEFAULT_MIN_REST_SECONDS,
    max_rest_seconds: form.maxRestSeconds ?? DEFAULT_MAX_REST_SECONDS,
    template_id: Number(form.templateId),
    template_slot_sequence: slot && slot !== AUTO_SLOT_VALUE ? Number(slot) : null,
  };
}

/**
 * Hora de parede LOCAL do dispositivo no formato `HH:MM` — o mesmo que o
 * `<input type="time">` de "Hora pretendida" (GenerateDraftModal) envia e que
 * `plan_time` (`time` no backend) aceita. Usada pelo Modo Treino, que não tem
 * campo de hora: a ficha é para agora, e sem `plan_time` o backend assume
 * meia-noite e encurta a projeção de recuperação congelada na ficha.
 * Nunca derivar de `toISOString()` — isso seria UTC, não a hora local.
 */
export function localPlanTime(now: Date = new Date()): string {
  const hh = String(now.getHours()).padStart(2, '0');
  const mm = String(now.getMinutes()).padStart(2, '0');
  return `${hh}:${mm}`;
}
