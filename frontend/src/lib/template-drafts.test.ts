import { describe, expect, it } from 'vitest';
import {
  PUBLISHED_WITHOUT_VERSION_MESSAGE,
  draftBannerText,
  draftMomentLabel,
  draftSavedMessage,
  publishNotice,
} from './template-drafts';

describe('rascunho do Modelo — textos', () => {
  it('publicar com versão nova mostra "Publicado como vN"', () => {
    expect(publishNotice({ version_created: true, current_version: { number: 4 } })).toBe('Publicado como v4');
  });

  it('publicar sem mudança estrutural avisa que não houve versão nova', () => {
    expect(publishNotice({ version_created: false, current_version: { number: 4 } })).toBe(
      PUBLISHED_WITHOUT_VERSION_MESSAGE,
    );
    expect(PUBLISHED_WITHOUT_VERSION_MESSAGE).toBe('Modelo atualizado (sem nova versão)');
  });

  it('rascunho salvo mostra o horário', () => {
    expect(draftSavedMessage('2026-10-06T14:32:00')).toMatch(/^Rascunho salvo às 14:32$/);
    expect(draftSavedMessage(null)).toBe('Rascunho salvo');
  });

  it('aviso do topo cita a data e a versão que os alunos usam', () => {
    const texto = draftBannerText('2026-10-06T14:32:00', 3);
    expect(texto).toContain('Rascunho não publicado — salvo em 06/10/2026');
    expect(texto).toContain('14:32');
    expect(texto.endsWith('Os alunos continuam usando a v3.')).toBe(true);
  });

  it('data inválida não quebra o aviso', () => {
    expect(draftMomentLabel('ontem')).toBe('ontem');
    expect(draftBannerText(undefined, null)).toContain('versão publicada');
  });
});
