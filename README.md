# 意图管理平台（生产版 v0.15）

基于 demo（`intent-platform/index.html` + `src3/`）开发的生产可用版本。界面、交互和种子数据以 demo 为准，接口严格按 `docs/openapi.yaml` 0.15.0 实现。

- **意图管理**：意图列表（左侧第一层分类筛选，列按层级名生成）/ 槽位 / 空间设置（含分类层级：0～5 层分类 + 意图，层数与名称按空间配置，插入 / 删除层级先预览再确认）/ 导入记录。「新建意图」弹窗有「管理分类」入口直达分类层级设置。槽位类型含文件 / 图片。按业务空间（`cs` 1688 智能客服、`mc` Moonclaw）隔离意图和槽位。
- **知识库**：全平台通用。可新建知识库、导入文档（异步构建）、编辑切片和标签、检索。
- **工具中心**：一个「注册工具」表单，类型切换为 API 接口 / MCP / CLI 命令 / 技能 / 工作流（「平台内置」是来源筛选，内置 8 个平台工具）。
  - **MCP**：按「服务 → 子工具」登记；凭据是「位置（请求头 / URL 参数 / stdio 环境变量）+ 名称 + 环境变量名」列表，带通用预设（Bearer Token、API Key 请求头、App ID + App Key、URL 参数）；URL 里的密钥参数保存时自动移除并转为凭据。保存后抽屉内直接在线同步（initialize + tools/list）或粘贴 tools/list JSON，先看差异再确认；子工具可单独启停、标记写操作需确认。
  - **技能**：上传含 SKILL.md 的文件夹或 .zip（服务端解析），name / description 取自前置元数据（描述只读）；重新上传即新版本，历史版本可下载；技能没有入参。
  - **工作流**：Dify（基址 + 应用 Key 环境变量，`POST {基址}/workflows/run`，user 常量或会话用户）、Coze（区域、工作流 ID、令牌环境变量、run / stream_run、可选 workflow_version，高级里 is_async / bot_id / app_id / ext）、通用 HTTP；入参从平台同步（临时密钥在线读取或粘贴 JSON，默认只读，可微调）；可选输出字段映射。
  - 意图里两级选择工具（类型 → 工具，>5 个可筛选，MCP 下钻多选子工具）；技能不做入参映射，工作流必填入参须映射或有默认值，文件入参只能映射到文件 / 图片槽位。
  - 「接入与密钥」页管理 API Key，并提供 REST、MCP 接入说明和运行时配置下载。
- **用户与审计**（仅管理员）：账号分 admin / editor / viewer 三种角色；所有写操作都记审计日志。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.13、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 17（扩展 pg_trgm） |
| 检索 | PostgreSQL 全文检索（中文二元组 tsvector）+ pg_trgm 相似度，封装在可替换的 `SearchBackend` 接口后（`IP_SEARCH_BACKEND`）。不含向量检索 |
| 前端 | React 18、TypeScript（strict）、Vite 5，样式沿用 demo |
| 测试 | pytest（按 openapi 做契约校验）、Playwright e2e |
| 部署 | Docker Compose（postgres / migrate / backend / nginx 前端）、GitHub Actions |

## 目录

```
backend/    FastAPI 应用（app/）、Alembic 迁移、种子数据 seed/demo_state.json、tests/
frontend/   React 控制台（src/）、Playwright 用例（e2e/）
deploy/     docker-compose.yml、nginx.conf、.env.example
docs/       AI接入说明.md、openapi.yaml、需求文档（控制台「接入与密钥」直接展示与下载）
scripts/    e2e-server.sh（e2e 专用后端）、export_demo_seed.cjs（从 demo 重新导出种子）
screenshots/ 主要页面截图（e2e 生成）
```

## 本地开发

前置：Python 3.12+、Node 20+、PostgreSQL 15+（当前账号需能 `CREATE EXTENSION pg_trgm`）。

```bash
# 1. 数据库
createuser -P intent        # 密码 intent
createdb -O intent intent
psql -d intent -c "CREATE EXTENSION IF NOT EXISTS pg_trgm"   # 非超级用户时由 DBA 执行

# 2. 后端
python -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
cd backend
cp .env.example .env                         # 按需修改
../.venv/bin/alembic upgrade head
../.venv/bin/python -m app.seed              # 空库时导入 demo 数据，并打印 3 个演示 API Key（只显示一次）
../.venv/bin/uvicorn app.main:app --reload --port 8000

# 3. 前端（另开终端）
cd frontend && npm ci && npm run dev         # http://localhost:5173，/api、/mcp 代理到 :8000
```

默认管理员为 `admin / admin123456`，来自 `IP_ADMIN_*`，生产环境必须修改。开发环境的接口文档在 `http://localhost:8000/api/docs`。

不想单独起前端时，可以执行 `npm run build`，再设置 `IP_FRONTEND_DIST=../frontend/dist`，由后端直接托管静态页面。

种子命令：

- `python -m app.seed --reset`：清空后重建，生产环境禁止。
- `--admin-only`：只创建管理员。
- `--demo-keys yes|no|auto`：是否创建演示 Key，默认 `auto`，即生产环境不创建。
- `--tokens-out f.json`：把演示 Key 的 Token 写入文件。

