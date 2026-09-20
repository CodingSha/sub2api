import { describe, expect, it } from 'vitest'
import { getGroupAvailableModels, isGroupAvailableModelMultimodal } from '../groupModels'

describe('groupModels', () => {
  it('uses group account configured models', () => {
    const models = getGroupAvailableModels({
      available_models: ['gpt-5.4', 'gpt-5.4', ' gpt-image-2 ']
    })

    expect(models).toEqual(['gpt-5.4', 'gpt-image-2'])
  })

  it('returns an empty list when the group has no configured models', () => {
    expect(getGroupAvailableModels({ available_models: [] })).toEqual([])
  })

  it('reads multimodal flags by model name', () => {
    const group = {
      available_models: ['gpt-5.4', 'gpt-image-2'],
      available_model_flags: {
        'gpt-image-2': ['multimodal']
      }
    }

    expect(isGroupAvailableModelMultimodal(group, 'gpt-image-2')).toBe(true)
    expect(isGroupAvailableModelMultimodal(group, 'gpt-5.4')).toBe(false)
  })
})
