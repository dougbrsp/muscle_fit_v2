import { describe, expect, it } from 'vitest';
import {
  buildMainNavItems,
  desktopOnlySectionLabel,
  isDesktopOnlyPath,
  mobileMainNavItems,
  navVisibilityFromUser,
  visibleMainNavItems,
} from './nav-items';

const ALL = navVisibilityFromUser({ role: 'admin', intervals_configured: true }, true);

describe('desktopOnly (mobile v2, D1)', () => {
  it('marca exatamente as seis seções pedidas', () => {
    const marked = buildMainNavItems(ALL)
      .filter((i) => i.desktopOnly)
      .map((i) => i.href)
      .sort();
    expect(marked).toEqual(
      [
        '/academias',
        '/dominio-exercicios',
        '/equipamentos-exercicios',
        '/metas-ativacao',
        '/modelos-treino',
        '/regras-carga',
      ].sort(),
    );
  });

  it('não altera o menu desktop: visibleMainNavItems continua listando-as', () => {
    expect(visibleMainNavItems(ALL).filter((i) => i.desktopOnly)).toHaveLength(6);
    expect(mobileMainNavItems(ALL).filter((i) => i.desktopOnly)).toHaveLength(0);
  });

  it('nomeia a seção da rota (inclui [id], diagnóstico de volume e legado)', () => {
    expect(desktopOnlySectionLabel('/metas-ativacao/3/')).toBe('Metas de Ativação');
    expect(desktopOnlySectionLabel('/diagnostico-volume/8')).toBe('Modelos de treino');
    expect(desktopOnlySectionLabel('/tipos-treinamento')).toBe('Metas de Ativação');
    expect(desktopOnlySectionLabel('/academias-extra')).toBeNull();
    expect(isDesktopOnlyPath('/perfil')).toBe(false);
  });
});
