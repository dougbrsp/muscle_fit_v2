import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { MantineProvider } from '@mantine/core';
import { createElement, type ComponentProps } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { MEAL_PHOTO } from '@/lib/nutrition-constants';
import { theme } from '@/theme';
import { PhotoReading } from './MealParseModal';

// Foto do prato (L10) por SSR, como os demais testes de componente do projeto (sem jsdom).
function html(extra: Partial<ComponentProps<typeof PhotoReading>> = {}): string {
  const props: ComponentProps<typeof PhotoReading> = {
    slot: null,
    onSlotChange: vi.fn(),
    quota: { feature: 'meal_photo', limit: 5, used: 1, remaining: 4, allowed: true },
    busy: false,
    stage: null,
    noItems: [],
    error: null,
    onPick: vi.fn(),
    onClose: vi.fn(),
    ...extra,
  };
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, createElement(PhotoReading, props)));
}

function desabilitado(markup: string, rotulo: string): boolean {
  const botoes = markup.match(/<button[\s\S]*?<\/button>/g) ?? [];
  const botao = botoes.find((b) => b.includes(rotulo));
  expect(botao, rotulo).toBeTruthy();
  return /\sdisabled(=|\s|>)/.test((botao as string).slice(0, (botao as string).indexOf('>') + 1));
}

describe('PhotoReading', () => {
  it('mostra a cota da API, o prazo do dono e oferece câmera e galeria', () => {
    const markup = html();
    expect(markup).toContain('1 de 5');
    expect(markup).toContain(MEAL_PHOTO.retention_label);
    expect(markup).toContain('capture="environment"');
    expect(desabilitado(markup, 'Tirar foto')).toBe(false);
    expect(desabilitado(markup, 'Escolher da galeria')).toBe(false);
  });

  it('foto sem comida mostra o motivo do motor', () => {
    const markup = html({ noItems: [{ code: 'meal_photo_no_food', label: 'Não encontramos comida nesta foto.' }] });
    expect(markup).toContain('Não encontramos comida nesta foto.');
  });

  it('cota esgotada avisa e bloqueia o envio', () => {
    const markup = html({ quota: { feature: 'meal_photo', limit: 5, used: 5, remaining: 0, allowed: false } });
    expect(markup).toContain(MEAL_PHOTO.quota_exceeded_label);
    expect(desabilitado(markup, 'Tirar foto')).toBe(true);
    expect(desabilitado(markup, 'Escolher da galeria')).toBe(true);
  });

  it('erro mostra a mensagem e os itens, e continua permitindo outra foto', () => {
    const markup = html({ error: { message: 'Falhou.', items: [{ code: 'x', label: 'Detalhe do motor.' }] } });
    expect(markup).toContain('Falhou.');
    expect(markup).toContain('Detalhe do motor.');
    expect(desabilitado(markup, 'Tirar foto')).toBe(false);
  });

  it('durante o envio mostra a etapa e bloqueia cancelar e a galeria', () => {
    const markup = html({ busy: true, stage: 'reading' });
    expect(markup).toContain('Identificando os alimentos');
    expect(desabilitado(markup, 'Cancelar')).toBe(true);
    expect(desabilitado(markup, 'Escolher da galeria')).toBe(true);
  });
});

describe('MealParseModal: regressão de estado (sem DOM, o contrato fica no código)', () => {
  const fonte = readFileSync(resolve(__dirname, 'MealParseModal.tsx'), 'utf8');
  const componente = fonte.slice(fonte.indexOf('export function MealParseModal'), fonte.indexOf('type PhotoReadingProps'));

  it('estado e mutações ficam fora do <Modal>, que desmonta o conteúdo ao fechar', () => {
    const antesDoModal = componente.slice(0, componente.indexOf('<Modal'));
    for (const trecho of ['useState', 'useMutation', 'lerFoto', 'lerTexto']) expect(antesDoModal).toContain(trecho);
    const leitura = fonte.slice(fonte.indexOf('export function PhotoReading'));
    expect(leitura).not.toContain('useMutation');
  });

  it('com envio em andamento o modal não fecha por nenhum caminho', () => {
    expect(componente).toContain('if (enviando) return;');
    expect(componente).toContain('closeOnEscape={!enviando}');
    expect(componente).toContain('closeOnClickOutside={!enviando}');
    expect(componente).toContain('withCloseButton={!enviando}');
  });

  it('os dois FileButton reiniciam o input, para a mesma foto disparar de novo', () => {
    expect(fonte).toContain('resetRef={resetCamera}');
    expect(fonte).toContain('resetRef={resetGaleria}');
  });
});
