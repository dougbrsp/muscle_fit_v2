import { describe, expect, it } from "vitest";
import {
  EXERCISE_PROGRESS_BASE_PATH,
  STATUS_REASON_TEXTS,
  COMPARABILITY_MISMATCH_HINTS,
  COMPARABILITY_MISMATCH_LABELS,
  fatigueContextLabel,
  statusWithContextLabel,
  exerciseProgressHref,
  buildExposurePair,
  E1RM_NOT_DECISIVE_NOTE,
  PAIR_NO_PREVIOUS_TEXT,
  PAIR_UNAVAILABLE_TEXT,
  PAIRED_REPS_EFFORT_LABEL,
  pairNotComparableText,
  buildExerciseProgressCurves,
  exerciseSearchTruncationNotice,
  buildRpeSeries,
  buildTrendSeries,
  comparabilityMismatch,
  decisionsInCurrentContext,
  e1rmReliabilityNote,
  outcomeTone,
  pairDeltaText,
  progressionDecisionLabel,
  SERIES_UNAVAILABLE_TEXT,
  sessionEvolutionView,
  statusReasonsText,
} from "./exercise-progress";
import { EMPTY_DASHBOARD_FILTERS } from "./dashboard-filters";
import type {
  ComparabilityKey,
  Exposure,
  LatestComparison,
  ProgressionDecision,
} from "./dashboard-v3-types";
import type { LoadConvention } from "./equipment-exercises-types";
import type { ExerciseProgressItem } from "./analytics-types";
import motorConstants from "./motor-constants.generated.json";

const LIMIARES = motorConstants.progression_thresholds;

const VERSIONS = {
  exposure: "exposure-v1",
  e1rm: "epley-rir-v1",
  tonnage: "tonnage-v1",
};

function key(
  equipmentId: number | null,
  convention: LoadConvention = "total",
  equipmentAgnostic = false,
): ComparabilityKey {
  return {
    canonical_exercise_id: 10,
    equipment_id: equipmentId,
    load_convention: convention,
    equipment_agnostic: equipmentAgnostic,
  };
}

function exposure(
  overrides: Partial<Exposure> & { comparability_key: ComparabilityKey },
): Exposure {
  return {
    activity_id: 1,
    local_date: "2026-09-01",
    started_at: null,
    equipment_id: overrides.comparability_key.equipment_id,
    equipment_name: "Supino máquina",
    load_convention: overrides.comparability_key.load_convention,
    load_convention_source: "equipment_exercises",
    position_in_session: 1,
    sets: [],
    total_reps: 24,
    best_load_kg: 60,
    avg_rpe: 8,
    e1rm: 75,
    tonnage: 1440,
    // Padrão: na sequência do contexto atual (API nova). API antiga: passar `in_context_run: undefined`.
    in_context_run: true,
    ...overrides,
  };
}

describe("exerciseProgressHref", () => {
  it("a janela padrão não polui a URL", () => {
    expect(exerciseProgressHref(42, EMPTY_DASHBOARD_FILTERS)).toBe(
      `${EXERCISE_PROGRESS_BASE_PATH}/42`,
    );
  });

  it("preserva o filtro global do hub (janela personalizada)", () => {
    const href = exerciseProgressHref(42, {
      ...EMPTY_DASHBOARD_FILTERS,
      window: "custom",
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
    });
    expect(href).toContain("window=custom");
    expect(href).toContain("date_from=2026-01-01");
    expect(href).toContain("date_to=2026-03-31");
  });

  it("preserva o grupo muscular selecionado no drill-down de músculos", () => {
    expect(
      exerciseProgressHref(7, {
        ...EMPTY_DASHBOARD_FILTERS,
        window: "90d",
        muscleGroupId: 3,
      }),
    ).toBe(`${EXERCISE_PROGRESS_BASE_PATH}/7?window=90d&muscleGroupId=3`);
  });
});

describe("statusReasonsText", () => {
  it("traduz cada código de comparability.REASON_* preservando a ordem", () => {
    const texts = statusReasonsText([
      "comparability_key_changed",
      "equipment_not_resolved",
    ]);
    expect(texts.map((item) => item.code)).toEqual([
      "comparability_key_changed",
      "equipment_not_resolved",
    ]);
    expect(texts.every((item) => item.known)).toBe(true);
    expect(texts[0].text).toBe(STATUS_REASON_TEXTS.comparability_key_changed);
  });

  it("exibe o próprio código quando o backend emite uma razão que este frontend não conhece", () => {
    const [item] = statusReasonsText(["razao_do_futuro"]);
    expect(item).toEqual({
      code: "razao_do_futuro",
      text: "razao_do_futuro",
      known: false,
    });
  });

  it("lista vazia para null/undefined (nunca quebra a tela)", () => {
    expect(statusReasonsText(null)).toEqual([]);
    expect(statusReasonsText(undefined)).toEqual([]);
  });

  it("a razão de dados insuficientes diz o que falta: duas exposições comparáveis", () => {
    expect(STATUS_REASON_TEXTS.not_enough_comparable_exposures).toContain(
      "duas exposições comparáveis",
    );
  });

  it("menos séries é aviso informativo, nunca regressão (progression-status-v2)", () => {
    const texts = statusReasonsText([
      "same_load_more_reps",
      "fewer_working_sets",
    ]);
    expect(texts.every((item) => item.known)).toBe(true);
    expect(texts[1].text).toContain("menos séries");
    expect(texts[1].text).toContain("não conta como regressão");
    expect(texts[0].text).toContain("por série");
  });
});

