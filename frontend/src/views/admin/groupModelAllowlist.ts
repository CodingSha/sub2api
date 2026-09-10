export interface ModelAllowlistConfig {
  enabled: boolean
  models: string[]
  multimodal_models?: string[]
}

export interface ModelAllowlistItem {
  id: string
  selected: boolean
  multimodal: boolean
}

export interface ModelAllowlistState {
  enabled: boolean
  savedModels: string[]
  savedMultimodalModels: string[]
  items: ModelAllowlistItem[]
}

// 自定义条目校验错误码，由视图映射为 i18n 提示。
export type ModelAllowlistAddError = 'empty' | 'invalid_wildcard' | 'duplicate'

export const createModelAllowlistState = (
  config?: Partial<ModelAllowlistConfig> | null,
): ModelAllowlistState => ({
  enabled: config?.enabled ?? false,
  savedModels: normalizeModels(config?.models ?? []),
  savedMultimodalModels: normalizeModels(config?.multimodal_models ?? []),
  items: [],
})

export const hydrateModelAllowlistState = (
  config: Partial<ModelAllowlistConfig> | null | undefined,
  candidates: string[],
): ModelAllowlistState => {
  const state = createModelAllowlistState(config)
  setModelAllowlistCandidates(state, candidates)
  return state
}

export const setModelAllowlistCandidates = (
  state: ModelAllowlistState,
  candidates: string[],
) => {
  const normalizedCandidates = normalizeModels(candidates)
  const currentSelected = new Set(
    state.items.filter(item => item.selected).map(item => item.id),
  )
  const currentKnown = new Set(state.items.map(item => item.id))
  const savedSelected = new Set(state.savedModels)
  const currentMultimodal = new Set(
    state.items.filter(item => item.multimodal).map(item => item.id),
  )
  const savedMultimodal = new Set(state.savedMultimodalModels)
  const hasExistingItems = state.items.length > 0
  const selectionOrder = normalizeModels([
    ...state.items.map(item => item.id),
    ...state.savedModels,
    ...normalizedCandidates,
  ])

  state.items = selectionOrder.map(id => {
    const selected = hasExistingItems
      ? currentSelected.has(id)
      : state.savedModels.length > 0
        ? savedSelected.has(id)
        : normalizedCandidates.includes(id)

    return {
      id,
      selected: selected && (currentKnown.has(id) || savedSelected.has(id) || state.savedModels.length === 0),
      multimodal: hasExistingItems ? currentMultimodal.has(id) : savedMultimodal.has(id),
    }
  })
}

export const toggleModelAllowlistItem = (
  state: ModelAllowlistState,
  modelID: string,
) => {
  const item = state.items.find(item => item.id === modelID)
  if (item) {
    item.selected = !item.selected
  }
}

// toggleModelAllowlistItemMultimodal 切换某个白名单模型的多模态标记，
// 保存后以 multimodal_models 形式下发，用户侧透出为 available_model_flags。
export const toggleModelAllowlistItemMultimodal = (
  state: ModelAllowlistState,
  modelID: string,
) => {
  const item = state.items.find(item => item.id === modelID)
  if (item) {
    item.multimodal = !item.multimodal
  }
}

export const selectAllModelAllowlistItems = (state: ModelAllowlistState) => {
  state.items.forEach(item => {
    item.selected = true
  })
}

export const invertModelAllowlistSelection = (state: ModelAllowlistState) => {
  state.items.forEach(item => {
    item.selected = !item.selected
  })
}

export const moveModelAllowlistItem = (
  state: ModelAllowlistState,
  fromIndex: number,
  toIndex: number,
) => {
  if (
    fromIndex === toIndex ||
    fromIndex < 0 ||
    toIndex < 0 ||
    fromIndex >= state.items.length ||
    toIndex >= state.items.length
  ) {
    return
  }
  const [item] = state.items.splice(fromIndex, 1)
  state.items.splice(toIndex, 0, item)
}

// addCustomModelAllowlistItem 把手工输入的条目追加到白名单末尾（选中状态）。
// 去重；`*` 只允许出现在末尾。返回错误码或 null（成功）。
export const addCustomModelAllowlistItem = (
  state: ModelAllowlistState,
  raw: string,
): ModelAllowlistAddError | null => {
  const entry = raw.trim()
  if (!entry) {
    return 'empty'
  }
  if (entry.slice(0, -1).includes('*')) {
    return 'invalid_wildcard'
  }
  if (
    state.items.some(item => item.id.toLowerCase() === entry.toLowerCase()) ||
    state.savedModels.some(model => model.toLowerCase() === entry.toLowerCase())
  ) {
    return 'duplicate'
  }
  state.items.push({ id: entry, selected: true, multimodal: false })
  return null
}

export const buildModelAllowlistConfig = (
  state: ModelAllowlistState,
): ModelAllowlistConfig => {
  const models = state.items.length > 0
    ? state.items.filter(item => item.selected).map(item => item.id)
    : [...state.savedModels]
  const multimodalModels = state.items.length > 0
    ? state.items.filter(item => item.multimodal).map(item => item.id)
    : [...state.savedMultimodalModels]

  return {
    enabled: state.enabled,
    models,
    ...(multimodalModels.length > 0 ? { multimodal_models: multimodalModels } : {}),
  }
}

export const selectedModelAllowlistCount = (state: ModelAllowlistState): number =>
  state.items.filter(item => item.selected).length

const normalizeModels = (models: string[]): string[] => {
  const seen = new Set<string>()
  const out: string[] = []
  for (const raw of models) {
    const model = raw.trim()
    if (!model || seen.has(model)) {
      continue
    }
    seen.add(model)
    out.push(model)
  }
  return out
}
