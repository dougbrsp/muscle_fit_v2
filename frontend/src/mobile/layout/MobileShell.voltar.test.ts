import { describe, expect, it } from 'vitest';
import { acaoVoltarCabecalho } from './nav-active';

// O projeto não monta o MobileShell em DOM (next/navigation, Mantine, registry de telas); o handler do
// clique no voltar do cabeçalho foi extraído para `acaoVoltarCabecalho` e o shell só executa o resultado.
const base = { pathname: '/nutricao/', search: 'nova=1', origemInterna: false, temVisaoAberta: false };

describe('clique no voltar do cabeçalho', () => {
  it('?nova=1 aberto direto navega para /nutricao/ (replace), sem sair do aplicativo', () => {
    expect(acaoVoltarCabecalho(base)).toBe('replace-nutricao');
  });
  it('?nova=1 vindo de dentro do aplicativo volta no histórico', () => {
    expect(acaoVoltarCabecalho({ ...base, origemInterna: true })).toBe('back');
  });
  it('visão de página inteira aberta tem prioridade: volta no histórico', () => {
    expect(acaoVoltarCabecalho({ ...base, temVisaoAberta: true })).toBe('back');
  });
  it('fora do editor ?nova=1 abre a folha Mais', () => {
    expect(acaoVoltarCabecalho({ ...base, search: '' })).toBe('abrir-mais');
    expect(acaoVoltarCabecalho({ ...base, pathname: '/treinos/', origemInterna: true })).toBe('abrir-mais');
  });
});
