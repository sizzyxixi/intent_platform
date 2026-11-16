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

/** 意图编辑器里搜索并选中一个工具（或 MCP 服务，name 形如 srv:shop）。 */
export async function pickTool(page: Page, name: string) {
  const input = page.locator('#eTool')
  await input.click()
  const item = page.locator('#eToolList [data-name="' + name + '"]')
  if (!(await item.count())) await input.fill(name)
  await expect(item).toBeVisible()
  await item.click()
}
