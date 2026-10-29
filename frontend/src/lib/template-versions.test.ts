import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import {
  INITIAL_VERSION_SUMMARY,
  VERSION_KEPT_MESSAGE,
  readRevertError,
  saveVersionNotice,
  versionHistoryRows,
  versionLabel,
  versionStartLabel,
  versionSummaryText,
  type TemplateVersionListItem,
} from './template-versions';

const ITEMS: TemplateVersionListItem[] = [
  {
    id: 30,
    number: 3,
    effective_from: '2026-10-05T21:30:00',
    superseded_at: null,
    author_user_id: 1,
    author_name: 'Doug',
    reverted_from_number: 1,
    change_summary: 'Revertido para a v1',
    is_current: true,
  },
  {
    id: 10,
    number: 1,
    effective_from: null,
    superseded_at: '2026-10-04T10:00:00',
    author_user_id: null,
    author_name: null,
    reverted_from_number: null,
    change_summary: null,
    is_current: false,
  },
];

describe('versionLabel', () => {
  it('formata vN e ignora ausente', () => {
    expect(versionLabel(3)).toBe('v3');
    expect(versionLabel(null)).toBeNull();
    expect(versionLabel(undefined)).toBeNull();
  });
});

describe('versionHistoryRows', () => {
  const rows = versionHistoryRows(ITEMS);

  it('mantém a ordem da API e destaca a vigente', () => {
    expect(rows.map((r) => [r.label, r.isCurrent])).toEqual([
      ['v3', true],
      ['v1', false],
    ]);
  });

  it('diz de qual versão veio a reversão e quem fez', () => {
    expect(rows[0].revertedFrom).toBe('revertida da v1');
    expect(rows[0].author).toBe('Doug');
    expect(rows[0].summary).toBe('Revertida para a v1');
  });

  it('a v1 da migração vale desde o início e pode não ter autor', () => {
    expect(rows[1].when).toBe('Desde o início');
    expect(rows[1].author).toBe('Autor não registrado');
    expect(rows[1].summary).toBe('Versão inicial (anterior ao versionamento)');
    expect(rows[1].revertedFrom).toBeNull();
    expect(versionStartLabel('2026-10-05T21:30:00')).toMatch(/05\/10\/2026/);
  });
});

describe('readRevertError', () => {
  it('o 422 vira mensagem + lista', () => {
    const error = new ApiError('erro', 422, {
      message: 'A v1 não pode ser reativada hoje:',
      errors: ['Slot A: técnica incompatível', 'Slot B: item inválido'],
    });
    expect(readRevertError(error)).toEqual({
      message: 'A v1 não pode ser reativada hoje:',
      errors: ['Slot A: técnica incompatível', 'Slot B: item inválido'],
    });
  });

  it('409/404 viram uma mensagem só', () => {
    expect(readRevertError(new ApiError('A v2 já é a versão vigente.', 409))).toEqual({
      message: 'A v2 já é a versão vigente.',
      errors: [],
    });
  });
});

describe('saveVersionNotice', () => {
  it('avisa só quando o PUT manteve a versão', () => {
    expect(saveVersionNotice({ version_created: false })).toBe(VERSION_KEPT_MESSAGE);
    expect(VERSION_KEPT_MESSAGE).toBe('Nenhuma mudança estrutural — versão mantida.');
    expect(saveVersionNotice({ version_created: true })).toBeNull();
    expect(saveVersionNotice({})).toBeNull();
    expect(saveVersionNotice(undefined)).toBeNull();
  });
});

describe('versionSummaryText (sempre português, sem jargão)', () => {
  const base = { number: 2, effective_from: '2026-10-05T10:00:00', reverted_from_number: null, change_summary: null };

  it('a v1 da 065 vira "Versão inicial", mesmo com o texto gravado pela migração', () => {
    expect(
      versionSummaryText({ number: 1, effective_from: null, reverted_from_number: null, change_summary: 'v1 (backfill da migracao 065)' }),
    ).toBe(INITIAL_VERSION_SUMMARY);
    expect(INITIAL_VERSION_SUMMARY).toBe('Versão inicial (anterior ao versionamento)');
  });

  it('a versão de órfãos da 066 vira texto para pessoas', () => {
    expect(versionSummaryText({ ...base, change_summary: 'slots orfaos da janela de deploy da 065 (066)' })).toBe(
      'Ajuste automático: slots salvos durante uma atualização do sistema',
    );
  });

  it('reversão, criação e edição', () => {
    expect(versionSummaryText({ ...base, number: 3, reverted_from_number: 1, change_summary: 'Revertido para a v1' })).toBe(
      'Revertida para a v1',
    );
    expect(versionSummaryText({ ...base, number: 1 })).toBe('Criação do modelo');
    expect(versionSummaryText(base)).toBe('Edição do modelo');
  });

  it('nenhum texto de tela traz jargão de migração', () => {
    const textos = [
      versionSummaryText({ number: 1, effective_from: null, reverted_from_number: null, change_summary: 'v1 (backfill da migracao 065)' }),
      versionSummaryText({ ...base, change_summary: 'slots orfaos da janela de deploy da 065 (066)' }),
    ];
    for (const texto of textos) expect(texto).not.toMatch(/backfill|migrac|orfao|deploy|065|066/i);
  });
});