describe("comparabilidade com a chave atual (pronta do backend, M76 Lote 3c)", () => {
  it("sem motivo do backend a exposição é comparável", () => {
    expect(
      comparabilityMismatch(exposure({ comparability_key: key(7) })),
    ).toBeNull();
    expect(
      comparabilityMismatch(
        exposure({ comparability_key: key(7), mismatch_with_current: null }),
      ),
    ).toBeNull();
  });

  it("repassa o motivo calculado pelo backend, sem refazer keys_match", () => {
    for (const motivo of [
      "equipment",
      "load_convention",
      "equipment_unknown",
    ] as const) {
      // A chave da exposição é IGUAL à atual: quem decide é o campo do backend.
      const item = exposure({
        comparability_key: key(7),
        mismatch_with_current: motivo,
      });
      expect(comparabilityMismatch(item)).toBe(motivo);
    }
  });
});

type Metrics = NonNullable<LatestComparison["metrics"]>;

function metric(
  code: Metrics[number]["code"],
  previous: number | null,
  current: number | null,
  delta: number | null,
  deltaPct: number | null,
  betterWhen: Metrics[number]["better_when"] = "up",
  outcome?: Metrics[number]["outcome"],
): Metrics[number] {
  return {
    code,
    previous,
    current,
    delta,
    delta_pct: deltaPct,
    better_when: betterWhen,
    outcome,
  };
}

function latest(overrides: Partial<LatestComparison> = {}): LatestComparison {
  return {
    comparable: true,
    paired_reps: null,
    e1rm_decisive: true,
    e1rm_max_reliable_reps: LIMIARES.e1rm_max_reliable_reps,
    reasons: [],
    previous_index: 0,
    current_index: 1,
    previous_activity_id: 1,
    current_activity_id: 2,
    metrics: [],
    ...overrides,
  };
}

