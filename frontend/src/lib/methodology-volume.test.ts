import { describe, expect, it } from 'vitest';
import { statusVisual } from './dashboard-status';
import {
  buildAxisView,
  buildDataQualityItems,
  buildEvaluationSummary,
  buildMethodologyRows,
  buildMethodologyVolumePayload,
  buildOverallView,
  buildRowView,
  formatRange,
  formatWeeklyFrequency,
  serializeMethodologyVolumePayload,
} from './methodology-volume';
import {
  FIXTURE_EVALUATION,
  FIXTURE_GROUP_BELOW_DERIVED,
  FIXTURE_GROUP_MODE_A,
  FIXTURE_GROUP_NOT_DIRECTLY_TRAINED,
  FIXTURE_GROUP_WITHIN,
} from './methodology-volume-fixtures';
import type { WorkoutPlanTemplateSlotPayload } from './workout-plan-templates-types';

const slot = (sequence: number, items: Array<Partial<WorkoutPlanTemplateSlotPayload['items'][number]>>) =>
  ({
    sequence,
    name: `Treino ${sequence}`,
    weekday: null,
    slot_type: 'normal',
    priority_min_pct: null,
    priority_max_pct: null,
    items: items.map((item, index) => ({
      item_order: index + 1,
      item_kind: 'equipment',
      muscle_group_id: null,
      mapping_id: 10 + index,
      equipment_id: null,
      sets_count: 3,
      reps_target: '10',
      rpe_target: null,
      prioritize: false,
      cardio_duration_minutes: null,
      priority_min_pct: null,
      priority_max_pct: null,
      advanced_technique: null,
      advanced_technique_applications: null,
      advanced_technique_duration_seconds: null,
      warmup_sets_count: null,
      warmup_reps: null,
      warmup_rest_seconds: null,
      feeler_set_sets_count: null,
      feeler_set_reps: null,
      feeler_set_rest_seconds: null,
      peak_contraction_seconds: null,
      ...item,
    })),
  }) as WorkoutPlanTemplateSlotPayload;

describe('corpo do preview de metodologia', () => {
  it('é o corpo mínimo do preview de ativação + intenção', () => {
    const payload = buildMethodologyVolumePayload([slot(1, [{}])], 'equipment_series', 'standard', []);
    expect(payload.intent).toBe('standard');
    expect(payload.target_group_ids).toEqual([]);
    expect(payload.volume_mode).toBe('equipment_series');
    expect(payload.slots[0].items[0].mapping_id).toBe(10);
    // Campos que não entram no cálculo saem do corpo (mesma queryKey ao editá-los).
    expect(payload.slots[0].items[0].reps_target).toBeNull();
    expect(payload.slots[0].name).toBe('Dia 1');
  });

  it('grupos-alvo só viajam na especialização, ordenados', () => {
    expect(buildMethodologyVolumePayload([], undefined, 'specialization', [52, 4]).target_group_ids).toEqual([4, 52]);
    expect(buildMethodologyVolumePayload([], undefined, 'maintenance', [52, 4]).target_group_ids).toEqual([]);
  });

  it('a string serializada muda com a intenção (queryKey) e não com reps', () => {
    const a = serializeMethodologyVolumePayload([slot(1, [{ reps_target: '8' }])], 'equipment_series', 'standard', []);
    const b = serializeMethodologyVolumePayload([slot(1, [{ reps_target: '12' }])], 'equipment_series', 'standard', []);
    const c = serializeMethodologyVolumePayload([slot(1, [{ reps_target: '8' }])], 'equipment_series', 'cutting', []);
    expect(a).toBe(b);
    expect(a).not.toBe(c);
  });
});

