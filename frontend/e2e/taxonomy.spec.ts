import { expect, test } from '@playwright/test'
import { login, watchErrors } from './helpers'

const shot = (n: string) => `../screenshots/${n}.png`

test('空间设置：插入一层 → 预览确认 → 列表多一列 → 删除这一层还原', async ({ page }) => {
  const errs = watchErrors(page)
  await login(page)
  await page.selectOption('#spSel', 'mc')
  await page.goto('/#/intents/cfg')
  await expect(page.locator('#lvCard')).toContainText('第 2 层')
  await page.evaluate(() => document.querySelector('#lvCard')?.scrollIntoView())
  await expect(page.locator('#toast')).not.toHaveClass(/show/, { timeout: 5000 })
  await page.screenshot({ path: shot('13-空间设置-分类层级') })
  // 在「问题桶」上方插入一层
  await page.click('#lvIns1')
  const pv = page.getByTestId('lvPreview')
  await expect(pv).toContainText('未分类')
  await page.fill('#lvNewName', '子类')
  await expect(page.locator('#lvImpact')).toContainText('大类 / 子类 / 问题桶 / 意图')
  await page.screenshot({ path: shot('14-层级调整预览') })
  await page.click('#lvConfirm')
  await expect(page.locator('#toast')).toContainText('层级已调整')
  await expect(page.locator('#lvName1')).toHaveValue('子类')
  await expect(page.locator('#taxCard')).toContainText('未分类')
  // 列表按层级名生成列
  await page.goto('/#/intents/list')
  await expect(page.locator('#intentTbl thead')).toContainText('大类子类问题桶意图')
  await expect(page.locator('#intentTbl tbody tr').first().locator('td').nth(2)).toHaveText('未分类')
  // 编辑器里的分类路径
  await page.locator('#intentTbl tbody tr').first().locator('button', { hasText: '编辑' }).click()
  await expect(page.locator('#eLv')).toContainText('子类未分类')
  await page.keyboard.press('Escape')
  // 删除这一层 → 还原
  await page.goto('/#/intents/cfg')
  await page.click('#lvDel1')
  await expect(page.getByTestId('lvPreview')).toContainText('并入上一层')
  await page.click('#lvConfirm')
  await expect(page.locator('#toast')).toContainText('层级已调整')
  await expect(page.locator('#lvName1')).toHaveValue('问题桶')
  await page.goto('/#/intents/list')
  await expect(page.locator('#intentTbl thead')).toContainText('大类问题桶意图')
  expect(errs).toEqual([])
})

test('分类树：新增一级与下级、改层级名称', async ({ page }) => {
  await login(page)
  await page.goto('/#/intents/cfg')
  await page.click('#catAddRoot')
  await page.fill('#catNewName', 'E2E 大类'); await page.fill('#catNewCode', 'EZE')
  await page.click('#catNewOk')
  await expect(page.locator('#toast')).toContainText('已新增分类')
  const row = page.locator('.catrow', { hasText: 'E2E 大类' })
  await row.locator('a[title="新增问题桶"]').click()
  await page.fill('#catNewName', 'E2E 桶'); await page.click('#catNewOk')
  await expect(page.locator('#taxCard')).toContainText('E2E 桶')
  await page.fill('#lvName0', '一级类目')
  await page.click('#lvSave')
  await expect(page.locator('#toast')).toContainText('层级名称已保存')
  await page.goto('/#/intents/list')
  await expect(page.locator('#intentTbl thead')).toContainText('一级类目')
  await expect(page.locator('#l1Tree')).toContainText('E2E 大类')
})
