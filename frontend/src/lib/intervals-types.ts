// Tipos da tela "Intervals.icu" (`/intervals-icu`, Fase 4 — Etapa 19).
//
// Contrato confirmado lendo `C:\muscle_fit\api\app\intervals_integration.py`
// (router `/integrations/intervals`, protegido por `get_current_identity` —
// nenhuma rota deste arquivo é admin-only, todas escopam por
// `identity.user_id`):
//
// - `GET /health`   — status de configuração/conectividade da integração
//                      (não confunda com `GET /status`, que é por usuário).
// - `GET /status`   — se o usuário autenticado já configurou a chave do
//                      Intervals.icu no perfil (nunca vaza a chave).
// - `POST /sync`    — SÍNCRONO (não retorna 202/job_id como o Agente de
//                      Cardio em `/agente-cardio`): busca do Intervals.icu ao
//                      vivo com as credenciais do usuário e já faz upsert no
//                      cache MySQL (`intervals_activities`/`intervals_wellness`),
//                      retornando o resultado completo na mesma resposta.
// - `GET /dashboard` — dashboard "ao vivo" (chama o Intervals.icu direto, não
//                      o cache MySQL) com atividades por dia, série de
//                      wellness (fitness/fadiga/forma) e atividades recentes;
//                      é o conteúdo principal desta tela.
// - `GET /cardio-dashboard` — removido na M98 C4 (cardio só pelo cache, em
//                      `GET /analytics/dashboard/cardio`).
// - `POST /activities` / `POST /wellness` — upsert manual usado pelo
//                      MCP/OpenClaw legado; não fazem sentido como UI aqui,
//                      não implementados nesta tela (fora de escopo).
// - `GET /summary`, `GET /activities`, `GET /wellness` (cache) — leem o cache
//                      MySQL; não usados nesta tela porque `GET /wellness`
//                      cacheado já é exibido em `/indicadores-fisicos`
//                      (seção Recuperação, ver `RecoveryMetricsSection.tsx`) e
//                      `GET /dashboard` cobre o mesmo tipo de agregação com
//                      dados ao vivo, sem duplicar chamada/lógica.

export type IntervalsStatus = {
  configured: boolean;
  message: string;
};

// `IntervalsSyncResult` foi removido na melhoria M2: o sync deixou de ser
// disparado pela tela (o job agendado `musclefit-intervals-sync` grava o
// cache e associa a FC aos treinos). A rota POST /integrations/intervals/sync
// continua existindo apenas como ferramenta de troubleshooting de ADMIN, e
// nenhuma tela a chama.

export type IntervalsDailyActivity = {
  date: string;
  activities: number;
  minutes: number;
  calories: number;
  distanceKm: number;
  load: number;
  avgHr: number | null;
};

// 'manual' = editado em /intervals-icu; o próximo sync sobrescreve se o
// Intervals.icu tiver o dado daquele dia (regra no backend, `upsert_wellness`).
export type IntervalsWellnessSource = 'intervals' | 'manual';

// Lido do cache `intervals_wellness` (mesma fonte do Histórico de
// /indicadores-fisicos): campo ausente vem `null` e a tela mostra "-".
// `hrv` é sempre rMSSD — SDNN não é usado como substituto. `*Source` diz se o
// valor foi digitado na tela (`manual`) ou veio do Intervals.icu.
export type IntervalsWellnessTrendPoint = {
  date: string;
  fitness: number | null;
  fatigue: number | null;
  form: number | null;
  restingHr: number | null;
  restingHrSource: IntervalsWellnessSource;
  hrv: number | null;
  hrvSource: IntervalsWellnessSource;
  rampRate: number | null;
};

// PATCH /integrations/intervals/wellness/{date}: só os campos enviados mudam;
// `null` limpa a edição (o próximo sync preenche). A faixa aceita é validada
// só no backend (422 com a mensagem).
export type IntervalsWellnessManualPatch = {
  resting_hr_bpm?: number | null;
  hrv_ms?: number | null;
};

export type IntervalsRecentActivity = {
  date: string;
  name: string;
  sport: string;
  minutes: number;
  calories: number;
  avgHr: number | null;
  load: number | null;
};

export type IntervalsDashboard = {
  range: {
    days: number;
    oldest: string;
    newest: string;
  };
  activity_count: number;
  wellness_count: number;
  wellness_source?: 'cache';
  wellness_refresh?: 'refreshed' | 'throttled' | 'not_configured' | 'failed';
  daily_activity: IntervalsDailyActivity[];
  wellness_trend: IntervalsWellnessTrendPoint[];
  recent_activities: IntervalsRecentActivity[];
};

export function sumBy<T>(items: T[], selector: (item: T) => number): number {
  return items.reduce((total, item) => total + selector(item), 0);
}

export function averageOf(values: Array<number | null | undefined>): number | null {
  const valid = values.filter(
    (value): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0
  );
  if (!valid.length) return null;
  return valid.reduce((total, value) => total + value, 0) / valid.length;
}
