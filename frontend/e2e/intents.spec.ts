import { expect, test } from '@playwright/test'
import { login, pickSelect, pickTool, watchErrors } from './helpers'

test.describe.configure({ mode: 'serial' })

test('意图列表：第一层分类筛选、按层级名生成列、搜索、分页', async ({ page }) => {
  const errs = watchErrors(page)
  await login(page)
  await expect(page.locator('.tree .on, #l1Tree .on').first()).toContainText('123')
  await page.locator('#l1Tree').getByText('库存与货期', { exact: true }).click()
  await expect(page.locator('#intentTbl tbody tr')).toHaveCount(6)
  await expect(page.locator('#intentTbl thead')).toContainText('大类问题桶意图')
  await page.fill('#fQ', '现货'); await page.click('#fApply')
  await expect(page.locator('#intentTbl tbody tr').first()).toContainText('B.STOCK.AVAIL')
  // 切换空间
  await pickSelect(page, 'spSel', 'mc')
  await expect(page.locator('#intentTbl tbody tr').first()).toContainText('M.')
  expect(errs).toEqual([])
})

test('新建意图 → 启用校验失败 → 补齐后启用', async ({ page }) => {
  await login(page)
  await page.click('#newIntent')
  await pickSelect(page, 'niP', { label: '库存与货期 / 现货' })
  await page.fill('#niC', 'B.STOCK.E2E_PREORDER')
  await page.fill('#niN', '预售测试')
  await page.click('#newIntentDo')
  const ed = page.getByTestId('intentEditor')
  await expect(ed).toBeVisible()
  await page.selectOption('#eSt', '启用')
  await page.click('#eSave')
  await expect(ed.locator('.err').first()).toContainText('校验未通过')
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
  await pickSelect(page, 'pkKb', 'kb_aq')
  await page.locator('.pkc').first().check()
  await page.click('#pkOk')
  await expect(page.locator('#eRefs tr')).toHaveCount(1)
  // 关联工具
  await page.selectOption('#eToolType', 'http')
  await pickTool(page, 'query_stock')
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

test('空间设置：展示并可编辑业务空间描述', async ({ page }) => {
  await login(page)
  await page.goto('/#/intents/cfg')
  await expect(page.locator('#spInfoCard')).toContainText('业务空间')
  await expect(page.locator('#spInfoDesc')).toBeVisible()
  await expect(page.locator('#rcCard')).toHaveCount(0)
  const cur = await page.locator('#spInfoDesc').inputValue()
  await page.fill('#spInfoDesc', (cur || '') + '（探针）')
  await page.click('#spInfoSave')
  await expect(page.locator('#toast')).toContainText('业务空间已保存')
  await page.fill('#spInfoDesc', cur)
  await page.click('#spInfoSave')
  await expect(page.locator('#toast')).toContainText('业务空间已保存')
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

async function openFirstEditor(page) {
  const ed = page.getByTestId('intentEditor')
  await page.locator('#intentTbl tbody tr').first().getByRole('button', { name: /编辑|查看/ }).click()
  await expect(ed).toBeVisible()
  await expect(ed.locator('.dh .sp')).not.toHaveText('加载中…', { timeout: 15_000 })
  return ed
}

test('打开意图详情抽屉：cs 与 mc 都不停滞在加载中', async ({ page }) => {
  const errs = watchErrors(page)
  await login(page)
  const ed = await openFirstEditor(page)
  await expect(ed.locator('.dh .sp')).toContainText('编辑意图')
  await expect(ed.locator('#eN')).toBeVisible()
  await ed.locator('a[aria-label="关闭"]').click()
  await expect(ed).toHaveCount(0)

  await pickSelect(page, 'spSel', 'mc')
  await expect(page.locator('#intentTbl tbody tr').first()).toContainText('M.')
  const ed2 = await openFirstEditor(page)
  await expect(ed2.locator('#eN')).toBeVisible()
  expect(errs).toEqual([])
})

test('工具目录可选请求失败时详情仍打开；必选请求失败时展示错误而非一直加载', async ({ page }) => {
  await login(page)
  await page.route('**/api/admin/mcp-servers', route => route.abort())
  await page.route('**/api/admin/tools**', route => {
    const u = route.request().url()
    if (u.includes('maintenance=false') && u.includes('page_size=1') && !u.includes('names=')) return route.abort()
    return route.continue()
  })
  const ed = await openFirstEditor(page)
  await expect(ed.locator('#eN')).toBeVisible()
  await expect(ed.locator('#intentLoadErr')).toHaveCount(0)
  await ed.locator('a[aria-label="关闭"]').click()

  await page.unroute('**/api/admin/mcp-servers')
  await page.unroute('**/api/admin/tools**')
  await page.route('**/api/admin/intents/*', route => route.fulfill({
    status: 500, contentType: 'application/json',
    body: JSON.stringify({ code: 'ERROR', message: '详情接口失败' }),
  }))
  await page.locator('#intentTbl tbody tr').first().getByRole('button', { name: /编辑|查看/ }).click()
  await expect(page.getByTestId('intentEditor')).toBeVisible()
  await expect(page.locator('#intentLoadErr')).toContainText('无法加载意图详情')
  await expect(page.getByTestId('intentEditor').locator('.dh .sp')).toHaveText('加载失败')
  await expect(page.getByTestId('intentEditor').locator('.dh .sp')).not.toHaveText('加载中…')
})


test('所属分类是可搜索的级联选择', async ({ page }) => {
  await login(page)
  await page.click('#newIntent')
  await expect(page.locator('#niP')).toBeVisible()
  await page.click('#niP')
  const panel = page.locator('#niPPanel')
  await expect(panel).toBeVisible()
  const firstCol = panel.locator('.cas-col').first()
  await expect(firstCol).toContainText('库存与货期')
  await firstCol.getByText('库存与货期', { exact: true }).hover()
  await expect(panel.locator('.cas-col').nth(1)).toContainText('现货')
  await page.screenshot({ path: '/workspace/category_cascader.png' })
  await page.fill('#niPSearch', '库存与货期 / 现货')
  await panel.locator('[data-pick="库存与货期 / 现货"]').click()
  await expect(panel).toBeHidden()
  await expect(page.locator('#niP')).toContainText('库存与货期 / 现货')
})