describe('view-model por eixo (unidade sempre escrita)', () => {
  it('faixa do autor: valor, unidade, faixa e origem "metodologia"', () => {
    const view = buildAxisView('sets', FIXTURE_GROUP_WITHIN.direct_sets_axis);
    expect(view.valueText).toBe('12');
    expect(view.unit).toBe('séries diretas por sessão');
    expect(view.rangeText).toBe('9–16');
    expect(view.isDerived).toBe(false);
    expect(view.sourceVisual?.label).toBe('Metodologia');
    expect(view.status.label).toBe('Na faixa');
    expect(view.ariaLabel).toContain('séries diretas por sessão: Na faixa');
    expect(view.ariaLabel).toContain('12 séries diretas por sessão');
    expect(view.ariaLabel).toContain('faixa 9–16');
    expect(view.ariaLabel).not.toContain('derivada');
  });

  it('faixa derivada fica marcada no view-model e no aria-label', () => {
    const view = buildAxisView('exercises', FIXTURE_GROUP_BELOW_DERIVED.direct_exercises_axis);
    expect(view.isDerived).toBe(true);
    expect(view.sourceVisual?.label).toBe('Derivada');
    expect(view.ariaLabel).toContain('faixa 1–2 (derivada)');
    expect(view.status.label).toBe('Abaixo da faixa');
    expect(view.isInformational).toBe(false);
  });

  it('NOT_DIRECTLY_TRAINED é neutro e se distingue de BELOW_RANGE por rótulo e ícone (paleta revista 2026-10-02)', () => {
    const view = buildAxisView('sets', FIXTURE_GROUP_NOT_DIRECTLY_TRAINED.direct_sets_axis);
    const below = statusVisual('methodologyVolume', 'BELOW_RANGE');
    expect(view.status.label).toBe('Sem trabalho direto');
    expect(view.status.color).toBe('gray');
    // Abaixo da faixa é laranja desde 2026-10-02; a distinção também é
    // rótulo + ícone, nunca só cor.
    expect(below.color).toBe('orange');
    expect(view.status.label).not.toBe(below.label);
    expect(view.status.Icon).not.toBe(below.Icon);
    expect(view.rangeText).toBe('—');
  });

  it('NOT_EVALUABLE traduz a reason e aponta onde corrigir', () => {
    const view = buildAxisView('exercises', FIXTURE_GROUP_MODE_A.direct_exercises_axis);
    expect(view.reasonLabel).toBe('Modo por grupo não define exercícios');
    expect(view.reasonHint).toContain('geração');
    expect(view.ariaLabel).toBe('exercícios por sessão: Não avaliável; Modo por grupo não define exercícios');

    const unclassified = buildAxisView('sets', { ...FIXTURE_GROUP_MODE_A.direct_exercises_axis, reason: 'UNCLASSIFIED_GROUP' });
    expect(unclassified.reasonLabel).toBe('Grupo sem macro-grupo');
    expect(unclassified.reasonHint).toContain('Macro-grupos de volume');
  });

  it('motivos novos da Fase 6 têm rótulo e apontam a aba de macro-grupos', () => {
    for (const [reason, label] of [
      ['NO_RANGE_FOR_FREQUENCY', 'Macro-grupo sem faixa para esta frequência'],
      ['NO_EXERCISES_RANGE', 'Macro-grupo sem faixa de exercícios'],
      ['NO_WEEKLY_TARGET', 'Macro-grupo sem meta semanal'],
    ] as const) {
      const view = buildAxisView('sets', { ...FIXTURE_GROUP_MODE_A.direct_exercises_axis, reason });
      expect(view.reasonLabel).toBe(label);
      expect(view.reasonHint).toContain('Macro-grupos de volume');
    }
  });

  it('eixo semanal lê value_weekly, chama a faixa de meta e formata fração com vírgula', () => {
    const view = buildAxisView('weekly', FIXTURE_GROUP_WITHIN.weekly_sets_axis);
    expect(view.unit).toBe('séries diretas por semana');
    expect(view.valueText).toBe('16,8');
    expect(view.rangeText).toBe('12,5–20');
    expect(view.rangeWord).toBe('meta');
    expect(view.sourceCode).toBe('admin');
    expect(view.sourceVisual?.label).toBe('Admin');
    expect(view.ariaLabel).toBe('séries diretas por semana: Na faixa; 16,8 séries diretas por semana; meta 12,5–20');
  });

  it('desvio esperado pela intenção vira informativo sem mudar o status', () => {
    const view = buildAxisView('sets', FIXTURE_GROUP_MODE_A.direct_sets_axis);
    expect(view.status.label).toBe('Acima da faixa');
    expect(view.isInformational).toBe(true);
    expect(view.ariaLabel).toContain('desvio esperado pela intenção declarada');
  });
});

