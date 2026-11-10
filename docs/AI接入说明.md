# 意图管理平台 · 给 AI 的接入说明

> 读者：运营使用的 AI 工具（如 Claude、Cursor、自建 Agent），以及读取运行时配置的 Bot / 智能体。请在每次会话开始时完整阅读本说明。
> 接口定义见同目录 `openapi.yaml`（OpenAPI 3.0）。Base URL：`https://intent.example.com`，鉴权：`Authorization: Bearer ipk_xxx`，空间：意图类接口带 `X-Space-Id: {space_id}`，知识库与工具接口全局、不需要。

## 1. 平台是什么

意图管理平台是**通用的意图与知识管理平台**，服务多个 Bot 和智能体（如 1688 智能客服、Moonclaw 跨境智能体）。
- **业务空间**：每个 Bot / 智能体对应一个空间。意图、意图分类、槽位、意图导入记录、运行时配置及其版本按空间隔离；**知识库和工具全平台通用，不区分空间**，任何空间的意图都能关联。
- **意图**：用户想办的一件事，比如「问是否有现货」「生成产品主图」。每个意图有编码、名称、分类路径、意图描述、例句、槽位（处理前要拿到的信息），以及由运营关联的知识和工具。
- **分类层级（v0.6 起按空间动态配置）**：每个空间在「空间设置」里自定义层数和每层名称，最后一层固定是意图，上面有 0～5 层分类。例如 1688 智能客服是「大类 → 问题桶 → 意图」，别的空间可能是「业务线 → 场景 → 子场景 → 意图」。意图必须挂在**最末一层分类**下；分类路径用 `category_path` 表示（从第一层到最末层）。**不要假设固定是两层分类**，每次先读 `GET /api/v1/taxonomy`。
- **空间设置**：每个空间一份，统一规定识别阈值、澄清、槽位追问次数、工具失败重试和最终动作（转人工 / 交给通用助手 / 告知无法处理）。意图上**不再配置兜底条件**（见第 5 节）。
- **工具**：技术在平台「工具中心」登记的外部能力，共 4 种类型：`http`（API 接口）、`mcp`（MCP 工具）、`cli`（CLI 命令）、`skill`（技能/工作流）。MCP 按「**MCP 服务 → 子工具**」两级登记：一个第三方 MCP 服务通常包含一批工具，平台同步它的 tools/list，意图只关联其中具体的子工具（工具名形如 `{服务名}.{远端工具名}`，如 `compliance.check_ip_risk`）。平台只登记，**执行在 Bot / 智能体侧**（见第 12 节）。本说明里的接口本身也作为「平台内置」工具登记在工具中心（见第 13 节）。
- **槽位**：按空间维护的槽位列表（如 `platform`、`site`、`sku`），每个空间一份、互不可见；意图只能从本空间的槽位里选。
- **知识库**：分「表格知识库」和「图文知识库-富文本」，导入后构建为知识切片。知识库和切片带适用范围标签（平台 / 站点 / 语言 / 类目）。意图关联知识时要标明用途：**参考**或**校验**。
- **状态**：草稿 / 启用 / 停用。只有运营可以启用；Bot 只使用启用的意图。

你的职责：帮运营**整理意图和知识**，并把结果写回平台的**草稿**，由运营在平台上审核、关联知识和工具后启用。

## 2. 工作流

