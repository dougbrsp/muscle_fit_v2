/**
 * Apresentação de `/dashboard/engine` (F3.1) — funções PURAS, testáveis sem
 * React e sem rede.
 *
 * O que mora aqui é rótulo e formatação: nenhuma regra é recalculada. Status
 * de item, limitações, contagens, orçamento por grupo e os códigos de motivo
 * chegam prontos do `response_model` (`engine_service`), e traduzir um código
 * em frase é a única coisa que o cliente pode fazer sem criar uma segunda
 * verdade.
 *
 * Um código DESCONHECIDO nunca vira texto inventado: ele aparece como veio do
 * motor. Um motor novo pode emitir um `reason` que esta versão do frontend não
 * conhece, e exibir o código cru é honesto — exibir "outro" seria apagar a
 * informação.
 */

import { EM_DASH, fmtDate, fmtDateTime, fmtDecimal } from './dashboard-format';
import type {
  EngineAuditSource,
  EngineItem,
  EnginePlanResponse,
  EnginePlanned,
  EngineReason,
  EngineReasonKind,
  EngineRun,
} from './dashboard-engine-types';

/**
 * Rótulo da FONTE da auditoria, com a ressalva junto. As três frases dizem
 * coisas diferentes de propósito: `plan_raw_payload` não é "quase igual" —
 * dele não sai data de geração, job nem pedido.
 */
export const AUDIT_SOURCE_META: Record<
  EngineAuditSource,
  { label: string; color: string; hint: string }
> = {
  generation_runs: {
    label: 'Registro da geração',
    color: 'green',
    hint: 'Auditoria lida de `generation_runs`: o bloco que o motor produziu no momento da geração, com data, job e o pedido que a originou.',
  },
  plan_raw_payload: {
    label: 'Auditoria ecoada na ficha',
    color: 'yellow',
    hint: 'Ficha anterior à tabela `generation_runs`: o bloco existe porque foi salvo junto com a ficha, mas a data da geração, o job e o pedido não existem para ela.',
  },
  unavailable: {
    label: 'Sem auditoria',
    color: 'gray',
    hint: 'Esta ficha não tem bloco de auditoria: ela foi criada à mão ou salva sem o payload do motor. O que ela prescreve continua visível; por que o motor escolheu isso, não.',
  },
};

export const REASON_KIND_META: Record<EngineReasonKind, { label: string; color: string }> = {
  selection: { label: 'Seleção', color: 'blue' },
  safety_clamp: { label: 'Limite de segurança', color: 'red' },
  substitution: { label: 'Substituição', color: 'grape' },
  fallback: { label: 'Caminho alternativo', color: 'orange' },
  progression: { label: 'Progressão', color: 'teal' },
};

/**
 * Códigos de motivo que os motores emitem hoje
 * (`generation/mode_a.py`, `mode_b.py`, `exercise_substitution.py`).
 * O que não estiver aqui aparece cru — ver o cabeçalho do arquivo.
 */
const REASON_CODE_LABELS: Record<string, string> = {
  // Seleção (`generation/mode_a.py`, `domain/equipment_ranking.py`)
  NO_DIRECT_WORK: 'a recuperação do grupo não permitia trabalho direto',
  SECONDARY_BUDGET_EXCEEDED: 'estourava o orçamento de outro grupo já atingido',
  UNAVAILABLE: 'o par exercício/equipamento não estava disponível',
  TECHNIQUE_INCOMPATIBLE: 'a técnica exigida não é compatível com o equipamento',
  FAILURE_REQUIRED_NOT_ALLOWED: 'a técnica exigia falha, que a segurança não permite aqui',
  // Caminho alternativo (`generation/mode_a.py`)
  NO_EQUIPMENT_FOR_GROUP: 'nenhum equipamento da academia atende ao grupo',
  ALL_CANDIDATES_EXCLUDED: 'todos os candidatos do grupo foram excluídos',
  SETS_REDUCED_FOR_SECONDARY_BUDGET: 'séries reduzidas para caber no orçamento de outro grupo',
  ACTIVATION_DEFICIT: 'o volume alvo do grupo não foi alcançado',
  // Limite de segurança (`domain/training_safety.py`)
  EQUIPMENT_SAFETY: 'teto de segurança do equipamento',
  RECOVERY: 'teto imposto pela recuperação do grupo',
  LEGACY_PLAN: 'teto aplicado a uma prescrição legada',
  AI_SUGGESTION: 'teto aplicado sobre a sugestão da IA',
  // Substituição (`generation/exercise_substitution.py`)
  EXERCISE_UNAVAILABLE_IN_GYM: 'o exercício do Modelo não existe nesta academia',
};