describe('linhas e cabeçalho', () => {
  it('frequência normalizada de ciclo de N dias é escrita', () => {
    const rows = buildMethodologyRows(FIXTURE_EVALUATION);
    const biceps = rows.find((row) => row.groupName === 'Bíceps');
    expect(biceps?.frequencyText).toBe('3,5x/sem');
    expect(biceps?.frequencyNote).toBe('contada em 7 dias a partir do 1º treino (ciclo de 10 dias)');
    expect(biceps?.volumeClassName).toBe('Pequenos');

    const peitoral = rows.find((row) => row.groupName === 'Peitoral');
    expect(peitoral?.frequencyNote).toBeNull();
    expect(peitoral?.memberCount).toBe(2);
    expect(peitoral?.volumeClassName).toBe('Grandes');
    expect(peitoral?.weekly.valueText).toBe('16,8');
    expect(peitoral?.overall.statusCode).toBe('WITHIN_RANGE');

    const quad = rows.find((row) => row.groupName === 'Quadríceps');
    expect(quad?.isIntentTarget).toBe(true);
    expect(quad?.weekly.status.label).toBe('Acima da faixa');
    expect(quad?.overall.isInformational).toBe(true);
  });

  it('status combinado: conflito escrito, motivo traduzido e fallback para payload antigo', () => {
    const conflict = buildOverallView({
      ...FIXTURE_GROUP_BELOW_DERIVED,
      status: 'BELOW_RANGE',
      severity: 'warning',
      status_conflict: true,
    });
    expect(conflict.status.label).toBe('Abaixo da faixa');
    expect(conflict.conflict).toBe(true);
    expect(conflict.ariaLabel).toBe('Status do grupo: Abaixo da faixa; eixos em conflito');
    expect(conflict.description).toContain('incoerentes');

    const unclassified = buildOverallView({
      ...FIXTURE_GROUP_BELOW_DERIVED,
      volume_class_id: null,
      volume_class_name: null,
      status: 'NOT_EVALUABLE',
      status_reason: 'UNCLASSIFIED_GROUP',
    });
    expect(unclassified.reasonLabel).toBe('Grupo sem macro-grupo');
    expect(buildRowView({ ...FIXTURE_GROUP_BELOW_DERIVED, volume_class_name: null }, 7).volumeClassName).toBe(
      'Sem macro-grupo',
    );

    // Payload anterior à Fase 6 (sem `status`): cai no eixo de séries por sessão.
    const legacy = { ...FIXTURE_GROUP_BELOW_DERIVED } as Partial<typeof FIXTURE_GROUP_BELOW_DERIVED>;
    delete legacy.status;
    delete legacy.weekly_sets_axis;
    const legacyRow = buildRowView(legacy as typeof FIXTURE_GROUP_BELOW_DERIVED, 7);
    expect(legacyRow.overall.statusCode).toBe('BELOW_RANGE');
    expect(legacyRow.weekly.statusCode).toBe('NOT_EVALUABLE');
  });

  it('resumo escreve modo, ciclo e normalização', () => {
    expect(buildEvaluationSummary(FIXTURE_EVALUATION)).toBe(
      'Modelo por equipamento (modo B) · ciclo de 10 dias (7 sessões de treino) — frequência e volume contados em 7 dias a partir do primeiro slot de treino',
    );
  });

  it('formatadores', () => {
    expect(formatRange(3, 3)).toBe('3');
    expect(formatRange(12.5, 20)).toBe('12,5–20');
    expect(formatRange(null, 4)).toBe('—');
    expect(formatWeeklyFrequency(2)).toBe('2x/sem');
  });
});

describe('rodapé de qualidade de dado', () => {
  it('lista só contadores > 0, com link para o Domínio de exercícios', () => {
    const items = buildDataQualityItems(FIXTURE_EVALUATION.data_quality);
    expect(items.map((item) => item.key)).toEqual(['items_without_primary_contribution', 'items_without_canonical']);
    expect(items[0].text).toContain('2 itens sem contribuição PRIMARY cadastrada');
    expect(items[0].fixHref).toBe('/dominio-exercicios');
    expect(items[1].text).toContain('1 item sem exercício canônico');
  });

  it('sem payload não há itens', () => {
    expect(buildDataQualityItems(undefined)).toEqual([]);
  });
});
