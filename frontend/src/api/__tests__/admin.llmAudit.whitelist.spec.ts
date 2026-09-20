import { beforeEach, describe, expect, it, vi } from 'vitest'

const { get, post, del } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
}))

vi.mock('../client', () => ({
  apiClient: { get, post, delete: del },
}))

import { addWhitelist, listWhitelist, removeWhitelist } from '../admin/llmAudit'

describe('admin LLM audit whitelist API', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lists, adds, and removes whitelist users through the audit endpoints', async () => {
    const entry = { user_id: 42, email: 'user@example.com', username: 'user', created_at: '2026-09-20T00:00:00Z' }
    get.mockResolvedValueOnce({ data: [entry] })
    post.mockResolvedValueOnce({ data: entry })
    del.mockResolvedValueOnce({ data: { user_id: 42 } })

    await expect(listWhitelist()).resolves.toEqual([entry])
    await expect(addWhitelist(42)).resolves.toEqual(entry)
    await expect(removeWhitelist(42)).resolves.toBeUndefined()

    expect(get).toHaveBeenCalledWith('/admin/audit/whitelist')
    expect(post).toHaveBeenCalledWith('/admin/audit/whitelist', { user_id: 42 })
    expect(del).toHaveBeenCalledWith('/admin/audit/whitelist/42')
  })
})
