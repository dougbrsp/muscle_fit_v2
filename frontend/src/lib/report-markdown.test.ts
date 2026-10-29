import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiFetchBlob } from '@/lib/api';
import { getApiBaseUrl } from '@/lib/config';
import {
  buildReportPath,
  downloadMarkdownReport,
  inclusiveDays,
  localTodayIso,
  reportFilename,
  validateCustomRange,
  type DownloadDeps,
} from './report-markdown';

function fakeDeps(fetchBlob: DownloadDeps['fetchBlob']) {
  const calls: string[] = [];
  const deps: DownloadDeps = {
    fetchBlob,
    createObjectURL: () => {
      calls.push('create');
      return 'blob:relatorio';
    },
    revokeObjectURL: (url) => calls.push(`revoke:${url}`),
    clickLink: (href, filename) => calls.push(`click:${href}:${filename}`),
  };
  return { deps, calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('buildReportPath', () => {
  it('janelas móveis só levam window', () => {
    expect(buildReportPath('90d')).toBe('/reports/markdown?window=90d');
    expect(buildReportPath('30d')).toBe('/reports/markdown?window=30d');
  });

  it('personalizado leva as duas datas', () => {
    expect(buildReportPath('custom', '2026-09-01', '2026-09-30')).toBe(
      '/reports/markdown?window=custom&date_from=2026-09-01&date_to=2026-09-30',
    );
  });
});

describe('validateCustomRange', () => {
  const hoje = '2026-10-01';

  it('aceita de 7 a 180 dias', () => {
    expect(validateCustomRange('2026-09-25', '2026-10-01', hoje)).toBeNull();
    expect(inclusiveDays('2026-04-04', '2026-09-30')).toBe(180);
    expect(validateCustomRange('2026-04-04', '2026-09-30', hoje)).toBeNull();
  });

  it('recusa fora dos limites, invertido, futuro e incompleto', () => {
    expect(validateCustomRange('2026-09-26', '2026-10-01', hoje)).toMatch('entre 7 e 180 dias');
    expect(validateCustomRange('2026-04-03', '2026-09-30', hoje)).toMatch('entre 7 e 180 dias');
    expect(validateCustomRange('2026-09-30', '2026-09-01', hoje)).toMatch('depois da data final');
    expect(validateCustomRange('2026-09-20', '2026-10-02', hoje)).toMatch('futuro');
    expect(validateCustomRange('', '2026-10-01', hoje)).toMatch('data inicial e a data final');
  });
});

describe('reportFilename', () => {
  it('segue o padrão do backend', () => {
    expect(reportFilename('90d', '2026-10-01')).toBe('relatorio-musclefit-2026-07-04_2026-10-01.md');
    expect(reportFilename('custom', '2026-10-01', '2026-09-01', '2026-09-30')).toBe(
      'relatorio-musclefit-2026-09-01_2026-09-30.md',
    );
    expect(localTodayIso(new Date(2026, 0, 5))).toBe('2026-01-05');
  });
});

describe('downloadMarkdownReport (botão "Baixar relatório")', () => {
  it('busca o .md com o cookie de sessão e baixa pelo link temporário', async () => {
    const fetchMock = vi.fn(async () => new Response('# Relatório MuscleFit\n', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const { deps, calls } = fakeDeps(apiFetchBlob);

    await downloadMarkdownReport('/reports/markdown?window=90d', 'relatorio.md', deps);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(`${getApiBaseUrl()}/reports/markdown?window=90d`, {
      credentials: 'include',
    });
    expect(calls).toEqual(['create', 'click:blob:relatorio:relatorio.md', 'revoke:blob:relatorio']);
  });

  it('erro da API vira ApiError e nada é baixado', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"detail":"x"}', { status: 422 })),
    );
    const { deps, calls } = fakeDeps(apiFetchBlob);

    await expect(downloadMarkdownReport('/reports/markdown?window=custom', 'r.md', deps)).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(calls).toEqual([]);
  });

  it('revoga a URL temporária mesmo se o clique falhar', async () => {
    const { deps, calls } = fakeDeps(async () => new Blob(['x']));
    deps.clickLink = () => {
      throw new Error('bloqueado');
    };
    await expect(downloadMarkdownReport('/x', 'r.md', deps)).rejects.toThrow('bloqueado');
    expect(calls).toEqual(['create', 'revoke:blob:relatorio']);
  });
});
