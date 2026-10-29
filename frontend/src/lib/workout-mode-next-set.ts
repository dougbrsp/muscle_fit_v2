// M22 — "qual é a carga da PRÓXIMA série", mostrada no `RestTimer` para o
// usuário já conseguir montar o aparelho enquanto descansa.
//
// Regra de ouro: este módulo NÃO calcula carga. Ele recebe pronta a MESMA
// carga que o `SetLoggerCard` vai oferecer quando o descanso acabar (a que
// sai de `workout-mode-load-suggestions.ts` / da prescrição congelada /
// da sugestão da IA). Recalcular por outro caminho faria o número do
// descanso divergir do número do registro — que é exatamente o bug que esta
// separação evita. O que decidimos aqui é só QUAL etapa vem a seguir e como
// rotulá-la.

import { isRepBased } from './set-measures';
import type { AdvancedSubStage } from './workout-mode-plan-flow';

export type NextSetPreview = {
  /** Mesmo rótulo do card de registro ("Aquecimento 1/2", "Série 2 de 3"). */
  label: string;
  /**
   * Carga em kg (canônica). A conversão para lb é só na exibição (M3).
   * `null` fora de repetições (Lote T5): série por tempo ou máximo de reps não
   * tem sugestão de carga (o servidor devolve `next_set_load: null`); o
   * descanso mostra só o rótulo.
   */
  weightKg: number | null;
};

export type NextSetPreviewInput = {
  /**
   * `true` quando o descanso é transição de exercício: o exercício atual
   * acabou e quem fala do que vem a seguir é o card do PRÓXIMO exercício.
   * Nesse caso não existe "próxima série deste exercício" — devolve `null`
   * em vez de um número de outro exercício.
   */
  isExerciseTransition: boolean;
  /**
   * Sub-etapa do modo "Fazer treino avançado" que vem A SEGUIR (o chamador
   * já resolve `advancedStage` com os contadores atualizados), ou `null`
   * quando a próxima é uma série de trabalho comum.
   */
  stage: AdvancedSubStage | null;
  /** Substantivo da aplicação da técnica ("Drop", "Mini-bloco", ...). */
  techniqueApplicationNoun?: string | null;
  /** Número (1-based) da próxima série de trabalho e meta do exercício. */
  workingSetNumber: number;
  workingSetTotal: number;
  /** Carga que o card vai oferecer nessa próxima série. */
  weightKg: number | null | undefined;
  /**
   * Medida da série (`reps` | `duration` | `max_reps`, espelho `set-measures`).
   * Ausente = `reps`: o comportamento de sempre.
   */
  measure?: string | null;
};

export function resolveNextSetPreview(input: NextSetPreviewInput): NextSetPreview | null {
  if (input.isExerciseTransition) return null;
  if (!isRepBased(input.measure)) {
    // Sem sub-etapa avançada fora de reps: só o rótulo da série de trabalho.
    return { label: workingSetLabel(input.workingSetNumber, input.workingSetTotal), weightKg: null };
  }
  const weightKg = Number(input.weightKg);
  if (input.weightKg == null || !Number.isFinite(weightKg)) return null;

  const stage = input.stage;
  if (stage) {
    if (stage.kind === 'warmup') return { label: `Aquecimento ${stage.index}/${stage.total}`, weightKg };
    if (stage.kind === 'feeler') return { label: `Série de ajuste ${stage.index}/${stage.total}`, weightKg };
    const noun = (input.techniqueApplicationNoun || '').trim();
    // Sem o substantivo da técnica não inventamos um rótulo: a carga ainda é
    // útil, o número da aplicação é que ficaria adivinhado.
    if (!noun) return { label: 'Próxima série', weightKg };
    return { label: `${noun} ${stage.index}/${stage.total}`, weightKg };
  }

  return { label: workingSetLabel(input.workingSetNumber, input.workingSetTotal), weightKg };
}

// Série de trabalho. Acima da meta (o usuário pode passar do alvo antes de
// encerrar o exercício) o "de N" viraria mentira — some com ele.
function workingSetLabel(workingSetNumber: number, workingSetTotal: number): string {
  if (workingSetNumber > workingSetTotal) return `Série ${workingSetNumber}`;
  return `Série ${workingSetNumber} de ${workingSetTotal}`;
}