1. **先选空间**：`GET /api/v1/spaces`，确认要写入的空间（如 `cs` 1688 智能客服、`mc` Moonclaw 跨境智能体）。之后**意图类请求**（taxonomy、slots、intents、runtime-config）都带 `X-Space-Id`（或 query 参数 `space`）；它必须是 Key 绑定的空间之一（一个 Key 可绑定多个空间），否则返回 403 `SPACE_FORBIDDEN`。知识库（kbs、imports）和工具（tools）请求不需要 X-Space-Id。**不确定该写入哪个空间就先问运营。**
2. **读分类**：`GET /api/v1/taxonomy`，拿到当前空间的层级定义 `levels`（每层名称，最后一层是意图）和分类树 `categories`（每个节点有 `name`，第一层节点可能有 `code`）。
3. **读本空间槽位**：`GET /api/v1/slots`（带空间）。整理意图时**优先复用本空间已有槽位**（见第 4 节）。
4. **读已有意图**：`GET /api/v1/intents?category={分类 id}`（含其下所有子分类；或 `q=关键词`），必要时 `GET /api/v1/intents/{code}` 读详情。
5. **查重**：对每个准备写入的意图，检查是否已有相同含义的意图（名称、意图描述、例句相近）。已存在就补充例句，不要另建一个新编码。
6. **整理**：按第 3、4 节的规则补全字段。
7. **写回**：`POST /api/v1/intents:batchUpsert`，`mode` 固定为 `merge`，每批 ≤ 100 条，带 `Idempotency-Key`。
8. **读结果**：逐条查看 `conflicts`，按第 8 节修正后只重提有问题的条目；`new_slots` 列出这次在本空间自动新建的槽位，请告知运营。每次写入都会在当前空间「意图管理 › 导入记录」留下一条记录（对应 `import_id`）。
9. **告知运营**：写回的都是**草稿**，请运营在平台上关联知识/工具并启用。**不要尝试启用**。

知识整理同理（知识只能导入到已有知识库）：`GET /api/v1/kbs` 找合适的库和表头（全平台的库都可用，按标签判断适用场景） → `GET /api/v1/kbs/{kb_id}/documents` 查同名文档 → `POST /api/v1/kbs/{kb_id}/documents:import` → `GET /api/v1/imports/{import_id}` 查构建结果。

## 3. 意图字段规则

| 字段 | 规则 |
|---|---|
| code | 全大写，单词用下划线连接，全局唯一，创建后不可改。第一层分类有编码时用 `{空间前缀}.{第一层编码}.{英文名}`（如 1688 智能客服 `B.STOCK.PREORDER`、Moonclaw `M.DESIGN.MAIN`）；第一层没有编码时用 `{空间前缀}.{英文名}` |
| name | 中文短名，≤ 30 字，同一空间内不能重名 |
| category_path | 分类路径数组，从第一层到最末层，每段写该层分类的 `code` 或 `name`，如 `["STOCK", "现货"]`；必须取自当前空间的 taxonomy，**不要自建**。旧字段 `l1` / `l2` 仍可用于两层分类的空间，但已不推荐 |
| description（意图描述） | **至少 15 个字**。写清三件事：用户想做什么；什么情况算这个意图；什么情况不算（用「→ 编码/名称」指出易混淆意图）。示例：用户想为商品生成符合平台规范的主图；"做主图""首图"算；只要换白底不算（→ M.DESIGN.WHITE_BG） |
| examples | **至少 3 条**，用用户原话风格，不重复，不含手机号/微信等隐私 |
| slots | `[{name, required, ask}]`。`name` 用本空间槽位列表里的英文名；`ask` 可省略，省略时继承该槽位的默认追问话术，只有需要特殊说法时才填 |

**0.5 → 0.6 兼容**：写入时仍接受 `l1` / `l2`（仅限两层分类的空间，等价于 `category_path: [l1, l2]`），列表筛选 `l1` / `l2` 仍可用；输出里不再有 `l1` / `l2`，改为 `category_path`；冲突原因 `UNKNOWN_L1` / `UNKNOWN_L2` 合并为 `UNKNOWN_CATEGORY`。工具的 `mcp_server` 由 URL 字符串改为连接信息对象。

关联知识（含用途）、绑定工具和入参映射由运营在平台上完成，你只需在意图描述或例句中把需要的信息体现清楚（可先 `GET /api/v1/tools` 了解工具需要哪些参数（工具全平台通用），并把这些参数设为槽位）。

## 4. 槽位规则

