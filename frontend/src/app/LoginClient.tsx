'use client';

import {
  Alert,
  Anchor,
  Box,
  Button,
  Center,
  Image,
  Paper,
  PasswordInput,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconAlertCircle, IconLock } from '@tabler/icons-react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState, type FormEvent } from 'react';
import { apiFetch } from '@/lib/api';
import { AuthError, login, type SessionUser } from '@/lib/auth';
import { sanitizeInternalPath } from '@/lib/safe-path';

const DEFAULT_AUTHENTICATED_PATH = '/dashboard';

/**
 * Sanitiza `?next=`: só aceita caminho interno (SEG-18, ver
 * `src/lib/safe-path.ts`); qualquer outra coisa cai no destino padrão. Mesma
 * regra do interceptor de 401 em `src/lib/api.ts`, que preenche o parâmetro.
 */
export function sanitizeNextPath(raw: string | null): string {
  return sanitizeInternalPath(raw) ?? DEFAULT_AUTHENTICATED_PATH;
}

export function LoginClient() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = sanitizeNextPath(searchParams.get('next'));

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loggedUser, setLoggedUser] = useState<SessionUser | null>(null);

  // Sessão já ativa (cookie httpOnly) ao montar a tela de login: pula direto
  // para o destino em vez de pedir login de novo. Como o cookie é httpOnly,
  // a única forma de checar é perguntando à API.
  useEffect(() => {
    let cancelled = false;
    apiFetch<SessionUser>('/auth/me')
      .then(() => {
        if (!cancelled) router.replace(nextPath);
      })
      .catch(() => {
        // sem sessão válida — permanece na tela de login normalmente.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const result = await login(username, password);
      setLoggedUser(result.user);
      if (result.user.must_change_password) {
        router.replace('/perfil?troque-senha=1');
      } else {
        router.replace(nextPath);
      }
    } catch (err) {
      if (err instanceof AuthError) {
        setError(err.message);
      } else {
        setError('Erro inesperado ao tentar entrar. Tente novamente.');
      }
    } finally {
      setLoading(false);
    }
  }

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
          style={{
            width: '100%',
            background: '#141414',
            borderColor: '#2A2A2A',
          }}
        >
          <Stack gap="md">
            <div>
              <Title order={2} className="brand-gradient-text">
                Entrar
              </Title>
              <Text size="sm" c="dimmed">
                Acesse sua conta MuscleFit
              </Text>
            </div>

            {loggedUser ? (
              <Alert color="green" title="Login realizado com sucesso" icon={<IconLock size={18} />}>
                Bem-vindo, {loggedUser.full_name || loggedUser.username}. Sessão ativa
                (papel: {loggedUser.role}).
              </Alert>
            ) : (
              <form onSubmit={handleSubmit}>
                <Stack gap="md">
                  <TextInput
                    label="Usuário"
                    placeholder="seu.usuario"
                    value={username}
                    onChange={(event) => setUsername(event.currentTarget.value)}
                    required
                    autoComplete="username"
                    disabled={loading}
                  />
                  <PasswordInput
                    label="Senha"
                    placeholder="••••••••"
                    value={password}
                    onChange={(event) => setPassword(event.currentTarget.value)}
                    required
                    autoComplete="current-password"
                    disabled={loading}
                  />

                  {error && (
                    <Alert color="red" title="Não foi possível entrar" icon={<IconAlertCircle size={18} />}>
                      {error}
                    </Alert>
                  )}

                  <Button
                    type="submit"
                    fullWidth
                    loading={loading}
                    variant="gradient"
                    gradient={{ from: 'brand.5', to: 'brandRed.6', deg: 135 }}
                  >
                    Entrar
                  </Button>

                  <Text size="sm" c="dimmed" ta="center">
                    <Anchor component={Link} href="/esqueci-senha" size="sm">
                      Esqueci minha senha
                    </Anchor>
                  </Text>
                </Stack>
              </form>
            )}
          </Stack>
        </Paper>

        <Text size="xs" c="dimmed">
          MuscleFit &middot; AI Powered Workout
        </Text>
      </Stack>
    </Box>
  );
}
