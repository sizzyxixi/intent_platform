import { expect, test } from '@playwright/test'
import { login } from './helpers'

test('未登录访问返回登录页；错误密码提示', async ({ page }) => {
  await page.goto('/#/kb')
  await expect(page.locator('#lgBtn')).toBeVisible()
  await page.fill('#lgU', 'admin'); await page.fill('#lgP', 'wrong-password')
  await page.click('#lgBtn')
  await expect(page.locator('.login .err')).toBeVisible()
})

test('管理员新建只读用户；只读用户看不到编辑入口', async ({ page }) => {
  await login(page)
  await page.goto('/#/system')
  await page.click('#userNew')
  await page.fill('#uN', 'e2e_viewer'); await page.fill('#uD', '只读-E2E')
  await page.selectOption('#uR', 'viewer'); await page.fill('#uP', 'viewer-pass-123')
  await page.click('#uSave')
  await expect(page.locator('#userTbl')).toContainText('e2e_viewer')
  await page.getByText('审计日志', { exact: true }).click()
  await expect(page.locator('#auditTbl tbody tr').first()).toContainText('e2e_viewer')
  await page.getByText('退出', { exact: true }).click()
  await login(page, 'e2e_viewer', 'viewer-pass-123')
  await expect(page.locator('#newIntent')).toHaveCount(0)
  await expect(page.locator('#intentTbl tbody tr').first().getByRole('button', { name: '查看' })).toBeVisible()
  await page.goto('/#/tools/list')
  await expect(page.locator('#toolNew')).toHaveCount(0)
})