describe("buildExposurePair (par escolhido pelo motor)", () => {
  it("devolve null sem exposições", () => {
    expect(
      buildExposurePair({
        exposures: [],
        metric_versions: VERSIONS,
        data_quality: "HIGH",
      }),
    ).toBeNull();
  });

  it("a anterior é exposures[previous_index], não a penúltima posição", () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(7), activity_id: 1 }),
        exposure({
          comparability_key: key(7),
          activity_id: 2,
          mismatch_with_current: "gym_changed",
        }),
        exposure({ comparability_key: key(7), activity_id: 3 }),
      ],
      metric_versions: VERSIONS,
      data_quality: "HIGH",
      latest_comparison: latest({
        previous_index: 0,
        current_index: 2,
        current_activity_id: 3,
      }),
    });
    expect(pair?.previous?.activity_id).toBe(1);
    expect(pair?.current.activity_id).toBe(3);
    expect(pair?.comparable).toBe(true);
    expect(pair?.unavailable).toBe(false);
  });

  it("variação e desfecho vêm de metrics, sem conta no frontend", () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(7, "assisted"), best_load_kg: 60 }),
        exposure({ comparability_key: key(7, "assisted"), best_load_kg: 66 }),
      ],
      metric_versions: VERSIONS,
      data_quality: "MEDIUM",
      // Números de propósito diferentes de (66 − 60) e desfecho que CONTRADIZ o
      // sinal com `better_when` ('down' + delta positivo daria piora): a cor só
      // pode vir do `outcome` pronto.
      latest_comparison: latest({
        metrics: [
          metric("best_load_kg", 50, 55, 5, 10, "down", "improved"),
          metric("avg_rpe", 8, 7.5, -0.5, -6.25, "down", "worsened"),
          metric("total_reps", 20, 24, 4, 20, "up", "neutral"),
        ],
      }),
    });
    const carga = pair!.rows.find((row) => row.code === "best_load_kg")!;
    expect(carga.previous.value).toBe(50);
    expect(carga.current.value).toBe(55);
    expect(carga.deltaAbs).toBe(5);
    expect(carga.deltaPct).toBe(10);
    expect(carga.outcome).toBe("improved");
    expect(outcomeTone(carga.outcome)).toBe("positive");
    const rpe = pair!.rows.find((row) => row.code === "avg_rpe")!;
    expect(outcomeTone(rpe.outcome)).toBe("negative");
    expect(
      outcomeTone(pair!.rows.find((row) => row.code === "total_reps")!.outcome),
    ).toBe("neutral");
    // Métrica ausente em metrics: sem variação e sem desfecho (neutro).
    const tonelagem = pair!.rows.find((row) => row.code === "tonnage")!;
    expect(tonelagem.deltaAbs).toBeNull();
    expect(tonelagem.outcome).toBeNull();
    expect(outcomeTone(tonelagem.outcome)).toBe("neutral");
    expect(pairDeltaText(tonelagem)).toBeNull();
  });

  it("API antiga sem outcome: variação aparece, cor neutra (nada decidido pelo sinal)", () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(7) }),
        exposure({ comparability_key: key(7) }),
      ],
      metric_versions: VERSIONS,
      data_quality: "HIGH",
      latest_comparison: latest({
        metrics: [metric("e1rm", 70, 80, 10, null, "up")],
      }),
    });
    const e1rm = pair!.rows.find((row) => row.code === "e1rm")!;
    expect(e1rm.outcome).toBeNull();
    expect(outcomeTone(e1rm.outcome)).toBe("neutral");
    expect(pairDeltaText(e1rm)).toBe("+10");
    expect(pairDeltaText({ deltaAbs: -0.5, deltaPct: null })).toBe("-0,5");
    expect(pairDeltaText({ deltaAbs: 0, deltaPct: null })).toBe("0");
  });

  it("sem anterior (previous_index nulo) não há par: mostra o motivo do backend", () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({
          comparability_key: key(9),
          activity_id: 1,
          mismatch_with_current: "equipment",
        }),
        exposure({ comparability_key: key(7), activity_id: 2 }),
      ],
      metric_versions: VERSIONS,
      data_quality: "MEDIUM",
      latest_comparison: latest({
        comparable: false,
        previous_index: null,
        previous_activity_id: null,
        reasons: ["comparability_key_changed"],
        metrics: [metric("best_load_kg", null, 40, null, null)],
      }),
    });
    expect(pair?.previous).toBeNull();
    expect(pair?.comparable).toBe(false);
    expect(
      pair!.rows.every((row) => row.deltaAbs === null && row.deltaPct === null),
    ).toBe(true);
    expect(
      pair!.rows.find((row) => row.code === "best_load_kg")!.current.value,
    ).toBe(40);
    expect(pairNotComparableText(pair!)).toBe(
      STATUS_REASON_TEXTS.comparability_key_changed,
    );
  });

  it("calibração (comparable=false) não vira comparação mesmo com anterior apontada", () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(7) }),
        exposure({ comparability_key: key(7) }),
      ],
      metric_versions: VERSIONS,
      data_quality: "LOW",
      latest_comparison: latest({
        comparable: false,
        reasons: ["equipment_context_in_calibration"],
        metrics: [metric("best_load_kg", 60, 60, 0, 0)],
      }),
    });
    expect(pair?.comparable).toBe(false);
    expect(
      pair!.rows.every(
        (row) => row.deltaAbs === null && row.previous.value === null,
      ),
    ).toBe(true);
    expect(pairNotComparableText(pair!)).toBe(
      STATUS_REASON_TEXTS.equipment_context_in_calibration,
    );
  });

  it("primeira exposição no contexto de fadiga: motivo com frase", () => {
    const pair = buildExposurePair({
      exposures: [exposure({ comparability_key: key(7) })],
      metric_versions: VERSIONS,
      data_quality: "INSUFFICIENT",
      latest_comparison: latest({
        comparable: false,
        previous_index: null,
        current_index: 0,
        reasons: ["first_exposure_in_fatigue_context"],
      }),
    });
    expect(pairNotComparableText(pair!)).toBe(
      STATUS_REASON_TEXTS.first_exposure_in_fatigue_context,
    );
  });

  it("API antiga (sem latest_comparison): par indisponível, sem variação, atual visível", () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(7), best_load_kg: 60 }),
        exposure({ comparability_key: key(7), best_load_kg: 66 }),
      ],
      metric_versions: VERSIONS,
      data_quality: "HIGH",
    });
    expect(pair?.unavailable).toBe(true);
    expect(pair?.previous).toBeNull();
    expect(pair?.comparable).toBe(false);
    expect(
      pair!.rows.every((row) => row.deltaAbs === null && row.deltaPct === null),
    ).toBe(true);
    expect(
      pair!.rows.find((row) => row.code === "best_load_kg")!.current.value,
    ).toBe(66);
    expect(
      pair!.rows.find((row) => row.code === "paired_reps")!.current.value,
    ).toBeNull();
    expect(pairNotComparableText(pair!)).toBe(PAIR_UNAVAILABLE_TEXT);
  });

  it("uma única exposição (objeto sem anterior): sem par, com os motivos do veredito em ordem", () => {
    const pair = buildExposurePair({
      exposures: [exposure({ comparability_key: key(7) })],
      metric_versions: VERSIONS,
      data_quality: "INSUFFICIENT",
      latest_comparison: latest({
        comparable: false,
        previous_index: null,
        current_index: 0,
        reasons: [
          "not_enough_comparable_exposures",
          "first_exposure_in_fatigue_context",
        ],
      }),
    });
    expect(pair?.unavailable).toBe(false);
    expect(pair?.previous).toBeNull();
    expect(pairNotComparableText(pair!)).toBe(
      `${STATUS_REASON_TEXTS.not_enough_comparable_exposures} ${STATUS_REASON_TEXTS.first_exposure_in_fatigue_context}`,
    );
  });

  it('latest_comparison null: sem par e sem motivo — "não há exposição anterior", não "indisponível"', () => {
    const pair = buildExposurePair({
      exposures: [exposure({ comparability_key: key(7) })],
      metric_versions: VERSIONS,
      data_quality: "INSUFFICIENT",
      latest_comparison: null,
    });
    expect(pair?.unavailable).toBe(false);
    expect(pairNotComparableText(pair!)).toBe(PAIR_NO_PREVIOUS_TEXT);
  });

  it("todo motivo que o par pode trazer tem frase própria", () => {
    for (const code of [
      "not_enough_comparable_exposures",
      "first_exposure_in_fatigue_context",
      "equipment_context_in_calibration",
      "other_gym_exposures_skipped",
      "measure_not_rep_based",
      "comparability_key_changed",
    ]) {
      expect(statusReasonsText([code])[0].known).toBe(true);
    }
  });

  it('sem anterior e sem motivo: frase de "não há exposição anterior"', () => {
    const pair = buildExposurePair({
      exposures: [exposure({ comparability_key: key(7) })],
      metric_versions: VERSIONS,
      data_quality: "INSUFFICIENT",
      latest_comparison: latest({
        comparable: false,
        previous_index: null,
        current_index: 0,
      }),
    });
    expect(pairNotComparableText(pair!)).toBe(PAIR_NO_PREVIOUS_TEXT);
  });

  it("e1RM ausente vira null no envelope (nunca 0) e carrega natureza ESTIMATED", () => {
    const pair = buildExposurePair({
      exposures: [
        exposure({ comparability_key: key(7), e1rm: null }),
        exposure({ comparability_key: key(7), e1rm: null }),
      ],
      metric_versions: VERSIONS,
      data_quality: "MEDIUM",
      latest_comparison: latest({
        metrics: [metric("e1rm", null, null, null, null)],
      }),
    });
    const linha = pair!.rows.find((row) => row.code === "e1rm")!;
    expect(linha.current.value).toBeNull();
    expect(linha.current.nature).toBe("ESTIMATED");
    expect(linha.current.metric_version).toBe("epley-rir-v1");
    expect(linha.deltaAbs).toBeNull();
  });

  it("versão de métrica ausente no payload não quebra o envelope", () => {
    const pair = buildExposurePair({
      exposures: [exposure({ comparability_key: key(7) })],
      metric_versions: {},
      data_quality: "HIGH",
    });
    expect(pair!.rows[0].current.metric_version).toBe("desconhecida");
  });
});

