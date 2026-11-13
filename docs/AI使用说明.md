# 意图管理平台 · AI 使用说明

> 读者：**你**——接入本平台的 AI。你可能是线上 Bot / 智能体（按平台配置和用户对话），也可能是维护型 AI（帮运营整理意图、导入知识）。每次会话开始先读完本说明。
> 本说明与代码一致（平台 v0.17，`openapi.yaml` 0.17.0）。字段细节以 `openapi.yaml` 为准；6 个 MCP 工具的完整 inputSchema 见 `AI接入说明.md` 第 9 节。
> 文中示例来自 1688 智能客服空间（`cs`）的真实数据；换成你所在的空间时，结构完全相同。

## 0. 接入前配置（必读）

没有可用的 API Key，就不要调用任何接口。下面约定 Key 从哪来、放到哪、怎么自检。示例里的 `ipk_xxx` / `ipk_…` 只表示 Key 的格式（前缀是 `ipk_`），**不是**能用的密钥。

### 0.1 环境地址

| 项 | 生产环境 | 本地开发 |
|---|---|---|
| 平台地址 | `http://intent.47.86.15.93.sslip.io` | `http://127.0.0.1:8000` |
| API Base | `http://intent.47.86.15.93.sslip.io/api/v1` | `http://127.0.0.1:8000/api/v1` |
| MCP 端点 | `http://intent.47.86.15.93.sslip.io/mcp`（`POST`，JSON-RPC 2.0） | `http://127.0.0.1:8000/mcp` |
| 鉴权 | 请求头 `Authorization: Bearer <API Key>`。意图类接口另带 `X-Space-Id`（或 `?space=`） | 同左 |

下文示例默认用生产地址，并用环境变量 `INTENT_PLATFORM_BASE_URL`、`INTENT_PLATFORM_API_KEY`、`INTENT_PLATFORM_SPACE`（也可用 `BASE` 作为地址的别名）。

### 0.2 管理员如何创建 API Key

只有**管理员**能创建。在控制台按这个路径操作（与页面文案一致）：

1. 打开 `http://intent.47.86.15.93.sslip.io/`（本地则为 `http://127.0.0.1:8000/`），用管理员账号登录。
2. 左侧菜单点「**工具中心**」，再点页内标签「**接入与密钥**」（地址是 `/#/tools/access`）。
3. 点「**＋ 新建 Key**」，填写：
   - **名称**：用来识别调用方，例如 `ai-agent-default`。
   - **绑定空间**（至少 1 个，可多选）：只约束意图、槽位、分类、空间设置、运行时配置。知识库和工具目录不受绑定限制。给 1688 智能客服接入时勾选「1688 智能客服」（空间 ID：`cs`）。
   - **权限**：`意图:读`、`意图:写`、`知识:读`、`知识:写`、`工具:读`。线上 Bot / 只读智能体勾选三个「读」即可（表单默认就是这三个）。只有要整理意图或导入知识时，才再勾选对应的「写」。
4. 点「创建」。完整 Key **只显示这一次**，关闭窗口后平台只保留哈希，无法再查看。请立刻复制并按 0.3 交给调用方，不要把 Key 写进代码仓库或公开聊天。

吊销在同一页的 Key 列表里操作。吊销后该 Key 再调用会返回 401 `KEY_REVOKED`。

### 0.3 你必须从哪里读取 Key（约定）

按这个顺序查找，**找到第一个就用，不要再往下搜**：

1. 环境变量 `INTENT_PLATFORM_API_KEY`。同时读取（若已设置）：
   - `INTENT_PLATFORM_BASE_URL`（未设置时用生产地址 `http://intent.47.86.15.93.sslip.io`）
   - `INTENT_PLATFORM_SPACE`（未设置时用 `cs`，或配置文件里的 `space_id`）
2. 配置文件 `~/.intent-platform/config.json`，权限必须是 `600`（仅文件所有者可读写）。字段如下：

```json
{
  "base_url": "http://intent.47.86.15.93.sslip.io",
  "api_key": "ipk_…",
  "space_id": "cs"
}
```

**如果上面两处都没有 Key：立即停止，向人要 Key，或请管理员按 0.2 创建并写入环境变量 / 该配置文件。禁止在 `~/.xclaw/`、`~/.workbuddy/`、插件目录、项目仓库、浏览器配置或其他目录里搜索 `ipk_`。那些位置即使出现类似字符串，也不是本平台的 API Key。**

### 0.4 自检

先装载配置，再请求 `GET /api/v1/spaces`。成功后再做别的调用。

```bash
if [ -z "$INTENT_PLATFORM_API_KEY" ] && [ -f "$HOME/.intent-platform/config.json" ]; then
  eval "$(python3 - <<'PY'
import json, os, shlex
d = json.load(open(os.path.expanduser("~/.intent-platform/config.json")))
print("export INTENT_PLATFORM_API_KEY="+shlex.quote(d["api_key"]))
print("export INTENT_PLATFORM_BASE_URL="+shlex.quote(d.get("base_url") or "http://intent.47.86.15.93.sslip.io"))
print("export INTENT_PLATFORM_SPACE="+shlex.quote(d.get("space_id") or "cs"))
PY
)"
fi
: "${INTENT_PLATFORM_BASE_URL:=http://intent.47.86.15.93.sslip.io}"
: "${INTENT_PLATFORM_SPACE:=cs}"
if [ -z "$INTENT_PLATFORM_API_KEY" ]; then
  echo "未找到 API Key。请管理员按《AI使用说明》0.2 创建，写入环境变量 INTENT_PLATFORM_API_KEY 或文件 ~/.intent-platform/config.json（chmod 600）。不要在其他目录搜索。"
  exit 1
fi

curl -sS -D - "$INTENT_PLATFORM_BASE_URL/api/v1/spaces" \
  -H "Authorization: Bearer $INTENT_PLATFORM_API_KEY"
```


键绑定了 `cs`、且具备 `意图:读` 时，成功响应是 **200**，例如：

```json
[{"space_id": "cs", "name": "1688 智能客服", "description": "1688 店铺询盘的智能客服试点",
  "bot": "客服 Bot（吴钢）", "code_prefix": "B"}]
```

返回的是该 Key **绑定的空间**，不会列出未绑定的空间。每个响应带 `X-Request-Id`。

