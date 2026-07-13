import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const tokenCss = readFileSync(new URL('../src/tokens.css', import.meta.url), 'utf8');

describe('Vanta editorial color tokens', () => {
  it.each([
    ['--v-bg', '#09090d'],
    ['--v-bg-raised', '#121218'],
    ['--v-panel-raised', '#191920'],
    ['--v-line', '#2a2830'],
    ['--v-accent', '#f0449b'],
    ['--v-accent-bright', '#ff5fae'],
    ['--v-accent-muted', '#c94e87'],
    ['--v-plum', '#702a63'],
    ['--v-text', '#f7f3f6'],
    ['--v-muted', '#a7a0a9'],
    ['--v-success', '#6fd6a7'],
    ['--v-warning', '#f0b85a'],
    ['--v-danger', '#f06478'],
  ])('defines %s as %s', (name, value) => {
    expect(tokenCss).toContain(`${name}: ${value};`);
  });
});
