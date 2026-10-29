'use client';

import { Group, Loader } from '@mantine/core';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

type LegacyPathRedirectProps = {
  /** Prefixo antigo, sem barra final (ex.: `/tipos-treinamento`). */
  from: string;
  /** Prefixo novo, sem barra final (ex.: `/metas-ativacao`). */
  to: string;
};

/**
 * Redireciona client-side um caminho legado para o novo prefixo, preservando
 * o restante do path (ex.: o id real de uma rota dinâmica), a query string e
 * o hash.
 *
 * Lê de `window.location` (e não de `useParams()`/`usePathname()`) pelo mesmo
 * motivo de `useDynamicRouteParam`: no static export o HTML servido é o de
 * `.../placeholder/`, então os hooks do App Router devolvem o placeholder e
 * não o id da barra de endereços.
 *
 * Em produção o redirect principal é um 301 na CloudFront Function
 * `spa-index-rewrite`; este componente é o fallback para ambientes sem ela
 * (ex.: `next dev` local).
 */
export function LegacyPathRedirect({ from, to }: LegacyPathRedirectProps) {
  const router = useRouter();

  useEffect(() => {
    const { pathname, search, hash } = window.location;
    const rest = pathname.startsWith(from) ? pathname.slice(from.length) : '';
    const target = `${to}${rest.startsWith('/') ? rest : `/${rest}`}${search}${hash}`;
    router.replace(target);
  }, [from, to, router]);

  return (
    <Group justify="center" py="xl">
      <Loader color="brand" />
    </Group>
  );
}