## 部署（Docker Compose）

```bash
cp deploy/.env.example deploy/.env     # 必改：POSTGRES_PASSWORD、IP_SECRET_KEY(≥32位)、IP_ADMIN_PASSWORD、IP_PUBLIC_BASE_URL
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
# 访问 http://<主机>:8080
```

启动顺序：`db` 健康 → `migrate` 执行 `alembic upgrade head` 和种子导入（已有数据时跳过；`SEED_DEMO=false` 时只建管理员）→ `backend`（2 个 worker）→ `frontend`（nginx 托管静态文件，并把 `/api`、`/mcp`、`/healthz` 反代到后端）。

通过 HTTPS 对外提供服务时，要设置 `IP_COOKIE_SECURE=true`，并在前置代理上终止 TLS。

升级时拉取新代码后重新执行 `up -d --build`。`migrate` 每次都会执行迁移。

## 环境变量（前缀 `IP_`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `IP_ENV` | development | `production` 时校验密钥长度、关闭 `/api/docs`、禁止 `--reset` |
| `IP_DATABASE_URL` | postgresql+psycopg://intent:intent@127.0.0.1:5432/intent | 数据库 |
| `IP_SECRET_KEY` | dev-insecure… | 会话 JWT 签名，以及工具密钥的加密。生产环境至少 32 位，否则拒绝启动 |
| `IP_ADMIN_USERNAME` / `IP_ADMIN_PASSWORD` / `IP_ADMIN_DISPLAY_NAME` | admin / admin123456 / 管理员 | 初始管理员，只在不存在时创建 |
| `IP_SESSION_HOURS` | 12 | 控制台会话时长 |
| `IP_COOKIE_SECURE` | false | 会话 Cookie 是否只走 HTTPS |
| `IP_PUBLIC_BASE_URL` | https://intent.example.com | 对外地址，用在接入说明、MCP 配置示例和种子数据的 URL 里 |
| `IP_RATE_LIMIT_PER_MINUTE` | 60 | 开放接口每个 Key 每分钟的请求上限（计数存数据库，多实例共享），超出返回 429 |
| `IP_IDEMPOTENCY_TTL_HOURS` | 24 | 幂等键保留时长 |
| `IP_MAX_BATCH` | 100 | batchUpsert 单次条数上限，超出返回 413 |
| `IP_MAX_UPLOAD_MB` | 50 | 上传文件大小上限 |
| `IP_SEARCH_BACKEND` | postgres | 检索实现 |
| `IP_CORS_ORIGINS` | 空 | 跨域来源，逗号分隔；同域部署留空 |
| `IP_DOCS_DIR` | 仓库 `docs/` | 接入说明文件所在目录 |
| `IP_FRONTEND_DIST` | 空 | 设置后由后端托管前端 |
| `IP_LOG_LEVEL` | INFO | 日志级别 |
| `IP_MCP_TIMEOUT_SECONDS` | 15 | MCP 在线同步的连接 / 读取超时（秒） |
| `IP_MCP_STDIO_ENABLED` | false | 是否允许在平台服务器上启动 stdio MCP 服务做在线同步（会执行登记的命令，仅管理员可触发）。默认关闭，stdio 服务用「手动导入」 |

## Bot / AI 工具接入

1. 管理员在「工具中心 › 接入与密钥」新建 API Key：绑定空间、勾选权限（意图:读/写、知识:读/写、工具:读）。Token 只显示一次，平台只保存 sha256 哈希，可随时吊销。
2. **REST**（`/api/v1/*`，见 `docs/openapi.yaml`）
   - 请求头：`Authorization: Bearer ipk_…`
   - 意图、槽位、分类、运行时配置是按空间的接口，要带 `X-Space-Id`（或 query 参数 `space`）。知识库和工具是全局的，不用带。
   - 写接口必须带 `Idempotency-Key`（≤64 字符）：
     - 同一个键、同样的请求体：直接重放原响应，并带上 `Idempotent-Replayed: true`。
     - 同一个键、不同的请求体：返回 409。
   - 错误统一为 `{code, message, details, request_id}` 格式。
3. **运行时配置**：Bot 定时请求 `GET /api/v1/runtime-config?since_version=<本地版本>`。
   - 版本未变：返回 304。
   - 版本有变：返回本空间已启用意图（含 `category_path`）、分类层级 `taxonomy`、槽位定义、recognition（阈值/澄清/追问/重试/最终动作）、校验集全文、用到的工具，以及用到的 MCP 服务 `mcp_servers`。
   - MCP 服务的密钥不下发：`mcp_servers[].credentials[]` 给出每条凭据的位置、名称和 Bot 侧环境变量名（`auth.token_env` 保留兼容）。
   - 技能：`tools[].skill` 给出版本和文件列表，技能包用 `GET /api/v1/tools/{name}/skill-package`（工具:读）下载；工作流：`tools[].workflow` 给出平台、调用方式、`auth_env`、输出映射。
   - 拉到新版本后应清空意图识别缓存。意图启用、停用、编辑，分类 / 层级调整，空间设置变更，MCP 同步或子工具启停影响已启用意图，都会让该空间的 version +1。
