'use client';

import { useEffect } from 'react';
import { BRAND } from '@/theme';

/**
 * Última rede de proteção (M56): erro no layout RAIZ (`src/app/layout.tsx` /
 * `Providers`), que nenhum `error.tsx` alcança. Substitui o layout raiz
 * inteiro, então traz o próprio `<html>`/`<body>` e NÃO pode usar Mantine (o
 * `MantineProvider` está justamente no layout que falhou) — por isso estilos
 * inline com os tokens da marca.
 *
 * Erro de uma TELA autenticada continua no `(app)/error.tsx`, dentro do app
 * shell; este arquivo só aparece se o próprio esqueleto do app quebrar.
 * Compatível com `output: 'export'` (componente cliente, sem servidor). O Next
 * só o usa no build de produção — em `next dev` aparece o overlay de erro.
 */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="pt-BR">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 16,
          background: BRAND.bg,
          color: BRAND.textPrimary,
          fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        }}
      >
        <div
          role="alert"
          style={{
            maxWidth: 480,
            width: '100%',
            padding: 24,
            borderRadius: 12,
            border: `1px solid ${BRAND.border}`,
            background: BRAND.bgElevated,
          }}
        >
          <h1 style={{ fontSize: 20, margin: '0 0 8px' }}>Algo deu errado</h1>
          <p style={{ margin: '0 0 16px', color: BRAND.textMuted, lineHeight: 1.5 }}>
            Um erro inesperado impediu o MuscleFit de carregar. Você continua conectado — tente de novo.
          </p>
          <button
            type="button"
            onClick={reset}
            style={{
              border: 0,
              borderRadius: 8,
              padding: '10px 18px',
              fontWeight: 600,
              fontSize: 15,
              cursor: 'pointer',
              color: '#fff',
              background: `linear-gradient(${BRAND.gradient.deg}deg, ${BRAND.gradient.from}, ${BRAND.gradient.to})`,
            }}
          >
            Tentar de novo
          </button>
        </div>
      </body>
    </html>
  );
}
