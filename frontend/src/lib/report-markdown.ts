import { apiFetchBlob } from '@/lib/api';

/**
 * Relatório em Markdown (`GET /reports/markdown`) — substitui o relatório PDF
 * com IA da antiga tela `/relatorios`.
 *
 * Tudo aqui é puro ou recebe as dependências por parâmetro, para o teste rodar
 * em `node` (sem DOM): a tela só junta estas peças.
 */

export type ReportPeriod = '30d' | '90d' | '180d' | 'custom';

export const REPORT_PERIOD_OPTIONS: { value: ReportPeriod; label: string }[] = [
  { value: '30d', label: 'Últimos 30 dias' },
  { value: '90d', label: 'Últimos 90 dias' },
  { value: '180d', label: 'Últimos 180 dias' },
  { value: 'custom', label: 'Personalizado' },
];

export const DEFAULT_REPORT_PERIOD: ReportPeriod = '90d';

/** Mesmos limites do backend (`app/reports.py`). */
export const MIN_REPORT_DAYS = 7;
export const MAX_REPORT_DAYS = 180;

const ROLLING_DAYS: Record<Exclude<ReportPeriod, 'custom'>, number> = { '30d': 30, '90d': 90, '180d': 180 };

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function parseIsoDate(value: string): Date | null {
  if (!ISO_DATE.test(value)) return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function toIsoDate(value: Date): string {
  return value.toISOString().slice(0, 10);
}

/** Dias inclusivos entre duas datas ISO (`2026-09-01`..`2026-09-07` = 7). */
export function inclusiveDays(dateFrom: string, dateTo: string): number | null {
  const from = parseIsoDate(dateFrom);
  const to = parseIsoDate(dateTo);
  if (!from || !to) return null;
  return Math.round((to.getTime() - from.getTime()) / 86_400_000) + 1;
}

/**
 * Valida o período personalizado antes de chamar a API (o backend valida de
 * novo e responde 422). Devolve a mensagem para o usuário ou `null` se ok.
 * `today` é a data local do navegador em ISO.
 */
export function validateCustomRange(dateFrom: string, dateTo: string, today: string): string | null {
  if (!dateFrom || !dateTo) return 'Informe a data inicial e a data final.';
  const days = inclusiveDays(dateFrom, dateTo);
  if (days === null) return 'Data inválida. Use o formato AAAA-MM-DD.';
  if (days < 1) return 'A data inicial não pode ser depois da data final.';
  if (dateTo > today) return 'A data final não pode estar no futuro.';
  if (days < MIN_REPORT_DAYS || days > MAX_REPORT_DAYS) {
    return `O período deve ter entre ${MIN_REPORT_DAYS} e ${MAX_REPORT_DAYS} dias (selecionado: ${days}).`;
  }
  return null;
}

/** Caminho da API para o período escolhido. */
export function buildReportPath(period: ReportPeriod, dateFrom = '', dateTo = ''): string {
  const params = new URLSearchParams({ window: period });
  if (period === 'custom') {
    params.set('date_from', dateFrom);
    params.set('date_to', dateTo);
  }
  return `/reports/markdown?${params.toString()}`;
}

/**
 * Nome do arquivo baixado — o mesmo padrão do `Content-Disposition` do
 * backend. Nas janelas móveis as datas saem do relógio do navegador; o
 * backend usa o fuso do perfil, então perto da meia-noite o nome pode diferir
 * em um dia do conteúdo (o cabeçalho do arquivo traz o período exato).
 */
export function reportFilename(period: ReportPeriod, today: string, dateFrom = '', dateTo = ''): string {
  if (period === 'custom') return `relatorio-musclefit-${dateFrom}_${dateTo}.md`;
  const end = parseIsoDate(today) ?? new Date();
  const start = new Date(end.getTime() - (ROLLING_DAYS[period] - 1) * 86_400_000);
  return `relatorio-musclefit-${toIsoDate(start)}_${toIsoDate(end)}.md`;
}

/** Data local do navegador em ISO (`AAAA-MM-DD`). */
export function localTodayIso(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export type DownloadDeps = {
  fetchBlob: (path: string) => Promise<Blob>;
  createObjectURL: (blob: Blob) => string;
  revokeObjectURL: (url: string) => void;
  /** Cria o `<a>` temporário, clica e remove. */
  clickLink: (href: string, filename: string) => void;
};

function browserDeps(): DownloadDeps {
  return {
    fetchBlob: (path) => apiFetchBlob(path),
    createObjectURL: (blob) => URL.createObjectURL(blob),
    revokeObjectURL: (url) => URL.revokeObjectURL(url),
    clickLink: (href, filename) => {
      const link = document.createElement('a');
      link.href = href;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    },
  };
}

/**
 * Baixa o relatório: busca o `.md` autenticado (cookie de sessão via
 * `apiFetchBlob` — um `<a href>` direto não enviaria o cookie httpOnly) e
 * dispara o download por um link temporário com `URL.createObjectURL`. A URL
 * temporária é sempre revogada, mesmo se o clique falhar.
 */
export async function downloadMarkdownReport(
  path: string,
  filename: string,
  deps: DownloadDeps = browserDeps(),
): Promise<void> {
  const blob = await deps.fetchBlob(path);
  const objectUrl = deps.createObjectURL(blob);
  try {
    deps.clickLink(objectUrl, filename);
  } finally {
    deps.revokeObjectURL(objectUrl);
  }
}
