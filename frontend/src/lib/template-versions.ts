// Histórico de versões do Modelo de Treino (PR4 do versionamento; backend em
// api/app/workout_plan_templates.py, rotas `/{id}/versions`). Funções puras:
// rótulos, linhas da lista e leitura do 422 da reversão — testadas em
// template-versions.test.ts.

import { ApiError } from './api';

export type TemplateVersionRef = { id: number; number: number };

/** Item de `GET /workout-plan-templates/{id}/versions` (lista leve). */
export type TemplateVersionListItem = {
  id: number;
  number: number;
  effective_from: string | null;
  superseded_at: string | null;
  author_user_id: number | null;
  author_name: string | null;
  reverted_from_number: number | null;
  change_summary: string | null;
  is_current: boolean;
  /** Objetivo de treino da versão (M97 F3; ausente em backend anterior = default). */
  training_goal?: string;
};

export type TemplateVersionListResponse = {
  template_id: number;
  items: TemplateVersionListItem[];
};

/** "v3" — o mesmo rótulo no builder, na lista de Modelos e no /dashboard/engine. */
export function versionLabel(number: number | null | undefined): string | null {
  return number != null && Number.isFinite(number) ? `v${number}` : null;
}

const DATE_FMT = new Intl.DateTimeFormat('pt-BR', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

/** Data de início da versão. `null` = a versão inicial, anterior ao versionamento. */
export function versionStartLabel(effectiveFrom: string | null): string {
  if (!effectiveFrom) return 'Desde o início';
  const parsed = new Date(effectiveFrom);
  return Number.isNaN(parsed.getTime()) ? effectiveFrom : DATE_FMT.format(parsed);
}

export const INITIAL_VERSION_SUMMARY = 'Versão inicial (anterior ao versionamento)';

type SummarySource = {
  number: number;
  effective_from: string | null;
  reverted_from_number: number | null;
  change_summary: string | null;
};

/**
 * Texto do "o que mudou" de uma versão, SEMPRE em português e sem jargão.
 * Determinístico pelos dados da versão, porque parte dos textos foi gravada
 * por migrações já aplicadas (e imutáveis):
 * - v1 sem início e sem reversão = a versão que o versionamento encontrou
 *   (gravada pela 065 como "v1 (backfill da migracao 065)");
 * - versão criada pela 066 para slots salvos durante uma atualização;
 * - reversão: "Revertida para a vN" (pelo número, não pelo texto gravado);
 * - sem texto: criação (v1) ou edição do modelo.
 */
export function versionSummaryText(version: SummarySource): string {
  const raw = (version.change_summary ?? '').trim();
  if (version.number === 1 && !version.effective_from && version.reverted_from_number == null) {
    return INITIAL_VERSION_SUMMARY;
  }
  if (/^v1 \(backfill/i.test(raw)) return INITIAL_VERSION_SUMMARY;
  if (version.reverted_from_number != null) return `Revertida para a v${version.reverted_from_number}`;
  if (/orfaos|órfãos/i.test(raw)) {
    return 'Ajuste automático: slots salvos durante uma atualização do sistema';
  }
  if (/^revertido para a v(\d+)/i.test(raw)) return raw.replace(/^revertido/i, 'Revertida');
  if (raw) return raw;
  return version.number === 1 ? 'Criação do modelo' : 'Edição do modelo';
}

export type VersionHistoryRow = {
  id: number;
  label: string;
  isCurrent: boolean;
  when: string;
  author: string;
  revertedFrom: string | null;
  summary: string | null;
  /** Objetivo da versão (M97 F3); null = backend sem o campo. */
  trainingGoal: string | null;
};

/** Linhas prontas para o painel, na ordem da API (mais nova primeiro). */
export function versionHistoryRows(items: readonly TemplateVersionListItem[]): VersionHistoryRow[] {
  return items.map((item) => ({
    id: item.id,
    label: versionLabel(item.number) ?? `#${item.id}`,
    isCurrent: item.is_current,
    when: versionStartLabel(item.effective_from),
    author: item.author_name?.trim() || 'Autor não registrado',
    revertedFrom: item.reverted_from_number != null ? `revertida da v${item.reverted_from_number}` : null,
    summary: versionSummaryText(item),
    trainingGoal: item.training_goal ?? null,
  }));
}

export type RevertError = { message: string; errors: string[] };

/**
 * Lê o erro de `POST /{id}/versions/{vid}/revert`. O 422 vem como
 * `{message, errors: [...]}` (tudo o que impede reativar a versão hoje); os
 * demais (404/409/rede) viram uma mensagem só, sem lista.
 */
export function readRevertError(error: unknown): RevertError {
  if (error instanceof ApiError) {
    const detail = error.detail as { message?: unknown; errors?: unknown } | undefined;
    if (error.status === 422 && detail && Array.isArray(detail.errors)) {
      return {
        message: typeof detail.message === 'string' ? detail.message : 'A versão não pode ser reativada hoje:',
        errors: detail.errors.map((e) => String(e)),
      };
    }
    return { message: error.message || 'Não foi possível reverter a versão.', errors: [] };
  }
  return { message: error instanceof Error ? error.message : 'Não foi possível reverter a versão.', errors: [] };
}

/** Aviso depois do PUT: `version_created: false` = nada estrutural mudou. */
export const VERSION_KEPT_MESSAGE = 'Nenhuma mudança estrutural — versão mantida.';

export function saveVersionNotice(response: { version_created?: boolean | null } | null | undefined): string | null {
  return response?.version_created === false ? VERSION_KEPT_MESSAGE : null;
}
