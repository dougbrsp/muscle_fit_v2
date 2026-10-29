/**
 * Contrato das rotas `/next-set-load-rules/*` (regras de carga da próxima
 * série, R2: `api/app/next_set_load_rules_admin.py` + loader
 * `api/app/next_set_load_rules.py`). Só forma: quem avalia, valida a
 * semântica e escreve os textos é o dono `api/app/domain/next_set_load.py`.
 */
import type { NextSetLoad } from './set-engine-status';

/** Número OU token (`floor`/`ceiling` nas reps, `max` no RPE). */
export type RuleValue = number | string;

export type RuleCondition = {
  op: string;
  value: RuleValue;
  value_to?: RuleValue | null;
};

export type RuleMagnitude = { kind: string; value: number };

/** Uma regra como a API devolve (`text` e `reason_code` só de saída). */
export type RulePayload = {
  position?: number;
  text?: string | null;
  set_position: string;
  fatigue: string;
  reps: RuleCondition | null;
  rpe: RuleCondition | null;
  respect_planned_ramp: boolean;
  action: string;
  magnitude: RuleMagnitude | null;
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

/** Fatos de uma série para `POST /simulate` (`SetFactsIn` do loader). */
export type SimulateFacts = {
  working_set_index: number | null;
  fatigue_band: string | null;
  reps: number | null;
  rpe: number | null;
  rpe_ceiling: number | null;
  planned_rpe: number | null;
  weight_kg: number | null;
  step_kg: number | null;
  joint_pain: boolean | null;
  muscle_pain: number | null;
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
