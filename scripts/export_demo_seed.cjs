// 从 demo（index.html + src3/*.js）导出种子数据：backend/seed/demo_state.json
// 用法：node scripts/export_demo_seed.cjs /path/to/intent-platform/index.html
// 依赖 frontend/node_modules 里的 @playwright/test（会用到本机 Chrome：CHROME_PATH 或 /usr/bin/google-chrome）
const path = require('path')
const fs = require('fs')
const { chromium } = require(path.join(__dirname, '../frontend/node_modules/@playwright/test'))

;(async () => {
  const html = path.resolve(process.argv[2] || '../intent-platform/index.html')
  const b = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome' })
  const p = await b.newPage()
  await p.goto('file://' + html)
  await p.evaluate(() => localStorage.clear())
  await p.reload()
  const d = await p.evaluate(() => JSON.stringify({
    // eslint-disable-next-line no-undef
    state: seedState(), TREE, MC_TREE, GEN_TREE: typeof GEN_TREE !== 'undefined' ? GEN_TREE : null,
  }))
  const out = path.join(__dirname, '../backend/seed/demo_state.json')
  fs.writeFileSync(out, d)
  console.log('已写入', out, (d.length / 1024).toFixed(0) + ' KB')
  await b.close()
})()
