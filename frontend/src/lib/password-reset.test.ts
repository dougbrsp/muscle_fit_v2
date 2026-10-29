import { describe, expect, it } from 'vitest';
import { buildPasswordResetRequestPayload, readResetToken, validateNewPassword } from './password-reset';

describe('buildPasswordResetRequestPayload', () => {
  it('identificador com @ vira email (minúsculo) e sempre leva o captcha', () => {
    expect(buildPasswordResetRequestPayload('  Doug@Example.com ', 'cf-tok')).toEqual({
      email: 'doug@example.com',
      captcha_token: 'cf-tok',
    });
  });

  it('sem @ vira username, preservando maiúsculas', () => {
    expect(buildPasswordResetRequestPayload('Doug', 'cf-tok')).toEqual({ username: 'Doug', captcha_token: 'cf-tok' });
  });

  it('vazio ou só espaços devolve null', () => {
    expect(buildPasswordResetRequestPayload('   ', 'cf-tok')).toBeNull();
  });
});

describe('readResetToken', () => {
  it('lê ?token= e ignora vazio/ausente', () => {
    expect(readResetToken(new URLSearchParams('token=abc_-123'))).toBe('abc_-123');
    expect(readResetToken(new URLSearchParams('token='))).toBeNull();
    expect(readResetToken(new URLSearchParams('x=1'))).toBeNull();
    expect(readResetToken(null)).toBeNull();
  });
});

describe('validateNewPassword', () => {
  it('exige o mínimo da API e confirmação igual', () => {
    expect(validateNewPassword('curta', 'curta')).toMatch(/mínimo 8/);
    expect(validateNewPassword('SenhaNova123', 'outra')).toMatch(/não conferem/);
    expect(validateNewPassword('SenhaNova123', 'SenhaNova123')).toBeNull();
  });
});