- **按空间**：槽位属于某个空间，其他空间看不到；同一个英文名在不同空间是两份独立的槽位。
- **先读后用**：写意图前 `GET /api/v1/slots`，按英文名、显示名和含义匹配本空间已有槽位。例如本空间已有 `qty` 就用 `qty`，不要写 `quantity`、`num`、`count`；已有 `sku` 就不要写「商品」「链接」。**不要新建同义槽位**（MVP 平台不做同义名自动归一，需要你自己避免）。
- 槽位字段只有 4 项：英文名 + 显示名、类型（文本 / 数字 / 日期 / 枚举）、可选值（仅枚举）、默认追问话术。
- 确实没有合适的槽位时，可以 `POST /api/v1/slots` 在本空间新建：`name` 小写英文 + 下划线；必填 `display_name`、`type`、`default_ask`；枚举类型要给 `options`。本空间已有同名槽位返回 409。
- 在 batchUpsert 里写了本空间不存在的槽位名时，平台会在本空间自动新建为「文本」槽位（默认追问「请补充{name}」），并在 `new_slots` 列出，请提醒运营去「意图管理 › 槽位」补全显示名和追问话术。所以写入前务必用英文名并先查重。
- `PUT /api/v1/slots/{name}` 只用于补全显示名、类型、可选值或默认追问话术，英文名不能改。不要调用 `DELETE /api/v1/slots/{name}`（被意图使用的槽位会被拒绝删除，返回 409 `SLOT_IN_USE`）。

## 5. 空间设置与 Bot 运行流程（兜底）

v0.5 起**取消意图级兜底条件**：意图上不再有 `fallback` 字段，写入时如果带了会被忽略（不报错，结果里 `ignored_fields` 会列出 `fallback`）。兜底改由**空间设置**统一处理，运营在「意图管理 › 空间设置」维护，`GET /api/v1/recognition` 可读取，运行时配置里是顶层的 `recognition`：

| 字段 | 含义 | 默认 |
|---|---|---|
| `threshold` | 识别阈值 0～1，Top1 置信度 ≥ 阈值才算命中。数值含义由 Bot 的识别模型决定，建议用单句测试和实际对话校准 | 0.6 |
| `candidate_min` | 澄清候选下限 = 阈值 ÷ 2 | 0.3 |
| `max_candidates` | 澄清时最多列出的候选数 | 3 |
| `clarify_prompt` | 澄清话术 | 我想确认一下，您是想要以下哪一项？ |
| `max_clarify` | 澄清最多次数，超过执行最终动作 | 2 |
| `max_slot_ask` | 每个必填槽位最多追问次数，超过执行最终动作 | 2 |
| `tool_retry` | 工具调用失败后的重试次数，仍失败执行最终动作 | 1 |
| `final_action` | `handoff_human`（转人工）/ `general_assistant`（交给通用助手）/ `decline`（告知无法处理，回复 `final_message`） | 1688 智能客服：转人工；Moonclaw：交给通用助手 |

**Bot 运行流程**（每轮对话）：

1. **识别**：用意图描述和例句对用户原话打分，得到 Top3 意图及置信度。
2. **阈值判定**：Top1 ≥ `threshold` → 命中；否则取置信度 ≥ `candidate_min` 的候选（最多 `max_candidates` 个），用 `clarify_prompt` 列给用户选；没有候选 → 直接执行最终动作。
3. **澄清**：用户选中候选即命中该意图；澄清超过 `max_clarify` 次仍不明确 → 最终动作。
4. **槽位追问**：命中后缺必填槽位时用 `slots[].ask` 追问；同一槽位追问超过 `max_slot_ask` 次 → 最终动作。
5. **知识校验**：按 `knowledge` 检索参考知识；生成结果逐条比对 `validation_sets`（校验知识），不通过就改写或拦截。
6. **调用工具**：按 `tools[].param_map` 组装入参调用（见第 12 节）。
7. **失败重试**：工具报错或超时，重试 `tool_retry` 次。
8. **最终动作**：以上任一环节超限或失败，执行 `final_action`。

「议价超过 3 轮转人工」「改价超过 ¥5000 转人工」这类**业务规则**不属于空间设置，规划在 V2 规则引擎中配置；MVP 期间由 Bot 侧自行处理。整理意图时不要把这类规则写进意图描述或例句。