describe("buildExposurePair — reps emparelhadas e e1RM decisivo (regra única)", () => {
  const exposures = [
    exposure({ comparability_key: key(7), activity_id: 1 }),
    exposure({ comparability_key: key(7), activity_id: 2 }),
  ];
  const paired = {
    pairs: 2,
    previous: [11, 10],
    current: [14, 13],
    previous_sum: 21,
    current_sum: 27,
    delta: 6,
  };
  const metrics = [
    metric("paired_reps", 21, 27, 6, 28.57),
    metric("total_reps", 31, 27, -4, -12.9, "neutral"),
    metric("e1rm", 54.7, 59.3, 4.6, 8.41, "neutral"),
  ];

  it("reps POR SÉRIE emparelhada com detalhe das séries; total neutro", () => {
    const pair = buildExposurePair({
      exposures,
      metric_versions: VERSIONS,
      data_quality: "HIGH",
      latest_comparison: latest({ paired_reps: paired, metrics }),
    });
    const reps = pair!.rows.find((row) => row.code === "paired_reps")!;
    expect(reps.previous.value).toBe(21);
    expect(reps.current.value).toBe(27);
    expect(reps.deltaAbs).toBe(6);
    expect(reps.previousDetail).toBe("11+10");
    expect(reps.currentDetail).toBe("14+13");
    const total = pair!.rows.find((row) => row.code === "total_reps")!;
    expect(total.deltaAbs).toBe(-4);
    expect(outcomeTone(total.outcome)).toBe("neutral");
    expect(total.label).toContain("informativo");
  });

  it("e1RM não decisivo leva a nota com o teto do JSON gerado", () => {
    const pair = buildExposurePair({
      exposures,
      metric_versions: VERSIONS,
      data_quality: "HIGH",
      latest_comparison: latest({
        paired_reps: paired,
        metrics,
        e1rm_decisive: false,
      }),
    });
    const e1rm = pair!.rows.find((row) => row.code === "e1rm")!;
    expect(e1rm.note).toBe(E1RM_NOT_DECISIVE_NOTE);
    expect(E1RM_NOT_DECISIVE_NOTE).toContain(
      `passou de ${LIMIARES.e1rm_max_reliable_reps} repetições`,
    );
  });

  it("e1RM decisivo: sem nota", () => {
    const pair = buildExposurePair({
      exposures,
      metric_versions: VERSIONS,
      data_quality: "HIGH",
      latest_comparison: latest({ paired_reps: paired, metrics }),
    });
    expect(pair!.rows.find((row) => row.code === "e1rm")!.note).toBeNull();
  });

  it("reps + RIR (M78): rótulo próprio e detalhe com as reps registradas, sem conta no frontend", () => {
    const pair = buildExposurePair({
      exposures,
      metric_versions: VERSIONS,
      data_quality: "HIGH",
      latest_comparison: latest({
        metrics: [metric("paired_reps", 23, 23.5, 0.5, 2.17)],
        paired_reps: {
          pairs: 2,
          previous: [12, 11],
          current: [12, 11.5],
          previous_sum: 23,
          current_sum: 23.5,
          delta: 0.5,
          effort_adjusted: true,
          previous_reps: [12, 11],
          current_reps: [11, 11],
        },
      }),
    });
    const reps = pair!.rows.find((row) => row.code === "paired_reps")!;
    expect(reps.label).toBe(PAIRED_REPS_EFFORT_LABEL);
    expect(reps.previous.value).toBe(23);
    expect(reps.current.value).toBe(23.5);
    expect(reps.previousDetail).toBe("12+11 (reps 12+11)");
    expect(reps.currentDetail).toBe("12+11,5 (reps 11+11)");
  });

  it("sem ajuste pelo esforço mantém o rótulo de reps", () => {
    const pair = buildExposurePair({
      exposures,
      metric_versions: VERSIONS,
      data_quality: "HIGH",
      latest_comparison: latest({ paired_reps: paired, metrics }),
    });
    expect(
      pair!.rows.find((row) => row.code === "paired_reps")!.label,
    ).not.toBe(PAIRED_REPS_EFFORT_LABEL);
    expect(statusReasonsText(["paired_reps_effort_adjusted"])[0].known).toBe(
      true,
    );
    expect(statusReasonsText(["same_reps_higher_rpe"])[0].known).toBe(true);
  });

  it("par não comparável ignora as reps emparelhadas", () => {
    const pair = buildExposurePair({
      exposures,
      metric_versions: VERSIONS,
      data_quality: "HIGH",
      latest_comparison: latest({
        comparable: false,
        previous_index: null,
        paired_reps: paired,
      }),
    });
    expect(
      pair!.rows.find((row) => row.code === "paired_reps")!.previousDetail,
    ).toBeUndefined();
  });
});

