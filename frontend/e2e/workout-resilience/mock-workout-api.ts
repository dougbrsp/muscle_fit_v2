import type { Page, Route } from '@playwright/test';

/**
 * API mockada e COM ESTADO para os cenarios de resiliencia do Modo Treino.
 * Replica so o contrato de idempotencia do backend (`client_key` por exercicio:
 * a 2a chamada devolve `duplicate: true` sem gravar); nao replica regra de
 * negocio (valores arbitrarios). Chamadas nao mapeadas respondem 404 e ficam em
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
export const EXERCISE_ID = 501;
export const PLAN_ITEM_ID = 7001;
export const FST_PLAN_ITEM_ID = 7003;
export const FST_EXERCISE_ID = 503;

type StoredSet = { set_number: number; client_key: string; body: Record<string, unknown> };

export type MockWorkoutApi = {
  /** Chamadas `POST .../sets` recebidas (inclui as recusadas por falha forcada). */
  setPosts: Array<{ client_key: string; status: number }>;
  /** Series efetivamente gravadas no "servidor" (dedupe por client_key). */
  serverSets: () => StoredSet[];
  unhandled: string[];
  /** Corpos das series FST-7 recebidas (para conferir a carga efetiva gravada). */
  fstBodies: Array<Record<string, unknown>>;
  /** Proximos N POST de serie respondem com `status` (padrao 500). */
  failNextSetPosts: (count: number, status?: number) => void;
  /** Rede fora: toda chamada a API falha na conexao (como sem sinal). */
  setNetworkDown: (down: boolean) => void;
  /** Troca o usuario autenticado (cookie simulado). */
  loginAs: (user: UserKey) => void;
  setClock: (ms: number) => void;
};

const JSON_HEADERS = { 'content-type': 'application/json' };

export async function installWorkoutApi(page: Page, initialUser: UserKey = 'ana'): Promise<MockWorkoutApi> {
  let user: UserKey = initialUser;
  let failures = 0;
  let failureStatus = 500;
  let networkDown = false;
  const unhandled: string[] = [];
  const fstBodies: Array<Record<string, unknown>> = [];
  const setPosts: MockWorkoutApi['setPosts'] = [];
  const sets = new Map<string, StoredSet>(); // client_key -> set
  const fstSets = new Map<string, StoredSet>();
  let fstCreated = false;
  const sessionOwner = 1;

  const activity = () => ({
    id: SESSION_ID,
    local_date: '2026-10-14',
    activity_type: 'strength',
    name: 'Treino A',
    gym_id: 1,
    session_status: 'in_progress',
    workout_plan_id: PLAN_ID,
    exercises: [
      {
        id: EXERCISE_ID,
        activity_id: SESSION_ID,
        sequence: 1,
        original_name: 'Supino reto',
        mapping_id: 11,
        workout_plan_exercise_id: PLAN_ITEM_ID,
        sets: Array.from(sets.values())
          .sort((a, b) => a.set_number - b.set_number)
          .map((stored) => ({ ...stored.body, set_number: stored.set_number, client_key: stored.client_key })),
      },
      ...(fstCreated
        ? [
            {
              id: FST_EXERCISE_ID,
              activity_id: SESSION_ID,
              sequence: 2,
              original_name: 'Peck deck',
              mapping_id: 13,
              workout_plan_exercise_id: FST_PLAN_ITEM_ID,
              sets: Array.from(fstSets.values())
                .sort((a, b) => a.set_number - b.set_number)
                .map((stored) => ({ ...stored.body, set_number: stored.set_number, client_key: stored.client_key })),
            },
          ]
        : []),
    ],
  });

  const plan = {
    id: PLAN_ID,
    name: 'Treino A',
    gym_id: 1,
    gym_name: 'Academia Central',
    exercises: [
      {
        workout_plan_exercise_id: PLAN_ITEM_ID,
        sequence: 1,
        exercise_name: 'Supino reto',
        mapping_id: 11,
        sets_count: 4,
        reps_target: '8-12',
        rest_seconds: 60,
      },
      {
        workout_plan_exercise_id: 7002,
        sequence: 2,
        exercise_name: 'Crucifixo',
        mapping_id: 12,
        sets_count: 3,
        reps_target: '10-15',
        rest_seconds: 60,
      },
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
      fstCreated = true;
      return send(200, {
        status: 'ok',
        session_id: SESSION_ID,
        exercise_id: FST_EXERCISE_ID,
        sequence: 2,
        mapping_id: 13,
        equipment_id: null,
        workout_plan_exercise_id: FST_PLAN_ITEM_ID,
        duplicate: false,
      });
    }
    const fstPath = new RegExp(`^/workout-sessions/${SESSION_ID}/exercises/${FST_EXERCISE_ID}/sets$`);
    if (req.method() === 'POST' && fstPath.test(path)) {
      const body = JSON.parse(req.postData() ?? '{}') as Record<string, unknown>;
      const clientKey = String(body.client_key ?? '');
      const existing = fstSets.get(clientKey);
      if (existing) {
        setPosts.push({ client_key: clientKey, status: 200 });
        return send(200, { status: 'ok', set_number: existing.set_number, client_key: clientKey, duplicate: true });
      }
      const setNumber = Number(body.set_number ?? fstSets.size + 1);
      fstSets.set(clientKey, { set_number: setNumber, client_key: clientKey, body });
      fstBodies.push(body);
      setPosts.push({ client_key: clientKey, status: 200 });
      return send(200, { status: 'ok', set_number: setNumber, client_key: clientKey, duplicate: false });
    }

    const setsPath = new RegExp(`^/workout-sessions/${SESSION_ID}/exercises/${EXERCISE_ID}/sets$`);
    if (req.method() === 'POST' && setsPath.test(path)) {
      const body = JSON.parse(req.postData() ?? '{}') as Record<string, unknown>;
      const clientKey = String(body.client_key ?? '');
      if (failures > 0) {
        failures -= 1;
        setPosts.push({ client_key: clientKey, status: failureStatus });
        return send(failureStatus, { detail: 'Erro interno.' });
      }
      const existing = sets.get(clientKey);
      if (existing) {
        setPosts.push({ client_key: clientKey, status: 200 });
        return send(200, { status: 'ok', set_number: existing.set_number, client_key: clientKey, duplicate: true });
      }
      const setNumber = Number(body.set_number ?? sets.size + 1);
      sets.set(clientKey, { set_number: setNumber, client_key: clientKey, body });
      setPosts.push({ client_key: clientKey, status: 200 });
      return send(200, { status: 'ok', set_number: setNumber, client_key: clientKey, duplicate: false });
    }

    unhandled.push(key);
    return send(404, { detail: 'e2e: rota nao mockada' });
  });

  return {
    setPosts,
    serverSets: () => Array.from(sets.values()),
    unhandled,
    fstBodies,
    failNextSetPosts: (count, status = 500) => {
      failures = count;
      failureStatus = status;
    },
    setNetworkDown: (down) => {
      networkDown = down;
    },
    loginAs: (next) => {
      user = next;
    },
    setClock: () => undefined,
  };
}

/** Sessao local legada como o app gravava antes do IndexedDB (migrada na 1a abertura). */
export const LEGACY_SESSION = {
  session_id: SESSION_ID,
  source: 'plan',
  workout_plan_id: PLAN_ID,
  gym_id: 1,
};