export function reasonCodeLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return REASON_CODE_LABELS[code] ?? code;
}

/** Texto de UMA razão: tipo, código traduzido (ou cru) e o detalhe do motor. */
export function reasonText(reason: EngineReason): string {
  const partes = [REASON_KIND_META[reason.kind]?.label ?? reason.kind];
  const codigo = reasonCodeLabel(reason.code);
  if (codigo) partes.push(codigo);
  if (reason.detail) partes.push(reason.detail);
  return partes.join(' · ');
}

/**
 * A coluna PLANEJADO em uma linha de texto.
 *
 * `muscle_group_budget` NUNCA nomeia exercício: o Modelo pediu volume para o
 * grupo, e escrever um nome de exercício ali diria que ele escolheu o que o
 * motor escolheu.
 */
export function plannedSummary(planned: EnginePlanned): string {
  if (planned.source === 'template_item') {
    // Item livre: o Modelo pediu o grupo e o motor escolheu o exercício — o
    // nome do exercício é o da coluna GERADO, nunca o do PLANEJADO.
    const nome = planned.free_exercise
      ? `${planned.muscle_group_name ?? 'Grupo'} (exercício escolhido pelo motor)`
      : (planned.exercise_name ?? planned.muscle_group_name ?? 'Item do Modelo');
    const partes: string[] = [nome];
    if (planned.sets_count != null) partes.push(`${planned.sets_count}x`);
    if (planned.reps_target) partes.push(planned.reps_target);
    if (planned.rpe_target) partes.push(`RPE ${planned.rpe_target}`);
    if (planned.advanced_technique) partes.push(planned.advanced_technique);
    return partes.join(' · ');
  }
  if (planned.source === 'muscle_group_budget') {
    const grupo = planned.muscle_group_name ?? 'grupo';
    const min = fmtDecimal(planned.budget_min_sets);
    const max = fmtDecimal(planned.budget_max_sets);
    return `${grupo}: ${min}–${max} séries equivalentes no slot`;
  }
  return EM_DASH;
}

/** "3x 8-10 · 60 · RPE 8" — o que o motor colocou na ficha. */
export function generatedSummary(item: EngineItem): string {
  const partes: string[] = [];
  const { generated } = item;
  if (generated.sets_count != null) partes.push(`${generated.sets_count}x`);
  if (generated.reps_target) partes.push(generated.reps_target);
  if (generated.load_target) partes.push(`${generated.load_target} kg`);
  if (generated.rpe_target) partes.push(`RPE ${generated.rpe_target}`);
  return partes.length > 0 ? partes.join(' · ') : EM_DASH;
}

/**
 * "3 séries · melhor 62,5 kg · RPE 8,5" — o que a sessão registrou.
 *
 * Item sem execução devolve `EM_DASH`, nunca "0 séries": zero registrado e
 * nada registrado são fatos diferentes, e quem separa os dois é o `status`.
 */
export function executedSummary(item: EngineItem): string {
  const executado = item.executed;
  if (!executado) return EM_DASH;
  const partes = [`${executado.working_sets} série(s) de trabalho`];
  if (executado.best_load_kg != null) partes.push(`melhor ${fmtDecimal(executado.best_load_kg)} kg`);
  if (executado.avg_rpe != null) partes.push(`RPE ${fmtDecimal(executado.avg_rpe)}`);
  return partes.join(' · ');
}

/** Quando a geração aconteceu — ou por que essa data não existe. */
export function runTimestampLabel(run: EngineRun): string {
  if (run.source === 'generation_runs') return fmtDateTime(run.created_at);
  if (run.plan_date) return `ficha de ${fmtDate(run.plan_date)} (data da geração não registrada)`;
  return 'data da geração não registrada';
}

/** Rótulo de uma geração na lista do seletor. */
export function runOptionLabel(run: EngineRun): string {
  const quando = run.plan_date ? fmtDate(run.plan_date) : fmtDateTime(run.created_at);
  const nome = run.plan_name ?? (run.workout_plan_id == null ? 'Rascunho não salvo' : `Ficha ${run.workout_plan_id}`);
  return `${quando} — ${nome}`;
}

/**
 * Todas as limitações da tela, sem repetir: as do envelope mais as dos itens.
 *
 * A deduplicação é o ponto — a mesma frase ("item sem decisão de progressão
 * vinculada") vale para vários itens e apareceria N vezes no alerta.
 */
export function collectEngineLimitations(payload: EnginePlanResponse): string[] {
  const todas = [
    ...payload.limitations,
    ...payload.run.limitations,
    ...payload.items.flatMap((item) => item.limitations),
  ];
  return [...new Set(todas)];
}
