'use client';

import { Box, Center, Image, Paper, Stack, Text, Title } from '@mantine/core';
import Link from 'next/link';
import type { ReactNode } from 'react';

interface PublicAuthShellProps {
  title: string;
  subtitle: string;
  children: ReactNode;
}

/**
 * Moldura das telas públicas de autenticação (login, esqueci/redefinir
 * senha): mesmo fundo, logo e cartão de `LoginClient`, sem o app-shell
 * autenticado do grupo `(app)`.
 */
export function PublicAuthShell({ title, subtitle, children }: PublicAuthShellProps) {
  return (
    <Box
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background:
          'radial-gradient(circle at 50% 0%, rgba(255,149,0,0.12), transparent 55%), #0A0A0A',
        padding: '24px',
      }}
    >
      <Stack align="center" gap="xl" style={{ width: '100%', maxWidth: 420 }}>
        <Center>
          <Link
            href="/"
            aria-label="MuscleFit - ir para a pagina inicial"
            style={{ display: 'inline-flex' }}
          >
            <Image src="/logo.svg" alt="MuscleFit" w={240} h={198} fit="contain" />
          </Link>
        </Center>

        <Paper
          withBorder
          radius="lg"
          p="xl"
          style={{ width: '100%', background: '#141414', borderColor: '#2A2A2A' }}
        >
          <Stack gap="md">
            <div>
              <Title order={2} className="brand-gradient-text">
                {title}
              </Title>
              <Text size="sm" c="dimmed">
                {subtitle}
              </Text>
            </div>
            {children}
          </Stack>
        </Paper>

        <Text size="xs" c="dimmed">
          MuscleFit &middot; AI Powered Workout
        </Text>
      </Stack>
    </Box>
  );
}
