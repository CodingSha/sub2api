import type { Group } from '@/types'

type GroupModelSource = Pick<Group, 'available_models' | 'available_model_flags'>

const normalizeModels = (models: string[] | undefined): string[] => {
  const seen = new Set<string>()
  const normalized: string[] = []
  for (const rawModel of models || []) {
    const model = rawModel.trim()
    if (!model || seen.has(model)) continue
    seen.add(model)
    normalized.push(model)
  }
  return normalized
}

export function getGroupAvailableModels(
  group: GroupModelSource | null | undefined
): string[] {
  if (!group) return []
  return normalizeModels(group.available_models)
}

export function isGroupAvailableModelMultimodal(
  group: GroupModelSource | null | undefined,
  model: string
): boolean {
  const flags = group?.available_model_flags?.[model]
  return Array.isArray(flags) && flags.includes('multimodal')
}
