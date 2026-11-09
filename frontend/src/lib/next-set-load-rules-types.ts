/**
 * Contrato das rotas `/next-set-load-rules/*` (regras de carga da próxima
 * série, R2: `api/app/next_set_load_rules_admin.py` + loader
 * `api/app/next_set_load_rules.py`). Só forma: quem avalia, valida a
 * semântica e escreve os textos é o dono `api/app/domain/next_set_load.py`.
 */
import type { NextSetLoad } from './set-engine-status';

/** Número OU token (`floor`/`ceiling` nas reps, `max` no RPE). */
export type RuleValue = number | string;

export type RuleMagnitude = { kind: string; value: number };

/** Folha da árvore `when` (v2): `ConditionLeafIn`. `value` ausente só com `ramp_cap`. */
export type ConditionLeafPayload = {
  variable: string;
  scope: string;
  op: string;
  value?: RuleValue | null;
  value_to?: RuleValue | null;
  ramp_cap?: boolean;
};

/** Grupo recursivo (v2): `ConditionGroupIn`. */
export type ConditionGroupPayload = {
  logic: string;
  children: Array<ConditionLeafPayload | ConditionGroupPayload>;
};

/**
 * Uma regra como a API devolve (`text` e `reason_code` só de saída). O servidor
 * sempre devolve a v2 (`when`).
 */
export type RulePayload = {
  position?: number;
  text?: string | null;
  when?: ConditionGroupPayload | null;
  action: string;
  magnitude?: RuleMagnitude | null;
  reason_code?: string | null;
};

/** Corpo aceito por `PUT /draft`, `POST /describe` e `POST /simulate`. */
export type RuleSetInput = {
  reps_floor: number;
  reps_ceiling: number;
  rules: RulePayload[];
};

/** Conjunto como a API devolve (com versão e origem). */
export type RuleSetView = RuleSetInput & { version?: number; source?: string };

export type PublishedRuleSetResponse = {
  id: number | null;
  version: number;
  published_at: string | null;
  source: string;
  ruleset: RuleSetView;
  summary: string;
};

/** Linha gravada (rascunho ou publicada): `StoredRuleSet.to_dict()`. */
export type StoredRuleSet = {
  id: number;
  status: string;
  version: number | null;
  current: boolean;
  notes: string | null;
  engine_version: string | null;
  created_by: number | null;
  published_by: number | null;
  published_at: string | null;
  superseded_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  ruleset: RuleSetView;
  summary: string;
};

export type DraftResponse = { draft: StoredRuleSet | null; warnings: string[] };
export type DraftPayload = RuleSetInput & { notes?: string | null };
export type PublishPayload = { draft_id: number; expected_published_version: number };
export type PublishResponse = { published: StoredRuleSet };
export type VersionsResponse = { versions: StoredRuleSet[] };

export type DescribeResponse = {
  texts: string[];
  summary: string;
  errors: string[];
  warnings: string[];
};

/** Série anterior (`PreviousSetFactsIn`) do simulador. */
export type SimulatePreviousFacts = { reps: number | null; rpe: number | null };

/** Fatos de uma série para `POST /simulate` (`SetFactsIn` do loader). */
export type SimulateFacts = {
  working_set_index: number | null;
  fatigue_band: string | null;
  reps: number | null;
  rpe: number | null;
  rpe_ceiling: number | null;
  planned_rpe: number | null;
  rpe_target: number | null;
  weight_kg: number | null;
  step_kg: number | null;
  engine_target_kg: number | null;
  joint_pain: boolean | null;
  muscle_pain: number | null;
  previous: SimulatePreviousFacts | null;
};

export type SimulatePayload = { ruleset: RuleSetInput | null; facts: SimulateFacts };

export type SimulateResponse = {
  ruleset_version: number;
  source: string;
  result: NextSetLoad | null;
};

/** `detail` estruturado dos 4xx destas rotas. */
export type RuleSetErrorDetail = {
  error: string;
  message: string;
  codes?: string[];
  current_version?: number | null;
};
