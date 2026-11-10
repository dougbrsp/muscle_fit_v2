// Edição da série executada na tela de descanso do Modo Treino.
//
// Só APRESENTAÇÃO e montagem de corpo: o resumo da série gravada, a
// elegibilidade do botão "Editar" e o diff (campo alterado -> corpo do
// PATCH, com a conversão kg/lb). Nenhuma regra de carga/RPE aqui: selos,
// `safety_event` e `next_set_load` vêm prontos da resposta do servidor
// (`update_session_set`, dono `api/app/domain/next_set_load.py`).

import { isRepBased } from './set-measures';
import { isWorkingSetType } from './set-types';
import { formatWeight, fromKg, toKg, type WeightUnit } from './weight-units';

/** Valores da série gravada (carga em kg, a convenção do backend). */
export type RestSetValues = {
  reps: number | null;
  weight_kg: number | null;
  rpe: number | null;
};

/** Rascunho do editor: carga NA UNIDADE do usuário (o que ele vê/digita). */
export type RestSetDraft = {
  reps: number | null;
  weight: number | null;
  rpe: number | null;
};

/** Corpo do PATCH: só os campos alterados (`weight_kg` em kg). */
export type RestSetPatchBody = Partial<Record<'reps' | 'weight_kg' | 'rpe', number>>;

export type RestSetSummary = {
  repsText: string | null;
  loadText: string | null;
  rpeText: string | null;
  /** "10 reps · 32.5 kg · RPE 8" (partes ausentes somem). */
  text: string;
};

function numberText(value: number): string {
  return String(Math.round(value * 100) / 100);
}

/** Resumo da série executada para o card "Série salva". */
export function restSetSummary(values: RestSetValues, unit: WeightUnit): RestSetSummary {
  const repsText = values.reps != null ? `${numberText(values.reps)} ${values.reps === 1 ? 'rep' : 'reps'}` : null;
  const loadText = values.weight_kg != null ? formatWeight(values.weight_kg, unit) : null;
  const rpeText = values.rpe != null ? `RPE ${numberText(values.rpe)}` : null;
  return { repsText, loadText, rpeText, text: [repsText, loadText, rpeText].filter(Boolean).join(' · ') };
}

/** Entrada do salvamento da série (subconjunto de `SetLoggerValues`). */
export type RestSetEditCandidate = {
  reps: number | null;
  set_type?: string | null;
  measure?: string | null;
  technique_code?: string | null;
  anchor_set_number?: number | null;
  extra_reps?: number | null;
  reps_left?: number | null;
  reps_right?: number | null;
  rpe_left?: number | null;
  rpe_right?: number | null;
};

/**
 * O botão "Editar" só existe para série de trabalho simples de repetições:
 * sem lados, sem medida por tempo/máximo, sem etapa de técnica, sem reps
 * extras (linha `forced_rep` ancorada) e sem exercício local ainda sem id.
 */
export function canEditRestSet(candidate: RestSetEditCandidate, exerciseId: number | null | undefined): boolean {
  if (exerciseId == null) return false;
  if (candidate.reps == null) return false;
  if (!isWorkingSetType(candidate.set_type ?? null)) return false;
  if (!isRepBased(candidate.measure)) return false;
  if (candidate.technique_code) return false;
  if (candidate.anchor_set_number != null) return false;
  if ((candidate.extra_reps ?? 0) > 0) return false;
  if (
    candidate.reps_left != null ||
    candidate.reps_right != null ||
    candidate.rpe_left != null ||
    candidate.rpe_right != null
  ) {
    return false;
  }
  return true;
}

/** Rascunho inicial: os valores gravados, a carga convertida para a unidade. */
export function restSetDraftFrom(values: RestSetValues, unit: WeightUnit): RestSetDraft {
  return {
    reps: values.reps,
    weight: values.weight_kg != null ? fromKg(values.weight_kg, unit) : null,
    rpe: values.rpe,
  };
}

/**
 * Diff rascunho -> corpo do PATCH. Só entram campos preenchidos e diferentes
 * do gravado; a carga é comparada e enviada em kg (`toKg`). Campo vazio não
 * limpa o valor gravado (o PATCH não aceita nulo): fica de fora.
 */
export function restSetPatchBody(original: RestSetValues, draft: RestSetDraft, unit: WeightUnit): RestSetPatchBody {
  const body: RestSetPatchBody = {};
  if (draft.reps != null && draft.reps !== original.reps) body.reps = draft.reps;
  // Compara na unidade exibida: quem não mexeu na carga não a reenvia (a ida e
  // volta kg -> lb -> kg não é exata e criaria uma "alteração" fantasma).
  if (draft.weight != null && (original.weight_kg == null || draft.weight !== fromKg(original.weight_kg, unit))) {
    body.weight_kg = toKg(draft.weight, unit);
  }
  if (draft.rpe != null && draft.rpe !== original.rpe) body.rpe = draft.rpe;
  return body;
}

/** Valores gravados depois de um PATCH aceito (o corpo enviado vale). */
export function applyRestSetPatch(original: RestSetValues, body: RestSetPatchBody): RestSetValues {
  return {
    reps: body.reps ?? original.reps,
    weight_kg: body.weight_kg ?? original.weight_kg,
    rpe: body.rpe ?? original.rpe,
  };
}

/**
 * A resposta de um PATCH em voo só vale para a tela se o descanso vigente
 * ainda é o que abriu a edição (`token` por descanso). Pular o descanso, o
 * relógio acabar ou gravar a série seguinte troca/zera o alvo: a resposta
 * tardia é descartada na tela (a invalidação do servidor segue).
 */
export function restSetResponseApplies(
  current: { token: number } | null | undefined,
  target: { token: number },
): boolean {
  return current != null && current.token === target.token;
}

/**
 * Guarda de sequência para pedidos assíncronos (IA por série, resumo): cada
 * pedido novo ou reset invalida os anteriores; só a resposta do último vale.
 */
export type SequenceGuard = {
  /** Inicia um pedido e devolve o seu número. */
  begin: () => number;
  /** Invalida os pedidos em voo (reset do estado). */
  invalidate: () => void;
  /** A resposta do pedido `seq` ainda pode ser aplicada? */
  isCurrent: (seq: number) => boolean;
};

export function createSequenceGuard(): SequenceGuard {
  let counter = 0;
  return {
    begin: () => ++counter,
    invalidate: () => {
      counter += 1;
    },
    isCurrent: (seq) => seq === counter,
  };
}