| 结果 | 含义 | 你该怎么做 |
|---|---|---|
| 200 且列出预期空间 | Key 有效，可以继续 | 进入「第一次调用」或第 3 节 |
| 401 `UNAUTHORIZED`，文案含「缺少 API Key」 | 没带 `Authorization: Bearer ipk_…`，或格式不对 | 检查环境变量 / 配置文件是否已装载，请求头是否写对；不要改路径重试 |
| 401 `KEY_REVOKED` | Key 已被吊销 | 停止，请管理员重新创建 |
| 403 `SPACE_FORBIDDEN` | `X-Space-Id` 不在 Key 的绑定范围内（例如绑定了 `cs` 却传 `mc`） | 改用绑定的空间，或请管理员给这个 Key 加上该空间 |
| 403 `SCOPE_FORBIDDEN` | 缺少该接口所需权限（例如只读 Key 去写槽位） | 停止，说明缺的权限（`details.required`），请管理员补发或另开写权限 Key |

自检失败就停，向人说明上表里的原因，不要换地址反复试，也不要搜索其他目录找 Key。

## 第一次调用（端到端）

以生产环境、1688 智能客服空间（`cs`）为例。下面每一步的接口形态都按当前生产环境核对过。平台**没有**「识别意图」的开放接口：识别是你根据运行时配置在本地完成的。

**第 1 步：自检。** 按 0.4 调用 `GET /api/v1/spaces`，得到 200 和空间列表后再继续。

**第 2 步：拉运行时配置。**

```bash
curl -sS -D - "$INTENT_PLATFORM_BASE_URL/api/v1/runtime-config?since_version=0" \
  -H "Authorization: Bearer $INTENT_PLATFORM_API_KEY" \
  -H "X-Space-Id: $INTENT_PLATFORM_SPACE"
```

第一次或本地没有缓存时，`since_version=0`（不要省略到误传一个对不上的旧版本还以为失败）。成功是 **200**，响应头有 `ETag: "cs-v61"`（版本号会随配置变化增加）。顶层字段：

```json
{
  "space": {"id": "cs", "name": "1688 智能客服", "bot": "客服 Bot（吴钢）"},
  "version": 61,
  "generated_at": "2026-11-13T08:17:31.641680Z",
  "recognition": {"threshold": 0.6, "candidate_min": 0.3, "max_candidates": 3, "max_clarify": 2,
                  "max_slot_ask": 2, "tool_retry": 1, "final_action": "handoff_human",
                  "final_action_label": "转人工", "clarify_prompt": "我想确认一下，您是想要以下哪一项？",
                  "final_message": null},
  "taxonomy": {"levels": [{"depth": 1, "name": "阶段"}], "intent_level_name": "意图",
               "categories": [{"code": "PRE", "name": "售前"}, {"code": "MID", "name": "售中"},
                              {"code": "POST", "name": "售后"}, {"code": "NONE", "name": "非询盘"}]},
  "slots": [{"name": "shop_id", "display_name": "店铺", "type": "文本",
             "default_ask": "（不向买家追问：由 Bot 按会话所属的 1688 店铺带入 ERP 店铺 ID）"},
            {"name": "order_id", "display_name": "订单号", "type": "文本", "default_ask": "麻烦发一下订单号～"}],
  "intents": [ ],
  "validation_sets": [ ],
  "tools": [ ],
  "mcp_servers": [ ]
}
```

当前生产环境是 **17 条已启用意图、4 个被引用的工具、18 组校验知识**。把整份配置缓存下来，记下 `version`。再用同一个 `since_version`（例如 61）请求，版本没变会返回 **304**，响应头仍是 `ETag: "cs-v61"`，没有 body。

**第 3 步：识别 / 路由意图。** 用 `intents[].description` 和 `intents[].examples` 对用户原话打分（算法由你决定）。例如用户说「拍好了帮我改下价」——这是意图 `B.MID.CHANGE_ORDER`（售中改价改单）的标准例句之一。若你的 Top1 是该意图且置信度 ≥ `recognition.threshold`（0.6），即命中；否则按第 3.2 节澄清或走最终动作。平台不提供 `/recognize` 之类的接口。

命中后看该意图的槽位：`order_id` 必填（向买家要订单号）；`shop_id`、`carriage`、`entry_discounts` 等以「（不向买家追问：…）」开头，不要问买家，来源见第 3.4 节后的「店铺 ID 与西之月登录」。

**第 4 步：取知识。** 该意图的 `knowledge` 里，`usage=validate` 的完整内容已在顶层 `validation_sets` 中（例如 `kb_cs_reply` 的 RULE-13、`kb_cs_conf` 整篇），生成回复后必须逐条对照。需要再检索时：

```bash
curl -sS "$INTENT_PLATFORM_BASE_URL/api/v1/kbs/kb_cs_reply/search" \
  -H "Authorization: Bearer $INTENT_PLATFORM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"改价","top_k":2}'
```

成功是 **200**，数组里每一项形如：`score`、`kb_name`、`doc_name`、`chunk`（`chunk_id`、`doc_id`、`content`、`cells`、`effective_tags`、`linked_intents`、`kb_id` 在 `chunk` 内）。检索「改价」时，第一条即为 `RULE-13`，`linked_intents` 含 `B.MID.CHANGE_ORDER`，禁止项包括「未改先说已改好」。

**第 5 步：解析绑定的工具。** 在 `intents[].tools` 里找到 `param_map`，再到顶层 `tools[]` 按 `name` 取定义。`B.MID.CHANGE_ORDER` 当前绑定：

- `xclaw_platform_alibaba_product_get`（查询商品，读）：`--shop-id` ← 槽位 `shop_id`，`--product-id` ← 槽位 `offer_id`，`--web-site` / `--scene` 为常量 `1688`
- `xclaw_platform_alibaba_trade_order_modifyorderprice_call`（改价，`confirm_required=true`）：还需要 `order_id`、`carriage`、`entry_discounts`；没有人工确认不得调用

**第 6 步：调用工具。** 先按第 3.4 节把 `cli.command_template` 里的 `{参数}` 换成入参，拼成 argv。两个前提缺一不可，否则不要执行：

