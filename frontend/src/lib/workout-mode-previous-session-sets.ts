// Seletor das séries de TRABALHO da sessão anterior do mesmo exercício
// (`GET /exercises/last-session-sets`), a linha "Última"/"1ª série - ..." do
// Modo Treino (PR B3 da Evolução ZEPP: saiu de `workout-mode-previous-session.ts`
// para um módulo PURO, sem formatação nem `toLocaleString`, exportado pelo
// núcleo `workout-mode-core` — o relógio usa o MESMO seletor do celular).
//
// Quais séries entram: só trabalho (`set-types.isWorkingSetType`, espelho de
// `api/app/set_types.is_working_set`) e sem a marca `is_technique_application`
// do backend; ordem de execução (`set_number` crescente). A posição no array
// (1-based) é a numeração exibida, NÃO o `set_number` físico (que inclui
// aquecimento/ajuste). Nenhum número de regra aqui.

import { isWorkingSetType } from './set-types';
import type { LastSessionSetsResponse, PreviousSessionSet } from './workout-mode-types';

/**
 * Séries de trabalho da sessão anterior, em ordem de execução
 * (`set_number` crescente). A posição no array (1-based) é a numeração
 * exibida — NÃO o `set_number` físico.
 */
export function selectPreviousSessionWorkingSets(
  response: LastSessionSetsResponse | undefined | null,
): PreviousSessionSet[] {
  return (response?.item?.sets || [])
    // Só série de TRABALHO (`set-types`, espelho de `set_types.is_working_set`):
    // aquecimento/ajuste, aplicação de técnica (mini-bloco, drop, reps além da
    // falha) e `backoff` (trabalho reduzido) não são "Série N" da sessão
    // anterior. M76 Lote 3b: antes o `backoff` passava como trabalho aqui,
    // diferente do backend. A marca `is_technique_application` do backend
    // continua valendo (linha `working` vinculada a uma âncora de técnica).
    .filter((set) => isWorkingSetType(set.set_type) && !set.is_technique_application)
    .slice()
    .sort((left, right) => (left.set_number ?? 0) - (right.set_number ?? 0));
}
