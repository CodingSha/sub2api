import { describe, expect, it } from 'vitest'
import { getGroupAvailableModels } from '../groupModels'

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

})
