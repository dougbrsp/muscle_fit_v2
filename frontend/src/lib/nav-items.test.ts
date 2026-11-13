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

describe('link admin Grupos e acessos', () => {
  it('aparece só para admin, ao lado de Usuários, e é só do computador', () => {
    const admin = visibleMainNavItems(ALL).map((i) => i.href);
    expect(admin).toContain('/grupos-acessos');
    expect(admin.indexOf('/grupos-acessos')).toBe(admin.indexOf('/usuarios') + 1);
    const regular = navVisibilityFromUser({ role: 'regular', intervals_configured: true }, true);
    expect(visibleMainNavItems(regular).map((i) => i.href)).not.toContain('/grupos-acessos');
    expect(isDesktopOnlyPath('/grupos-acessos')).toBe(true);
    expect(desktopOnlySectionLabel('/grupos-acessos')).toBe('Grupos e acessos');
    expect(mobileMainNavItems(ALL).map((i) => i.href)).not.toContain('/grupos-acessos');
  });
});

describe('desktopOnly (mobile v2, D1)', () => {
  it('marca exatamente as seis seções pedidas (mais Grupos e acessos)', () => {
    const marked = buildMainNavItems(ALL)
      .filter((i) => i.desktopOnly)
      .map((i) => i.href)
      .sort();
    expect(marked).toEqual(
      [
        '/academias',
        '/dominio-exercicios',
        '/equipamentos-exercicios',
        '/grupos-acessos',
        '/metas-ativacao',
        '/modelos-treino',
        '/regras-carga',
      ].sort(),
    );
  });

  it('não altera o menu desktop: visibleMainNavItems continua listando-as', () => {
    expect(visibleMainNavItems(ALL).filter((i) => i.desktopOnly)).toHaveLength(7);
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
