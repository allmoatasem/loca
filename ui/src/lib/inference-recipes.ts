/**
 * Inference recipe presets — mirror Loca-SwiftUI/Sources/Loca/Backend/Models.swift
 * (struct InferenceRecipe). When adding a preset, update both files.
 */
export interface InferenceRecipe {
  name: string;
  temperature: number;
  top_p: number;
  top_k: number;
  repeat_penalty: number;
  max_tokens: number;
}

export const RECIPES: readonly InferenceRecipe[] = [
  { name: 'Balanced', temperature: 0.70, top_p: 0.90, top_k: 40, repeat_penalty: 1.10, max_tokens: 2048 },
  { name: 'Creative', temperature: 1.00, top_p: 0.95, top_k: 50, repeat_penalty: 1.00, max_tokens: 2048 },
  { name: 'Precise',  temperature: 0.20, top_p: 0.80, top_k: 20, repeat_penalty: 1.15, max_tokens: 2048 },
  { name: 'Fast',     temperature: 0.70, top_p: 0.90, top_k: 40, repeat_penalty: 1.10, max_tokens: 512  },
  { name: 'Custom',   temperature: 0.70, top_p: 0.90, top_k: 40, repeat_penalty: 1.10, max_tokens: 2048 },
];

export function recipeByName(name: string): InferenceRecipe {
  return RECIPES.find((r) => r.name === name) ?? RECIPES[0];
}