1. 运行环境已登录西之月：执行过 `xclaw-cli auth login`，或已设置环境变量 `XCLAW_UMC_TOKEN`（工具定义里 `cli.auth_env` / `cli.login_command` 即此）。
2. 已经有 `shop_id`（见「店铺 ID 与西之月登录」）。没有就回到槽位处理：不向买家追问，向使用你的人或接入层要；不要编造，也不要改去跑 `platform alibaba product list` 来「找」店铺（那条命令本身也要求 `--shop-id`）。

读商品在入参齐全、已登录时的 argv 示例（值请换成真实的店铺 ID 和商品 ID）：

```
["xclaw-cli","platform","alibaba","product","get","--shop-id","<ERP店铺ID>","--product-id","<商品ID>","--web-site","1688","--scene","1688","--json"]
```

改价是写操作，还须先按第 3.5 节取得确认，金额单位是分。

## 1. 你面对的是什么

平台把一个 Bot 需要的三样东西放在一起：**意图**（用户想办的事）、**知识**（回答依据和硬性规则）、**工具**（能调用的外部能力）。你要建立的心智模型是这样一棵树：

```
业务空间 space（一个 Bot 一个空间，例：cs = 1688 智能客服，编码前缀 B）
├─ 分类层级 levels：0～5 层分类 + 最末层「意图」（例：cs 为「阶段 → 意图」）
│   └─ 分类 categories（第一层可有编码，例：PRE 售前 / MID 售中 / POST 售后 / NONE 非询盘）
│       └─ 意图 intent（编码 B.MID.CHANGE_ORDER，名称、描述、例句、状态）
│           ├─ 槽位 slots：处理前要拿到的信息（order_id 必填 …），每个有追问话术
│           ├─ 知识 knowledge：引用某个知识库的切片或整篇文档，用途 reference（参考）或 validate（校验）
│           └─ 工具 tools：绑定的工具 + param_map（每个入参从槽位 / 常量 / 上下文取值）
├─ 槽位定义 slots（本空间共用）
└─ 空间设置 recognition：识别阈值、澄清、追问次数、工具重试、最终动作
知识库 kbs、工具 tools：全平台通用，任何空间的意图都能引用
```

几条不变的事实：
- 只有状态为「启用」的意图会出现在运行时配置里；「草稿」「停用」不参与线上。
- 启用只能由人在控制台操作。你通过接口写入的意图一律是草稿。
- 平台只登记工具怎么调；**线上调用工具的是你（Bot）**，凭据在你的运行环境里，平台不下发密钥。
- 每个空间有一个 `version`，配置有任何影响线上的改动都会 +1。

## 2. 接入与鉴权

| 项 | 值 |
|---|---|
| Base URL | 见第 0.1 节。优先用 `INTENT_PLATFORM_BASE_URL` |
| 鉴权 | 请求头 `Authorization: Bearer $INTENT_PLATFORM_API_KEY`。Key 的创建、存放和自检见第 0 节，不要使用文档里的 `ipk_xxx` 占位符去发请求 |
| 权限 scope | `意图:读`、`意图:写`、`知识:读`、`知识:写`、`工具:读`。线上 Bot 通常只有三个「读」；维护型 AI 另有两个「写」 |
| 空间 | 意图类接口（spaces、taxonomy、recognition、slots、intents、runtime-config）带 `X-Space-Id: cs`（或 `?space=cs`）；不传时用 Key 绑定的第一个空间；不在绑定范围返回 403 `SPACE_FORBIDDEN`。知识库、导入任务、工具接口全局，不需要空间 |
| MCP | `POST {Base URL}/mcp`，JSON-RPC 2.0，同一个 Key；意图类工具可传 `space` 参数 |
| 响应 | 每个响应带 `X-Request-Id`；错误体统一为 `{code, message, details, request_id}` |

MCP 客户端配置示例：

```json
{ "mcpServers": { "intent-platform": { "url": "http://intent.47.86.15.93.sslip.io/mcp",
    "headers": { "Authorization": "Bearer <INTENT_PLATFORM_API_KEY>", "X-Space-Id": "cs" } } } }
```

先按第 0.4 节确认身份和范围（`GET /api/v1/spaces`）。不要把 Key 写进长期存放的 MCP 配置文件明文里（除非该文件权限为 600，并与 `~/.intent-platform/config.json` 同样保密）。

```json
[{"space_id": "cs", "name": "1688 智能客服", "description": "1688 店铺询盘的智能客服试点",
  "bot": "客服 Bot（吴钢）", "code_prefix": "B"}]
```

## 3. 读路径：线上 Bot 怎么用

### 3.1 拉取并缓存运行时配置

```bash
curl -sS -D - "$INTENT_PLATFORM_BASE_URL/api/v1/runtime-config?since_version=61" \
  -H "Authorization: Bearer $INTENT_PLATFORM_API_KEY" \
  -H "X-Space-Id: $INTENT_PLATFORM_SPACE"
```

- 版本没变：`304`，响应头 `ETag: "cs-v61"`（版本号以实际返回为准），继续用本地缓存。
- 版本变了：`200` 返回完整配置。替换本地缓存，并**清空意图识别缓存**（例句、向量等）。`space` 是对象 `{"id","name","bot"}`，不是字符串。
- 建议定时轮询（如每分钟一次）；不要每轮对话都拉。

顶层结构：

| 字段 | 内容 |
|---|---|
| `space` / `version` / `generated_at` | 空间对象 `{"id","name","bot"}`、版本、生成时间 |
| `recognition` | 空间设置：`threshold`、`candidate_min`、`max_candidates`、`max_clarify`、`max_slot_ask`、`tool_retry`、`final_action`（`handoff_human` / `general_assistant` / `decline`）、`clarify_prompt`、`final_message` |
| `taxonomy` | `levels`、`intent_level_name`、`categories` 分类树 |
| `slots` | 本空间槽位定义（name、display_name、type、options、default_ask） |
| `intents` | 已启用意图：code、name、category_path、description、examples、slots（含生效的 `ask`）、knowledge、tools |
| `validation_sets` | 所有「校验」知识的**完整切片内容**（含表格 cells、标签、被哪些意图使用） |
| `tools` | 被已启用意图用到的工具的完整定义（类型、入参 schema、调用方式、`confirm_required`） |
| `mcp_servers` | 被用到的 MCP 服务连接信息（不含密钥） |