## 6. 知识用途与标签

- **用途**：意图关联知识时，`usage=reference`（参考：检索后用于回答或生成）或 `usage=validate`（校验：生成结果要逐条比对，是硬约束，例如违禁词、平台图片规范）。可以关联单个切片，也可以关联整篇文档（`chunk_id="*"`，适合作为校验集）。
- **运行时配置**：`GET /api/v1/runtime-config` 返回的 `validation_sets` 包含所有「校验」知识的**完整内容**，Bot 在生成后必须逐条校验；「参考」知识只给出引用，由 Bot 按需检索。
- **标签**：知识库和切片带 `platform`、`site`、`language`、`category` 四类标签；切片没设置的标签继承知识库的。标签为空表示不限。
- **按标签检索**：`POST /api/v1/kbs/{kb_id}/search` 和 MCP `search_knowledge` 都可传 `platform`、`site`、`language`、`category` 过滤。例如给 TikTok 美区生成主图前，用 `platform=TikTok, site=US` 检索规范。
- 知识库全平台通用：`GET /api/v1/kbs` 返回全部库，用标签（platform / site / language / category）判断是否适用于当前场景；不需要 X-Space-Id。
- 演示数据中的「违禁词库」「平台图片规范」「视频规范」「POD 印刷规范」「IP / 侵权风险词」「节日营销日历」「店铺装修规范」都是**示例**，不是真实平台规则（切片以「【示例，非真实平台规定】」开头）。

## 7. 禁止事项

- 不在未确认空间的情况下写入；不把一个空间的意图写到另一个空间。
- 不自建分类，不改分类层级（层级由运营在「空间设置」调整）。
- 不建同义槽位；不改槽位英文名；不跨空间复用槽位（每个空间自己维护）。
- 不删除任何意图、知识库、文档、切片（接口也不提供删除）；不删除槽位。
- 不覆盖已启用意图：只用 `mode=merge`；`overwrite` 作用于已启用意图会被拒绝（`ENABLED_PROTECTED`）。
- 不把任何意图的状态改为「启用」。
- 未经运营确认，不覆盖知识文档（不自行传 `overwrite=true`）。
- 不注册、不修改工具；除本说明列出的平台内置接口外，不调用工具中心里的外部工具（它们由 Bot 在运行时执行）。
- 整理意图时不要用 `GET /api/v1/runtime-config`：它是给 Bot 运行时用的，只含已启用意图；整理请用 intents 系列接口。
- 不在例句或知识中写入用户隐私和站外联系方式。

## 8. 常见错误及处理

| 现象 | 原因 | 处理 |
|---|---|---|
| 403 `SPACE_FORBIDDEN` | 意图类请求没带空间，或空间不在 Key 绑定范围内 | 用 `GET /api/v1/spaces` 确认空间；换对应空间的 Key |
| conflicts: `INVALID_CODE` | 编码格式不对 | 按「前缀.第一层编码.NAME」（第一层无编码时「前缀.NAME」）全大写重写编码 |
| conflicts: `CODE_IN_OTHER_SPACE` | 编码已被其他空间使用 | 换编码，或确认是否写错空间 |
| conflicts: `UNKNOWN_CATEGORY` | 分类路径在当前空间不存在、层数不对或没到最末一层 | 重新读 taxonomy，选最接近的已有最末层分类，写完整路径 |
| conflicts: `DUPLICATE_NAME` | 与同空间其他意图同名 | 查该意图，含义相同就并入它的编码，否则改名 |
| conflicts: `DUPLICATE_IN_BATCH` | 同批重复编码 | 合并成一条 |
| conflicts: `DESCRIPTION_TOO_SHORT` | 意图描述少于 15 个字 | 按第 3 节补写「想做什么 / 什么算 / 什么不算」 |
| conflicts: `TOO_FEW_EXAMPLES` | 例句不足 3 条 | 补足例句后重提该条 |
| conflicts: `SLOT_ASK_MISSING` | 必填槽位没有追问话术，且该槽位也没有默认追问话术 | 补 ask，或请运营补槽位默认追问 |
| conflicts: `ENABLED_PROTECTED` | 试图覆盖已启用意图 | 改用 merge，只补充例句 |
| 409 `SLOT_EXISTS` | 本空间已有同名槽位 | 直接使用已有槽位 |
| 409 `SLOT_IN_USE` | 删除的槽位正被意图使用 | 不要删除；如运营确需删除，先在 `details.used_by` 列出的意图中移除 |
| 400 `HEADER_MISMATCH` | 表头不一致 | 按 `GET /api/v1/kbs` 返回的 headers 调整文件表头 |
| 409 `OVERWRITE_REQUIRED` | 文档同名或同内容 | 把受影响意图告诉运营，等确认 |

