'use client';

import { Alert, Anchor, Button, PasswordInput, Stack } from '@mantine/core';
import { IconAlertCircle, IconLockCheck } from '@tabler/icons-react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useState, type FormEvent } from 'react';
import { PublicAuthShell } from '@/components/PublicAuthShell';
import { ApiError, apiFetch } from '@/lib/api';
import { readResetToken, validateNewPassword } from '@/lib/password-reset';

export function RedefinirSenhaClient() {
  const searchParams = useSearchParams();
  const token = readResetToken(searchParams);

  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    const validation = validateNewPassword(password, confirmation);
    if (validation) {
      setError(validation);
      return;
    }

    setError(null);
    setLoading(true);
    try {
      await apiFetch<{ status: string }>('/auth/password-reset/confirm', {
        method: 'POST',
        body: JSON.stringify({ token, new_password: password }),
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Erro inesperado. Tente novamente.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <PublicAuthShell title="Redefinir senha" subtitle="Escolha uma nova senha para a sua conta">
      {!token ? (
        <Stack gap="md">
          <Alert color="red" title="Link inválido" icon={<IconAlertCircle size={18} />}>
            Este link não contém um código de redefinição. Abra o link exatamente como veio no e-mail ou
            solicite um novo.
          </Alert>
          <Anchor component={Link} href="/esqueci-senha" size="sm">
            Solicitar um novo link
          </Anchor>
        </Stack>
      ) : done ? (
        <Stack gap="md">
          <Alert color="green" title="Senha redefinida" icon={<IconLockCheck size={18} />}>
            Sua senha foi alterada e todas as sessões anteriores foram encerradas. Entre com a nova senha.
          </Alert>
          <Button component={Link} href="/" fullWidth variant="gradient" gradient={{ from: 'brand.5', to: 'brandRed.6', deg: 135 }}>
            Ir para o login
          </Button>
        </Stack>
      ) : (
        <form onSubmit={handleSubmit}>
          <Stack gap="md">
            <PasswordInput
              label="Nova senha"
              placeholder="mínimo 8 caracteres"
              value={password}
              onChange={(event) => setPassword(event.currentTarget.value)}
              required
              autoComplete="new-password"
              disabled={loading}
            />
            <PasswordInput
              label="Confirme a nova senha"
              placeholder="repita a senha"
              value={confirmation}
              onChange={(event) => setConfirmation(event.currentTarget.value)}
              required
              autoComplete="new-password"
              disabled={loading}
            />

            {error && (
              <Alert color="red" title="Não foi possível redefinir" icon={<IconAlertCircle size={18} />}>
                {error}{' '}
                <Anchor component={Link} href="/esqueci-senha" size="sm">
                  Solicitar um novo link
                </Anchor>
              </Alert>
            )}

            <Button
              type="submit"
              fullWidth
              loading={loading}
              variant="gradient"
              gradient={{ from: 'brand.5', to: 'brandRed.6', deg: 135 }}
            >
              Redefinir senha
            </Button>
          </Stack>
        </form>
      )}
    </PublicAuthShell>
  );
}
