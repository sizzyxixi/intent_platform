import { expect, Page } from '@playwright/test'

export async function login(page: Page, user = 'admin', pw = 'admin123456') {
  await page.goto('/#/intents/list')
  await page.fill('#lgU', user)
  await page.fill('#lgP', pw)
  await page.click('#lgBtn')
  await expect(page.locator('#intentTbl')).toBeVisible()
}

export function watchErrors(page: Page) {
  const errs: string[] = []
  page.on('pageerror', e => errs.push(String(e)))
  page.on('console', m => { if (m.type() === 'error' && !m.text().includes('status of 401')) errs.push(m.text()) })
  return errs
}