真实片段（cs，v61，已截短；`space` 为对象）：

```json
{
  "recognition": {"threshold": 0.6, "candidate_min": 0.3, "max_candidates": 3, "max_clarify": 2,
                  "max_slot_ask": 2, "tool_retry": 1, "final_action": "handoff_human",
                  "final_action_label": "转人工", "clarify_prompt": "我想确认一下，您是想要以下哪一项？",
                  "final_message": null},
  "intents": [{
    "code": "B.MID.CHANGE_ORDER", "name": "售中改价改单",
    "category_path": [{"code": "MID", "name": "售中"}],
    "description": "买家已拍下或已下单，要求修改订单价格、数量或运费；说「拍好了帮我改价」「数量改成 200」算；还没下单的议价不算（→ 比价中）",
    "examples": ["拍好了帮我改下价", "订单数量改成200件", "改好价我就付款", "运费能帮我改一下吗"],
    "slots": [
      {"name": "order_id", "required": true, "ask": "麻烦发一下订单号～", "ask_overridden": false},
      {"name": "target_price", "required": false, "ask": "您期望的价格是多少？", "ask_overridden": false},
      {"name": "shop_id", "required": false, "ask": "（不向买家追问：由 Bot 按会话所属的 1688 店铺带入 ERP 店铺 ID）", "ask_overridden": false}
    ],
    "knowledge": [
      {"kb_id": "kb_cs_reply", "doc_id": "RULE", "chunk_id": "RULE-13", "usage": "validate"},
      {"kb_id": "kb_cs_conf", "doc_id": "CONF", "chunk_id": "*", "usage": "validate"},
      {"kb_id": "kb_cs_close", "doc_id": "CLOSE", "chunk_id": "CLOSE-13", "usage": "reference"}
    ],
    "tools": [
      {"tool": "xclaw_platform_alibaba_trade_order_modifyorderprice_call", "type": "cli",
       "param_map": {"shop_id": {"source": "槽位", "value": "shop_id"},
                     "order_id": {"source": "槽位", "value": "order_id"},
                     "carriage": {"source": "槽位", "value": "carriage"},
                     "entry_discounts": {"source": "槽位", "value": "entry_discounts"}}},
      {"tool": "xclaw_platform_alibaba_product_get", "type": "cli",
       "param_map": {"shop_id": {"source": "槽位", "value": "shop_id"},
                     "product_id": {"source": "槽位", "value": "offer_id"},
                     "web_site": {"source": "常量", "value": "1688"},
                     "scene": {"source": "常量", "value": "1688"}}}
    ]
  }]
}
```

### 3.2 每一轮对话的处理顺序

1. **识别**：用每个已启用意图的 `description`（尤其是「什么不算 → 哪个意图」）和 `examples` 给用户原话打分，得到 Top3 及置信度。识别模型由你决定，平台只给语料。
2. **阈值判定**：Top1 ≥ `threshold` → 命中。否则取置信度 ≥ `candidate_min` 的候选（最多 `max_candidates` 个），用 `clarify_prompt` 请用户选；没有候选 → 执行最终动作。
3. **澄清**：用户选中即命中；澄清超过 `max_clarify` 次仍不明确 → 最终动作。
4. **槽位**：从对话和上下文抽取槽位。缺必填槽位时用该槽位的 `ask` 追问；同一槽位追问超过 `max_slot_ask` 次 → 最终动作。`ask` 以「（不向买家追问：…）」这类说明开头的槽位，是给你或人工填的，不要拿去问用户。
5. **知识**：按 `knowledge` 取参考知识组织回答；回答生成后逐条对照 `validation_sets` 里本意图用到的校验知识，不通过就改写，改写不了就执行最终动作。
6. **工具**：按 3.4 组装入参并调用；写操作先按 3.5 取得确认。
7. **重试**：工具报错或超时，最多重试 `tool_retry` 次，仍失败 → 按 3.6 处理，必要时最终动作。
8. **最终动作**：`handoff_human` 转人工；`general_assistant` 交给通用助手；`decline` 回复 `final_message`。

### 3.3 置信度分档与空间规则

`recognition.threshold` 是平台统一的命中线。有的空间把更细的业务规则写成了**校验知识**并整篇关联到每个意图，你必须照做。例如 cs 的 `kb_cs_conf`（1688客服-置信度与并行规则）：

| 规则 | 条件 | 你该怎么做 |
|---|---|---|
| R1 | 置信 ≥ 0.85 且槽位齐 | 主工具 + 辅工具可并行调用，输出完整回复草稿（由人点发送） |
| R2 | 0.60～0.85，或缺关键槽位 | 只问 1 个澄清问题；**不得**调用改价、锁仓等成交类工具 |
| R3 | < 0.60，或投诉 / 超出授权 | 转人工并说明理由；不调用任何成交类工具 |
| R4 | 工具失败 | 不得估计有货或价格；改为澄清或转人工 |
| R5 | 售中且有订单号 | 订单类工具（物流、改价）优先，结果放在回复第一句 |
| R6 | 并行上限 | 每轮最多主工具 + 2 个辅工具 |

每个意图的「必填槽位 / 主辅工具 / 三档动作 / 禁止项」在 `kb_cs_reply` 对应行里（也是校验知识），例如售中改价改单的禁止项是「未改先说已改好」。

### 3.4 调用绑定的工具

先解析入参：对 `param_map` 里每个入参，`source=槽位` 取该槽位的值，`source=常量` 直接用 `value`，`source=上下文` 取会话上下文（`session_id`、`buyer_id`、`order_id`、`sku`）。**必填入参拿不到值时不要调用**，回到第 4 步追问。未映射的选填入参不传。然后到 `tools[]` 里找同名工具，按 `type` 执行：

