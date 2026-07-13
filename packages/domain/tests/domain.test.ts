import { describe, expect, it } from 'vitest';
import {
  compilePrompt,
  copyPresetForEditing,
  recommendModelPack,
  selectDefaultModel,
  transitionComponent,
  type Preset,
} from '../src/index';

const builtin: Preset = {
  id: 'builtin-light',
  category: 'lighting',
  name: 'Window',
  prompt: 'soft window light',
  negativePrompt: '',
  tags: [],
  favorite: false,
  origin: 'builtin',
  scope: 'global',
  sourcePresetId: null,
};

describe('preset editing', () => {
  it('copies built-ins into user ownership without mutating the source', () => {
    const copy = copyPresetForEditing(builtin, 'user-copy');
    expect(copy).toMatchObject({
      id: 'user-copy',
      origin: 'user',
      sourcePresetId: 'builtin-light',
    });
    expect(builtin.origin).toBe('builtin');
  });
});

describe('prompt compilation', () => {
  it('preserves recipe order and combines freeform tags', () => {
    const result = compilePrompt({
      name: 'Test',
      selections: [{ category: 'lighting', preset: builtin }],
      freeform: 'portrait',
      negative: 'blur',
      tags: ['film grain'],
    });
    expect(result.positive).toBe('soft window light, portrait, film grain');
    expect(result.negative).toBe('blur');
  });
});

describe('engine state transitions', () => {
  it('allows installation completion and rejects impossible transitions', () => {
    expect(transitionComponent('installing', 'ready')).toBe('ready');
    expect(() => transitionComponent('unsupported', 'ready')).toThrow();
  });
});

describe('model selection and hardware recommendations', () => {
  const packs = [
    {
      id: 'preview',
      alias: 'preview_fast',
      family: 'sdxl',
      minVramGb: 6,
      diskGb: 8,
      recommendedVramGb: 8,
    },
    {
      id: 'balanced',
      alias: 'photoreal_balanced',
      family: 'sdxl',
      minVramGb: 10,
      diskGb: 14,
      recommendedVramGb: 12,
    },
    {
      id: 'maximum',
      alias: 'photoreal_max',
      family: 'flux',
      minVramGb: 16,
      diskGb: 24,
      recommendedVramGb: 24,
    },
  ];
  it('recommends the balanced alias for a 12 GB GPU', () => {
    expect(
      recommendModelPack(
        { gpuName: 'RTX 4070 Super', vramGb: 12, ramGb: 32, freeDiskGb: 100 },
        packs,
      )?.alias,
    ).toBe('photoreal_balanced');
  });
  it('only selects an installed model pack', () => {
    expect(selectDefaultModel(null, packs[1]!, ['photoreal_balanced'])).toBe('photoreal_balanced');
    expect(() => selectDefaultModel(null, packs[2]!, [])).toThrow();
  });
});
