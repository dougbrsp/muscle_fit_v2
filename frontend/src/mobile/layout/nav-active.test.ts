import { describe, expect, it } from 'vitest';
import { registrarRota, origemEhInterna, voltarDoEditorNova, type OrigemRota } from './nav-active';

const vazio: OrigemRota = { primeira: null, atual: null, anterior: null };

describe('origem da entrada atual do histórico', () => {
  it('abertura direta de ?nova=1 é entrada direta: navega para a rota de origem, nunca history.back()', () => {
    const estado = registrarRota(vazio, '/nutricao/?nova=1');
    expect(origemEhInterna(estado)).toBe(false);
    expect(voltarDoEditorNova('/nutricao/', 'nova=1', origemEhInterna(estado))).toBe('replace');
  });

  it('efeito executado duas vezes na abertura direta (StrictMode) continua entrada direta', () => {
    const estado = registrarRota(registrarRota(vazio, '/nutricao/?nova=1'), '/nutricao/?nova=1');
    expect(origemEhInterna(estado)).toBe(false);
  });

  it('veio de dentro do aplicativo: volta no histórico', () => {
    const estado = registrarRota(registrarRota(vazio, '/dashboard/?'), '/nutricao/?nova=1');
    expect(origemEhInterna(estado)).toBe(true);
    expect(voltarDoEditorNova('/nutricao/', 'nova=1', true)).toBe('back');
  });

  it('direto em ?nova=1, outra aba, voltar do navegador a ?nova=1: continua entrada direta (replace)', () => {
    let estado = registrarRota(vazio, '/nutricao/?nova=1');
    estado = registrarRota(estado, '/dashboard/?');
    estado = registrarRota(estado, '/nutricao/?nova=1');
    expect(origemEhInterna(estado)).toBe(false);
    expect(voltarDoEditorNova('/nutricao/', 'nova=1', origemEhInterna(estado))).toBe('replace');
  });

  it('primeira = dashboard, vai a ?nova=1, volta ao dashboard e vai de novo a ?nova=1: interna (back)', () => {
    let estado = registrarRota(vazio, '/dashboard/?');
    estado = registrarRota(estado, '/nutricao/?nova=1');
    estado = registrarRota(estado, '/dashboard/?');
    estado = registrarRota(estado, '/nutricao/?nova=1');
    expect(origemEhInterna(estado)).toBe(true);
    expect(voltarDoEditorNova('/nutricao/', 'nova=1', origemEhInterna(estado))).toBe('back');
  });

  it('a origem vale por entrada: efeito em dobro depois da troca não a altera', () => {
    const a = registrarRota(registrarRota(vazio, '/dashboard/?'), '/nutricao/?nova=1');
    expect(registrarRota(a, '/nutricao/?nova=1')).toEqual(a);
  });
});
