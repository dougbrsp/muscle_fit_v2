import { describe, expect, it } from 'vitest';

import { FALLBACK_ADVANCED_TECHNIQUES } from './advanced-techniques';
import { toKg } from './weight-units';
import {
  createIntensiveBlock,
  INTENSIVE_INTERRUPTED_SAVE,
  intensiveItemDone,
  intensiveLoadConventionSuffix,
  quickAdjustOptions,
  quickAdjustPlannedKg,
  intensiveRecordStatus,
  intensiveReducer,
  intensiveRestSeconds,
  intensiveSetPayload,
  isIntensiveBlockActive,
  nextIntensiveSetNumber,
  normalizeIntensiveBlock,
  pendingRepsRecords,
  reconcileIntensiveBlock,
  resolveIntensiveProfile,
  restoreIntensiveBlocks,
  type IntensiveBlockState,
  type IntensiveEvent,
  type IntensiveProfile,
  techniquePauseSeconds,
} from './workout-mode-intensive';

const T0 = Date.parse('2026-10-24T12:00:00Z');

// Parâmetros como o backend congela (`technique_parameters`): o alvo vem pronto.
const FST7_PARAMS = { technique_code: 'fst7', sets_only: true, default_sets_count: 7, target_rpe: 10 };

function fst7Profile(overrides: Partial<Parameters<typeof resolveIntensiveProfile>[0]> = {}): IntensiveProfile {
  const profile = resolveIntensiveProfile({
    techniqueCode: 'fst7',
    catalog: FALLBACK_ADVANCED_TECHNIQUES,
    parameters: FST7_PARAMS,
    plannedSetsCount: 7,
    itemRestSeconds: 30,
    ...overrides,
  });
  if (!profile) throw new Error('perfil esperado');
  return profile;
}

function run(state: IntensiveBlockState, ...events: IntensiveEvent[]): IntensiveBlockState {
  return events.reduce(intensiveReducer, state);
}

function complete(index: number, reps: number | null, now = T0 + index * 60_000): IntensiveEvent {
  return { type: 'COMPLETE_SET', reps, clientKey: `k${index}`, setNumber: index + 2, now, restSeconds: 30 };
}

describe('intensiveProfile', () => {
  it('FST-7: bloco de séries, 7 séries, descanso do item, RPE-alvo do backend', () => {
    const profile = fst7Profile();
    expect(profile).toMatchObject({
      techniqueCode: 'fst7',
      techniqueName: 'FST-7',
      stageKind: 'working_block',
      totalSets: 7,
      restBetween: { kind: 'item_rest', seconds: 30 },
      loadConstant: true,
      allowsFatigueDrop: true,
      targetRpe: 10,
    });
    expect(profile.stretchGuidance).toMatch(/alongue/i);
    expect(intensiveRestSeconds(profile)).toBe(30);
  });

  it('a prescrição define o nº de séries; sem ela, o default do catálogo', () => {
    expect(fst7Profile({ plannedSetsCount: 5 }).totalSets).toBe(5);
    expect(fst7Profile({ plannedSetsCount: null, parameters: null }).totalSets).toBe(7);
  });

  it('snapshot sem `target_rpe` (ou de outra técnica) não inventa número', () => {
    expect(fst7Profile({ parameters: null }).targetRpe).toBeNull();
    expect(fst7Profile({ parameters: { technique_code: 'gvt', target_rpe: 9 } }).targetRpe).toBeNull();
  });

  it('sem técnica, técnica sem tela ou família não suportada: fluxo normal', () => {
    expect(resolveIntensiveProfile({ techniqueCode: null, itemRestSeconds: 30 })).toBeNull();
    // Fora do catálogo (Myo-Reps/Cluster ficaram no backlog) ou fora do recorte.
    expect(resolveIntensiveProfile({ techniqueCode: 'myo_reps', catalog: FALLBACK_ADVANCED_TECHNIQUES, itemRestSeconds: 60 })).toBeNull();
    expect(
      resolveIntensiveProfile({
        techniqueCode: 'gvt',
        catalog: FALLBACK_ADVANCED_TECHNIQUES,
        plannedSetsCount: 10,
        itemRestSeconds: 60,
        enabledTechniques: new Set(['fst7']),
      }),
    ).toBeNull();
    expect(
      resolveIntensiveProfile({
        techniqueCode: 'drop_set',
        catalog: FALLBACK_ADVANCED_TECHNIQUES,
        plannedSetsCount: 3,
        itemRestSeconds: 60,
        supportedStageKinds: new Set(['working_block']),
      }),
    ).toBeNull();
  });

  it('abstração pronta para o PR 3: drop set = aplicação sem descanso', () => {
    const drop = resolveIntensiveProfile({
      techniqueCode: 'drop_set',
      catalog: FALLBACK_ADVANCED_TECHNIQUES,
      parameters: { technique_code: 'drop_set', applications: 2, pause_seconds: null, load_factor_per_application: 0.8 },
      plannedSetsCount: 3,
      itemRestSeconds: 60,
      enabledTechniques: new Set(['drop_set']),
      supportedStageKinds: new Set(['application']),
    });
    expect(drop).toMatchObject({ stageKind: 'application', restBetween: { kind: 'none' }, loadConstant: false });
    expect(drop && intensiveRestSeconds(drop)).toBe(0);
  });
});

