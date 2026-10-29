'use client';

import { List, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import { SafeMarkdown } from '@/components/SafeMarkdown';
import { BRAND } from '@/theme';

/**
 * Markdown gerado por IA renderizado com componentes Mantine (nunca HTML
 * cru). Usado em `/dashboard/treinos` e `/insights`. A política de links,
 * imagens e HTML (IA-15) vem de `SafeMarkdown`.
 */
const components = {
  h1: ({ children }: { children?: ReactNode }) => (
    <Title order={3} mt="md" mb="xs" c={BRAND.textPrimary}>
      {children}
    </Title>
  ),
  h2: ({ children }: { children?: ReactNode }) => (
    <Title order={4} mt="md" mb="xs" c={BRAND.textPrimary}>
      {children}
    </Title>
  ),
  h3: ({ children }: { children?: ReactNode }) => (
    <Title order={5} mt="sm" mb="xs" c={BRAND.textPrimary}>
      {children}
    </Title>
  ),
  p: ({ children }: { children?: ReactNode }) => (
    <Text mb="xs" c={BRAND.textPrimary} style={{ whiteSpace: 'pre-wrap' }}>
      {children}
    </Text>
  ),
  strong: ({ children }: { children?: ReactNode }) => (
    <Text component="strong" fw={700} c={BRAND.textPrimary}>
      {children}
    </Text>
  ),
  ul: ({ children }: { children?: ReactNode }) => (
    <List spacing={4} mb="xs" c={BRAND.textPrimary}>
      {children}
    </List>
  ),
  ol: ({ children }: { children?: ReactNode }) => (
    <List type="ordered" spacing={4} mb="xs" c={BRAND.textPrimary}>
      {children}
    </List>
  ),
  li: ({ children }: { children?: ReactNode }) => <List.Item>{children}</List.Item>,
};

export function AiMarkdown({ children }: { children: string }) {
  return <SafeMarkdown components={components}>{children}</SafeMarkdown>;
}