## 9. MCP 工具

MCP 地址：`https://intent.example.com/mcp`，请求头 `Authorization: Bearer ipk_xxx`、`X-Space-Id: {space_id}`。意图类工具（list_intents、get_intent、upsert_intents）也接受 `space` 参数，不传时使用 Key 绑定的第一个空间；search_knowledge、import_knowledge、list_tools 是全局的，不需要 space。

```json
[
  {
    "name": "list_intents",
    "description": "列出当前空间的意图，或读取元数据：view=spaces 读空间列表，view=taxonomy 读本空间的分类层级定义（levels）与分类树（categories），view=slots 读本空间槽位列表，view=recognition 读本空间的空间设置。写入前先读 spaces、taxonomy 和 slots。只读。",
    "inputSchema": {
      "type": "object",
      "properties": {
        "space": {
          "type": "string",
          "description": "空间 ID，如 cs、mc；不传则用 Key 绑定的空间"
        },
        "view": {
          "type": "string",
          "enum": [
            "intents",
            "taxonomy",
            "slots",
            "spaces",
            "recognition"
          ],
          "default": "intents"
        },
        "category": {
          "type": "string",
          "description": "分类路径筛选，每段填编码或名称，用 / 分隔，可只写前几层，如 STOCK 或 STOCK/现货"
        },
        "status": {
          "type": "string",
          "enum": [
            "草稿",
            "启用",
            "停用"
          ]
        },
        "q": {
          "type": "string",
          "description": "关键词，匹配编码/名称/意图描述/例句"
        },
        "updated_after": {
          "type": "string",
          "format": "date-time"
        },
        "page_size": {
          "type": "integer",
          "minimum": 1,
          "maximum": 100,
          "default": 50
        }
      }
    }
  },
  {
    "name": "get_intent",
    "description": "按编码读取单个意图的完整信息（例句、槽位、关联知识及用途、工具映射）。只读。",
    "inputSchema": {
      "type": "object",
      "required": [
        "code"
      ],
      "properties": {
        "space": {
          "type": "string"
        },
        "code": {
          "type": "string",
          "pattern": "^[A-Z]{1,4}\\.[A-Z]+\\.[A-Z0-9_]+$"
        }
      }
    }
  },
  {
    "name": "upsert_intents",
    "description": "批量写回当前空间的意图，结果一律为草稿。mode 只用 merge；不能启用、不能删除、不能覆盖已启用意图。槽位名用本空间已有槽位的英文名，不要建同义槽位。单次最多 100 条，必须提供 idempotency_key。",
    "inputSchema": {
      "type": "object",
      "required": [
        "mode",
        "intents",
        "idempotency_key"
      ],
      "properties": {
        "space": {
          "type": "string"
        },
        "mode": {
          "type": "string",
          "enum": [
            "merge"
          ]
        },
        "idempotency_key": {
          "type": "string",
          "maxLength": 64
        },
        "intents": {
          "type": "array",
          "minItems": 1,
          "maxItems": 100,
          "items": {
            "type": "object",
            "required": [
              "code",
              "name",
              "description",
              "examples"
            ],
            "properties": {
              "code": {
                "type": "string",
                "pattern": "^[A-Z]{1,4}(\\.[A-Z0-9_]+){1,6}$"
              },
              "name": {
                "type": "string",
                "maxLength": 30
              },
              "category_path": {
                "type": "array",
                "items": {
                  "type": "string"
                },
                "minItems": 0,
                "maxItems": 5,
                "description": "从第 1 层到最末层分类的完整路径，每段填编码或名称（先用 list_intents view=taxonomy 读取），如 [\"STOCK\", \"现货\"]。空间有分类层时必填；0.5 版的 l1 / l2 仍兼容"
              },
              "l1": {
                "type": "string",
                "description": "已废弃（兼容）：等价于 category_path 第 1 段"
              },
              "l2": {
                "type": "string",
                "description": "已废弃（兼容）：等价于 category_path 第 2 段"
              },
              "description": {
                "type": "string",
                "minLength": 15,
                "description": "意图描述：用户想做什么；什么算；什么不算（易混淆意图）"
              },
              "examples": {
                "type": "array",
                "minItems": 3,
                "items": {
                  "type": "string"
                }
              },
              "slots": {
                "type": "array",
                "items": {
                  "type": "object",
                  "required": [
                    "name",
                    "required"
                  ],
                  "properties": {
                    "name": {
                      "type": "string",
                      "description": "本空间槽位英文名"
                    },
                    "required": {
                      "type": "boolean"
                    },
                    "ask": {
                      "type": "string",
                      "description": "留空则继承该槽位默认追问话术"
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  },
  {
    "name": "search_knowledge",
    "description": "在全部知识库（全平台通用）中按问题检索最相关的知识切片，可按平台/站点/语言/类目标签过滤，返回相似度、标签和已关联意图。只读。",
    "inputSchema": {
      "type": "object",
      "required": [
        "query"
      ],
      "properties": {
        "kb_id": {
          "type": "string",
          "description": "不传则检索全部知识库"
        },
        "query": {
          "type": "string"
        },
        "platform": {
          "type": "string",
          "description": "如 TikTok、Shopee、Amazon、1688"
        },
        "site": {
          "type": "string",
          "description": "如 US、VN、CN"
        },
        "language": {
          "type": "string",
          "description": "如 en、vi、zh"
        },
        "category": {
          "type": "string"
        },
        "top_k": {
          "type": "integer",
          "minimum": 1,
          "maximum": 20,
          "default": 5
        }
      }
    }
  },
  {
    "name": "import_knowledge",
    "description": "向已有知识库导入文件（异步构建）。表格库表头必须完全一致；与已有文档同名/同内容时默认返回冲突并列出受影响意图，需运营确认后才能 overwrite=true。",
    "inputSchema": {
      "type": "object",
      "required": [
        "kb_id",
        "files",
        "idempotency_key"
      ],
      "properties": {
        "kb_id": {
          "type": "string"
        },
        "idempotency_key": {
          "type": "string",
          "maxLength": 64
        },
        "overwrite": {
          "type": "boolean",
          "default": false
        },
        "files": {
          "type": "array",
          "maxItems": 100,
          "items": {
            "type": "object",
            "required": [
              "name",
              "content_base64"
            ],
            "properties": {
              "name": {
                "type": "string"
              },
              "content_base64": {
                "type": "string"
              }
            }
          }
        }
      }
    }
  },
  {
    "name": "list_tools",
    "description": "列出全部工具（外部：API 接口 / MCP 工具 / CLI 命令 / 技能·工作流；以及平台内置工具）及入参，用于在意图中准备对应槽位。全局，不区分空间。只读；不能注册或调用工具。",
    "inputSchema": {
      "type": "object",
      "properties": {
        "source": {
          "type": "string",
          "enum": [
            "external",
            "builtin"
          ]
        },
        "q": {
          "type": "string"
        },
        "type": {
          "type": "string",
          "enum": [
            "http",
            "mcp",
            "cli",
            "skill"
          ]
        }
      }
    }
  }
]
```

