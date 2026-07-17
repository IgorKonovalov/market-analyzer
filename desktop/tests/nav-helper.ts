/**
 * Shared e2e navigation helper.
 *
 * Since Plan 0096 phase 5 (commit 6420c4b, the chart/app declutter) every
 * destination except Chart folds into the collapsed `NavMenu` popup
 * (`renderer/components/NavMenu.tsx`): each destination is a `menuitem` button
 * that lives inside a panel with the `hidden` attribute until the menu trigger
 * (`nav-menu-trigger`) is clicked. Specs authored before the declutter clicked
 * the destination testid directly; against the current UI those clicks time out
 * because the target is inside a `hidden` container.
 *
 * `nav-chart` stays on the top bar and must NOT be routed through here — click
 * it directly.
 */
import { expect, type Page } from '@playwright/test'

/**
 * Open the collapsed nav menu and click the destination with the given testid.
 * Waits for the trigger and the revealed item so callers keep a single line.
 */
export async function navigateViaMenu(window: Page, testid: string): Promise<void> {
  const trigger = window.getByTestId('nav-menu-trigger')
  await expect(trigger).toBeVisible({ timeout: 15_000 })
  await trigger.click()
  const item = window.getByTestId(testid)
  await expect(item).toBeVisible({ timeout: 15_000 })
  await item.click()
}