| type | 你怎么调用 |
|---|---|
| `cli` | 在你的运行环境执行 `cli.command_template`：把 `{参数名}` 替换为入参值；**选填参数为空时，连同它前面的 `--选项` 一起去掉**；按参数数组执行，不拼 shell 字符串。凭据放在 `cli.auth_env` 指定的环境变量里，或预先用 `cli.login_command` 完成登录（不要向用户索要账号、密码、token）。没安装就按 `cli.install` 安装。`output_format=json` 时把 stdout 解析为 JSON；返回 `{"ok": false, "error": {...}}` 视为失败 |
| `mcp` | 按 `mcp_server`（或顶层 `mcp_servers` 里同名服务）建立连接，同一服务复用一个会话；调用 `tools/call`，`name` = `mcp_tool`，`arguments` = 入参。凭据按 `credentials[]` 逐条放：`location=header` 放请求头（`scheme=Bearer` 时加 `Bearer ` 前缀）、`location=query` 放 URL 参数、`location=env` 放进 stdio 进程的环境变量，值都取自你环境里名为 `env` 的变量 |
| `http` | 按 `method` + `url` 发请求：GET 把入参放 query，其他方法放 JSON body；`url` 里的 `{参数}` 用入参替换。凭据由你托管 |
| `workflow` | 按 `workflow.platform`：dify → `POST {base_url}/workflows/run`，body `{inputs, response_mode, user}`；coze → `POST https://api.{region}/v1/workflow/run`（region 为 coze.cn / coze.com；streaming 用 `/v1/workflow/stream_run`），body `{workflow_id, parameters}` 及可选字段；generic → 按 `url` + `method` 发 `{inputs}`。令牌取 `workflow.auth_env` 环境变量，放 `Authorization: Bearer …`；必填入参没映射时用 `input_schema` 的 `default`；按 `output_mapping` 取结果 |
| `skill` | 用 `GET /api/v1/tools/{name}/skill-package` 下载 zip（按 `skill.version` 缓存，响应头 `X-Skill-Version`），读其中的 SKILL.md 并按它执行；技能**没有入参映射**，把本意图抽到的槽位作为上下文交给它 |
| 平台内置（`source=builtin`） | 就是本平台的接口：http 类按 `url` 调用并带你自己的 API Key；按空间的（`space_scoped=true`）带你所在空间的 `X-Space-Id`；`intent_platform.search_knowledge` 走本平台 `/mcp` 的 `search_knowledge` |

CLI 示例（cs 的「获取商品」，模板来自 runtime-config，值为说明用）：

```
模板：xclaw-cli platform alibaba product get --shop-id {shop_id} --product-id {product_id} --web-site {web_site} --scene {scene} --json
入参：shop_id ← 槽位 shop_id；product_id ← 槽位 offer_id；web_site = 1688（常量）；scene = 1688（常量）
argv：["xclaw-cli","platform","alibaba","product","get","--shop-id","<店铺ID>","--product-id","<商品ID>","--web-site","1688","--scene","1688","--json"]
环境：XCLAW_UMC_TOKEN=<由运行环境注入>（或事先执行 xclaw-cli auth login）
```

参数的确切含义和返回结构以 `cli.output_description` 指向的 `xclaw-cli schema … --json` 为准，不要猜字段。

### 店铺 ID 与西之月登录

cs 的 17 个已启用意图里，有 15 个带槽位 `shop_id`（显示名「店铺」，**不是**向买家追问的必填项）。绑定到这些意图上的西之月 CLI，入参 `--shop-id` 都映射到这个槽位。没有 `shop_id` **不要调用**这些命令。

`shop_id` 从哪里来，按这个顺序：

1. **会话上下文（设计预期）**：询盘来自某一家已授权的 1688 店铺时，由接入层（IM / 客服工作台）把该店铺的 ERP 店铺 ID 注入会话，你写入槽位 `shop_id`。槽位的默认追问是「（不向买家追问：由 Bot 按会话所属的 1688 店铺带入 ERP 店铺 ID）」，不要把这句话发给买家。
2. **本平台目前没有「空间级运行时变量」或「默认店铺 ID」**。空间设置只有识别阈值、澄清、追问、工具重试和最终动作，运行时配置里也没有 `shop_id` 字段。不要臆造 `GET /api/v1/spaces/{id}/vars` 或类似接口。
3. 会话里没有时：向使用你的人要这个店铺的 ERP 店铺 ID，拿到再调用工具。
4. 若运行环境已经登录西之月，可以列出已授权店铺供人挑选（有多家时不要自己猜）：

```bash
xclaw-cli mall platform-store shop list --platform-code alibaba --json
```

这条命令没有必填参数，可选 `--platform-code alibaba` 只看 1688。也可以用 `xclaw-cli operate customer shop alibaba get --page 1 --size 20 --json`（`page`、`size` 必填）。

不要用 `xclaw-cli platform alibaba product list` 去「发现店铺」：它是「指定店铺的商品列表」，**必填** `--shop-id`，没有店铺 ID 时这条命令会直接失败。

调用任何 `xclaw-cli` 之前，运行环境必须先具备西之月凭证，任选一种即可：

- 执行 `xclaw-cli auth login`（通过 UMC OAuth 登录 MoonClaw）；或
- 设置环境变量 `XCLAW_UMC_TOKEN`

工具定义里写的是 `cli.auth_env = XCLAW_UMC_TOKEN`、`cli.login_command = xclaw-cli auth login`。未登录就执行，属于运行环境问题：转人工并记录，不要向用户索要账号或 token。可用 `xclaw-cli auth status --json` 查看当前登录状态。

### 3.5 写操作与「需确认」

- 工具的 `confirm_required=true`（顶层或 `cli` / `mcp` / `workflow` 内）表示它会改动外部数据。调用前**必须先得到确认**：面向用户的场景要用户明确同意；客服类场景按空间规则由人工确认（cs 的 R1：输出草稿，由人点发送）。没有确认就不调用。
- cs 中需确认的工具：`xclaw_platform_alibaba_trade_order_modifyorderprice_call`（修改订单价格）、`xclaw_platform_alibaba_trade_refund_sellerview_query`（退款单查询，目录标注为高风险）。
- 改价入参 `carriage`、`entry_discounts` 的单位是**分**（`entry_discounts` 正数涨价、负数减价），由人按目标价换算后填写；你不要自行换算后直接执行。
- 不要对用户说「已改好」「已退款」，除非工具返回成功。

### 3.6 失败时怎么办

