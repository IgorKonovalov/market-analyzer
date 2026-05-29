/**
 * Plan 0014 phase 3 done-when: AgentModeToggle component.
 *
 * Defends: it renders as an accessible switch with a stable testid + label;
 * the OFF state reads `aria-checked="false"` and shows OFF; clicking calls the
 * injected `setEnabled` with the inverted value; and it carries the
 * chart-header region marker (not the sidebar).
 */
import '@testing-library/jest-dom'

import { fireEvent, render, screen } from '@testing-library/react'

import { AgentModeToggle } from './AgentModeToggle'

it('renders an accessible switch with a stable testid and label', () => {
  render(<AgentModeToggle enabled={false} setEnabled={jest.fn()} />)
  const toggle = screen.getByRole('switch', { name: 'Toggle agent mode' })
  expect(toggle).toBeInTheDocument()
  expect(toggle).toHaveAttribute('data-testid', 'agent-mode-toggle')
})

it('reflects the OFF state via aria-checked and visible text', () => {
  render(<AgentModeToggle enabled={false} setEnabled={jest.fn()} />)
  const toggle = screen.getByRole('switch', { name: 'Toggle agent mode' })
  expect(toggle).toHaveAttribute('aria-checked', 'false')
  expect(toggle).toHaveTextContent(/OFF/)
})

it('reflects the ON state via aria-checked and visible text', () => {
  render(<AgentModeToggle enabled setEnabled={jest.fn()} />)
  const toggle = screen.getByRole('switch', { name: 'Toggle agent mode' })
  expect(toggle).toHaveAttribute('aria-checked', 'true')
  expect(toggle).toHaveTextContent(/ON/)
})

it('clicking calls setEnabled with the inverted value (OFF → true)', () => {
  const setEnabled = jest.fn()
  render(<AgentModeToggle enabled={false} setEnabled={setEnabled} />)
  fireEvent.click(screen.getByRole('switch', { name: 'Toggle agent mode' }))
  expect(setEnabled).toHaveBeenCalledTimes(1)
  expect(setEnabled).toHaveBeenCalledWith(true)
})

it('clicking calls setEnabled with the inverted value (ON → false)', () => {
  const setEnabled = jest.fn()
  render(<AgentModeToggle enabled setEnabled={setEnabled} />)
  fireEvent.click(screen.getByRole('switch', { name: 'Toggle agent mode' }))
  expect(setEnabled).toHaveBeenCalledTimes(1)
  expect(setEnabled).toHaveBeenCalledWith(false)
})

it('lives in the chart-header region, not the sidebar', () => {
  render(<AgentModeToggle enabled={false} setEnabled={jest.fn()} />)
  const toggle = screen.getByRole('switch', { name: 'Toggle agent mode' })
  expect(toggle).toHaveAttribute('data-region', 'chart-header-right')
})
