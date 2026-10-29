'use client';

import { Alert, Anchor, Button, Stack, Text, TextInput } from '@mantine/core';
import { IconAlertCircle, IconMailCheck } from '@tabler/icons-react';
import Link from 'next/link';
import { useState, type FormEvent } from 'react';
import { PublicAuthShell } from '@/components/PublicAuthShell';
import { TurnstileWidget } from '@/components/TurnstileWidget';
import { ApiError, apiFetch } from '@/lib/api';
import { TURNSTILE_SITE_KEY } from '@/lib/config';
import { buildPasswordResetRequestPayload } from '@/lib/password-reset';

interface ResetRequestResponse {
  status: string;
  detail: string;
}

export function EsqueciSenhaClient() {
  const [identifier, setIdentifier] = useState('');
  const [captchaToken, setCaptchaToken] = useState<string | null>(null);
  const [captchaReset, setCaptchaReset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const captchaConfigured = TURNSTILE_SITE_KEY.length > 0;
  const canSubmit = captchaConfigured && !!captchaToken && identifier.trim().length > 0 && !loading;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!captchaToken) return;
    const payload = buildPasswordResetRequestPayload(identifier, captchaToken);
    if (!payload) return;

    setError(null);
    setLoading(true);
    try {
      const result = await apiFetch<ResetRequestResponse>('/auth/password-reset/request', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      setDone(result.detail);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Erro inesperado. Tente novamente.');
      // O token do Turnstile é de uso único: qualquer erro exige novo desafio.
      setCaptchaToken(null);
      setCaptchaReset((n) => n + 1);
    } finally {
      setLoading(false);
    }
  }

  return (
    <PublicAuthShell title="Esqueci minha senha" subtitle="Enviaremos um link de redefinição por e-mail">
      {done ? (
        <Stack gap="md">
          <Alert color="green" title="Solicitação recebida" icon={<IconMailCheck size={18} />}>
            {done}
          </Alert>
          <Anchor component={Link} href="/" size="sm">
            Voltar para o login
          </Anchor>
        </Stack>
      ) : (
        <form onSubmit={handleSubmit}>
          <Stack gap="md">
            <TextInput
              label="Usuário ou e-mail"
              placeholder="seu.usuario ou voce@exemplo.com"
              value={identifier}
              onChange={(event) => setIdentifier(event.currentTarget.value)}
              required
              autoComplete="username"
              disabled={loading}
            />

            {captchaConfigured ? (
              <TurnstileWidget siteKey={TURNSTILE_SITE_KEY} onToken={setCaptchaToken} resetKey={captchaReset} />
            ) : (
              <Alert color="yellow" title="Verificação de segurança indisponível" icon={<IconAlertCircle size={18} />}>
                O captcha não está configurado neste ambiente. Não é possível solicitar a redefinição.
              </Alert>
            )}

            {error && (
              <Alert color="red" title="Não foi possível enviar" icon={<IconAlertCircle size={18} />}>
                {error}
              </Alert>
            )}

            <Button
              type="submit"
              fullWidth
              loading={loading}
              disabled={!canSubmit}
              variant="gradient"
              gradient={{ from: 'brand.5', to: 'brandRed.6', deg: 135 }}
            >
              Enviar link de redefinição
            </Button>

            <Text size="sm" c="dimmed" ta="center">
              Lembrou a senha?{' '}
              <Anchor component={Link} href="/" size="sm">
                Entrar
              </Anchor>
            </Text>
          </Stack>
        </form>
      )}
    </PublicAuthShell>
  );
}
