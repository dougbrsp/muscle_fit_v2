// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest';
import { clearRecipeDrafts, RECIPE_DRAFT_PREFIX } from './recipe-draft-storage';

describe('clearRecipeDrafts', () => {
  beforeEach(() => window.localStorage.clear());

  it('remove só as chaves com o prefixo', () => {
    window.localStorage.setItem(`${RECIPE_DRAFT_PREFIX}7:new`, '{}');
    window.localStorage.setItem(`${RECIPE_DRAFT_PREFIX}7:3`, '{}');
    window.localStorage.setItem('outra', 'x');
    clearRecipeDrafts();
    expect(window.localStorage.length).toBe(1);
    expect(window.localStorage.getItem('outra')).toBe('x');
  });
});