## 10. 错误码

| HTTP | 含义 | AI 下一步 |
|---|---|---|
| 400 | 请求参数或文件格式错误（字段类型、表头不一致、扩展名不支持） | 读 `code` 和 `details`，修正请求后重试；不要原样重试 |
| 401 | 未带 Key、Key 无效或已吊销 | 停止调用，请运营在「工具中心 › 接入与密钥」页检查或新建 Key |
| 403 | Key 无此权限、空间不在 Key 绑定范围内，或操作被规则禁止（启用意图、修改已启用意图） | 不要重试；告诉运营需要的权限/空间，或改由运营在平台操作 |
| 404 | 空间、意图、知识库、文档、切片或槽位不存在 | 重新列表查询，确认 ID 后再操作 |
| 409 | 冲突：文档同名/同内容需确认覆盖，本空间槽位已存在或仍被使用，或 Idempotency-Key 被不同请求体复用 | 覆盖类：把受影响意图告诉运营等待确认；幂等类：换一个新的 Key |
| 413 | 单次超过 100 条或文件过大 | 拆成每批 ≤ 100 条 / 拆分文件后分批提交 |
| 422 | 内容校验不通过（例句不足、必填槽位缺追问话术、分类不存在） | 按 `details` 补全对应字段，只重提有问题的条目 |
| 429 | 超过每分钟 60 次 | 按 `Retry-After` 秒数等待后重试，并合并请求、改用批量接口 |

