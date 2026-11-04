import { describe, expect, it } from 'vitest';
import { isTextEntry } from './keyboard';

describe('isTextEntry (MB-8)', () => {
  it('campos de texto abrem o teclado', () => {
    expect(isTextEntry({ tagName: 'INPUT', type: 'text' })).toBe(true);
    expect(isTextEntry({ tagName: 'input', type: 'number' })).toBe(true);
    expect(isTextEntry({ tagName: 'INPUT' })).toBe(true);
    expect(isTextEntry({ tagName: 'TEXTAREA' })).toBe(true);
    expect(isTextEntry({ tagName: 'DIV', isContentEditable: true })).toBe(true);
  });
  it('readOnly, disabled e inputMode none nao contam (Select do Mantine e readOnly)', () => {
    expect(isTextEntry({ tagName: 'INPUT', type: 'text', readOnly: true })).toBe(false);
    expect(isTextEntry({ tagName: 'INPUT', type: 'text', disabled: true })).toBe(false);
    expect(isTextEntry({ tagName: 'INPUT', type: 'text', inputMode: 'none' })).toBe(false);
    expect(isTextEntry({ tagName: 'TEXTAREA', readOnly: true })).toBe(false);
    expect(isTextEntry({ tagName: 'INPUT', type: 'text', inputMode: 'numeric' })).toBe(true);
  });
  it('controles sem teclado não escondem a barra', () => {
    for (const type of ['checkbox', 'radio', 'button', 'submit', 'range', 'file']) {
      expect(isTextEntry({ tagName: 'INPUT', type }), type).toBe(false);
    }
    expect(isTextEntry({ tagName: 'BUTTON' })).toBe(false);
    expect(isTextEntry({ tagName: 'A' })).toBe(false);
    expect(isTextEntry(null)).toBe(false);
  });
});
