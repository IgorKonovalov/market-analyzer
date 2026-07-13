/**
 * Plan 0096 phase 5 done-when: the collapsed navigation menu.
 *
 * Defends: every old-tab destination is listed under its group and reachable;
 * selecting one navigates to the correct view and closes the menu; the menu
 * starts collapsed and toggles; Escape closes; a current item highlights it and
 * the trigger.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen } from '@testing-library/react'

import { NavMenu, type NavMenuGroup } from './NavMenu'

const GROUPS: NavMenuGroup[] = [
  {
    label: 'Analyze',
    items: [
      {
        view: 'technical-read',
        label: 'Technical read',
        testid: 'nav-technical-read',
        current: false,
      },
      { view: 'forecast', label: 'Forecast', testid: 'nav-forecast', current: false },
      { view: 'convergence', label: 'Convergence', testid: 'nav-convergence', current: false },
    ],
  },
  {
    label: 'Ideas',
    items: [
      { view: 'signals', label: 'Signals', testid: 'nav-signals', current: false },
      {
        view: 'recommendations',
        label: 'Recommendations',
        testid: 'nav-recommendations',
        current: false,
      },
      { view: 'recent-backtests', label: 'Backtests', testid: 'nav-backtests', current: false },
    ],
  },
  {
    label: 'Portfolio',
    items: [
      { view: 'defi', label: 'DeFi', testid: 'nav-defi', current: false },
      { view: 'track-record', label: 'Track record', testid: 'nav-track-record', current: false },
    ],
  },
  {
    label: 'System',
    items: [
      { view: 'news', label: 'News', testid: 'nav-news', current: false },
      { view: 'alerts', label: 'Alerts', testid: 'nav-alerts', current: false },
      { view: 'settings', label: 'Settings', testid: 'nav-settings', current: false },
    ],
  },
]

const ALL = GROUPS.flatMap((g) => g.items)

function renderMenu(groups: NavMenuGroup[] = GROUPS): { onNavigate: jest.Mock } {
  const onNavigate = jest.fn()
  render(
    <NavMenu
      groups={groups}
      onNavigate={onNavigate}
      triggerLabel="Menu"
      triggerAria="More views"
    />,
  )
  return { onNavigate }
}

it('lists all eleven destinations under their group headings', () => {
  renderMenu()
  expect(ALL).toHaveLength(11)
  for (const item of ALL) {
    expect(screen.getByTestId(item.testid)).toHaveTextContent(item.label)
  }
  for (const group of GROUPS) {
    expect(screen.getByText(group.label)).toBeInTheDocument()
  }
})

it('starts collapsed and toggles open', () => {
  renderMenu()
  expect(screen.getByTestId('nav-menu-panel')).toHaveAttribute('hidden')
  expect(screen.getByTestId('nav-menu-trigger')).toHaveAttribute('aria-expanded', 'false')

  fireEvent.click(screen.getByTestId('nav-menu-trigger'))
  expect(screen.getByTestId('nav-menu-panel')).not.toHaveAttribute('hidden')
  expect(screen.getByTestId('nav-menu-trigger')).toHaveAttribute('aria-expanded', 'true')
})

it('navigates to the correct view for every destination and closes after each', () => {
  const { onNavigate } = renderMenu()
  for (const item of ALL) {
    fireEvent.click(screen.getByTestId('nav-menu-trigger')) // open
    fireEvent.click(screen.getByTestId(item.testid))
    expect(onNavigate).toHaveBeenLastCalledWith(item.view)
    // Selecting closes the menu.
    expect(screen.getByTestId('nav-menu-panel')).toHaveAttribute('hidden')
  }
})

it('closes on Escape', () => {
  renderMenu()
  fireEvent.click(screen.getByTestId('nav-menu-trigger'))
  expect(screen.getByTestId('nav-menu-panel')).not.toHaveAttribute('hidden')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.getByTestId('nav-menu-panel')).toHaveAttribute('hidden')
})

it('highlights the current destination and the trigger when a menu item is active', () => {
  const groups = GROUPS.map((g) => ({
    ...g,
    items: g.items.map((it) => ({ ...it, current: it.testid === 'nav-defi' })),
  }))
  renderMenu(groups)
  expect(screen.getByTestId('nav-defi')).toHaveAttribute('aria-current', 'page')
  expect(screen.getByTestId('nav-signals')).not.toHaveAttribute('aria-current')
  expect(screen.getByTestId('nav-menu-trigger')).toHaveAttribute('aria-current', 'page')
})