- **绝不编造**库存、价格、交期、物流状态、退款结果。这些只能来自工具返回；没有就说明需要确认，或转人工。
- 工具失败：按 `tool_retry` 重试；仍失败时澄清（缺什么信息）或执行最终动作。不要把失败结果当作数据使用。
- CLI 返回登录 / 授权类错误：这是运行环境问题，转人工并记录，不要让用户提供凭据。
- 平台接口 401：Key 失效，停止调用并报告；429：按 `Retry-After` 秒数等待。
- 工具缺口：某些意图需要的能力还没有接口（cs 例：锁仓、预计到货日、包邮门槛、资质证书、打样费）。这类问题只说明会由人工确认，或转人工，不要承诺。

### 3.7 检索知识：参考 vs 校验

- **参考（reference）**：回答或生成时的依据。按 `knowledge` 里的 `kb_id` / `doc_id` / `chunk_id` 读取，或在对话中按需检索。
- **校验（validate）**：硬约束，全文已在 `validation_sets` 里，生成后逐条比对。`chunk_id="*"` 表示整篇文档都是校验项。
- 检索接口：
  - 指定知识库：`POST /api/v1/kbs/{kb_id}/search`，body `{"query": "改价", "top_k": 2}`，可加 `platform` / `site` / `language` / `category` 过滤、`threshold`。
  - 跨全部知识库：MCP `search_knowledge` 不传 `kb_id`（REST 没有跨库检索）。
  - 读切片：`GET /api/v1/kbs/{kb_id}/chunks?doc_id=…&q=…`。

```json
[{"score": 0.9, "kb_name": "1688客服-回复规则与禁止项", "doc_name": "回复规则与禁止项_v1.json",
  "chunk": {"chunk_id": "RULE-13", "doc_id": "RULE", "kb_id": "kb_cs_reply", "order": 13,
            "content": "意图：售中改价改单\n阶段：售中\n…\n禁止：未改先说已改好\n…",
            "cells": {"意图": "售中改价改单", "禁止": "未改先说已改好", "主工具(按序)": "apply_change_price"},
            "effective_tags": {"platform": ["1688"], "site": [], "language": ["zh"], "category": []},
            "linked_intents": ["B.MID.CHANGE_ORDER"]}}]
```

切片 `tags` 是切片自己设置的标签，`effective_tags` 是继承知识库后的生效标签，过滤按生效标签。

## 4. 写路径：维护型 AI 怎么写入

你的职责是帮运营**整理意图和知识**，写回草稿，由运营审核、关联知识与工具后启用。需要具备 `意图:写` / `知识:写` 的 Key（第 0.2 节创建时勾选对应权限；只有三个「读」的 Key 会返回 403 `SCOPE_FORBIDDEN`）。

分类节点、分类层级、工具注册、以及意图上的知识关联 / 工具绑定，**没有**开放给 API Key 或 MCP `intent_platform` 工具（不要臆造 `/api/v1` 或 MCP 接口去写它们）。这些只在控制台完成，后台走的是登录会话下的 `/api/admin` 接口，不接受 API Key。

### 4.1 你能写什么、不能写什么

| 对象 | 能否通过接口写 | 方式 |
|---|---|---|
| 意图 | 能（只到草稿） | `POST /api/v1/intents:batchUpsert`、`PATCH /api/v1/intents/{code}`、MCP `upsert_intents` |
| 槽位 | 能 | `POST /api/v1/slots`、`PUT /api/v1/slots/{name}`（`DELETE` 存在，但不要调用） |
| 空间设置 | 能（维护类，慎用） | `PUT /api/v1/recognition`，仅在运营明确要求时 |
| 知识 | 能 | `POST /api/v1/kbs/{kb_id}/documents:import`、`PUT /api/v1/kbs/{kb_id}/chunks/{chunk_id}`、MCP `import_knowledge` |
| 分类、层级 | **不能** | 只在控制台「空间设置 › 分类层级」由运营维护；你只读 `GET /api/v1/taxonomy` 并按已有分类写 |
| 意图的知识关联、工具绑定、启用 | **不能** | 由运营在控制台完成；`PATCH` 只接受 name、description、examples、slots、status（草稿 / 停用） |
| 工具注册、MCP 同步、技能上传 | **不能** | 由技术在工具中心完成；你只读 `GET /api/v1/tools` |
| 新建知识库、删除任何数据 | **不能** | 知识只能导入到已有知识库 |

### 4.2 写意图的步骤

1. `GET /api/v1/spaces` 确认空间；不确定写哪个空间就先问运营。之后意图类请求都带 `X-Space-Id`。
2. `GET /api/v1/taxonomy`：拿到层级和分类树。`category_path` 必须从第一层写到最末层，每段写编码或名称；cs 只有一层，写 `["PRE"]` 或 `["售前"]`。
3. `GET /api/v1/slots`：本空间槽位，**优先复用**（已有 `qty` 就不要写 `quantity`）。
4. `GET /api/v1/intents?category=PRE&q=关键词`：查重。含义相同的意图只补例句，不另建编码。
5. 按规则整理：
   - `code`：`{前缀}.{第一层编码}.{英文名}`，全大写，下划线连接，全局唯一，如 `B.PRE.PAYMENT`；第一层没有编码时 `{前缀}.{英文名}`。
   - `name` ≤ 30 字，同空间不重名。
   - `description` ≥ 15 字，写清：用户想做什么；什么算；什么不算（→ 易混意图）。
   - `examples` ≥ 3 条，用户原话风格，不含手机号、微信等隐私。
   - `slots`：`[{name, required, ask}]`，`ask` 留空则继承槽位默认追问。
6. 写回（每批 ≤ 100 条，必须带 `Idempotency-Key`）：

```http
POST /api/v1/intents:batchUpsert
Authorization: Bearer $INTENT_PLATFORM_API_KEY
X-Space-Id: cs
Idempotency-Key: 7f3a-20261111-0001
Content-Type: application/json

{"mode": "merge", "intents": [{
  "code": "B.PRE.PAYMENT", "name": "付款方式咨询（示例）", "category_path": ["PRE"],
  "description": "示例：买家询问支持哪些付款方式；问「能用信用卡吗」算；已下单问改价不算（→ B.MID.CHANGE_ORDER）",
  "examples": ["支持什么付款方式", "可以用信用卡付吗", "能先发货后付款吗"],
  "slots": [{"name": "offer_id", "required": false}]}]}
```

