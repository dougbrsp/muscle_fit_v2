// Rascunho do Modelo de Treino ("Salvar rascunho" x "Publicar modelo";
// backend em api/app/template_drafts.py, migração 067). Funções puras: textos
// das notificações e do aviso do topo do builder — testadas em
// template-drafts.test.ts.
//
// Não confundir com o rascunho da PERIODIZAÇÃO por IA (`lib/periodization.ts`,
// `PeriodizationDraftPanel.tsx`): aquele vive só na sessão do navegador antes
// de o Modelo existir; este é gravado no banco, para um Modelo já publicado.

import { versionLabel } from './template-versions';

export const SAVE_DRAFT_LABEL = 'Salvar rascunho';
export const PUBLISH_LABEL = 'Publicar modelo';
export const DISCARD_DRAFT_LABEL = 'Descartar rascunho';
export const PUBLISHED_WITHOUT_VERSION_MESSAGE = 'Modelo atualizado (sem nova versão)';

const MOMENT_FMT = new Intl.DateTimeFormat('pt-BR', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});

const TIME_FMT = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit' });

function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** "06/10/2026, 14:32" — mesmo formato das datas do histórico de versões. */
export function draftMomentLabel(updatedAt: string | null | undefined): string {
  const parsed = parse(updatedAt);
  return parsed ? MOMENT_FMT.format(parsed) : (updatedAt ?? '—');
}

/** Notificação do "Salvar rascunho": "Rascunho salvo às 14:32". */
export function draftSavedMessage(updatedAt: string | null | undefined): string {
  const parsed = parse(updatedAt);
  return parsed ? `Rascunho salvo às ${TIME_FMT.format(parsed)}` : 'Rascunho salvo';
}

/**
 * Notificação do "Publicar modelo": "Publicado como v3" quando o PUT criou
 * versão; sem mudança estrutural, "Modelo atualizado (sem nova versão)".
 */
export function publishNotice(response: {
  version_created?: boolean | null;
  current_version?: { number: number } | null;
}): string {
  const label = versionLabel(response.current_version?.number);
  if (response.version_created && label) return `Publicado como ${label}`;
  if (response.version_created) return 'Publicado como nova versão';
  return PUBLISHED_WITHOUT_VERSION_MESSAGE;
}

/**
 * Aviso do topo do builder quando o editor abriu o RASCUNHO:
 * "Rascunho não publicado — salvo em 06/10/2026, 14:32. Os alunos continuam
 * usando a v3."
 */
export function draftBannerText(updatedAt: string | null | undefined, currentVersionNumber?: number | null): string {
  const label = versionLabel(currentVersionNumber);
  const alunos = label ? ` Os alunos continuam usando a ${label}.` : ' Os alunos continuam usando a versão publicada.';
  return `Rascunho não publicado — salvo em ${draftMomentLabel(updatedAt)}.${alunos}`;
}

/** Complemento da confirmação de "Reverter" quando há rascunho. */
export const REVERT_DISCARDS_DRAFT_WARNING =
  'Este modelo tem um rascunho não publicado. Reverter descarta o rascunho — as alterações dele serão perdidas.';
