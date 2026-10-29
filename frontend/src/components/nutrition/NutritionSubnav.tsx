'use client';

import { Button, Group } from '@mantine/core';
import Link from 'next/link';

const SECOES = [
  { href: '/nutricao', label: 'Diário' },
  { href: '/nutricao/plano', label: 'Plano' },
  { href: '/nutricao/alimentos', label: 'Alimentos' },
] as const;

type Props = { active: (typeof SECOES)[number]['href'] };

/**
 * Seções da Nutrição (diário, plano e catálogo de alimentos). O menu lateral
 * tem um item só, "Nutrição" (`AppShellLayout`, prefixo `/nutricao`, que abre o
 * diário); a troca de seção fica aqui, sem item novo no menu.
 */
export function NutritionSubnav({ active }: Props) {
  return (
    <Group gap="xs" wrap="wrap" aria-label="Seções da Nutrição" role="navigation">
      {SECOES.map((secao) => (
        <Button
          key={secao.href}
          component={Link}
          href={secao.href}
          size="xs"
          radius="xl"
          color="brand"
          variant={secao.href === active ? 'filled' : 'light'}
          aria-current={secao.href === active ? 'page' : undefined}
        >
          {secao.label}
        </Button>
      ))}
    </Group>
  );
}
