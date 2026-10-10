import { describe, expect, it } from 'vitest'

import { tasksFromListResponse } from './api'

describe('tasksFromListResponse', () => {
  it('reads the real TaskListResponse object shape { tasks, total }', () => {
    const data = { tasks: [{ task_id: 'a' }, { task_id: 'b' }], total: 2 }
    expect(tasksFromListResponse<{ task_id: string }>(data).map((t) => t.task_id)).toEqual([
      'a',
      'b',
    ])
  })

  it('still accepts a bare array', () => {
    expect(tasksFromListResponse([{ task_id: 'x' }])).toHaveLength(1)
  })

  it('returns an empty list for missing or malformed payloads', () => {
    expect(tasksFromListResponse(undefined)).toEqual([])
    expect(tasksFromListResponse(null)).toEqual([])
    expect(tasksFromListResponse({ tasks: 'nope' })).toEqual([])
    expect(tasksFromListResponse({})).toEqual([])
  })
})