describe('máquina de estados do bloco', () => {
  const profile = fst7Profile();
  const fresh = () => createIntensiveBlock(profile, 60);

  it('briefing → execução → descanso → pronta → execução … → revisão → concluída', () => {
    let s = fresh();
    expect(s.phase).toBe('BRIEFING');
    s = run(s, { type: 'START' });
    expect(s.phase).toBe('SERIE_EM_EXECUCAO');
    s = run(s, complete(1, 12));
    expect(s.phase).toBe('DESCANSO');
    expect(s.currentSet).toBe(2);
    s = run(s, { type: 'REST_EXPIRED' });
    expect(s.phase).toBe('PROXIMA_SERIE_PRONTA');
    s = run(s, { type: 'START_NEXT' });
    expect(s.phase).toBe('SERIE_EM_EXECUCAO');
    for (let i = 2; i <= 7; i += 1) {
      s = run(s, complete(i, 10), { type: 'SAVE_OK', clientKey: `k${i}` }, { type: 'SKIP_REST' });
    }
    s = run(s, { type: 'SAVE_OK', clientKey: 'k1' });
    expect(s.phase).toBe('REVISAO_FINAL');
    expect(s.records).toHaveLength(7);
    s = run(s, { type: 'FINISH' });
    expect(s.phase).toBe('TECNICA_CONCLUIDA');
  });

  it('o descanso começa na conclusão, com os 30 s do item (sem esperar o servidor)', () => {
    const s = run(fresh(), { type: 'START' }, complete(1, null, T0));
    expect(s.phase).toBe('DESCANSO');
    expect(s.rest).toMatchObject({ endsAt: T0 + 30_000, totalSeconds: 30 });
    expect(s.records[0].sync).toBe('saving');
  });

  it('fim do descanso não inicia a série sozinho', () => {
    const s = run(fresh(), { type: 'START' }, complete(1, 10), { type: 'REST_EXPIRED' });
    expect(s.phase).toBe('PROXIMA_SERIE_PRONTA');
    expect(run(s, complete(2, 10)).records).toHaveLength(1);
  });

  it('ajuste rápido durante o cronômetro mantém o `endsAt` e só muda as próximas séries', () => {
    const resting = run(fresh(), { type: 'START' }, complete(1, 12, T0));
    const adjusted = run(resting, { type: 'ADJUST_LOAD', kg: 55, source: 'fatigue' });
    expect(adjusted.rest).toEqual(resting.rest);
    expect(adjusted.records[0].weightKg).toBe(60);
    expect(adjusted.planned).toEqual({ kg: 55, source: 'fatigue' });
    const next = run(adjusted, { type: 'SKIP_REST' }, complete(2, 10), { type: 'SKIP_REST' }, complete(3, 9));
    expect(next.records.map((r) => [r.weightKg, r.plannedKg, r.loadAdjustSource])).toEqual([
      [60, 60, null],
      [55, 55, 'fatigue'],
      // A carga propaga; a origem fica só na série que estreou o ajuste.
      [55, 55, null],
    ]);
  });

  it('desfazer volta a carga planejada anterior sem tocar nas séries concluídas', () => {
    let s = run(fresh(), { type: 'START' }, complete(1, 12));
    s = run(s, { type: 'ADJUST_LOAD', kg: 57.5, source: 'fatigue' }, { type: 'ADJUST_LOAD', kg: 52.5, source: 'manual' });
    expect(s.planned.kg).toBe(52.5);
    s = run(s, { type: 'UNDO_ADJUST' });
    expect(s.planned).toEqual({ kg: 57.5, source: 'fatigue' });
    s = run(s, { type: 'UNDO_ADJUST' });
    expect(s.planned).toEqual({ kg: 60, source: null });
    expect(run(s, { type: 'UNDO_ADJUST' })).toBe(s);
    expect(s.records[0].weightKg).toBe(60);
  });

  it('reps vazias, zero ou inválidas gravam como pendente (nunca 0 nem a meta)', () => {
    const s = run(fresh(), { type: 'START' }, complete(1, 0));
    expect(s.records[0].reps).toBeNull();
    const body = intensiveSetPayload(s.records[0], 30);
    expect(body).toMatchObject({ reps: null, reps_pending: true, rpe_target_source: 'protocol', client_key: 'k1' });
    expect(body).not.toHaveProperty('rpe_target_effective');
    expect(body.rpe).toBeNull();
  });

  it('RPE relatado da série (pré-preenchido com o alvo na tela) vai no POST e sobrevive à confirmação', () => {
    const withRpe = (index: number, reps: number | null, rpe: number | null): IntensiveEvent => ({
      type: 'COMPLETE_SET', reps, rpe, clientKey: `k${index}`, setNumber: index + 2, now: T0 + index * 60_000, restSeconds: 30,
    });
    let s = createIntensiveBlock({ techniqueCode: 'fst7', totalSets: 2 }, 40);
    s = run(s, { type: 'START' }, withRpe(1, null, 10), { type: 'SAVE_OK', clientKey: 'k1' });
    expect(intensiveSetPayload(s.records[0], 30)).toMatchObject({ rpe: 10, rpe_target_source: 'protocol', reps_pending: true });
    // Confirmar reps / corrigir carga (`UPDATE_RECORD` sem `rpe`) não apaga o RPE enviado.
    s = run(s, { type: 'UPDATE_RECORD', clientKey: 'k1', reps: 11, weightKg: 42.5 });
    expect(s.records[0]).toMatchObject({ reps: 11, weightKg: 42.5, rpe: 10 });
    expect(intensiveSetPayload(s.records[0], 30)).toMatchObject({ rpe: 10, reps: 11, reps_pending: false });
    // Campo apagado na tela = `rpe: null` (nunca o alvo no lugar).
    s = run(s, { type: 'SKIP_REST' }, withRpe(2, 8, null));
    expect(intensiveSetPayload(s.records[1], 30).rpe).toBeNull();
  });

  it('erro de rede: a série fica com erro e a nova tentativa usa a mesma chave', () => {
    let s = run(fresh(), { type: 'START' }, complete(1, 12));
    s = run(s, { type: 'SAVE_FAILED', clientKey: 'k1', error: 'Sem conexão' });
    expect(s.records[0]).toMatchObject({ sync: 'error', error: 'Sem conexão' });
    expect(s.phase).toBe('DESCANSO');
    s = run(s, { type: 'RETRY', clientKey: 'k1' });
    expect(s.records[0]).toMatchObject({ sync: 'saving', clientKey: 'k1' });
  });

  it('revisão com pendentes: espera a gravação, destaca as pendentes e confirma por PATCH', () => {
    let s = createIntensiveBlock({ techniqueCode: 'fst7', totalSets: 2 }, 40);
    s = run(s, { type: 'START' }, complete(1, null), { type: 'SAVE_OK', clientKey: 'k1' }, { type: 'SKIP_REST' }, complete(2, 8));
    expect(s.phase).toBe('REGISTRO_PENDENTE');
    expect(run(s, { type: 'FINISH' }).phase).toBe('REGISTRO_PENDENTE');
    s = run(s, { type: 'SAVE_OK', clientKey: 'k2' });
    expect(s.phase).toBe('REVISAO_FINAL');
    expect(pendingRepsRecords(s).map((r) => r.index)).toEqual([1]);
    expect(intensiveRecordStatus(s.records[0])).toBe('pending_reps');
    s = run(s, { type: 'UPDATE_RECORD', clientKey: 'k1', reps: 11 });
    expect(pendingRepsRecords(s)).toHaveLength(0);
    expect(intensiveRecordStatus(s.records[0])).toBe('done');
  });

  it('encerrar técnica antes da última série abre a revisão; sem séries, conclui', () => {
    const s = run(fresh(), { type: 'START' }, complete(1, 12), { type: 'SAVE_OK', clientKey: 'k1' }, { type: 'END_TECHNIQUE' });
    expect(s.phase).toBe('REVISAO_FINAL');
    expect(s.rest).toBeNull();
    expect(run(fresh(), { type: 'END_TECHNIQUE' }).phase).toBe('TECNICA_CONCLUIDA');
  });

  it('pular série avança sem gravar', () => {
    const s = run(fresh(), { type: 'START' }, { type: 'SKIP_SET' });
    expect(s).toMatchObject({ currentSet: 2, skipped: [1], phase: 'SERIE_EM_EXECUCAO', records: [] });
  });

  it('próximo set_number físico considera as séries locais ainda não confirmadas', () => {
    const s = run(fresh(), { type: 'START' }, complete(1, 12));
    expect(nextIntensiveSetNumber(s, [{ set_number: 1 }, { set_number: 2 }])).toBe(4);
  });
});

