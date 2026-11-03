import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// Regressão (ajustes pós-teste, 2026-11-03): o seletor de aluno existe só na montagem do plano.
// O contrato é do código-fonte (sem DOM): quem importa `UserPicker` e quem fala `user_id`.

const SRC = resolve(__dirname, '..');

function arquivos(dir: string): string[] {
  return readdirSync(dir).flatMap((nome) => {
    const caminho = join(dir, nome);
    if (statSync(caminho).isDirectory()) return arquivos(caminho);
    return /\.(ts|tsx)$/.test(nome) && !/\.test\./.test(nome) ? [caminho] : [];
  });
}

const rel = (p: string) => relative(SRC, p).replace(/\\/g, '/');

describe('escolha de aluno só na montagem do plano', () => {
  const todos = arquivos(SRC);

  it('UserPicker é importado apenas por /nutricao/plano', () => {
    const usam = todos
      .filter((p) => !p.endsWith('UserPicker.tsx') && /\bUserPicker\b/.test(readFileSync(p, 'utf8')))
      .map(rel);
    expect(usam).toEqual(['app/(app)/nutricao/plano/PlanoClient.tsx']);
  });

  it('o diário e os componentes de refeição não falam user_id (URLs de check-in/painel: testes de chamada)', () => {
    const diario = readFileSync(join(SRC, 'lib/nutrition-api.ts'), 'utf8');
    const trecho = diario.slice(diario.indexOf('export const diaryKeys'), diario.indexOf('export function previewMeal'));
    expect(trecho).not.toMatch(/user_id/);
    for (const arq of ['components/nutrition/MealParseModal.tsx', 'components/nutrition/MealEditor.tsx']) {
      expect(readFileSync(join(SRC, arq), 'utf8'), arq).not.toMatch(/user_id|userId/);
    }
  });
});