describe("frases com limiares do motor (JSON gerado, F5)", () => {
  it("interpolam os números de progression_thresholds", () => {
    expect(STATUS_REASON_TEXTS.same_load_more_reps).toContain(
      `dentro de ${LIMIARES.same_load_tolerance_percent}%`,
    );
    expect(STATUS_REASON_TEXTS.e1rm_up).toContain(
      `pelo menos ${LIMIARES.e1rm_progress_percent}%`,
    );
    expect(STATUS_REASON_TEXTS.e1rm_up).toContain(
      `até ${LIMIARES.e1rm_max_reliable_reps} repetições`,
    );
    expect(STATUS_REASON_TEXTS.e1rm_down).toContain(
      `${LIMIARES.e1rm_progress_percent}% ou mais`,
    );
    expect(STATUS_REASON_TEXTS.e1rm_down).toContain(
      `até ${LIMIARES.e1rm_max_reliable_reps} repetições`,
    );
  });
});

describe("buildTrendSeries", () => {
  it("plota só in_context_run do motor (mismatch_with_current não decide) e conta quantas saíram", () => {
    const series = buildTrendSeries(
      [
        // Sem motivo de mismatch, mas fora da sequência do contexto: fica de fora.
        exposure({
          comparability_key: key(7),
          in_context_run: false,
          e1rm: 50,
        }),
        // Com motivo de mismatch, mas o motor a pôs na sequência: entra.
        exposure({
          comparability_key: key(7),
          mismatch_with_current: "gym_changed",
          e1rm: 70,
          local_date: "2026-09-05",
        }),
        exposure({
          comparability_key: key(7),
          e1rm: 75,
          local_date: "2026-09-12",
        }),
      ],
      "e1rm",
    );
    expect(series.metric).toBe("e1rm");
    expect(series.excluded).toBe(1);
    expect(series.unavailable).toBe(false);
    expect(series.points.map((point) => point.value)).toEqual([70, 75]);
    expect(series.points[0].label).toBe("05/09/2026");
  });

  it("calibração: o contexto continua plotado mesmo com o veredito vazio", () => {
    const series = buildTrendSeries(
      [
        exposure({
          comparability_key: key(7),
          e1rm: 70,
          in_verdict_run: false,
        }),
        exposure({
          comparability_key: key(7),
          e1rm: 72,
          in_verdict_run: false,
        }),
      ],
      "e1rm",
    );
    expect(series.points.map((point) => point.value)).toEqual([70, 72]);
    expect(
      buildRpeSeries([
        exposure({
          comparability_key: key(7),
          avg_rpe: 8,
          in_verdict_run: false,
        }),
        exposure({
          comparability_key: key(7),
          avg_rpe: 9,
          in_verdict_run: false,
        }),
      ]).hasSeries,
    ).toBe(true);
  });

  it("API antiga (sem in_context_run): série indisponível, nenhum ponto escolhido no cliente", () => {
    const old = [
      exposure({
        comparability_key: key(7),
        e1rm: 70,
        in_context_run: undefined,
      }),
      exposure({
        comparability_key: key(7),
        e1rm: 75,
        in_context_run: undefined,
      }),
    ];
    const series = buildTrendSeries(old, "e1rm");
    expect(series.unavailable).toBe(true);
    expect(series.metric).toBeNull();
    expect(series.points).toEqual([]);
    const rpe = buildRpeSeries(old);
    expect(rpe.unavailable).toBe(true);
    expect(rpe.points).toEqual([]);
    expect(SERIES_UNAVAILABLE_TEXT).toContain("não vieram do servidor");
  });

  // Todas as exposições têm as três métricas; `e1rm` tem mais pontos que as
  // outras para um recálculo no cliente (que escolheria e1rm) falhar.
  const rica = [
    exposure({ comparability_key: key(7), e1rm: 80, best_load_kg: 40, total_reps: 10 }),
    exposure({ comparability_key: key(7), e1rm: 82, best_load_kg: null, total_reps: null }),
    exposure({ comparability_key: key(7), e1rm: 84, best_load_kg: 45, total_reps: 12 }),
  ];

  it.each([
    ["e1rm", "kg", [80, 82, 84]],
    ["best_load_kg", "kg", [40, null, 45]],
    ["total_reps", null, [10, null, 12]],
  ] as const)("trend_metric %s escolhe esse campo, não o de mais pontos", (metric, unit, values) => {
    const series = buildTrendSeries(rica, metric);
    expect(series.metric).toBe(metric);
    expect(series.unit).toBe(unit);
    expect(series.unavailable).toBe(false);
    expect(series.points.map((point) => point.value)).toEqual(values);
  });

  it("trend_metric null: sem gráfico (sem pontos suficientes), mesmo com dados", () => {
    const series = buildTrendSeries(rica, null);
    expect(series.metric).toBeNull();
    expect(series.points).toEqual([]);
    expect(series.unavailable).toBe(false);
  });

  it("trend_metric ausente (API antiga): série indisponível, sem escolha no cliente", () => {
    const series = buildTrendSeries(rica, undefined);
    expect(series.metric).toBeNull();
    expect(series.points).toEqual([]);
    expect(series.unavailable).toBe(true);
  });

  it("histórico vazio devolve série vazia sem exclusões", () => {
    expect(buildTrendSeries([], null)).toEqual({
      metric: null,
      label: "",
      unit: null,
      points: [],
      excluded: 0,
      unavailable: false,
    });
  });
});