7. 读结果：`items` 是成功的条目，`conflicts` 是被跳过的条目和原因，只修正并重提有问题的条目；`new_slots` 是本次自动新建的「文本」槽位（写了本空间没有的槽位名时发生），请提醒运营补全；`ignored_fields` 是被忽略的字段（如旧的 `fallback`）。

```json
{"created": 1, "updated": 0, "skipped": 2,
 "items": [{"code": "B.PRE.PAYMENT", "result": "created", "status": "草稿"}],
 "conflicts": [
   {"code": "B.PRE.STOCK", "reason": "DESCRIPTION_TOO_SHORT", "message": "意图描述至少 15 个字（当前 1）"},
   {"code": "B.BAD", "reason": "UNKNOWN_CATEGORY", "message": "分类「XX」不存在（第一层：阶段）；…"}],
 "import_id": "IMP-1008", "space": "cs", "new_slots": [], "ignored_fields": []}
```

8. 告诉运营：写入的是草稿，需要在控制台关联知识、绑定工具并启用。

`mode` 只用 `merge`：只补充新例句、新槽位，不覆盖已有非空字段。`overwrite` 会替换名称、描述、例句、槽位，作用于已启用意图时被拒绝（`ENABLED_PROTECTED`），未经运营要求不要用。

改单个草稿意图：

```http
PATCH /api/v1/intents/B.PRE.PAYMENT
X-Space-Id: cs
Idempotency-Key: 7f3a-20261111-0002
Content-Type: application/json

{"examples": ["支持什么付款方式", "可以用信用卡付吗", "能先发货后付款吗", "支持对公转账吗"]}
```

已启用意图不能 PATCH（403 `ENABLED_PROTECTED`）；把 status 设为「启用」返回 403 `ENABLE_FORBIDDEN`。

### 4.3 写槽位

```http
POST /api/v1/slots
X-Space-Id: cs
Idempotency-Key: 7f3a-20261111-0003
Content-Type: application/json

{"name": "color", "display_name": "颜色", "type": "枚举", "options": ["黑", "白"], "default_ask": "您要什么颜色？"}
```

- `name` 小写英文 / 数字 / 下划线，创建后不可改；`type` 为 文本 / 数字 / 日期 / 枚举 / 文件 / 图片；枚举必须给 `options`；`default_ask` 必填。
- 同名返回 409 `SLOT_EXISTS`：直接用已有的。被意图使用的槽位删除会返回 409 `SLOT_IN_USE`。
- 文件 / 图片槽位的值是文件链接；工具的 file / file_list 入参只能映射到这两类槽位。

### 4.4 导入和编辑知识

1. `GET /api/v1/kbs` 找合适的库，看 `type`、`headers`（表格库）和 `tags`。
2. `GET /api/v1/kbs/{kb_id}/documents` 查同名文档。
3. 导入（REST 用 multipart；MCP `import_knowledge` 用 base64）：

```bash
BASE=${INTENT_PLATFORM_BASE_URL:-http://intent.47.86.15.93.sslip.io}
curl -X POST "$BASE/api/v1/kbs/kb_cs_reply/documents:import" \
  -H "Authorization: Bearer $INTENT_PLATFORM_API_KEY" -H "Idempotency-Key: 7f3a-20261111-0004" \
  -F "files=@回复规则补充.csv"
```

```json
{"import_id": "IMP-1010", "status": "构建中", "documents": [{"name": "回复规则补充.csv", "action": "created"}],
 "affected_intents": [], "message": "构建中"}
```

4. `GET /api/v1/imports/{import_id}` 查构建结果（「构建成功」「构建失败」）。

规则：表格库的表头必须与 `headers` 完全一致（列名和顺序），否则 400 `HEADER_MISMATCH`；每行一个切片。与已有文档同名或同内容时返回 409 `OVERWRITE_REQUIRED` 并列出受影响意图，**把影响告诉运营，等确认后**才带 `overwrite=true` 重提。编辑单个切片用 `PUT /api/v1/kbs/{kb_id}/chunks/{chunk_id}`，body 可含 `content`（图文库）、`cells`（表格库，按表头）、`tags`。

知识只写来源可靠的内容。不要补充价格、库存、时效、政策等你无法证实的事实。

## 5. 通用约定

| 约定 | 规则 |
|---|---|
| 幂等 | 所有写请求（POST / PUT / PATCH / DELETE）带 `Idempotency-Key`（≤ 64 字符）。网络超时重试时**复用同一个值**：24 小时内同键同请求体返回首次结果（响应头 `Idempotent-Replayed: true`）；同键不同请求体 409 `IDEMPOTENCY_CONFLICT`。缺失返回 400 `IDEMPOTENCY_KEY_REQUIRED`。MCP 写工具用参数 `idempotency_key` |
| 批量与大小 | 单次最多 100 条意图或 100 个文件；单文件 ≤ 50MB；超出 413 `PAYLOAD_TOO_LARGE` |
| 限流 | 每个 Key 每分钟 60 次，超出 429 `RATE_LIMITED`，按 `Retry-After` 等待；能批量就批量 |
| 分页 | `GET /intents`、`GET /kbs/{kb_id}/chunks` 用 `page`、`page_size`（≤ 100），返回 `{total, items}` |
| 版本 | 每个空间独立的 `version`。启用 / 停用意图、编辑已启用意图、改空间设置、调整分类或层级、知识覆盖或工具变化影响已启用意图时 +1。`GET /recognition` 也返回 `version` |
| 时间 | 时间字段为 ISO 8601 UTC（以 `Z` 结尾） |

## 6. 错误码