describe('retomada', () => {
  const profile = fst7Profile();

  it('snapshot: o servidor confirma por client_key e o descanso continua do ponto', () => {
    const stored = run(createIntensiveBlock(profile, 60), { type: 'START' }, complete(1, null, T0), {
      type: 'ADJUST_LOAD',
      kg: 55,
      source: 'fatigue',
    });
    const persisted = JSON.parse(JSON.stringify(stored));
    const resumed = reconcileIntensiveBlock({
      stored: persisted,
      profile,
      serverSets: [
        { set_number: 1, set_type: 'warmup', reps: 15, weight_kg: '20' },
        { set_number: 3, set_type: 'working', reps: null, reps_pending: 1, weight_kg: '60.00', client_key: 'k1' },
      ],
      initialPlannedKg: 70,
      now: T0 + 10_000,
    });
    expect(resumed.phase).toBe('DESCANSO');
    expect(resumed.rest?.endsAt).toBe(T0 + 30_000);
    expect(resumed.records[0]).toMatchObject({ sync: 'saved', setNumber: 3, reps: null });
    expect(resumed.planned).toEqual({ kg: 55, source: 'fatigue' });
    expect(resumed.undo).toHaveLength(1);
  });

  it('snapshot com descanso vencido: próxima série pronta, sem início automático', () => {
    const stored = run(createIntensiveBlock(profile, 60), { type: 'START' }, complete(1, 12, T0));
    const resumed = reconcileIntensiveBlock({ stored, profile, serverSets: [], initialPlannedKg: 60, now: T0 + 120_000 });
    expect(resumed.phase).toBe('PROXIMA_SERIE_PRONTA');
    expect(normalizeIntensiveBlock(stored, T0 + 1_000).phase).toBe('DESCANSO');
  });

  it('PATCH local não volta a pendente com um refetch atrasado', () => {
    let stored = run(createIntensiveBlock(profile, 60), { type: 'START' }, complete(1, null), { type: 'SAVE_OK', clientKey: 'k1' });
    stored = run(stored, { type: 'UPDATE_RECORD', clientKey: 'k1', reps: 9 });
    const resumed = reconcileIntensiveBlock({
      stored,
      profile,
      serverSets: [{ set_number: 3, set_type: 'working', reps: null, reps_pending: true, client_key: 'k1' }],
      initialPlannedKg: 60,
      now: T0,
    });
    expect(resumed.records[0].reps).toBe(9);
  });

  it('sem snapshot: reconstrói pelo servidor (pendentes, completed_at e descanso)', () => {
    const completed = new Date(T0).toISOString();
    const resumed = reconcileIntensiveBlock({
      stored: null,
      profile,
      serverSets: [
        { set_number: 2, set_type: 'working', reps: 12, weight_kg: '60', completed_at: completed },
        { set_number: 3, set_type: 'working', reps: null, reps_pending: 1, weight_kg: '57.5', planned_weight_kg: '57.5', completed_at: completed },
      ],
      initialPlannedKg: 70,
      now: T0 + 5_000,
    });
    expect(resumed.phase).toBe('DESCANSO');
    expect(resumed.currentSet).toBe(3);
    expect(resumed.rest?.endsAt).toBe(T0 + 30_000);
    expect(resumed.planned.kg).toBe(57.5);
    expect(pendingRepsRecords(resumed).map((r) => r.index)).toEqual([2]);
  });

  it('snapshot inválido ou de outra técnica é ignorado', () => {
    const resumed = reconcileIntensiveBlock({ stored: { version: 9 }, profile, serverSets: [], initialPlannedKg: 50, now: T0 });
    expect(resumed.phase).toBe('BRIEFING');
    expect(resumed.planned.kg).toBe(50);
  });
});

