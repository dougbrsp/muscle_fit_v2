import type { Page, Route } from '@playwright/test';

/**
 * API mockada e COM ESTADO para os cenarios de resiliencia do Modo Treino.
 * Replica so o contrato de idempotencia do backend (`client_key` por exercicio e
 * por serie: a 2a chamada devolve `duplicate: true` sem gravar); nao replica regra
 * de negocio (valores arbitrarios). Chamadas nao mapeadas respondem 404 e ficam em
 * `unhandled` para inspecao.
 */
export const API = 'http://mock-api.test';

export const USERS = {
  ana: { id: 1, username: 'ana', full_name: 'Ana Teste' },
  bia: { id: 2, username: 'bia', full_name: 'Bia Teste' },
} as const;
export type UserKey = keyof typeof USERS;

export const SESSION_ID = 900;
export const PLAN_ID = 50;
export const EXERCISE_ID = 501; // Supino (ja existe no servidor ao abrir a sessao)
export const PLAN_ITEM_ID = 7001;
export const CRUCIFIXO_PLAN_ITEM_ID = 7002;
export const CRUCIFIXO_EXERCISE_ID = 502; // criado so quando o exercicio e lancado
export const FST_PLAN_ITEM_ID = 7003;
export const FST_EXERCISE_ID = 503;

type StoredSet = { set_number: number; client_key: string; body: Record<string, unknown> };

export type MockWorkoutApi = {
  /** Chamadas `POST .../sets` recebidas (inclui as recusadas por falha forcada). */
  setPosts: Array<{ client_key: string; status: number }>;
  /** Chamadas `POST .../exercises` recebidas (client_key, plan item). */
  exercisePosts: Array<{ client_key: string | null; workout_plan_exercise_id: unknown; duplicate: boolean }>;
  /** Series efetivamente gravadas no "servidor" para o Supino (dedupe por client_key). */
  serverSets: () => StoredSet[];
  /** Series gravadas do Crucifixo (exercicio criado offline nos cenarios). */
  crucifixoSets: () => StoredSet[];
  unhandled: string[];
  /** Corpos das series FST-7 recebidas (para conferir a carga efetiva gravada). */
  fstBodies: Array<Record<string, unknown>>;
  /** Proximos N POST de serie (qualquer exercicio) respondem com `status` (padrao 500). */
  failNextSetPosts: (count: number, status?: number) => void;
  /** Rede fora: toda chamada a API falha na conexao (como sem sinal). */
  setNetworkDown: (down: boolean) => void;
  /** `GET /auth/me` passa a responder este status (null = normal). */
  setAuthMeStatus: (status: number | null) => void;
  /** Troca o usuario autenticado (cookie simulado). */
  loginAs: (user: UserKey) => void;
};

const JSON_HEADERS = { 'content-type': 'application/json' };

