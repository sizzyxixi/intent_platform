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


/** 选择 Cascader / SearchSelect；若目标仍是原生 select 则回退到 selectOption。 */
export async function pickSelect(page: Page, id: string, by: string | { label: string }) {
  const loc = page.locator('#' + id)
  await expect(loc).toBeVisible()
  const tag = await loc.evaluate(el => el.tagName)
  const label = typeof by === 'object' ? by.label : undefined
  const value = typeof by === 'string' ? by : undefined
  if (tag === 'SELECT' || tag === 'select') {
    if (label) await loc.selectOption({ label })
    else await loc.selectOption(value!)
    return
  }
  await loc.click()
  const panel = page.locator('#' + id + 'Panel')
  await expect(panel).toBeVisible()
  if (label) {
    const search = page.locator('#' + id + 'Search')
    if (await search.count()) await search.fill(label)
    await panel.locator('[data-pick="' + label + '"]').first().click()
  } else {
    const hit = panel.locator('[data-value="' + value + '"]')
    if (!(await hit.count())) {
      await page.locator('#' + id + 'Search').fill(String(value))
    }
    await expect(hit.first()).toBeVisible()
    await hit.first().click()
  }
  await expect(panel).toBeHidden()
}
