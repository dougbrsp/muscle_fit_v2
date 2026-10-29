import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import './globals.css';

import type { Metadata, Viewport } from 'next';
import { ColorSchemeScript, mantineHtmlProps } from '@mantine/core';
import { Providers } from './providers';

export const metadata: Metadata = {
  title: 'MuscleFit',
  description: 'MuscleFit — AI Powered Workout',
  // App instalável ("Adicionar à Tela de Início" no iOS). Declarado no layout
  // RAIZ para valer em todas as rotas, com caminhos absolutos. Sem manifest, o
  // iOS usa a URL da página onde o atalho foi criado como start_url e o
  // diretório dela como escopo (ex.: /dashboard/), então navegar para fora
  // dele reabre a barra do Safari. O manifest fixa start_url/scope em "/".
  // Ícones gerados de public/logo.svg: `npm run icons:generate`.
  manifest: '/manifest.json',
  applicationName: 'MuscleFit',
  appleWebApp: { capable: true, title: 'MuscleFit', statusBarStyle: 'black' },
  // O favicon continua vindo de src/app/icon.svg (convenção de arquivo do Next).
  icons: { apple: [{ url: '/apple-touch-icon.png', sizes: '180x180', type: 'image/png' }] },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#0a0a0a',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR" {...mantineHtmlProps}>
      <head>
        <ColorSchemeScript defaultColorScheme="dark" />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