describe("decisões do motor (evidência secundária)", () => {
  const decisions: ProgressionDecision[] = [
    {
      id: 1,
      kind: "SESSION",
      decision: "INCREASE_LOAD",
      phase_before: "F1",
      phase_after: "F2",
      engine_version: "progression-v2.0.0",
      created_at: "2026-09-12T10:00:00",
      equipment_id: 7,
    },
    {
      id: 2,
      kind: "SESSION",
      decision: "HOLD",
      phase_before: "F2",
      phase_after: "F2",
      engine_version: "progression-v2.0.0",
      created_at: "2026-09-05T10:00:00",
      equipment_id: 9,
    },
    {
      id: 3,
      kind: "SESSION",
      decision: "SKIP",
      phase_before: null,
      phase_after: null,
      engine_version: "progression-v2.0.0",
      created_at: "2026-08-30T10:00:00",
      equipment_id: null,
    },
  ];

  it("mostra só as marcadas in_current_context pelo backend (equipment_id não decide)", () => {
    // A decisão 1 tem o mesmo equipment_id de outras, mas o backend a tirou do contexto.
    const marcadas = [
      { ...decisions[0], in_current_context: false },
      { ...decisions[1], in_current_context: true },
      { ...decisions[2], in_current_context: true },
    ];
    expect(decisionsInCurrentContext(marcadas).map((item) => item.id)).toEqual([
      2, 3,
    ]);
  });

  it("API antiga (sem in_current_context): mostra todas, sem filtro próprio", () => {
    expect(decisionsInCurrentContext(decisions).map((item) => item.id)).toEqual(
      [1, 2, 3],
    );
  });

  it("rotula a decisão em pt-BR e devolve o código quando é desconhecida", () => {
    expect(progressionDecisionLabel("INCREASE_LOAD")).toBe("Aumentar carga");
    expect(progressionDecisionLabel("ALGO_NOVO")).toBe("ALGO_NOVO");
    expect(progressionDecisionLabel(null)).toBe("—");
  });
});

// ---------------------------------------------------------------------------
// RPE médio por SESSÃO (F2.5 / M35)
// ---------------------------------------------------------------------------