| HTTP | 常见 code | 你下一步 |
|---|---|---|
| 400 | `BAD_REQUEST`、`IDEMPOTENCY_KEY_REQUIRED`、`IDEMPOTENCY_KEY_INVALID`（超过 64 字符）、`HEADER_MISMATCH`、`UNSUPPORTED_EXTENSION`、`EMPTY_FILE`、`BAD_CONTENT_TYPE`、`NOT_A_SKILL`（对非技能工具下载技能包） | 读 `message` / `details` 修正后再提，不要原样重试 |
| 401 | `UNAUTHORIZED`（缺少 API Key）、`KEY_REVOKED` | 按第 0.4 节处理：没配 Key 就向人要，已吊销就请管理员重发；不要到其他目录搜索 Key |
| 403 | `SCOPE_FORBIDDEN`、`SPACE_FORBIDDEN`、`ENABLE_FORBIDDEN`、`ENABLED_PROTECTED` | 不重试；说明缺什么权限或改由运营在控制台操作 |
| 404 | `INTENT_NOT_FOUND`、`KB_NOT_FOUND`、`CHUNK_NOT_FOUND`、`SLOT_NOT_FOUND`、`IMPORT_NOT_FOUND`、`TOOL_NOT_FOUND` | 重新列表查询确认 ID |
| 409 | `IDEMPOTENCY_CONFLICT`、`SLOT_EXISTS`、`SLOT_IN_USE`、`OVERWRITE_REQUIRED` | 幂等冲突换新键；覆盖类等运营确认；槽位已存在就复用 |
| 413 | `PAYLOAD_TOO_LARGE` | 拆批 |
| 422 | `VALIDATION_FAILED` | 按 `details.problems` 补全后只重提问题条目 |
| 429 | `RATE_LIMITED` | 按 `Retry-After` 等待 |

`batchUpsert` 的逐条冲突原因（在 `conflicts[].reason`，HTTP 仍为 200）：`INVALID_CODE`、`CODE_IN_OTHER_SPACE`、`UNKNOWN_CATEGORY`、`DUPLICATE_NAME`、`DUPLICATE_IN_BATCH`、`MISSING_FIELD`、`DESCRIPTION_TOO_SHORT`、`TOO_FEW_EXAMPLES`、`SLOT_ASK_MISSING`、`ENABLED_PROTECTED`。处理方法见 `AI接入说明.md` 第 8 节。

## 7. 用 MCP 代替 REST

本平台自带一个 MCP 服务 `intent_platform`（`POST /mcp`，方法 `initialize`、`ping`、`tools/list`、`tools/call`）。偏好 MCP 的 AI 可以只用它：

| MCP 工具 | 对应 REST | scope |
|---|---|---|
| `list_intents` | `view=intents`（默认）→ `GET /intents`；`view=taxonomy` / `slots` / `spaces` / `recognition` → 对应读接口 | 意图:读 |
| `get_intent` | `GET /intents/{code}` | 意图:读 |
| `upsert_intents` | `POST /intents:batchUpsert`（只 merge，必须带 `idempotency_key`） | 意图:写 |
| `search_knowledge` | `POST /kbs/{kb_id}/search`；不传 `kb_id` 时检索全部知识库 | 知识:读 |
| `import_knowledge` | `POST /kbs/{kb_id}/documents:import`（文件用 `content_base64`） | 知识:写 |
| `list_tools` | `GET /tools`（另可按 `type`、`q` 筛选） | 工具:读 |

```json
{"jsonrpc": "2.0", "id": 2, "method": "tools/call",
 "params": {"name": "search_knowledge", "arguments": {"query": "催发货", "top_k": 1}}}
```

结果在 `result.content[0].text`（JSON 字符串）和 `result.structuredContent` 里；接口错误时 `result.isError=true`，内容是同样的 `{code, message, details}`。参数不合法返回 `isError=true` 的 `BAD_REQUEST`。`get_intent` 的 `code` 接受 2～7 段编码（如 `B.MID.FULFILL`）。

写类 MCP 工具的额外规则：`upsert_intents` 和 `import_knowledge` 必须带 `idempotency_key`（含义同 HTTP 的 `Idempotency-Key`）；写入的意图一律是草稿，不能启用。覆盖已有知识文档前必须先得到运营确认，再把 `overwrite=true` 传入（否则 409 `OVERWRITE_REQUIRED`）。这与第 3.5 节「调用外部写操作工具前必须先确认」是两件事：前者是改平台配置，后者是 Bot 调用 `confirm_required=true` 的外部工具。

## 8. 底线

- 不编造库存、价格、交期、物流、退款结果；工具没给就不说。
- 写操作先确认；没确认不调用 `confirm_required=true` 的工具。
- 不向用户索要账号、密码、token、cookie；凭据只在运行环境里。本平台 API Key 只从第 0.3 节约定的环境变量或 `~/.intent-platform/config.json` 读取，找不到就停下来问人。
- 不跨空间写入；不自建分类；不建同义槽位；不删除任何数据；不启用意图；未经确认不覆盖知识文档。
- 维护时不要用 `runtime-config` 当整理依据（它只含已启用意图）；整理用 intents 系列接口。

## 9. 接口速查

| 方法 | 路径 | scope | 空间 |
|---|---|---|---|
| GET | `/api/v1/spaces` | 意图:读 | — |
| GET | `/api/v1/taxonomy` | 意图:读 | 按空间 |
| GET / PUT | `/api/v1/recognition` | 意图:读 / 意图:写 | 按空间 |
| GET / POST | `/api/v1/slots` | 意图:读 / 意图:写 | 按空间 |
| PUT / DELETE | `/api/v1/slots/{name}` | 意图:写 | 按空间 |
| GET | `/api/v1/intents` | 意图:读 | 按空间 |
| GET / PATCH | `/api/v1/intents/{code}` | 意图:读 / 意图:写 | 按空间 |
| POST | `/api/v1/intents:batchUpsert` | 意图:写 | 按空间 |
| GET | `/api/v1/runtime-config` | 意图:读 | 按空间 |
| GET | `/api/v1/kbs` | 知识:读 | 全局 |
| GET | `/api/v1/kbs/{kb_id}/documents` | 知识:读 | 全局 |
| POST | `/api/v1/kbs/{kb_id}/documents:import` | 知识:写 | 全局 |
| GET | `/api/v1/kbs/{kb_id}/chunks` | 知识:读 | 全局 |
| PUT | `/api/v1/kbs/{kb_id}/chunks/{chunk_id}` | 知识:写 | 全局 |
| POST | `/api/v1/kbs/{kb_id}/search` | 知识:读 | 全局 |
| GET | `/api/v1/imports/{import_id}` | 知识:读 | 全局 |
| GET | `/api/v1/tools` | 工具:读 | 全局 |
| GET | `/api/v1/tools/{name}/skill-package` | 工具:读 | 全局 |
| POST | `/mcp` | 按工具 | 意图类可传 `space` |