## 11. 限制

- 单次写入最多 **100 条**（意图条数或文件数）。
- 所有写请求（POST / PUT / PATCH）必须带 **`Idempotency-Key`**；网络超时重试时复用同一个值，平台 24 小时内返回首次结果，不会重复写入。
- 每个 API Key **每分钟最多 60 次**请求，超出返回 429；意图类接口只能访问 Key 绑定的空间（可绑定多个），知识与工具接口不受空间限制。

## 12. 工具类型与 Bot 执行方式（给 Bot / 智能体）

> 运营的 AI 工具整理意图时只需了解工具入参（`GET /api/v1/tools`），**不注册、不调用工具**。本节给读取 `GET /api/v1/runtime-config` 的 Bot 看。

平台只登记工具和意图里的入参映射，执行在 Bot。四种类型的入参映射规则相同：`param_map` 中每个入参的 `source` 取 `槽位`（用识别到的槽位值）、`常量`（固定值）或 `上下文`（如 session_id、buyer_id、order_id、sku）；必填入参缺值时先按槽位追问，不要带空值执行。

| type | 平台登记的字段 | Bot 怎么执行 |
|---|---|---|
| `http`（API 接口） | `method`、`url`、`auth`、`input_schema` | 按 method + url 发请求；GET 把入参放 query，其他放 JSON body；凭据由 Bot 侧密钥托管提供，平台不下发密钥 |
| `mcp`（MCP 子工具） | `server`（服务名）、`mcp_server`（连接信息：`transport`、`url` 或 `command`/`args`/`env`、`auth`）、`mcp_tool`（远端工具名）、`input_schema`（同步自远端的 inputSchema）、`confirm_required` | 按 `mcp_server` 连接（同一服务只建一个会话），调用 `tools/call`，`name`=mcp_tool，`arguments`=入参；`confirm_required=true` 时先向用户确认再调用 |
| `cli`（CLI 命令） | `cli.bin`、`cli.command_template`、`cli.auth`、`cli.login_command`、`cli.install`、`cli.verified_version`、`cli.output_format`、`cli.confirm_required`、`input_schema` | 在 Bot 的运行环境里执行：未安装时按 `install` 安装；未登录或返回 `AUTH_REQUIRED` 时按 `login_command` 引导完成授权（不要向用户索要账号密码、cookie、token）；把模板里的 `{参数名}` 替换为入参值，**选填参数为空时连同它前面的 `--选项` 一起省略**，值含空格时加引号；以参数数组方式执行，不拼接 shell 字符串；`output_format=json` 时把 stdout 解析为 JSON；`confirm_required=true` 时先向用户确认再执行 |
| `skill`（技能/工作流） | `skill_id`、`platform`、`output_description`、`input_schema` | 在对应平台运行该技能或工作流，`inputs`=入参 |