describe("buildRpeSeries", () => {
  const atual = key(3);

  it("uma exposição com RPE não desenha linha — não há com o que comparar", () => {
    const serie = buildRpeSeries([
      exposure({ comparability_key: atual, avg_rpe: 8 }),
    ]);
    expect(serie.hasSeries).toBe(false);
  });

  it("não calcula média no cliente e declara a sessão sem RPE", () => {
    const serie = buildRpeSeries([
      exposure({ activity_id: 1, comparability_key: atual, avg_rpe: 8 }),
      exposure({ activity_id: 2, comparability_key: atual, avg_rpe: null }),
      exposure({ activity_id: 3, comparability_key: atual, avg_rpe: 9 }),
    ]);
    expect(serie.average).toBeNull(); // sem avg_rpe_period da API, nada é calculado
    expect(buildRpeSeries([exposure({ activity_id: 1, comparability_key: atual, avg_rpe: 8 })], 8.25).average).toBe(8.25);
    expect(serie.missing).toBe(1);
    expect(serie.hasSeries).toBe(true);
    // O ponto sem RPE continua no gráfico, com valor nulo — o buraco é visível.
    expect(serie.points.map((point) => point.value)).toEqual([8, null, 9]);
  });

  it("exposição fora da sequência do contexto (in_context_run false) fica fora e é declarada", () => {
    const serie = buildRpeSeries([
      exposure({ activity_id: 1, comparability_key: atual, avg_rpe: 8 }),
      exposure({ activity_id: 2, comparability_key: atual, avg_rpe: 9 }),
      exposure({
        activity_id: 3,
        comparability_key: key(99),
        in_context_run: false,
        avg_rpe: 5,
      }),
    ]);
    expect(serie.excluded).toBe(1);
    expect(serie.points).toHaveLength(2);
  });

  it("nenhuma sessão com RPE: sem série", () => {
    const serie = buildRpeSeries([
      exposure({ activity_id: 1, comparability_key: atual, avg_rpe: null }),
      exposure({ activity_id: 2, comparability_key: atual, avg_rpe: null }),
    ]);
    expect(serie.hasSeries).toBe(false);
  });
});

describe('sessionEvolutionView (seta "Evolução" de /dashboard/progressao)', () => {
  it("só traduz o status que veio pronto do backend", () => {
    expect(
      sessionEvolutionView({ status: "PROGRESSING", reasons: [] }).kind,
    ).toBe("up");
    expect(
      sessionEvolutionView({ status: "REGRESSING", reasons: [] }).kind,
    ).toBe("down");
    expect(sessionEvolutionView({ status: "STABLE", reasons: [] }).kind).toBe(
      "flat",
    );
    expect(
      sessionEvolutionView({ status: "NOT_COMPARABLE", reasons: [] }).kind,
    ).toBe("not_comparable");
  });

  it("sem anterior comparável não desenha seta", () => {
    expect(sessionEvolutionView(null)).toEqual({
      kind: "none",
      label: "—",
      tooltip: "",
    });
    expect(sessionEvolutionView(undefined).kind).toBe("none");
    expect(
      sessionEvolutionView({ status: "CODIGO_NOVO", reasons: [] }).kind,
    ).toBe("none");
  });

  it("cenário sintético do agachamento: progresso com aviso de menos séries no tooltip", () => {
    const view = sessionEvolutionView({
      status: "PROGRESSING",
      reasons: ["same_load_more_reps", "fewer_working_sets"],
    });
    expect(view.label).toBe("Progrediu");
    expect(view.tooltip).toContain(STATUS_REASON_TEXTS.same_load_more_reps);
    expect(view.tooltip).toContain(STATUS_REASON_TEXTS.fewer_working_sets);
  });

  it("troca de equipamento explica por que não compara", () => {
    const view = sessionEvolutionView({
      status: "NOT_COMPARABLE",
      reasons: ["comparability_key_changed"],
    });
    expect(view.tooltip).toBe(STATUS_REASON_TEXTS.comparability_key_changed);
  });
});

describe("e1rmReliabilityNote (M76 Lote 3d)", () => {
  const base = { value: 58.7, activity_id: 1, local_date: "2026-09-20" };

  it("e1RM não confiável vem marcado com o teto do payload", () => {
    expect(
      e1rmReliabilityNote({ ...base, reliable: false, max_reliable_reps: 12 }),
    ).toBe("não confiável (>12 reps)");
  });

  it("confiável, resposta antiga ou sem e1RM: sem marca", () => {
    expect(
      e1rmReliabilityNote({ ...base, reliable: true, max_reliable_reps: 12 }),
    ).toBeNull();
    expect(e1rmReliabilityNote(base)).toBeNull();
    expect(e1rmReliabilityNote(null)).toBeNull();
    expect(
      e1rmReliabilityNote({ ...base, value: null, reliable: false }),
    ).toBeNull();
  });

  it("sem o teto no payload não inventa o número", () => {
    expect(e1rmReliabilityNote({ ...base, reliable: false })).toBe(
      "não confiável",
    );
  });
});

