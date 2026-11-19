import { describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import { getTaskCase } from './api'
vi.mock('@/api/client', () => ({ apiClient: { get: vi.fn() } }))
describe('正式案例分页请求', () => {
  it('精确区分本人及管理员路径、编码case id并保留服务端原响应', async () => {
    const data = { page: 2, page_size: 50, total: 51, records: [{ id: 'last' }] }
    vi.mocked(apiClient.get).mockResolvedValue({ data })
    expect(await getTaskCase(false, 17, 'case/a', 2)).toBe(data)
    expect(apiClient.get).toHaveBeenLastCalledWith('/zhiyongli/me/reports/17/task-cases/case%2Fa', { params: { page: 2, page_size: 50 } })
    await getTaskCase(true, 18, 'case-b')
    expect(apiClient.get).toHaveBeenLastCalledWith('/admin/zhiyongli/reports/18/task-cases/case-b', { params: { page: 1, page_size: 50 } })
  })
})
