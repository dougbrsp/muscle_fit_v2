import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './api';
import {
  CURRENT_USER_QUERY_KEY,
  PASSWORD_CHANGE_FALLBACK_ERROR,
  passwordChangeErrorMessage,
  refreshAfterPasswordChange,
} from './password-change';

describe('passwordChangeErrorMessage', () => {
  it('usa o detail da API (ex.: senha atual incorreta, conta bloqueada)', () => {
    expect(passwordChangeErrorMessage(new ApiError('Senha atual incorreta.', 400))).toBe('Senha atual incorreta.');
    expect(passwordChangeErrorMessage(new ApiError('Conta bloqueada.', 429))).toBe('Conta bloqueada.');
  });

  it('erro sem ApiError cai na mensagem de reserva', () => {
    expect(passwordChangeErrorMessage(new Error('render'))).toBe(PASSWORD_CHANGE_FALLBACK_ERROR);
  });
});

describe('refreshAfterPasswordChange', () => {
  const unsubscribers: Array<() => void> = [];

  afterEach(() => {
    unsubscribers.splice(0).forEach((unsubscribe) => unsubscribe());
  });

  function observe<T>(queryClient: QueryClient, queryKey: readonly unknown[], queryFn: () => Promise<T>) {
    const observer = new QueryObserver(queryClient, { queryKey, queryFn, retry: false });
    unsubscribers.push(observer.subscribe(() => {}));
    return observer;
  }

  async function waitSettled(observer: { getCurrentResult(): { fetchStatus: string } }) {
    await vi.waitFor(() => expect(observer.getCurrentResult().fetchStatus).toBe('idle'));
  }

  it('refaz GET /auth/me: must_change_password passa a false', async () => {
    const queryClient = new QueryClient();
    let mustChange = true;
    const fetchMe = vi.fn(async () => ({ id: 7, must_change_password: mustChange }));
    const me = observe(queryClient, CURRENT_USER_QUERY_KEY, fetchMe);
    await waitSettled(me);
    expect(me.getCurrentResult().data).toEqual({ id: 7, must_change_password: true });

    mustChange = false; // o backend zerou na troca
    await refreshAfterPasswordChange(queryClient);

    expect(fetchMe).toHaveBeenCalledTimes(2);
    expect(me.getCurrentResult().data).toEqual({ id: 7, must_change_password: false });
  });

  it('refaz as consultas que falharam (403 da troca pendente) e não toca nas que deram certo', async () => {
    const queryClient = new QueryClient();
    const fetchPlans = vi
      .fn<() => Promise<string[]>>()
      .mockRejectedValueOnce(new ApiError('Troca de senha obrigatória.', 403))
      .mockResolvedValue(['plano']);
    const fetchProfile = vi.fn(async () => ({ item: { id: 7 } }));
    const plans = observe(queryClient, ['workout-plans'], fetchPlans);
    const profile = observe(queryClient, ['my-profile'], fetchProfile);
    await waitSettled(plans);
    await waitSettled(profile);
    expect(plans.getCurrentResult().status).toBe('error');

    await refreshAfterPasswordChange(queryClient);

    expect(fetchPlans).toHaveBeenCalledTimes(2);
    expect(plans.getCurrentResult().data).toEqual(['plano']);
    // não reseta o formulário do perfil com um refetch desnecessário
    expect(fetchProfile).toHaveBeenCalledTimes(1);
  });
});
