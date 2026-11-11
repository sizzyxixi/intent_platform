import { expect, test } from '@playwright/test'
import { login, watchErrors } from './helpers'

// e2e-server.sh 同时启动了测试用最小 MCP 服务（tests/mini_mcp_server.py，3 个工具）
const MCP = `http://127.0.0.1:${process.env.E2E_MCP_PORT || 8766}`
const out = (n: string) => `../screenshots/${n}.png`
const settle = async (page: any) => { await expect(page.locator('#toast')).not.toHaveClass(/show/, { timeout: 5000 }); await page.waitForTimeout(200) }

test('MCP：注册服务 → 在线同步 → 意图下钻绑定 → 远端变化后差异标红', async ({ page, request }) => {
  const errs = watchErrors(page)
  await request.post(MCP + '/_reset')
  await login(page)
  await page.goto('/#/tools/list')
  // 种子里的 MCP 能力已迁移为「服务 → 子工具」
  await expect(page.locator('#toolTbl tr.srvrow')).toHaveCount(5)
  await page.click('#toolNew')
  await page.selectOption('#tTy', 'mcp')
  await page.fill('#mN', 'shop')
  await page.fill('#mD', '商城 MCP（测试）')
  await page.fill('#mDs', '订单查询 / 取消、帮助中心搜索')
  await page.fill('#mU', MCP + '/mcp')
  await page.click('#toolSave')
  // 保存后抽屉里直接出现同步面板
  await expect(page.locator('#syPanel')).toBeVisible()
  await page.click('#syRun')
  await expect(page.locator('#syDiff')).toContainText('新增 3')
  await expect(page.locator('#syDiff')).toContainText('mini-mcp')
  await page.click('#syApply')
  await expect(page.locator('#syDiff')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('toolEdit')).toHaveCount(0)
  const sub = page.locator('#toolTbl tr.subrow')
  await expect(page.locator('#toolTbl tr[data-tool^="shop."]')).toHaveCount(3)
  // annotations.destructiveHint → 默认勾选「写操作需确认」
  await expect(page.locator('tr[data-tool="shop.cancel_order"] .mCf')).toBeChecked()
  await expect(page.locator('tr[data-tool="shop.lookup_order"] .mCf')).not.toBeChecked()
  // 停用 search_faq
  await page.locator('tr[data-tool="shop.search_faq"] .mEn').click()
  await expect(page.locator('tr[data-tool="shop.search_faq"] .mEn')).not.toBeChecked()
  await settle(page)
  await page.locator('tr[data-server="shop"]').scrollIntoViewIfNeeded()
  await page.screenshot({ path: out('15-MCP服务展开'), fullPage: true })
  expect(await sub.count()).toBe(3)

  // 意图编辑器：选 MCP 服务 → 下钻多选具体工具
  await page.goto('/#/intents/list')
  await page.fill('#fQ', 'B.STOCK.AVAIL'); await page.click('#fApply')
  await page.locator('#intentTbl tbody tr').first().locator('button', { hasText: '编辑' }).click()
  await expect(page.getByTestId('intentEditor')).toBeVisible()
  await page.selectOption('#eToolType', 'mcp')
  await page.selectOption('#eTool', 'srv:shop')
  await page.click('#toolAdd')
  const pick = page.getByTestId('mcpPick')
  await expect(pick).toBeVisible()
  await expect(pick.locator('.pickrow')).toHaveCount(2) // search_faq 已停用，不可选
  await expect(pick).toContainText('另有 1 个已停用')
  await page.fill('#mpQ', '订单')
  await expect(pick.locator('.pickrow')).toHaveCount(2)
  await pick.locator('.pickrow', { hasText: 'lookup_order' }).locator('input').check()
  await pick.locator('.pickrow', { hasText: 'cancel_order' }).locator('input').check()
  await page.waitForTimeout(200)
  await page.screenshot({ path: out('17-MCP下钻选择工具') })
  await page.click('#mpOk')
  await expect(pick).toHaveCount(0)
  for (const t of ['shop.lookup_order', 'shop.cancel_order']) {
    const card = page.locator(`[data-bound="${t}"]`)
    await expect(card).toBeVisible()
    await card.locator('tbody tr', { hasText: 'order_no' }).locator('select').nth(1).selectOption('sku')
  }
  await expect(page.locator('[data-bound="shop.cancel_order"]')).toContainText('写操作需确认')
  await page.click('#eSave')
  await expect(page.getByTestId('intentEditor')).toHaveCount(0)

  // 远端变化：cancel_order、search_faq 消失，lookup_order 新增入参
  await request.post(MCP + '/_set', { data: { tools: [
    { name: 'lookup_order', title: '查询订单', description: '按订单号查询订单状态与物流', annotations: { readOnlyHint: true },
      inputSchema: { type: 'object', properties: { order_no: { type: 'string' }, lang: { type: 'string' } }, required: ['order_no'] } },
    { name: 'track_parcel', title: '包裹轨迹', description: '按运单号查询包裹轨迹', inputSchema: { type: 'object', properties: { no: { type: 'string' } } } },
  ] } })
  await page.goto('/#/tools/list')
  await page.locator('tr[data-server="shop"]').getByRole('button', { name: '同步工具' }).click()
  await page.click('#syRun')
  const diff = page.locator('#syDiff')
  await expect(diff).toContainText('新增 1 · 变更 1 · 消失 2')
  const used = diff.locator('tr.rm-used')
  await expect(used).toHaveCount(1)
  await expect(used).toContainText('shop.cancel_order')
  await expect(used).toContainText('B.STOCK.AVAIL')
  await expect(diff).toContainText('未被使用，将从目录删除')
  await settle(page)
  await page.screenshot({ path: out('16-同步差异') })
  await page.click('#syApply')
  await expect(page.locator('#toast')).toContainText('转为草稿')
  await expect(page.locator('tr[data-tool="shop.cancel_order"]')).toHaveClass(/missing/)
  await expect(page.locator('tr[data-tool="shop.search_faq"]')).toHaveCount(0)
  await expect(page.locator('tr[data-tool="shop.track_parcel"]')).toHaveCount(1)
  await request.post(MCP + '/_reset')
  expect(errs).toEqual([])
})