export async function installWorkoutApi(page: Page, initialUser: UserKey = 'ana'): Promise<MockWorkoutApi> {
  let user: UserKey = initialUser;
  let failures = 0;
  let failureStatus = 500;
  let networkDown = false;
  let authMeStatus: number | null = null;
  const unhandled: string[] = [];
  const fstBodies: Array<Record<string, unknown>> = [];
  const setPosts: MockWorkoutApi['setPosts'] = [];
  const exercisePosts: MockWorkoutApi['exercisePosts'] = [];
  const sessionOwner = 1;

  // Series por exercicio do servidor: client_key -> serie.
  const setsByExercise = new Map<number, Map<string, StoredSet>>([
    [EXERCISE_ID, new Map()],
    [CRUCIFIXO_EXERCISE_ID, new Map()],
    [FST_EXERCISE_ID, new Map()],
  ]);
  // Exercicios criados no servidor (por client_key) -> id.
  const exercisesByKey = new Map<string, number>();
  const createdExercises = new Set<number>([EXERCISE_ID]);
  const planItemOf: Record<number, { id: number; sequence: number; name: string; mapping: number }> = {
    [CRUCIFIXO_PLAN_ITEM_ID]: { id: CRUCIFIXO_EXERCISE_ID, sequence: 2, name: 'Crucifixo', mapping: 12 },
    [FST_PLAN_ITEM_ID]: { id: FST_EXERCISE_ID, sequence: 3, name: 'Peck deck', mapping: 13 },
  };
  const wpeOfExercise: Record<number, number> = {
    [EXERCISE_ID]: PLAN_ITEM_ID,
    [CRUCIFIXO_EXERCISE_ID]: CRUCIFIXO_PLAN_ITEM_ID,
    [FST_EXERCISE_ID]: FST_PLAN_ITEM_ID,
  };

  const rowsOf = (exerciseId: number) =>
    Array.from(setsByExercise.get(exerciseId)?.values() ?? [])
      .sort((a, b) => a.set_number - b.set_number)
      .map((stored) => ({ ...stored.body, set_number: stored.set_number, client_key: stored.client_key }));

  const activity = () => ({
    id: SESSION_ID,
    local_date: '2026-10-14',
    activity_type: 'strength',
    name: 'Treino A',
    gym_id: 1,
    session_status: 'in_progress',
    workout_plan_id: PLAN_ID,
    exercises: Array.from(createdExercises).map((exerciseId) => {
      const wpe = wpeOfExercise[exerciseId];
      const meta = wpe === PLAN_ITEM_ID ? { sequence: 1, name: 'Supino reto', mapping: 11 } : planItemOf[wpe];
      return {
        id: exerciseId,
        activity_id: SESSION_ID,
        sequence: meta.sequence,
        original_name: meta.name,
        mapping_id: meta.mapping,
        workout_plan_exercise_id: wpe,
        sets: rowsOf(exerciseId),
      };
    }),
  });

  const plan = {
    id: PLAN_ID,
    name: 'Treino A',
    gym_id: 1,
    gym_name: 'Academia Central',
    exercises: [
      { workout_plan_exercise_id: PLAN_ITEM_ID, sequence: 1, exercise_name: 'Supino reto', mapping_id: 11, sets_count: 4, reps_target: '8-12', rest_seconds: 60 },
      { workout_plan_exercise_id: CRUCIFIXO_PLAN_ITEM_ID, sequence: 2, exercise_name: 'Crucifixo', mapping_id: 12, sets_count: 3, reps_target: '10-15', rest_seconds: 60 },
      {
        workout_plan_exercise_id: FST_PLAN_ITEM_ID,
        sequence: 3,
        exercise_name: 'Peck deck',
        mapping_id: 13,
        sets_count: 7,
        reps_target: '10-12',
        rest_seconds: 30,
        advanced_technique: 'fst7',
      },
    ],
  };

  await page.route(`${API}/**`, async (route: Route) => {
    const req = route.request();
    const origin = req.headers()['origin'] ?? '*';
    const cors = {
      'access-control-allow-origin': origin,
      'access-control-allow-credentials': 'true',
      'access-control-allow-headers': '*',
      'access-control-allow-methods': '*',
    };
    const send = (status: number, body: unknown) =>
      route.fulfill({ status, headers: { ...cors, ...JSON_HEADERS }, body: JSON.stringify(body) });

    if (networkDown) return route.abort('connectionfailed');
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });

    const url = new URL(req.url());
    const path = url.pathname;
    const key = `${req.method()} ${path}`;

    if (key === 'GET /auth/me') {
      if (authMeStatus != null) return send(authMeStatus, { detail: 'e2e: /auth/me forcado' });
      return send(200, {
        ...USERS[user],
        role: 'user',
        must_change_password: false,
        intervals_configured: false,
        timezone: 'America/Sao_Paulo',
        features: { exercise_cards: false },
      });
    }
    if (key === 'POST /auth/logout') return send(204, null);

    if (key === `GET /activities/${SESSION_ID}`) {
      // GET /activities/{id} e filtrado por usuario no backend real: 404 para outra conta.
      return USERS[user].id === sessionOwner ? send(200, activity()) : send(404, { detail: 'Atividade nao encontrada.' });
    }
    if (key === `GET /workout-plans/${PLAN_ID}`) return send(200, plan);
    if (key === 'GET /gym-equipment') return send(200, { items: [] });
    if (key === 'GET /activities') return send(200, { items: [] });
    if (key === 'GET /gyms') return send(200, { items: [{ id: 1, name: 'Academia Central', is_active: true }] });

    if (key === `POST /workout-sessions/${SESSION_ID}/exercises`) {
      const body = JSON.parse(req.postData() ?? '{}') as Record<string, unknown>;
      const clientKey = typeof body.client_key === 'string' ? body.client_key : null;
      const meta = planItemOf[Number(body.workout_plan_exercise_id)];
      if (!meta) {
        unhandled.push(`${key} wpe=${String(body.workout_plan_exercise_id)}`);
        return send(404, { detail: 'e2e: item de ficha desconhecido' });
      }
      const existing = clientKey ? exercisesByKey.get(clientKey) : undefined;
      exercisePosts.push({ client_key: clientKey, workout_plan_exercise_id: body.workout_plan_exercise_id, duplicate: existing != null });
      if (clientKey && existing == null) exercisesByKey.set(clientKey, meta.id);
      createdExercises.add(meta.id);
      return send(200, {
        status: 'ok',
        session_id: SESSION_ID,
        exercise_id: meta.id,
        sequence: meta.sequence,
        mapping_id: meta.mapping,
        equipment_id: null,
        workout_plan_exercise_id: Number(body.workout_plan_exercise_id),
        client_key: clientKey,
        duplicate: existing != null,
      });
    }

    const setsMatch = new RegExp(`^/workout-sessions/${SESSION_ID}/exercises/(\\d+)/sets$`).exec(path);
    if (req.method() === 'POST' && setsMatch) {
      const exerciseId = Number(setsMatch[1]);
      const bucket = setsByExercise.get(exerciseId);
      if (!bucket || !createdExercises.has(exerciseId)) return send(404, { detail: 'Exercício não encontrado nesta sessão.' });
      const body = JSON.parse(req.postData() ?? '{}') as Record<string, unknown>;
      const clientKey = String(body.client_key ?? '');
      if (failures > 0) {
        failures -= 1;
        setPosts.push({ client_key: clientKey, status: failureStatus });
        return send(failureStatus, { detail: 'Erro interno.' });
      }
      const existing = bucket.get(clientKey);
      if (existing) {
        setPosts.push({ client_key: clientKey, status: 200 });
        return send(200, { status: 'ok', set_number: existing.set_number, client_key: clientKey, duplicate: true });
      }
      const setNumber = Number(body.set_number ?? bucket.size + 1);
      bucket.set(clientKey, { set_number: setNumber, client_key: clientKey, body });
      if (exerciseId === FST_EXERCISE_ID) fstBodies.push(body);
      setPosts.push({ client_key: clientKey, status: 200 });
      return send(200, { status: 'ok', set_number: setNumber, client_key: clientKey, duplicate: false });
    }

    unhandled.push(key);
    return send(404, { detail: 'e2e: rota nao mockada' });
  });

  return {
    setPosts,
    exercisePosts,
    serverSets: () => Array.from(setsByExercise.get(EXERCISE_ID)?.values() ?? []),
    crucifixoSets: () => Array.from(setsByExercise.get(CRUCIFIXO_EXERCISE_ID)?.values() ?? []),
    unhandled,
    fstBodies,
    failNextSetPosts: (count, status = 500) => {
      failures = count;
      failureStatus = status;
    },
    setNetworkDown: (down) => {
      networkDown = down;
    },
    setAuthMeStatus: (status) => {
      authMeStatus = status;
    },
    loginAs: (next) => {
      user = next;
    },
  };
}

/** Sessao local legada como o app gravava antes do IndexedDB (migrada na 1a abertura). */
export const LEGACY_SESSION = {
  session_id: SESSION_ID,
  source: 'plan',
  workout_plan_id: PLAN_ID,
  gym_id: 1,
};