**MCP 服务与密钥**：runtime-config 顶层的 `mcp_servers` 列出已启用意图用到的 MCP 服务（只含被用到的服务；`tools` 里也只含被用到的子工具）。平台**不下发密钥**，`auth.token_env` 给出 Bot 运行环境里应保存密钥的环境变量名，例如：

```json
{"name": "compliance", "transport": "streamable_http", "url": "https://mcp.example.com/compliance",
 "auth": {"type": "API Key", "header": "X-API-Key", "token_env": "MCP_COMPLIANCE_TOKEN"}}
```

Bot 用 `X-API-Key: ${MCP_COMPLIANCE_TOKEN}` 连接；`Bearer Token` 类型用 `Authorization: Bearer ${token_env}`；stdio 服务按 `command` + `args` 在 Bot 机器上启动，`auth.type=环境变量` 时把 `token_env` 的值放进进程环境变量 `auth.env`。

CLI 示例（Moonclaw 空间，命令来自西之月开放平台 CLI 文档 https://open.westmonth.com/cli）：

```
模板：xclaw-cli mall mall-product detail get --sku {sku} --locale {locale} --include-mainpicture {include_mainpicture} --json
入参：sku=SKU00022155（槽位 sku），locale 为空，include_mainpicture=1（常量）
执行：xclaw-cli mall mall-product detail get --sku SKU00022155 --include-mainpicture 1 --json
```

## 13. 平台内置工具（本平台自己的接口）

本平台的接口也作为「平台内置」工具登记在工具中心（不可编辑、不可删除），共 8 个能力，覆盖上面的 REST 接口和 6 个 MCP 工具：

| 工具 | 类型 | 覆盖的接口 | 空间 | 分类 |
|---|---|---|---|---|
| `intent_platform.search_knowledge` 检索知识（MCP 服务 `intent_platform` 的子工具） | mcp | `search_knowledge`、`POST /kbs/{kb_id}/search` | 全局 | 读取 |
| `pf_read_knowledge` 读取知识库 | http | `GET /kbs`、`/kbs/{kb_id}/documents`、`/kbs/{kb_id}/chunks`、`/imports/{import_id}` | 全局 | 读取 |
| `pf_read_intents` 读取意图与槽位 | http | `GET /spaces`、`/taxonomy`、`/slots`、`/recognition`、`/intents`、`/intents/{code}`；`list_intents`、`get_intent` | 按空间 | 读取 |
| `pf_list_tools` 读取工具目录 | http | `GET /tools`；`list_tools` | 全局 | 读取 |
| `pf_runtime_config` 读取运行时配置 | http | `GET /runtime-config` | 按空间 | 读取 |
| `pf_write_intents` 写回意图 | http | `POST /intents:batchUpsert`、`PATCH /intents/{code}`、`PUT /recognition`；`upsert_intents` | 按空间 | 维护类 |
| `pf_write_slots` 槽位写入 / 删除 | http | `POST /slots`、`PUT /slots/{name}`、`DELETE /slots/{name}` | 按空间 | 维护类 |
| `pf_import_knowledge` 导入 / 编辑知识 | http | `POST /kbs/{kb_id}/documents:import`、`PUT /kbs/{kb_id}/chunks/{chunk_id}`；`import_knowledge` | 全局 | 维护类 |

- **读取类**可以被运营关联到任何空间的意图里（自循环，如让 Bot 回答前先检索知识）；Bot 在 runtime-config 里看到 `source: builtin` 的工具时，按 type 正常调用，按空间的内置工具使用 Bot 自己所在的空间作为 `X-Space-Id`。
- **维护类**只给维护用 AI 通过 API Key 调用，不能关联到意图；你作为运营的 AI 工具调用它们时仍须遵守第 2、7 节（只写草稿、不删除、不覆盖已启用意图）。
