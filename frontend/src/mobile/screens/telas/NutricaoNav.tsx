'use client';

import { useState } from 'react';
import { BottomSheet, ListGroup, ListRow } from '../../ui';

const SECOES = [
  { href: '/nutricao', label: 'Diário' },
  { href: '/nutricao/plano', label: 'Plano' },
  { href: '/nutricao/checkin', label: 'Check-in' },
  { href: '/nutricao/alimentos', label: 'Alimentos' },
  { href: '/nutricao/favoritos', label: 'Preferidos' },
  { href: '/dashboard/nutricao', label: 'Painel' },
] as const;

export type NutricaoSecao = (typeof SECOES)[number]['href'];

/** Seção atual da Nutrição numa linha só; o toque abre a lista das demais (substitui a faixa de botões do desktop). */
export function NutricaoNav({ active }: { active: NutricaoSecao }) {
  const [opened, setOpened] = useState(false);
  return (
    <>
      <ListGroup aria-label="Seções da Nutrição">
        <ListRow title="Outras seções da Nutrição" onClick={() => setOpened(true)} />
      </ListGroup>
      <BottomSheet opened={opened} onClose={() => setOpened(false)} title="Nutrição">
        <ListGroup>
          {SECOES.map((secao) => (
            <ListRow key={secao.href} title={secao.label} href={secao.href} current={secao.href === active} />
          ))}
        </ListGroup>
      </BottomSheet>
    </>
  );
}
