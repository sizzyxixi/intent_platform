import { expect, test } from '@playwright/test'
import { login, watchErrors } from './helpers'

test.describe.configure({ mode: 'serial' })

test('意图列表：L1 筛选、搜索、分页', async ({ page }) => {
  const errs = watchErrors(page)
  await login(page)
  await expect(page.locator('.tree .on, #l1Tree .on').first()).toContainText('123')
  await page.locator('#l1Tree').getByText('库存与货期', { exact: true }).click()
  await expect(page.locator('#intentTbl tbody tr')).toHaveCount(6)
  await page.fill('#fQ', '现货'); await page.click('#fApply')
  await expect(page.locator('#intentTbl tbody tr').first()).toContainText('B.STOCK.AVAIL')
  // 切换空间
  await page.selectOption('#spSel', 'mc')
  await expect(page.locator('#intentTbl tbody tr').first()).toContainText('M.')
  expect(errs).toEqual([])
})

test('新建意图 → 启用校验失败 → 补齐后启用', async ({ page }) => {
  await login(page)
  await page.click('#newIntent')
  await page.selectOption('#niP', 'STOCK|现货')
  await page.fill('#niC', 'B.STOCK.E2E_PREORDER')
  await page.fill('#niN', '预售测试')
  await page.click('#newIntentDo')
  const ed = page.getByTestId('intentEditor')
  await expect(ed).toBeVisible()
  await page.selectOption('#eSt', '启用')
  await page.click('#eSave')
  await expect(ed.locator('.err')).toContainText('校验未通过')
  await page.fill('#eD', '用户想在缺货时先下单预订；问能否预定算；问何时补货不算（→ 补货/到货时间）')
  await page.fill('#eEx', '没货能先拍吗\n可以预定吗\n先下单等补货可以吗')
  // 槽位：选已有 sku 并设为必填
  await page.click('#slotAdd')
  await page.selectOption('#sN0', 'sku')
  await page.selectOption('#sR0', '必填')
  // 内联新建槽位
  await page.click('#slotNewInline')
  await page.fill('#slN', 'e2e_color'); await page.fill('#slD', '颜色'); await page.fill('#slA', '请问要什么颜色？')
  await page.click('#slotSave')
  await expect(page.locator('#sN1')).toHaveValue('e2e_color')
  // 关联知识
  await page.click('#kbPick')
  await expect(page.getByTestId('kbPicker')).toBeVisible()
  await page.selectOption('#pkKb', 'kb_aq')
  await page.locator('.pkc').first().check()
  await page.click('#pkOk')
  await expect(page.locator('#eRefs tr')).toHaveCount(1)
  // 关联工具
  await page.selectOption('#eTool', 'query_stock')
  await page.click('#toolAdd')
  await page.click('#eCheck')
  await expect(page.locator('#toast')).toContainText('校验通过')
  await page.click('#eSave')
  await expect(ed).toHaveCount(0)
  await page.fill('#fQ', 'E2E_PREORDER'); await page.click('#fApply')
  await expect(page.locator('#intentTbl tbody tr').first()).toContainText('启用')
})

test('槽位：被使用时禁止删除', async ({ page }) => {
  await login(page)
  await page.goto('/#/intents/slots')
  const row = page.locator('#slotTbl tbody tr').filter({ has: page.locator('td:first-child .mono', { hasText: /^sku$/ }) })
  await row.getByRole('button', { name: '删除' }).click()
  await expect(page.locator('.modal.show')).toContainText('该槽位正被')
})

test('导入意图：示例 JSON → 预览 → 入库 → 查看草稿', async ({ page }) => {
  await login(page)
  await page.click('#imOpen')
  await page.click('#imSample_aiJson')
  await expect(page.locator('#imRows tbody tr').first()).toBeVisible()
  await page.click('#imConfirm')
  await expect(page.locator('#imResult')).toContainText('导入完成')
  await page.click('#imGo')
  await expect(page.locator('#fSt')).toHaveValue('草稿')
  await page.goto('/#/intents/log')
  await expect(page.locator('#imLog tbody tr').first()).toContainText('林夏AI整理')
})

test('空间设置：修改阈值后版本 +1', async ({ page }) => {
  await login(page)
  await page.goto('/#/intents/cfg')
  await page.fill('#rcTh', '0.65')
  await page.click('#rcSave')
  await expect(page.locator('#toast')).toContainText('配置版本 v')
})

test('导入意图：Excel 模板示例、Markdown 示例都能解析', async ({ page }) => {
  await login(page)
  for (const k of ['csv', 'md']) {
    await page.click('#imOpen')
    await page.click('#imSample_' + k)
    await expect(page.locator('#imRows tbody tr').first()).toBeVisible()
    await page.keyboard.press('Escape')
  }
})
