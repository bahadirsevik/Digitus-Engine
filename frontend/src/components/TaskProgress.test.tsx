import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import TaskProgress from './TaskProgress'

describe('TaskProgress', () => {
  it('keeps displayed progress monotonic while running', () => {
    const { rerender } = render(
      <TaskProgress taskId="task-1" status="running" progress={40} message="Intent" />
    )

    expect(screen.getByText('40%')).toBeInTheDocument()

    rerender(<TaskProgress taskId="task-1" status="running" progress={20} message="Older update" />)

    expect(screen.getByText('40%')).toBeInTheDocument()
  })

  it('resets displayed progress for a new task id', () => {
    const { rerender } = render(
      <TaskProgress taskId="task-1" status="running" progress={40} message="Intent" />
    )

    rerender(<TaskProgress taskId="task-2" status="running" progress={5} message="New task" />)

    expect(screen.getByText('5%')).toBeInTheDocument()
  })
})