4. **MCP**：地址为 `POST {IP_PUBLIC_BASE_URL}/mcp`（JSON-RPC 2.0，非流式），请求头与 REST 相同。
   - 支持的方法：`initialize`、`tools/list`、`tools/call`。
   - 提供的工具：`list_intents`、`get_intent`、`upsert_intents`、`search_knowledge`、`import_knowledge`、`list_tools`。
   - 客户端配置示例：

   ```json
   { "mcpServers": { "intent-platform": { "url": "https://<域名>/mcp",
       "headers": { "Authorization": "Bearer ipk_xxx", "X-Space-Id": "cs" } } } }
   ```
5. 通过接口或 MCP 写入的意图一律为草稿，并在「导入记录」中显示来源为「AI 工具（API）」。启用只能在控制台操作。

## 升级到 v0.15

`alembic upgrade head` 执行 `0004_tool_types_v15`：

- MCP 服务：`auth / auth_header / auth_env` 转为 `credentials` 列表（Bearer → `Authorization` + Bearer 前缀，API Key → 对应请求头，环境变量 → stdio env），环境变量名沿用原来的 `MCP_{服务名}_TOKEN`；原密钥保留并对应到第一条凭据；新增 `example_data`。
- 新建 `skill_packages` 表（技能包文件、SKILL.md、版本、zip 归档）。
- 旧的「技能/工作流」（skill_id + platform）工具转为**技能**类型、入参清空，原字段保存在 `config.legacy`；意图里对这些工具的入参映射被清空。**这些技能需要在工具中心重新上传 SKILL.md 技能包**（未上传前列表显示「待上传技能包」，绑定它们的意图再次保存/启用时校验不通过）。真正的工作流请按 Dify / Coze / 通用 HTTP 重新登记为「工作流」。
- 支持 `alembic downgrade 0003`。

接口兼容：`McpServerConn.auth` 继续输出（第一条凭据摘要）；创建 / 更新 MCP 服务仍接受旧的 `auth` + `secret` 写法。`Tool.skill_id / platform` 已移除，改为 `Tool.skill` / `Tool.workflow`。

## 升级到 v0.6

`alembic upgrade head` 会依次执行：

- `0002_dynamic_taxonomy`：`taxonomy_l1/l2` 与 `intents.l1/l2` 转成 `categories` 邻接表 + `intents.category_id`，现有空间设为三层「大类 → 问题桶 → 意图」。
- `0003_mcp_servers`：原 MCP 工具按 Server URL 归为 MCP 服务（服务名取 URL 最后一段，平台内置为 `intent_platform`），工具改名为 `{服务名}.{MCP 工具名}`，意图里的绑定同步改名，工具上的密钥移到服务上。

接口兼容：写入与筛选仍接受 `l1` / `l2`；输出改为 `category_path`；`Tool.mcp_server` 由 URL 字符串改为连接信息对象。

## 测试

```bash
# 后端：需要 intent_test 库（测试会重建 schema）；MCP 用例会在本机随机端口起 tests/mini_mcp_server.py
createdb -O intent intent_test
cd backend && ../.venv/bin/python -m pytest -q

# 前端类型检查与构建
cd frontend && npm run build

# e2e：自动重建 intent_e2e 库、灌入种子、在 :8100 启动后端（托管 dist）和 :8766 的测试 MCP 服务，截图输出到 screenshots/
cd frontend && npm run build && npx playwright test        # 本机 Chrome：CHROME_PATH=/usr/bin/google-chrome
```

CI 配置见 `.github/workflows/ci.yml`：在 postgres 服务上跑 pytest、前端构建和 Playwright，另外校验 compose 配置并构建镜像。

## 已知限制

- 单句测试用例句的二元组相似度模拟识别（返回 `simulated: true`），分数与 Bot 实际使用的模型不同，只用来自查例句是否合理。
- 工具「测试调用」只生成请求预览（`executed: false`），平台不执行外部工具；实际执行在 Bot 侧。平台主动连接外部服务只有两处：MCP 在线同步（只调 initialize / tools/list，不调 tools/call）和工作流「在线读取入参」（Dify `GET /parameters`、Coze `GET /v1/workflows/{id}`，密钥由操作人临时输入、不保存；平台不读取服务器环境变量）。
- 种子里的 `sellersprite` MCP 服务只有 4 个示例子工具（标「示例数据」），需要真实凭据在线同步才能拿到完整列表；示例技能的技能包内容（除 SKILL.md 外）是占位文件。
- MCP 在线同步和工作流在线读取入参会让平台服务器访问登记的地址，部署时请用出站网络策略限制可访问的范围（平台未内置地址白名单）。
- 知识构建在后端进程内用后台任务完成，进程重启时会把卡在「构建中」的任务标为失败，需要重新导入。超大文件建议以后改用独立队列（如 Celery/RQ）。
- 图片（png/jpg/webp）不做 OCR，导入后显示「构建失败」并给出原因。
