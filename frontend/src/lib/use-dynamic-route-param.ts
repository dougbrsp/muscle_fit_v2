'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

/**
 * Slug único gerado por `generateStaticParams()` em toda rota dinâmica do app
 * (`/metas-ativacao/[id]`, `/modelos-treino/[id]`, `/planos-treino/[id]`,
 * `/registro-exercicios/[activityId]`).
 *
 * Com `output: 'export'` não existe servidor para pré-renderizar um HTML por
 * id real, então o build gera apenas `.../placeholder/index.html` e a
 * CloudFront Function `spa-index-rewrite` (ver
 * `infra/cloudfront-functions/spa-index-rewrite.js`) serve esse objeto para
 * qualquer id na URL.
 */
export const STATIC_EXPORT_PLACEHOLDER = 'placeholder';

/**
 * Resolve o valor REAL do segmento dinâmico da URL do navegador.
 *
 * Por que não basta `useParams()`: o HTML servido é o de `.../placeholder/`, e
 * o payload de Flight embutido nele carrega literalmente
 * `["id","placeholder","d"]` na `initialTree`. O App Router monta a árvore a
 * partir desse payload, então `useParams().id` devolve `"placeholder"` — e
 * NÃO o `"novo"`/`"123"` que está na barra de endereços. Consequência em
 * produção (app.musclefit.com.br): `isNew` ficava `false` e
 * `Number("placeholder")` virava `NaN`, a query de detalhe nunca era
 * habilitada e a tela ficava eternamente no `<Loader />` — em qualquer
 * navegador, não só no Safari.
 *
 * A leitura real precisa acontecer em `useEffect` (não durante o render) para
 * não divergir do HTML pré-renderizado e quebrar a hidratação. Por isso o hook
 * devolve `null` no primeiro render e o valor correto logo em seguida — quem
 * consome deve tratar `null` como "ainda resolvendo".
 *
 * @param paramValue valor vindo de `useParams()` (pode ser o placeholder).
 */
export function useDynamicRouteParam(paramValue: string | undefined): string | null {
  const isReal = !!paramValue && paramValue !== STATIC_EXPORT_PLACEHOLDER;
  const [resolved, setResolved] = useState<string | null>(isReal ? (paramValue as string) : null);
  // Só entra como dependência do efeito: numa navegação client-side entre dois
  // ids da MESMA rota o `paramValue` continua sendo o placeholder, então sem
  // isto o efeito não re-rodaria e o id anterior ficaria preso.
  const pathname = usePathname();

  useEffect(() => {
    if (isReal) {
      setResolved(paramValue as string);
      return;
    }
    if (typeof window === 'undefined') return;
    // Todas as rotas dinâmicas do app têm o formato `/<base>/<id>[/]`, então o
    // último segmento não-vazio do pathname é sempre o parâmetro. Lê-se de
    // `window.location` e não de `usePathname()` porque este pode refletir a
    // árvore do build (com o placeholder), não a barra de endereços.
    const segments = window.location.pathname.split('/').filter(Boolean);
    setResolved(segments.length ? segments[segments.length - 1] : null);
  }, [isReal, paramValue, pathname]);

  return resolved;
}
