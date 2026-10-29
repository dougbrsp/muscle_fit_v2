import { Alert, Button, Card, PasswordInput, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconCheck } from '@tabler/icons-react';
import type { FormEvent, Ref } from 'react';
import { passwordChangeErrorMessage } from '@/lib/password-change';
import { BRAND } from '@/theme';

export interface PasswordChangeCardProps {
  /** Troca obrigatória (`must_change_password`): a "senha atual" é a temporária recebida. */
  forced: boolean;
  minLength: number;
  currentPassword: string;
  newPassword: string;
  onCurrentPasswordChange: (value: string) => void;
  onNewPasswordChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  /** Erro de validação local (ex.: senha curta). */
  validationError: string | null;
  /** Erro da mutação (`changePasswordMutation.error`); `null` sem erro. */
  error: unknown;
  pending: boolean;
  success: boolean;
  cardRef?: Ref<HTMLDivElement>;
}

/**
 * Card "Trocar senha" do perfil (apresentação pura — o estado e a mutação
 * ficam em `PerfilClient`). Mostra SEMPRE o erro da API junto do formulário:
 * antes, a senha atual errada respondia 401, o `apiFetch` redirecionava e o
 * usuário da troca obrigatória nunca via a mensagem (bug de 2026-10-10).
 */
export function PasswordChangeCard({
  forced,
  minLength,
  currentPassword,
  newPassword,
  onCurrentPasswordChange,
  onNewPasswordChange,
  onSubmit,
  validationError,
  error,
  pending,
  success,
  cardRef,
}: PasswordChangeCardProps) {
  const highlight = forced && !success;

  return (
    <Card
      ref={cardRef}
      withBorder
      radius="lg"
      p="lg"
      style={{
        background: BRAND.bgElevated,
        borderColor: highlight ? '#F5A623' : BRAND.border,
      }}
    >
      <Stack gap="md">
        <Title order={4} c={BRAND.textPrimary}>
          Trocar senha
        </Title>
        <form onSubmit={onSubmit}>
          <Stack gap="md">
            <PasswordInput
              label={highlight ? 'Senha temporária (a que você recebeu)' : 'Senha atual'}
              description={
                highlight
                  ? 'Digite a senha temporária enviada pelo administrador ou por e-mail — não a senha nova.'
                  : undefined
              }
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => onCurrentPasswordChange(event.currentTarget.value)}
              required
            />
            <PasswordInput
              label={`Nova senha (mínimo ${minLength} caracteres)`}
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => onNewPasswordChange(event.currentTarget.value)}
              minLength={minLength}
              required
            />

            {validationError && (
              <Alert color="red" icon={<IconAlertTriangle />}>
                {validationError}
              </Alert>
            )}
            {error != null && !pending && (
              <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao trocar senha" role="alert">
                {passwordChangeErrorMessage(error)}
              </Alert>
            )}
            {success && !pending && (
              <Alert color="green" icon={<IconCheck />}>
                <Text size="sm">
                  Senha alterada com sucesso.
                  {forced ? ' A senha temporária deixou de valer; use a nova nos próximos logins.' : ''}
                </Text>
              </Alert>
            )}

            <Button type="submit" color="brand" loading={pending}>
              Trocar senha
            </Button>
          </Stack>
        </form>
      </Stack>
    </Card>
  );
}