describe('conclusão do exercício', () => {
  it('o bloco ativo segura o item; concluído libera; pendente sem snapshot reabre', () => {
    expect(intensiveItemDone({ stored: { phase: 'REVISAO_FINAL' }, countersDone: true, serverSets: [] })).toBe(false);
    expect(intensiveItemDone({ stored: { phase: 'TECNICA_CONCLUIDA' }, countersDone: false, serverSets: [] })).toBe(true);
    expect(intensiveItemDone({ stored: { phase: 'BRIEFING' }, countersDone: false, serverSets: [] })).toBe(false);
    expect(
      intensiveItemDone({
        stored: null,
        countersDone: true,
        serverSets: [{ set_number: 1, set_type: 'working', reps: null, reps_pending: 1 }],
      }),
    ).toBe(false);
    expect(intensiveItemDone({ stored: null, countersDone: true, serverSets: [] })).toBe(true);
    expect(isIntensiveBlockActive(null)).toBe(false);
  });
});

describe('ajuste rápido na unidade exibida', () => {
  it('kg: usa o passo do equipamento e arredonda para baixo (espelho do dono)', () => {
    expect(quickAdjustPlannedKg({ plannedKg: 70, unit: 'kg', loadIncrementKg: 5, action: 'minus_step' })).toBe(65);
    expect(quickAdjustPlannedKg({ plannedKg: 70, unit: 'kg', loadIncrementKg: 5, action: 'minus_10pct' })).toBe(60);
  });

  it('lb: converte carga e passo antes do espelho e devolve kg', () => {
    // 70,31 kg = 155 lb; passo default em lb (5 lb) -> 150 lb.
    const kg = quickAdjustPlannedKg({ plannedKg: 70.31, unit: 'lb', loadIncrementKg: null, action: 'minus_step' });
    expect(kg).toBe(toKg(150, 'lb'));
  });

  it('rótulos derivados do passo e dos fatores do backend', () => {
    expect(quickAdjustOptions('kg', 2.5).map((o) => o.label)).toEqual(['−2.5 kg', '−5%', '−10%']);
  });

  it('rótulo da convenção só quando o número vale por unidade', () => {
    expect(intensiveLoadConventionSuffix('per_dumbbell')).toBe('por halter');
    expect(intensiveLoadConventionSuffix('total')).toBeNull();
    expect(intensiveLoadConventionSuffix(null)).toBeNull();
  });
});

