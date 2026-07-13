import { z } from 'zod';

export const presetCategories = [
  'identity_modifier',
  'wardrobe',
  'expression',
  'pose',
  'location',
  'lighting',
  'camera',
  'quality',
  'negative',
  'motion',
] as const;
export type PresetCategory = (typeof presetCategories)[number];
export type Origin = 'builtin' | 'user';
export type Scope = 'global' | 'character' | 'project';
export type ComponentState =
  'not_installed' | 'installing' | 'ready' | 'update_available' | 'repair_needed' | 'unsupported';
export type ModelPackState = ComponentState | 'paused' | 'verifying';

export const presetSchema = z.object({
  id: z.string().min(1),
  category: z.enum(presetCategories),
  name: z.string().min(1).max(100),
  prompt: z.string().max(8000),
  negativePrompt: z.string().max(8000).default(''),
  tags: z.array(z.string().min(1)).default([]),
  favorite: z.boolean().default(false),
  origin: z.enum(['builtin', 'user']),
  scope: z.enum(['global', 'character', 'project']).default('global'),
  sourcePresetId: z.string().nullable().default(null),
});
export type Preset = z.infer<typeof presetSchema>;

export interface Character {
  id: string;
  name: string;
  identityDescription: string;
  archived: boolean;
  defaultRecipeId: string | null;
  defaultModelProfile: string;
  referenceAssets: string[];
}

export interface RecipeSelection {
  category: PresetCategory;
  preset: Preset;
}
export interface PromptRecipe {
  id?: string;
  name: string;
  selections: RecipeSelection[];
  freeform: string;
  negative: string;
  tags: string[];
}

export interface CompiledPrompt {
  positive: string;
  negative: string;
  presetIds: string[];
}

export function copyPresetForEditing(preset: Preset, id: string): Preset {
  if (preset.origin === 'user') return { ...preset };
  return {
    ...preset,
    id,
    name: `${preset.name} — Copy`,
    origin: 'user',
    sourcePresetId: preset.id,
  };
}

export function compilePrompt(recipe: PromptRecipe): CompiledPrompt {
  const positive = recipe.selections.map(({ preset }) => preset.prompt.trim()).filter(Boolean);
  if (recipe.freeform.trim()) positive.push(recipe.freeform.trim());
  if (recipe.tags.length) positive.push(recipe.tags.join(', '));
  const negative = recipe.selections
    .map(({ preset }) => preset.negativePrompt.trim())
    .filter(Boolean)
    .concat(recipe.negative.trim() ? [recipe.negative.trim()] : [])
    .join(', ');
  return {
    positive: positive.join(', '),
    negative,
    presetIds: recipe.selections.map(({ preset }) => preset.id),
  };
}

const transitions: Record<ComponentState, readonly ComponentState[]> = {
  not_installed: ['installing', 'unsupported'],
  installing: ['ready', 'not_installed', 'repair_needed'],
  ready: ['update_available', 'repair_needed', 'not_installed'],
  update_available: ['installing', 'ready'],
  repair_needed: ['installing', 'not_installed'],
  unsupported: ['not_installed'],
};

export function transitionComponent(from: ComponentState, to: ComponentState): ComponentState {
  if (!transitions[from].includes(to))
    throw new Error(`Invalid component transition: ${from} → ${to}`);
  return to;
}

export interface HardwareProfile {
  gpuName: string;
  vramGb: number;
  ramGb: number;
  freeDiskGb: number;
}
export interface ModelPackSummary {
  id: string;
  alias: string;
  family: string;
  minVramGb: number;
  diskGb: number;
  recommendedVramGb: number;
}

export function recommendModelPack(
  hardware: HardwareProfile,
  packs: ModelPackSummary[],
): ModelPackSummary | null {
  const compatible = packs.filter(
    (pack) => hardware.vramGb >= pack.minVramGb && hardware.freeDiskGb >= pack.diskGb,
  );
  return (
    compatible.sort((a, b) => {
      const aFit =
        a.alias === 'photoreal_balanced' && hardware.vramGb >= a.recommendedVramGb
          ? 100
          : a.recommendedVramGb;
      const bFit =
        b.alias === 'photoreal_balanced' && hardware.vramGb >= b.recommendedVramGb
          ? 100
          : b.recommendedVramGb;
      return bFit - aFit;
    })[0] ?? null
  );
}

export function selectDefaultModel(
  current: string | null,
  next: ModelPackSummary,
  installedAliases: string[],
): string {
  if (!installedAliases.includes(next.alias))
    throw new Error('Model pack must be installed before it can be selected');
  return current === next.alias ? current : next.alias;
}

export interface ApiEnvelope<T> {
  data: T;
  message?: string;
}
