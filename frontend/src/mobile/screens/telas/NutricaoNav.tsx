'use client';

import { ListGroup, ListRow } from '../../ui';

const SECOES = [
  { href: '/nutricao', label: 'Diário' },
  { href: '/nutricao/plano', label: 'Plano' },
  { href: '/nutricao/checkin', label: 'Check-in' },
  { href: '/nutricao/alimentos', label: 'Alimentos' },
  { href: '/nutricao/favoritos', label: 'Preferidos' },
  { href: '/dashboard/nutricao', label: 'Painel' },
] as const;

/** Seções da Nutrição em lista (substitui a faixa de botões do desktop). */
export function NutricaoNav({ active }: { active: (typeof SECOES)[number]['href'] }) {
  return (
    <ListGroup title="Seções da Nutrição" aria-label="Seções da Nutrição">
      {SECOES.filter((secao) => secao.href !== active).map((secao) => (
        <ListRow key={secao.href} title={secao.label} href={secao.href} />
      ))}
    </ListGroup>
  );
}