describe('snapshot local', () => {
  it('chaves numéricas; série "gravando" vira erro visível com a mesma chave', () => {
    const profile = fst7Profile();
    const stored = run(createIntensiveBlock(profile, 60), { type: 'START' }, complete(1, 12));
    const restored = restoreIntensiveBlocks(JSON.parse(JSON.stringify({ '12': stored, x: 1, '13': { version: 2 } })));
    expect(Object.keys(restored)).toEqual(['12']);
    expect(restored[12].records[0]).toMatchObject({ sync: 'error', clientKey: 'k1', error: INTENSIVE_INTERRUPTED_SAVE });
    expect(restoreIntensiveBlocks(null)).toEqual({});
  });
});

describe('techniquePauseSeconds (fluxo normal e Intensivo)', () => {
  it('usa a pausa pronta da prescrição quando é desta técnica', () => {
    expect(techniquePauseSeconds('rest_pause', { technique_code: 'rest_pause', pause_seconds: 15 }, null)).toBe(15);
  });
  it('cai na duração da ficha sem pausa da prescrição ou com parâmetros de outra técnica', () => {
    expect(techniquePauseSeconds('rest_pause', null, 20)).toBe(20);
    expect(techniquePauseSeconds('rest_pause', { technique_code: 'drop_set', pause_seconds: 9 }, 20)).toBe(20);
  });
  it('drop set sem pausa em lugar nenhum fica sem pausa', () => {
    expect(techniquePauseSeconds('drop_set', { technique_code: 'drop_set', pause_seconds: null }, null)).toBeNull();
  });
});
