import { defineConfig } from '@playwright/test'

const port = Number(process.env.E2E_PORT || 8100)
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  workers: 1,
  fullyParallel: false,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    launchOptions: process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {},
    screenshot: 'only-on-failure',
  },
  // 启动独立的 E2E 后端（重建 intent_e2e 库并灌种子数据），前端使用 npm run build 产物
  webServer: process.env.E2E_NO_SERVER ? undefined : {
    command: '../scripts/e2e-server.sh',
    url: `http://127.0.0.1:${port}/healthz`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: { E2E_PORT: String(port) },
  },
})
