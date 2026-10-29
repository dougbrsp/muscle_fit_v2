import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ApiError } from '@/lib/api';
import { theme } from '@/theme';
import { PasswordChangeCard, type PasswordChangeCardProps } from './PasswordChangeCard';

/** Card "Trocar senha" renderizado via `react-dom/server` (sem jsdom). */

const noop = () => {};

function render(overrides: Partial<PasswordChangeCardProps> = {}): string {
  const props: PasswordChangeCardProps = {
    forced: false,
    minLength: 8,
    currentPassword: '',
    newPassword: '',
    onCurrentPasswordChange: noop,
    onNewPasswordChange: noop,
    onSubmit: noop,
    validationError: null,
    error: null,
    pending: false,
    success: false,
    ...overrides,
  };
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, createElement(PasswordChangeCard, props)));
}

describe('PasswordChangeCard', () => {
  it('mostra "Senha atual incorreta" junto do formulário', () => {
    const html = render({ forced: true, error: new ApiError('Senha atual incorreta.', 400) });
    expect(html).toContain('Erro ao trocar senha');
    expect(html).toContain('Senha atual incorreta.');
  });

  it('mostra qualquer outro erro da API (ex.: conta bloqueada)', () => {
    const html = render({ error: new ApiError('A conta está bloqueada por até 15 minutos.', 429) });
    expect(html).toContain('A conta está bloqueada por até 15 minutos.');
  });

  it('erro sem ApiError usa a mensagem de reserva', () => {
    expect(render({ error: new Error('x') })).toContain('Não foi possível trocar a senha.');
  });

  it('sem erro não mostra alerta de erro; durante o envio também não', () => {
    expect(render()).not.toContain('Erro ao trocar senha');
    expect(render({ error: new ApiError('Senha atual incorreta.', 400), pending: true })).not.toContain(
      'Erro ao trocar senha',
    );
  });

  it('troca obrigatória: o campo deixa claro que é a senha temporária recebida', () => {
    const html = render({ forced: true });
    expect(html).toContain('Senha temporária (a que você recebeu)');
    expect(html).toContain('não a senha nova');
  });

  it('troca normal: campo "Senha atual" sem a explicação da temporária', () => {
    const html = render();
    expect(html).toContain('Senha atual');
    expect(html).not.toContain('Senha temporária');
  });

  it('sucesso na troca obrigatória confirma que a temporária deixou de valer', () => {
    const html = render({ forced: true, success: true });
    expect(html).toContain('Senha alterada com sucesso.');
    expect(html).toContain('A senha temporária deixou de valer');
    expect(html).not.toContain('Senha temporária (a que você recebeu)');
  });
});
