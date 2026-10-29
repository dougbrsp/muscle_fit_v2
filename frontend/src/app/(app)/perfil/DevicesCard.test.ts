import { MantineProvider } from '@mantine/core';
import { createElement, type FunctionComponent } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { theme } from '@/theme';
import { DeviceTokenReveal, DevicesList, platformLabel, type DeviceItem } from './DevicesCard';

/** Seção "Dispositivos" do perfil (M40) renderizada via `react-dom/server` (sem jsdom). */

const noop = () => {};

function render<P extends object>(element: FunctionComponent<P>, props: P): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, createElement(element, props)));
}

const ATIVO: DeviceItem = {
  id: 1,
  device_name: 'Amazfit Balance',
  platform: 'zepp',
  paired_at: '2026-10-18 10:00:00',
  last_seen_at: null,
  revoked_at: null,
  status: 'active',
};

const REVOGADO: DeviceItem = {
  ...ATIVO,
  id: 2,
  device_name: 'Relógio antigo',
  revoked_at: '2026-10-18 11:00:00',
  status: 'revoked',
};

describe('DevicesList', () => {
  it('mostra nome, plataforma, status e só oferece revogar o ativo', () => {
    const html = render(DevicesList, { items: [ATIVO, REVOGADO], onRevoke: noop });
    expect(html).toContain('Amazfit Balance');
    expect(html).toContain('Relógio antigo');
    expect(html).toContain('Zepp OS (Amazfit)');
    expect(html).toContain('Ativo');
    expect(html).toContain('Revogado');
    expect(html.match(/>Revogar</g)).toHaveLength(1);
  });

  it('status vem da API, não é recalculado na tela', () => {
    const html = render(DevicesList, { items: [{ ...ATIVO, status: 'revoked' }], onRevoke: noop });
    expect(html).not.toContain('>Revogar<');
  });

  it('lista vazia', () => {
    expect(render(DevicesList, { items: [], onRevoke: noop })).toContain('Nenhum dispositivo pareado.');
  });
});

describe('DeviceTokenReveal', () => {
  it('exibe o token, o botão de copiar e o passo a passo do app Zepp', () => {
    const html = render(DeviceTokenReveal, {
      device: { device_id: 3, device_name: 'Balance', platform: 'zepp', device_token: 'mfd_abc123' },
      onDone: noop,
    });
    expect(html).toContain('mfd_abc123');
    expect(html).toContain('Copiar token');
    expect(html).toContain('aparece só desta vez');
    expect(html).toContain('Abra o app Zepp');
    expect(html).toContain('MuscleFit Treino → Configurações');
    expect(html).toContain('Testar conexão');
  });
});

describe('platformLabel', () => {
  it('plataforma desconhecida aparece como veio', () => {
    expect(platformLabel('wearos')).toBe('wearos');
  });
});
