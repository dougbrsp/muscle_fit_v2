/**
 * Tipos do CRUD `/daily-checkins` (F2.2, migração 050) — espelho do router
 * `api/app/daily_checkins.py` e do vocabulário de
 * `api/app/daily_checkins_schema.py`.
 *
 * As regras são as mesmas dos outros arquivos de tipo do hub: o tipo É o
 * contrato e nada aqui recalcula.
 *
 * Duas coisas que o tipo precisa deixar óbvias:
 * - **escore ausente é `null`, nunca 0**: "não respondi" é ausência, não o
 *   menor grau da escala. O backend ignora escore ausente na baseline pessoal;
 * - **um check-in por dia**: o POST é upsert por `(user_id, local_date)`.
 *   Responder de novo corrige, não acrescenta.
 */

/** Colunas de escore da 050, na ordem canônica. */
export const CHECKIN_SCORE_FIELDS = ['energy', 'soreness', 'stress', 'mood'] as const;

export type CheckinScoreField = (typeof CHECKIN_SCORE_FIELDS)[number];

export const CHECKIN_SCORE_MIN = 1;
export const CHECKIN_SCORE_MAX = 5;

/** Rótulos (espelho de `SCORE_LABELS`). */
export const CHECKIN_SCORE_LABELS: Record<CheckinScoreField, string> = {
  energy: 'Disposição',
  soreness: 'Dor muscular',
  stress: 'Estresse',
  mood: 'Humor',
};

/** O que 1 e 5 significam em cada escore — a escala precisa de âncora. */
export const CHECKIN_SCALE_ENDS: Record<CheckinScoreField, { low: string; high: string }> = {
  energy: { low: 'sem energia', high: 'muita energia' },
  soreness: { low: 'sem dor', high: 'muita dor' },
  stress: { low: 'tranquilo', high: 'muito estressado' },
  mood: { low: 'péssimo', high: 'ótimo' },
};

export type DailyCheckin = {
  id: number;
  local_date: string;
  /** 1..5 ou `null` — nunca 0. */
  energy: number | null;
  soreness: number | null;
  stress: number | null;
  mood: number | null;
  notes: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** Quais escores foram respondidos (vem pronto do backend). */
  answered: CheckinScoreField[];
  score_labels: Record<string, string>;
};

export type DailyCheckinsListResponse = {
  items: DailyCheckin[];
  limit: number;
  offset: number;
  has_more: boolean;
  /** `false` antes da migração 050: a tela declara e desabilita o formulário. */
  table_available: boolean;
  limitations: string[];
};

/** Entrada do POST (upsert do dia). Escore omitido NÃO apaga o já gravado. */
export type DailyCheckinInput = {
  local_date: string;
  energy?: number | null;
  soreness?: number | null;
  stress?: number | null;
  mood?: number | null;
  notes?: string | null;
};

export type DailyCheckinWriteResponse = {
  status: 'created' | 'updated';
  item: DailyCheckin;
};