describe("contexto de fadiga prévia (PR 2 da fadiga prévia)", () => {
  const pre = {
    band: "pre_fatigued" as const,
    band_label: "pré-fatigado",
    muscle_group_id: 1,
    muscle_group_name: "Peitoral",
    prior_sets: 3,
  };

  it("o rótulo vem pronto do backend e acompanha o status", () => {
    expect(fatigueContextLabel(pre)).toBe("pré-fatigado");
    expect(fatigueContextLabel(null)).toBeNull();
    expect(statusWithContextLabel("Progresso", pre)).toBe(
      "Progresso · pré-fatigado",
    );
    expect(statusWithContextLabel("Progresso", null)).toBe("Progresso");
  });

  it("o motivo novo tem rótulo, dica e frase", () => {
    expect(COMPARABILITY_MISMATCH_LABELS.fatigue_context_changed).toBeTruthy();
    expect(COMPARABILITY_MISMATCH_HINTS.fatigue_context_changed).toBeTruthy();
    expect(STATUS_REASON_TEXTS.first_exposure_in_fatigue_context).toBeTruthy();
    expect(
      STATUS_REASON_TEXTS.other_fatigue_context_exposures_skipped,
    ).toBeTruthy();
  });
});

describe("contexto de academia e calibração (#188)", () => {
  it("os códigos novos têm frase, rótulo e dica", () => {
    expect(STATUS_REASON_TEXTS.equipment_context_in_calibration).toBeTruthy();
    expect(STATUS_REASON_TEXTS.other_gym_exposures_skipped).toBeTruthy();
    expect(COMPARABILITY_MISMATCH_LABELS.gym_changed).toBe("Outra academia");
    expect(COMPARABILITY_MISMATCH_HINTS.gym_changed).toBeTruthy();
    expect(
      statusReasonsText(["equipment_context_in_calibration"])[0].known,
    ).toBe(true);
  });

  it("a seta em calibração é não comparável e traz a frase no tooltip", () => {
    const view = sessionEvolutionView({
      status: "NOT_COMPARABLE",
      reasons: ["equipment_context_in_calibration"],
    });
    expect(view.kind).toBe("not_comparable");
    expect(view.tooltip).toBe(
      STATUS_REASON_TEXTS.equipment_context_in_calibration,
    );
  });
});

describe("exerciseSearchTruncationNotice", () => {
  it("só avisa com truncated e usa os números da resposta", () => {
    const serie = { exercise_id: 1, exercise_name: "A", items: [] };
    expect(exerciseSearchTruncationNotice(undefined)).toBeNull();
    expect(
      exerciseSearchTruncationNotice({ series: [serie], truncated: false, matched_exercises: 1 }),
    ).toBeNull();
    expect(
      exerciseSearchTruncationNotice({
        series: [serie, serie, serie],
        truncated: true,
        matched_exercises: 20,
      }),
    ).toContain("Mostrando 3 de 20");
  });
});

describe("buildExerciseProgressCurves (/exercicios, #188)", () => {
  const point = (
    exercise_id: number,
    local_date: string,
    extra: Partial<ExerciseProgressItem> = {},
  ): ExerciseProgressItem => ({
    exercise_id,
    local_date,
    activity_name: "Treino",
    original_name: "X",
    sets_count: 2,
    reps_total: 20,
    best_weight_kg: 60,
    volume_kg: 1200,
    avg_rpe: 8,
    high_rpe_sets: 0,
    ...extra,
  });

  it("uma curva por série, sem misturar exercícios nem juntar dias iguais", () => {
    const curves = buildExerciseProgressCurves({
      series: [
        {
          exercise_id: 1,
          exercise_name: "Agachamento",
          mapped_name: "Squat",
          items: [
            point(1, "2026-09-18"),
            point(1, "2026-09-18", { best_weight_kg: 70 }),
          ],
        },
        {
          exercise_id: 2,
          exercise_name: "Agachamento hack",
          items: [point(2, "2026-09-18", { best_weight_kg: 100 })],
        },
      ],
    });
    expect(
      curves?.map((c) => [c.key, c.name, c.weight.map((w) => w.melhorCarga)]),
    ).toEqual([
      ["1", "Squat", [60, 70]],
      ["2", "Agachamento hack", [100]],
    ]);
  });

  it("volume nulo (convenção sem tonelagem) e RPE ausente ficam null, nunca 0", () => {
    const curves = buildExerciseProgressCurves({
      series: [
        {
          exercise_id: 1,
          exercise_name: "Barra fixa",
          items: [point(1, "2026-09-18", { volume_kg: null, avg_rpe: null })],
        },
      ],
    });
    expect(curves?.[0].weight[0].volume).toBeNull();
    expect(curves?.[0].rpe[0].rpeMedio).toBeNull();
  });

  it("API antiga sem series: null (a tela avisa, não reagrupa items); sem resposta: vazio", () => {
    expect(buildExerciseProgressCurves({})).toBeNull();
    expect(buildExerciseProgressCurves(undefined)).toEqual([]);
  });
});
