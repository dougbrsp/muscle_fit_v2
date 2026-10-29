// Defaults de prescrição (M76, Lote 4c — E8). Dono:
// `api/app/domain/prescription_defaults.py`, exportado para
// `motor-constants.generated.json` (seção `prescription`) por
// `api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`. Nenhum número aqui
// é escrito à mão.
//
// `DEFAULT_REST_SECONDS` é o default de PRESCRIÇÃO (o que o timer do Modo Treino
// usa quando o exercício não tem descanso). Não confundir com "descanso não
// definido" na estimativa de tempo do Modelo, que conta 0 s
// (`REST_WHEN_UNDEFINED_SECONDS` em `template-time-estimate.ts`).

import motorConstants from './motor-constants.generated.json';

const PRESCRIPTION = motorConstants.prescription;

/** Alvo de repetições quando ninguém definiu (placeholder da ficha). */
export const DEFAULT_REPS_TARGET: string = PRESCRIPTION.default_reps_target;
/** Alvo de RPE quando ninguém definiu (placeholder da ficha). */
export const DEFAULT_RPE_TARGET: string = PRESCRIPTION.default_rpe_target;
/** Intervalo de descanso padrão pedido na geração da ficha. */
export const DEFAULT_MIN_REST_SECONDS: number = PRESCRIPTION.default_min_rest_seconds;
export const DEFAULT_MAX_REST_SECONDS: number = PRESCRIPTION.default_max_rest_seconds;
/** Descanso do timer quando o exercício não tem `rest_seconds`. */
export const DEFAULT_REST_SECONDS: number = PRESCRIPTION.default_rest_seconds;
/** Limites de sanidade dos campos de descanso da geração. */
export const REST_SECONDS_SANITY_MIN: number = PRESCRIPTION.rest_seconds_sanity_min;
export const REST_SECONDS_SANITY_MAX: number = PRESCRIPTION.rest_seconds_sanity_max;
/** Piso de séries de um item (alvo de trabalho sem `sets_count`; aquecimento/ajuste sem contagem). */
export const MIN_SETS_COUNT: number = PRESCRIPTION.min_sets_count;
/**
 * Ponto de partida do registro da série no Modo Treino quando não há NENHUMA
 * referência (sugestão, série anterior ou alvo numérico): reps e carga em kg.
 */
export const SET_LOGGER_FALLBACK_REPS: number = PRESCRIPTION.set_logger_fallback_reps;
export const SET_LOGGER_FALLBACK_LOAD_KG: number = PRESCRIPTION.set_logger_fallback_load_kg;
