import { PLAN_ITEM_STATUS_ORDER, type PlanItemStatusCode } from './dashboard-status';
import type { ItemStatusCounts, WorkoutSession, WorkoutSessionGroup } from './dashboard-workouts-types';

/**
 * Lógica PURA de `/dashboard/treinos` (F1.1) — extraída da tela para ser
 * testada sem React e sem rede.
 *
 * Nenhuma regra de negócio mora aqui: status dos itens, séries efetivas,
 * recordes, limitações e o GRUPO de cada sessão chegam prontos do
 * `response_model`. O que este módulo faz é JUNTAR por `group.key` e ORDENAR.
 *
 * ## Identidade do grupo
 *
 * O grupo vem do dono `api/app/intelligence/slot_session_comparison.py`
 * (`session_group_of`, a MESMA identidade de slot do comparativo T-0..T-3 de
 * `/volume-grupamentos`): Modelo (`template_id`, estável entre versões) +
 * posição do slot (`template_slot_sequence`). Renomear o Modelo ou o título
 * do slot não parte o grupo; o nome é só rótulo (o da sessão mais recente).
 * Um Modelo duplicado conta como o de origem: o backend resolve a linhagem e
 * manda a raiz em `group.lineage_template_id` (a chave já vem com ela).
 * Fichas sem Modelo caem num grupo único, e treinos sem ficha em outro — a
 * tela nunca reagrupa por nome.
 */

export type SessionGroupKind = WorkoutSessionGroup['kind'];

export type SessionGroup = {
  key: string;
  kind: SessionGroupKind;
  /** Rótulo exibido: nome do slot na sessão mais recente do grupo. */
  label: string;
  /** Nome atual do Modelo (só rótulo), quando o grupo é um slot. */
  templateName: string | null;
  /** Modelo da sessão mais recente do grupo (só exibição). */
  templateId: number | null;
  /** Raiz da linhagem do Modelo: ordena os grupos do mesmo Modelo juntos. */
  lineageTemplateId: number | null;
  /** `sequence` do slot no Modelo, quando o grupo é um slot. */
  slotSequence: number | null;
  sessions: WorkoutSession[];
};

function sessionOrder(a: WorkoutSession, b: WorkoutSession): number {
  const dateA = a.local_date ?? '';
  const dateB = b.local_date ?? '';
  if (dateA !== dateB) return dateA < dateB ? -1 : 1;
  return a.activity_id - b.activity_id;
}

const KIND_ORDER: Record<SessionGroupKind, number> = { slot: 0, no_template: 1, unlinked: 2 };

/**
 * Junta as sessões da janela pelo grupo pronto do backend.
 *
 * Dentro de cada grupo as sessões ficam em ordem CRESCENTE de data: comparar
 * evolução exige a mais antiga primeiro. Os grupos de slot vêm por Modelo (o
 * treinado mais recentemente primeiro) e, dentro dele, pela `sequence` do
 * ciclo; depois "Fichas sem Modelo" e, por último, "Treinos sem ficha".
 */
export function groupSessionsBySlot(sessions: readonly WorkoutSession[]): SessionGroup[] {
  const groups = new Map<string, SessionGroup>();
  const latestByTemplate = new Map<number, string>();

  for (const session of sessions) {
    const info = session.group;
    let group = groups.get(info.key);
    if (!group) {
      group = {
        key: info.key,
        kind: info.kind,
        label: info.label,
        templateName: info.template_name,
        templateId: info.template_id,
        lineageTemplateId: info.lineage_template_id,
        slotSequence: info.slot_sequence,
        sessions: [],
      };
      groups.set(info.key, group);
    }
    group.sessions.push(session);
    if (info.lineage_template_id != null) {
      const date = session.local_date ?? '';
      const atual = latestByTemplate.get(info.lineage_template_id);
      if (atual === undefined || date > atual) latestByTemplate.set(info.lineage_template_id, date);
    }
  }

  for (const group of groups.values()) {
    group.sessions.sort(sessionOrder);
    // Rótulo = o da sessão mais recente (o nome pode ter mudado no caminho).
    const latest = group.sessions[group.sessions.length - 1];
    if (latest) {
      group.label = latest.group.label;
      group.templateName = latest.group.template_name;
      group.templateId = latest.group.template_id;
    }
  }

  return Array.from(groups.values()).sort((a, b) => {
    if (a.kind !== b.kind) return KIND_ORDER[a.kind] - KIND_ORDER[b.kind];
    if (a.lineageTemplateId !== b.lineageTemplateId) {
      const dateA = latestByTemplate.get(a.lineageTemplateId ?? -1) ?? '';
      const dateB = latestByTemplate.get(b.lineageTemplateId ?? -1) ?? '';
      if (dateA !== dateB) return dateA > dateB ? -1 : 1;
      return (a.lineageTemplateId ?? 0) - (b.lineageTemplateId ?? 0);
    }
    const seqA = a.slotSequence ?? Number.MAX_SAFE_INTEGER;
    const seqB = b.slotSequence ?? Number.MAX_SAFE_INTEGER;
    return seqA - seqB;
  });
}

/**
 * Contagens por status em ordem canônica, SEM os zeros — um badge "0 pulados"
 * só ocupa espaço. Lista vazia significa sessão sem nenhum item classificado.
 */
export function itemStatusEntries(
  counts: ItemStatusCounts,
): Array<{ code: PlanItemStatusCode; count: number }> {
  return PLAN_ITEM_STATUS_ORDER.map((code) => ({
    code,
    count: counts[code.toLowerCase() as keyof ItemStatusCounts] ?? 0,
  })).filter((entry) => entry.count > 0);
}

/** Total de itens classificados numa sessão. */
export function totalItems(counts: ItemStatusCounts): number {
  return (
    counts.completed + counts.interrupted + counts.skipped + counts.substituted + counts.extra
  );
}

/**
 * Taxa (0–1) → pontos percentuais. `null` continua `null`: sem denominador
 * (nenhum Modelo atribuído) não existe "0% de aderência" — existe "não dá para
 * dizer".
 */
export function ratePercent(value: number | null | undefined): number | null {
  if (value == null || !Number.isFinite(Number(value))) return null;
  return Number(value) * 100;
}
