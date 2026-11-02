import { describe, expect, it } from 'vitest';

import { hasActiveWorkProviders, registerActiveWorkProvider } from './active-work';

describe('hasActiveWorkProviders', () => {
  it('reflete o registro e a remoção de provedores', () => {
    expect(hasActiveWorkProviders()).toBe(false);
    const offA = registerActiveWorkProvider(() => false);
    const offB = registerActiveWorkProvider(() => false);
    expect(hasActiveWorkProviders()).toBe(true);
    offA();
    expect(hasActiveWorkProviders()).toBe(true);
    offB();
    expect(hasActiveWorkProviders()).toBe(false);
  });
});
