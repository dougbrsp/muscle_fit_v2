/** Seções da Nutrição no iPhone: fonte única da folha da tela e do submenu do "Mais". */
export const NUTRICAO_SECOES = [
  { href: '/nutricao', label: 'Diário' },
  { href: '/nutricao/plano', label: 'Plano' },
  { href: '/nutricao/checkin', label: 'Check-in' },
  { href: '/nutricao/alimentos', label: 'Alimentos' },
  { href: '/nutricao/receitas', label: 'Receitas' },
  { href: '/nutricao/favoritos', label: 'Preferidos' },
  { href: '/dashboard/nutricao', label: 'Painel' },
] as const;

export type NutricaoSecao = (typeof NUTRICAO_SECOES)[number]['href'];

/** O Painel é sub-rota do Dashboard: exige `dashboards/ver`. */
export const NUTRICAO_PAINEL_HREF: NutricaoSecao = '/dashboard/nutricao';
