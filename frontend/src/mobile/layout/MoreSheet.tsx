'use client';

import { Divider, Drawer, Stack, Text } from '@mantine/core';
import { useReducedMotion } from '@mantine/hooks';
import { IconLogout } from '@tabler/icons-react';
import Link from 'next/link';
import { Suspense } from 'react';
import {
  DashboardNavLinks,
  DashboardNavLinksFallback,
} from '@/components/dashboard/DashboardNavLinks';
import { isNavItemActive } from '@/lib/nav-items';
import { MOBILE_TOKENS } from '@/theme';
import type { MoreSections } from './primary-nav';
import styles from './mobile-shell.module.css';

/**
 * Folha "Mais": todas as rotas do menu desktop que não são aba fixa, mais
 * "Sair". Sub-rotas do Dashboard reutilizam `DashboardNavLinks` (carregam o
 * filtro global na query string, igual ao desktop).
 */
export function MoreSheet({
  opened,
  onClose,
  sections,
  pathname,
  onLogout,
}: {
  opened: boolean;
  onClose: () => void;
  sections: MoreSections;
  pathname: string | null;
  onLogout: () => void;
}) {
  const reduceMotion = useReducedMotion();
  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      position="bottom"
      size="85%"
      title="Menu"
      transitionProps={{ duration: reduceMotion ? 0 : 200 }}
      styles={{
        content: { background: MOBILE_TOKENS.surface, borderRadius: '16px 16px 0 0' },
        header: { background: MOBILE_TOKENS.surface },
        close: { width: MOBILE_TOKENS.touchTarget, height: MOBILE_TOKENS.touchTarget },
      }}
    >
      <Stack gap={4} className={styles.sheetBody} onClick={onClose}>
        <Text size="xs" fw={700} tt="uppercase" c={MOBILE_TOKENS.textMuted} px="xs">
          Dashboard
        </Text>
        <div
          className={styles.dash}
          onClick={(event) => event.stopPropagation()}
          role="presentation"
        >
          <Suspense fallback={<DashboardNavLinksFallback items={sections.dashboard} />}>
            <DashboardNavLinks items={sections.dashboard} />
          </Suspense>
        </div>
        <Divider my="xs" color={MOBILE_TOKENS.border} />
        <Text size="xs" fw={700} tt="uppercase" c={MOBILE_TOKENS.textMuted} px="xs">
          Mais telas
        </Text>
        {sections.main.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={styles.moreLink}
            aria-current={isNavItemActive(item, pathname) ? 'page' : undefined}
          >
            <item.icon size={20} aria-hidden />
            {item.label}
          </Link>
        ))}
        <Divider my="xs" color={MOBILE_TOKENS.border} />
        <button
          type="button"
          className={styles.moreLink}
          style={{
            background: 'transparent',
            border: 0,
            font: 'inherit',
            fontWeight: 600,
            cursor: 'pointer',
            width: '100%',
            textAlign: 'left',
            color: MOBILE_TOKENS.danger,
          }}
          onClick={(event) => {
            event.stopPropagation();
            onLogout();
          }}
        >
          <IconLogout size={20} aria-hidden />
          Sair
        </button>
      </Stack>
    </Drawer>
  );
}
