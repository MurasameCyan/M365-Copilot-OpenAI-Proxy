# Ciallo Ms-365 OpenAI Proxy Docker · 多租户版（Multi-Account）

来都来了 不点个⭐再走吗~?

将 Microsoft 365 Copilot 与个人版 Copilot 暴露为 **OpenAI / Anthropic 兼容 API** 的 Docker 代理服务。**多租户版**：可同时管理多个 M365 / Consumer 账户与多个 API Key，给多人共用；每个 Key 绑定一个账户，并拥有独立的对话模式与提示词。

> 这是主项目的 `multi` 分支，镜像标签为 `:multi`。单租户（单账户单 Key）请用 `main` 分支 / `:latest` 镜像。

## 目录

- [功能概览](#功能概览)
- [可调用模型目录](#可调用模型目录)
- [快速部署](#快速部署)
- [API 端点](#api-端点)
- [多账户刷新与保活](#多账户刷新与保活)
- [环境变量](#环境变量)
- [客户端配置](#客户端配置)
- [认证](#认证)
- [多租户使用](#多租户使用)
- [速率限制](#速率限制)
- [出站代理](#出站代理)
- [媒体 / Designer 授权抓取](#媒体--designer-授权抓取)
- [持久会话与上下文优化](#持久会话与上下文优化)
- [提示词增强与兜底重试](#提示词增强与兜底重试)
- [个人版（消费者版 Copilot）账户](#个人版消费者版-copilot账户)
- [企业版与个人版的差异](#企业版与个人版的差异)
- [架构](#架构)
- [开发与调试](#开发与调试)
- [License](#license)
- [致谢](#致谢)

## 功能概览

- **多账户池** — M365 与 Consumer 共用同一账户池；每个账户独立保存对应 provider 的凭据和刷新状态。M365 使用账户专属 Chromium profile，Consumer 在 `-camoufox` 镜像中使用账户专属 Camoufox profile，并可配置账户级出站代理
- **多 API Key** — 每个 Key 绑定一个账户，可单独设置对话模式 / 提示词，随时启用停用
- **模型即模式** — `GET /v1/models` 按 API Key 绑定的账户类型返回目录：M365 为对话模式（含「-持续」变体），个人版为可配置的 `model → mode` 别名
- **非驻留串行刷新** — 按 provider 分流：M365 优先 RT 纯 HTTP 换 Token，失败再拉起账户专属 Chromium；Consumer 按年龄由账户专属 Camoufox 重铸 ChatAI Token 与 Cookie。两条路径共享全局浏览器锁，峰值内存接近单租户
- **分层界面** — `/admin` 运营总控台（账户池 + Key 管理），`/` 用户自助页（用自己的 Key 管理对话模式、提示词和对应 provider 的账户凭据）
- **运行概览面板** — `/admin` 首页四个环形图：账户有效 / 过期、用户启用 / 停用、用户绑定 / 未绑定、累计用量。累计用量按**调用次数**分份额（环心是 Token 总量），份额小到画不成弧形（短于环的厚度）或排不进图例六行的模型合并成灰色 `other`；图例一行一个模型、只显示百分比并统一右对齐到上方 KPI 卡片的右边框，原始次数仍在 `GET /admin/stats` 的 `usage.model_counts` 里
- **请求前凭据检查** — 每个 `/v1/` 请求先检查绑定账户；M365 到期时自动续期，Consumer 遇到明确认证失败时可重铸一次并重试
- **油猴脚本** — Tampermonkey 一键推送 Token + Cookie（及 media / designer 凭据），M365 与个人版共用同一个脚本
- **M365 授权登录** — `/` 与 `/admin` 均可走 OAuth2 授权码 + PKCE 登录（native client，滑动 `refresh_token`），不装扩展、不抓包，登录后续期纯 HTTP
- **M365 增量上下文** — 复用会话时只发送新增内容，不重发完整历史
- **M365 会话持久化** — 容器重启后旧对话仍可正确续接
- **提示词增强** — Web 可调 tool_call 行为与系统提示词，持久保存；服务端兜底重试 + 散文兜底救援（半成品）
- **速率限制** — 令牌桶按 Key 限流，防止单个客户端跑飞拖垮共用账户；全局默认值 + 单 Key 覆盖
- **出站代理** — Web 可配 http/socks5 全局代理，覆盖 WebSocket 与刷新浏览器；Consumer 还支持账户级覆盖，本地 CDP 始终直连
- **API Key 认证** + **Web 管理页面**

## 可调用模型目录

`GET /v1/models` 会按请求 API Key 绑定账户的 provider 返回不同目录：

| Key 绑定账户 | 模型目录 |
| ------------ | -------- |
| M365 企业版 | 当前全局「对话模式列表」，每个模式生成普通与「-持续」两个模型 ID |
| Consumer 个人版 | 当前全局「个人版模型 / Mode」列表，每条 `model → mode` 映射生成一个模型 ID，无持续变体 |

### M365 模型目录

每个 M365 对话模式产出 **2 个模型 ID**：

| 变体 | 模型 ID 形态 | 会话行为 |
| ---- | ------------ | -------- |
| 普通 | `<显示名>` | 默认按首条用户消息哈希自动分组会话；首轮无 assistant 时开新线程 |
| 持续 | `<显示名>-持续` | 同一 API Key 下固定复用该模型会话（也兼容底层后缀 `:persist`） |

显示名中的空格会自动转为下划线，便于客户端当作 model id 使用。也可直接用 **底层 tone 值**（如 `Magic`、`Gpt_5_5_Chat`、`Claude_Sonnet`）请求；未匹配到任何模式时，回退到该 Key / 全局默认对话模式。

#### 默认内置模式

与代码中 `TONE_OPTIONS`（经规范化后）一致。可用 `curl -H "Authorization: Bearer <KEY>" http://localhost:8000/v1/models` 核对当前实例实际列表。

| 底层 tone（发给 M365） | 显示名 / 普通模型 ID | 持续模型 ID | 说明 |
| ---------------------- | -------------------- | ----------- | ---- |
| `Magic` | `Copilot_自动` | `Copilot_自动-持续` | Copilot 自动选模 |
| `Chat` | `Copilot_快速答复` | `Copilot_快速答复-持续` | Copilot 快速答复 |
| `Reasoning` | `Copilot_深度思考` | `Copilot_深度思考-持续` | Copilot 深度思考 |
| `Claude_Sonnet` | `claude-sonnet-4-6` | `claude-sonnet-4-6-持续` | Claude Sonnet |
| `Claude_Sonnet_Reasoning` | `claude-sonnet-4-5` | `claude-sonnet-4-5-持续` | Claude Sonnet 思考 |
| `Claude_Fable` | `claude-fable-5` | `claude-fable-5-持续` | Claude Fable |
| `Claude_Opus` | `claude-opus` | `claude-opus-持续` | Claude Opus |
| `Gpt_6_Astra` | `gpt-6_Chat` | `gpt-6_Chat-持续` | 普通直连与 Studio 实测成功，底层型号未确认 |
| `Gpt_6_Reasoning` | `gpt-6` | `gpt-6-持续` | Studio 路径实测成功；普通直连失败，使用前见下方限制 |
| `Gpt_5_6_Chat` | `gpt-5.6_Chat` | `gpt-5.6_Chat-持续` | GPT 5.6 快速 |
| `Gpt_5_6_Reasoning` | `gpt-5.6` | `gpt-5.6-持续` | GPT 5.6 思考 |
| `Gpt_5_5_Chat` | `gpt-5.5_Chat` | `gpt-5.5_Chat-持续` | GPT 5.5 快速 |
| `Gpt_5_5_Reasoning` | `gpt-5.5` | `gpt-5.5-持续` | GPT 5.5 思考 |
| `Gpt_5_4_Chat` | `gpt-5.4_Chat` | `gpt-5.4_Chat-持续` | GPT 5.4 快速 |
| `Gpt_5_4_Reasoning` | `gpt-5.4` | `gpt-5.4-持续` | GPT 5.4 思考 |
| `Gpt_5_3_Chat` | `gpt-5.3_Chat` | `gpt-5.3_Chat-持续` | GPT 5.3 快速 |
| `Gpt_5_3_Reasoning` | `gpt-5.3` | `gpt-5.3-持续` | GPT 5.3 思考 |
| `Gpt_5_2_Chat` | `gpt-5.2_Chat` | `gpt-5.2_Chat-持续` | GPT 5.2 快速 |
| `Gpt_5_2_Reasoning` | `gpt-5.2` | `gpt-5.2-持续` | GPT 5.2 思考 |

共 **38** 个默认可选模型 ID（19 模式 × 2 变体）。目录表示可选择的 tone，**不表示当前账户能在所有路径下调用成功**。`Gpt_5_6_Chat` 与 `Gpt_5_3_Reasoning` 分别在此前的 08-28 / 08-25 复测中确认可用。

**与历史默认列表完全相同的旧配置会在升级时自动迁移**，包括加入 Astra 之前的 17 模式列表和仅加入 Astra 的 18 模式列表。与这些历史默认列表不相同的自定义配置会被保留；需要新增模式时，请在 `/admin` → 运行设置中添加 `Gpt_6_Astra | gpt-6_Chat`、`Gpt_6_Reasoning | gpt-6`。

Docker 部署升级时，需要在部署目录执行 `docker compose pull`，再执行 `docker compose up -d --force-recreate`，拉取新镜像并重建容器；之后在客户端刷新模型列表。代码推送和镜像构建不会自动更新已经运行的容器。

#### Astra 与 Reasoning 的区别及使用限制

以下是 **2026-09-07 至 09-08、同一 M365 账户**的实测结果，区别首先在于调用路径：

| 场景 | `gpt-6_Chat` → `Gpt_6_Astra` | `gpt-6` → `Gpt_6_Reasoning` |
| ---- | --------------------------- | -------------------------- |
| 普通直连问答，不携带 Studio agent | 完成实际任务，`Completed / Success` | 多次 `Failed / InternalError`，没有任务答案 |
| 直接携带有效 Studio agent 的问答实验 | 成功 | 成功 |
| Studio 真实 `Read → 工具结果 → 最终 JSON` 流程 | 成功 | 成功 |
| 当前使用建议 | 可用于普通聊天和已验证的 Studio 工具流程 | 仅按下列条件使用 Studio；普通聊天不适用 |

这里的 `gpt-6_Chat`、`gpt-6` 是本项目明确配置的显示名映射。上游回包没有提供实际模型 ID、模型版本或部署标识，不能据名称或模型自述认定底层是真正的 GPT-6，也没有证据支持“Reasoning 更聪明、更慢或上下文更大”等差异。普通直连与 Studio 问答的证据见[初始实测](docs/gpt6-tone-verification-2026-09-07.md)、[Studio tone 实测](docs/studio-tone-verification-2026-09-07.md)。七个 tone 的真实工具流程验证通过 7/7，严格 JSON 格式通过 6/7（Claude Sonnet 附带额外前言），见[工具流程验证](docs/studio-selected-tone-2026-09-07.md)。

**使用 `Gpt_6_Reasoning` 的步骤：**

1. 使用绑定 **M365 企业账户**的 API Key，确认该账户已有有效、已就绪的 Studio agent。在 `/` 用户自助页的「默认配置」→「工具调用规划」选择 **Studio Agent**；也可在 `/admin` →「运行设置」设为全局值，并让该 Key 继承。
2. 客户端选择 `gpt-6`（或直接传 `Gpt_6_Reasoning`），请求中声明实际要用的 `tools`。有效工具列表必须非空，`tool_choice` 必须不是 `none`；通常可用 `auto`，允许模型在工具完成后返回最终文本。
3. 收到工具调用后，由客户端执行工具并提交真实结果。续轮继续使用相同模型、Studio 设置和有效工具定义；不要为了让模型总结而清空 `tools` 或改成 `tool_choice=none`，否则会离开 Studio 路径。
4. 检查完整的“工具调用 → 真实结果 → 最终答案”，并检查调用记录中的实际规划阶段与回退信息。首轮成功或 HTTP 200 都不能单独证明完整流程成功。

**必须了解的限制：**

- **Studio 是工具规划模式。** 请求没有 `tools`、有效工具列表为空，或 `tool_choice=none` 时，即使设置了 Studio，也会走普通直连。因此上表“携带 agent 的问答实验成功”不等于当前兼容 API 的无工具纯聊天可用。
- **有效 agent 是前提。** agent 缺失或未就绪时会回退 Router；Studio 在首个输出前不可用也可能触发回退。对当前实测的 Reasoning 而言，回到普通路径仍会失败。选择 Studio 设置本身不能保证 agent 可用。
- **工具调用规划的 `auto` / `native` 不等同于 `studio`，Router 也不能替代上述条件。** 2026-09-08 的 Router 实测中，普通 Reasoning 分类先报 `InternalError`，Studio 兜底成功发出 `Read`；提交工具结果后，续轮又走普通 Reasoning 并失败。两轮 HTTP 均为 200，第二轮正文却是上游错误，未完成最终答案。
- **`-持续` / `:persist` 只改变会话复用方式**，不会解锁 tone、补齐 agent，也不会把普通聊天转成 Studio。
- 以上可用性限于实测账户和时间窗口，会受 Microsoft rollout 影响。加入默认目录方便选择，不构成通用可用或长期稳定的保证。

三个兼容 API 的 Studio 工具首轮、结果续轮、纠正重试，以及进入 Studio 的路由回退，都使用客户端所选模型解析出的 tone。升级前固定 `Magic` 的 Studio 会话会与新版会话隔离；切换 tone 会隔离上游线程，A→B→A 切回时按客户端历史恢复必要上下文。这里保证的是代理实际发送的 tone；回退到普通路径能否成功，仍取决于该 tone 在那条路径上的可用性。

某个模式能不能用由 M365 侧的 rollout 决定：M365 拒绝服务的模式在响应尚未开始时会返回 **400** 并在错误里点名该模式，不会静默回一句「Sorry, I wasn't able to respond to that.」当成模型回复。流式响应已经开始时，HTTP 状态可能仍为 200，错误会写入流或正文，调用方必须检查最终内容。用 400 而非 502，是因为重试改变不了上游的拒绝——502 会让客户端把它当成网关故障反复重试。传输层故障（空闲超时、断流）与凭据问题仍然是 502。想知道当前账号实际能用哪些，跑仓库根目录的 `scan_tones.py`。

#### 请求示例

```bash
# 列出模型
curl -s -H "Authorization: Bearer YOUR_API_KEY" http://localhost:8000/v1/models | jq '.data[].id'

# Chat Completions：选 GPT 5.5 Chat + 自动分组会话
curl -s http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.5_Chat","messages":[{"role":"user","content":"你好"}]}'

# 同一模式的持续会话（也可用 Gpt_5_5_Chat:persist）
curl -s http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.5_Chat-持续","messages":[{"role":"user","content":"继续上面的话题"}]}'
```

#### 能力说明

- **多模态输入（M365 与个人版都支持）**：M365 目录中的每个模型都声明 `vision` / `input_modalities: text+image`（底层均为 M365 多模态后端）。三种协议都接受**纯图片消息**（不带任何文字），上游会照常描述图片。Consumer 个人版自 2026-09-04 起同样识图，图片改由个人版自己的附件接口上传，与选哪个模型 / `mode` 无关（格式与张数上限见[个人版图片能力](#consumer-个人版模型目录)）。部分客户端（如 CherryStudio）可能仍依赖内置模型名正则，需在客户端手动开启图片。
- **响应中的 `model` 字段**：返回体里的 `model` 使用运行时别名（默认 `m365-copilot`，可由 `M365_MODEL_ALIAS` 或 Key 级 `model_alias` 覆盖），**不等于**请求时选用的对话模式 ID。
- **可自定义模式列表**：在 `/admin` → 运行设置中编辑「对话模式列表」，格式每行：`底层tone值 | 显示名`。保存后立即反映到 `/v1/models` 与解析逻辑；显示名会作为模型 ID，空格转下划线，每个模式仍生成普通 + `-持续` 两个模型。

#### 与「Key 默认对话模式」的关系

- 请求 **`model` 命中** 某显示名 / 底层 tone → 使用该模式。
- 请求 **未命中**（任意字符串、旧别名等）→ 使用该 API Key 在 Web 上配置的默认 tone，否则用全局默认（通常为 `Magic`）。
- 因此客户端既可「按模型选模式」，也可继续用固定模型名 + Web 侧默认模式。

### Consumer 个人版模型目录

个人版模型 ID 是本项目提供的**兼容别名**，不是不同的基础模型。每个 ID 映射到一个上游 `mode`，请求时原样发送给 Microsoft。可用性会随账户、地区和 Microsoft rollout 变化；同一个 mode 在不同时段也可能表现不同，因此下表只代表当前部署的实测结果，不是长期可用性保证。

| 模型 ID | 上游 mode | 文本对话 | Claude Code 工具调用 | 当前建议 |
| ------- | --------- | -------- | -------------------- | -------- |
| `copilot-reasoning` | `reasoning` | ✅ | ✅ 已完成真实 `Read → tool_result → 最终答复` 循环 | 工具调用首选 |
| `copilot-thinking` | `reasoning` | ✅ | ✅ 已完成真实工具循环 | `reasoning` 的兼容别名，工具备选 |
| `copilot-research` | `research` | ✅ | ✅ 已完成真实工具循环 | 实验性工具备选 |
| `copilot-coco` | `coco` | ✅ | ✅ 已完成真实工具循环 | 实验性工具备选 |
| `copilot-search` | `search` | ✅ | ⚠️ 曾成功，也出现过 `method=null` | 工具行为不稳定 |
| `copilot` | `smart` | ✅ | ❌ 强制工具时上游断开 | 仅建议文本 |
| `copilot-smart` | `smart` | ✅ | ❌ 与 `copilot` 相同 | 仅建议文本 |
| `copilot-chat` | `chat` | ✅ | ⚠️ 可能忽略工具或直接编造结果 | 仅建议文本 |
| `copilot-study` | `study` | ✅ | ❌ 工具路径曾在开始响应后断开 | 仅建议文本 |

**图片能力**（两个方向要分开看，实测 2026-08-25 与 2026-09-04）：

- **识图（发图给模型）—— 支持，与 `model` / `mode` 无关**（2026-09-04 移植了浏览器那套图片上传握手）：代理把入站图片先 `POST /c/api/attachments` 上传，再把返回的相对 URL 作为 `{"type":"image","url":...}` 放在文字之前发给上游，与网页版同形。三种协议都可用，**纯图片消息**（不带任何文字）也能得到对图片的描述。实测细节：`png` / `jpeg` / `webp` 直接接受；`gif` / `bmp` 会被上游按 `content-type` 拒收，代理改标为 `image/png` 再试（尽力而为，上游收下了但能否读懂未逐格实测）；单轮最多 10 张，多出的会被丢弃并记 WARNING；单张实测 7MB 可用，上游会自行转码。上传失败时只丢这张图、照常发问（日志里有 WARNING），只有「一张都没上传成功且本来没有文字」才在本地报错。远程 `http(s)` 图片沿用 M365 那套下载器（同一份 SSRF 与体积限制）。
- **出图（让模型画图）—— 按 `mode` 分**：`copilot` / `copilot-smart`（`smart`）、`copilot-chat`（`chat`）、`copilot-search`（`search`）实测会真的返回图片；`copilot-reasoning` / `copilot-thinking` 会声称「已为你生成」却一帧图都不发，`copilot-study` 只讲不画，`copilot-research` 返回的是网页图搜结果，`copilot-coco` 会先反问一句。后四种是上游行为，代理这边没有图可交付。`/` 自助页在选到不出图的模式时会显示同样的提示。

`copilot-default` 与 `copilot-computer-use` 当前连续返回 `invalid-event`，不列入默认可用清单。升级时，若持久配置仍精确等于旧版 11 项默认目录，或等于此前旧顺序的 9 项默认目录，会自动迁移到上述顺序；不精确匹配旧默认的自定义目录不会因本次升级被自动删减或重排，仍只执行既有的大小写、空白和 `status` 规范化。个人版没有 `-持续` / `:persist` 变体；每轮会新建上游对话，并在本地压缩、重发必要历史。`/admin` → 运行设置中的「个人版模型 / Mode」可编辑这份目录，格式为 `model | mode | status`。其中 `experimental` 只标记 rollout 风险并影响错误提示，不改变请求执行策略。

### 推断知识新鲜度（离线实测）

以下结果来自 **2026-08-15** 的 `/v1/responses` 文本请求。请求未提供任何工具，并明确要求模型不得联网、搜索或按当前日期外推，只能依据内置知识回答多种软件的最新稳定版本与发布日期。它只能反映模型当次回答覆盖到的公开信息，**不是 Microsoft 公布的训练数据截止日期，也不是稳定能力保证**。

| Provider / 模型 | 版本时间锚点 | 推断知识新鲜度 |
| --------------- | ------------ | -------------- |
| Consumer / `copilot-reasoning` | 能识别 [Git 2.43.0（2023-11-20）](https://github.com/git/git/releases/tag/v2.43.0)、Python 3.12.0、Rust 1.74.0、PostgreSQL 16、Ubuntu 23.10 与 Linux 6.6 已发布；对 [Git 2.44.0（2024-02-23）](https://github.com/git/git/releases/tag/v2.44.0) 回答 `unknown` | **约 2023-11**；保守区间为 2023-11 至 2024-02 之前 |
| M365 / `gpt-5.6` | 回答 Git 2.52.0、Python 3.14.1、Rust 1.92.0、Kubernetes 1.35.0 等 2025 年末版本已发布；对多项 2026 年初版本回答 `unknown` | **约 2025-12**；最晚正向锚点为 [Kubernetes 1.35.0（2025-12-17）](https://github.com/kubernetes/kubernetes/releases/tag/v1.35.0) |

Consumer 的自由回答曾在 Git 2.42.0 / 2.43.0 之间变化，并出现过错误发布日期；M365 的部分低置信度日期也有偏差。因此这里依据多软件版本和“已知 / 未知”边界做月份级推断，不把模型自报日期直接当事实。

## 快速部署

### 1. 创建 .env 文件

```bash
cp .env.example .env
```

按需填写 `ADMIN_PASSWORD`、`API_KEY`（见 [环境变量](#环境变量)）。

### 2. 启动服务

```bash
docker compose up -d
```

服务在 `http://localhost:8000` 启动。打开浏览器：

- `/` — 用户自助页（用自己的 API Key 登录）
- `/admin` — 运营总控台（管理密码：`ADMIN_PASSWORD`，未设则回退 `API_KEY`）

### 3. 登录账号

账户池里的每个账户都要有自己的凭据。`/admin` 新建的空账户先绑定 Key，之后用户在 `/` 自助页自己完成登录即可，不必找管理员代劳。M365 与个人版的登录方式不同，下面按推荐顺序列出。

#### 方式一：M365 授权登录（推荐，仅 M365）

`/` 自助页的「授权登录 ( M365 Only )」走标准 OAuth2 授权码 + PKCE：不装扩展、不抓包，拿到的是**滑动过期**的 `refresh_token`（native client `c0ab8ce9-e9a0-42e7-b064-33d422df41f1`），之后续期是纯 HTTP 交换，不拉浏览器、不消耗 Copilot 配额。

1. 先让 Key 绑上一个账户 —— 由管理员在 `/admin` 添加并绑定，或先用下面任一方式推一次凭据（未绑定时面板会直接提示）
2. 点 **授权登录**：服务端生成登录链接并弹出微软登录页（被拦了就点「没弹窗？点这里打开」）
3. 用**该账户对应的**微软账号登录。成功后地址栏会跳到 `https://login.microsoftonline.com/common/oauth2/nativeclient`，页面本身是空白的 —— 要的就是地址栏里那条带 `?code=...` 的完整 URL
4. 把整个 URL 粘进输入框，点 **完成登录**

服务端兑换授权码后写入 substrate token 与 `refresh_token`，并用同一个 RT 顺手铸出附件 key 与图片 key（这两个失败不影响聊天，保活会补）。四条硬约束：

- 只对 **M365** 账户有效；个人版账户调用返回 400
- 一次登录 15 分钟内有效且一次性，超时或重复提交返回 400，重新点「授权登录」即可
- 登录的微软身份必须与该账户已绑定的 tenant / object id 一致，换成别人登录返回 409 —— 防止把另一个人的凭据写进你的账户
- 管理员侧有等价端点 `POST /admin/pkce/start` / `complete` / `mint`，在请求体里点名 `account_id`

#### 方式二：油猴脚本一键推送（M365 与个人版都可用）

1. 安装 [Tampermonkey BETA](https://www.tampermonkey.net/) 浏览器扩展
2. 点击 [一键脚本](https://gh-proxy.com/https://raw.githubusercontent.com/MurasameCyan/Ciallo-Ms-365-OpenAI-Proxy-Docker/main/get_token.user.js) 安装油猴脚本
3. 面板（右上角，`Ctrl+Shift+M` 开合）里填两项：**代理地址** = 本服务地址（如 `http://localhost:8000`），**用户 API Key** = 你在 `/` 页面拿到的 Key
4. **M365**：打开 [M365 Copilot](https://m365.cloud.microsoft/chat) 登录后，在对话框**输入任意字符**触发 WebSocket；面板显示 `✓ Token 可用` 后点 **一键推送**（Token + Cookie 一起推）
5. **个人版**：打开 [copilot.microsoft.com](https://copilot.microsoft.com) 登录后**发送一条消息** —— ChatAI token 只出现在聊天 WebSocket 的 URL 里，不发消息抓不到；面板「个人版 Copilot」显示 `✓ ChatAI Token 可用` 后点 **一键推送个人版**（Cookie + ChatAI Token 一起推，并把该账户切到 `consumer` provider）

> 脚本按当前域名切换面板内容：在 copilot.microsoft.com 上只展开「个人版 Copilot」，M365 那套收进底部「其他产品」抽屉，反之亦然。个人版的完整步骤与代理配置见[个人版账户 → 推送凭据](#2-推送凭据)；媒体 / Designer 凭据见[媒体 / Designer 授权抓取](#媒体--designer-授权抓取)。

#### 方式三：手动粘贴 Token（应急，仅 M365）

1. 在浏览器中打开 M365 Copilot
2. F12 → Network → WS → 找到 `wss://substrate.office.com/...` 连接
3. 复制 URL 中的 `access_token` 参数值
4. 粘到 `/` 自助页「推送 / 更新账户 Token」框并点更新（整条 `wss://` URL 也接受，服务端自己取参数）

> **手动 Token 不带任何续期材料**：没有 `refresh_token`、没有 Cookie，substrate token 本身不到两小时就过期，到期只能再粘一次。要让账户自己活下去，用方式一或方式二。

#### 查看状态

`/` 与 `/admin` 都会显示账户的 Token 剩余有效期、Cookie 状态、`refresh_token` 是否入库。个人版的 ChatAI token 不是可解码的 JWT，只能显示「有没有存」—— 过期表现为 `/v1/` 返回 502，处理方式见[凭据过期后手动重推](#3-凭据过期后手动重推)。

> **Check Login / Auto Capture / Cookie 注入依赖共享 admin Chromium（9222），仅在 `ENABLE_ADMIN_CDP=true` 时可用**。默认多租户部署下这些按钮对应的端点未注册；请改用 `/` 用户自助页推送账户 Token / Cookie，刷新由每账户独立 Chromium 或 RT 承担。

## API 端点

<details>
<summary>展开查看全部 API 端点</summary>

### OpenAI / Anthropic 兼容 API

| 端点 | 说明 |
| ---- | ---- |
| `GET /v1/models` | 模型列表（对话模式 × 普通/持续） |
| `POST /v1/chat/completions` | OpenAI Chat Completions（支持流式） |
| `POST /v1/responses` | Responses API 兼容子集（方案 A，支持流式与 function tools） |
| `POST /v1/messages` | Anthropic Messages API（支持流式） |

### 会话与页面

| 端点 | 说明 |
| ---- | ---- |
| `GET /healthz` | 健康检查 |
| `GET /` | 用户自助页面（API Key 登录） |
| `GET /admin` | 运营总控台页面（管理密码登录） |
| `POST /admin/login` | 运营总控台登录 |
| `POST /admin/logout` | 运营总控台登出 |

### 管理端点 — 账户池与 API Key

| 端点 | 说明 |
| ---- | ---- |
| `GET POST /admin/accounts` | 列出 / 添加账户 |
| `POST /admin/accounts/{id}/token` | 更新账户 Token |
| `POST /admin/accounts/{id}/token/clear` | 清除账户 Token |
| `POST /admin/accounts/{id}/rename` | 重命名账户 |
| `POST /admin/accounts/{id}/refresh` | 立即刷新账户 Token（CDP） |
| `POST /admin/accounts/{id}/cookie-refresh` | 用 Cookie 拉起 Chromium 刷新 |
| `POST /admin/pkce/start` | 为指定账户生成 PKCE 授权登录链接（仅 M365） |
| `POST /admin/pkce/complete` | 提交回调 URL，兑换 substrate token + 滑动 `refresh_token` |
| `POST /admin/pkce/mint` | 用已入库的 RT 铸媒体 / Designer key（`kind=media\|designer`） |
| `DELETE /admin/accounts/{id}` | 删除账户（解绑其 Key） |
| `GET POST /admin/keys` | 列出 / 新建 API Key |
| `POST /admin/keys/{id}` | 更新 Key（绑定/模式/启停等） |
| `POST /admin/keys/{id}/regenerate` | 重置 Key 明文 |
| `DELETE /admin/keys/{id}` | 删除 API Key |

### 管理端点 — 设置与可观测性

| 端点 | 说明 |
| ---- | ---- |
| `GET /admin/token/status` | Token 有效性与自动刷新状态 |
| `POST /admin/token/update` | 手动推送 Token |
| `POST /admin/token/auto-refresh-toggle` | 切换自动刷新开关 |
| `GET POST /admin/tone` | 查询 / 设置默认对话模式 |
| `GET POST /admin/tool-prompt` | 查询 / 设置提示词增强 |
| `GET POST /admin/system-prompt` | 查询 / 设置系统提示词 |
| `GET POST /admin/runtime-settings` | 查询 / 设置运行设置（含对话模式列表、日志开关等） |
| `GET /admin/call-log` | API 调用记录 |
| `POST /admin/call-log/clear` | 清空调用记录 |
| `GET /admin/summary` | 总览统计 |
| `GET /admin/stats` | 明细统计 |
| `GET /admin/metrics-history` | 指标历史 |
| `POST /admin/metrics-history/clear` | 清空指标历史 |
| `GET /admin/media-proxy/events` | 媒体代理事件 |
| `POST /admin/media-proxy/events/clear` | 清空媒体代理事件 |
| `GET POST /admin/capture-payload` | 查询 / 接收模式抓包数据 |
| `POST /admin/capture-payload/clear` | 清空抓包数据 |
| `GET POST /admin/capture-toggle` | 查询 / 切换抓包开关 |

> `POST /admin/capture-payload` 是唯一**不校验管理员 cookie** 的管理端点：油猴脚本跨源推送带不了 cookie，所以由「抓包开关」把门 —— 开关关闭时一律 403。开着的时候任何人都能往这块面板里塞数据，抓完请关掉。收下的内容限 20 条 / 256 KB，展示前做 HTML 转义；`optionsSets` 不是数组也照常渲染（数组拼成 `a, b`，其他形状按 JSON / 原文显示），不会再让整块面板空白。

### 管理端点 — 共享 CDP（仅 `ENABLE_ADMIN_CDP=true` 时注册）

> 默认多租户部署下 `ENABLE_ADMIN_CDP=false`，以下端点**不注册**（调用返回 404），刷新由每账户独立 Chromium 承担。设为 `true` 才启用 9222 共享浏览器及这些端点。

| 端点 | 说明 |
| ---- | ---- |
| `POST /admin/token/auto-capture` | 触发共享 Chromium 捕获 Token |
| `POST /admin/cookie/inject` | 注入 Cookie 到共享 Chromium |
| `GET /admin/chromium/login-status` | 共享 Chromium 登录状态 |
| `POST /admin/chromium/logout` | 共享 Chromium 登出 |

### 用户自助端点（用自己的 API Key 认证）

| 端点 | 说明 |
| ---- | ---- |
| `POST /user/login` | 用户页登录 |
| `POST /user/repassword` | 修改自己的登录密码 |
| `GET /user/me` | 查询自己的 Key 信息与绑定账户状态 |
| `POST /user/tone` | 设置自己的对话模式 |
| `POST /user/tool-prompt` | 设置自己的提示词增强 |
| `POST /user/system-prompt` | 设置自己的系统提示词 |
| `POST /user/account/token` | 推送/更新绑定账户的 Token（无则自动创建） |
| `POST /user/pkce/start` | 对自己绑定的 M365 账户发起授权登录 |
| `POST /user/pkce/complete` | 提交回调 URL 完成登录（只认自己那次登录） |
| `POST /user/pkce/mint` | 用自己账户的 RT 铸媒体 / Designer key |
| `POST /user/account/cookies` | 推送绑定账户的 Cookie（供 CDP 刷新） |
| `POST /user/account/consumer` | 推送个人版（消费者版 Copilot）凭据，把账户切到 consumer provider |
| `POST /user/account/refresh-token` | 立即刷新绑定账户的 Token |
| `POST /user/account/media-auth` | 推送媒体（图片）访问凭据 |
| `POST /user/account/designer-auth` | 推送 Designer 访问凭据 |
| `POST /user/account/logout` | 登出绑定账户（清凭据） |
| `POST /user/account/unbind` | 解绑当前账户 |
| `POST /user/regenerate-key` | 重置自己的 API Key |

</details>

### Responses API（方案 A）

`POST /v1/responses` 实现的是当前项目所需的 **Responses API 兼容子集**，不是与 OpenAI 官方接口完全等价：

- **Function tools**：支持扁平定义（例如 `{"type":"function","name":"Read",...}`），也支持 `{"type":"namespace","name":"filesystem","description":"...","tools":[...]}` 分组。`namespace` 只承载元数据，内部目前仅接受 `function`；代理会把子函数展平给微软上游，并在返回的 `function_call` 上恢复 `namespace`。由于上游只生成裸函数名，namespace 子函数名不得与其他 namespace 子函数或同名扁平函数重复。模型返回 `function_call` 后，由调用方执行函数，再把同一 `call_id` 的 `function_call_output`（以及需要保留的 `function_call` / 消息历史）放入下一次 `input`；重复此循环，直到返回最终 `message`。`strict:true` 会在代理侧按声明的 JSON Schema 校验模型参数；不合规调用不会返回给客户端。Microsoft 上游仍没有原生 schema 强制。
- **Tool choice**：支持 `auto`、`none`、`required` 和 named function（例如 `{"type":"function","name":"Read"}`）。`required` 与 named function 会严格执行：上游首次未返回合法调用时代理重试一次，连续两次失败后，非流式请求返回 HTTP 502，流式请求以 `error` + `response.failed` 结束。`allowed_tools` 当前不支持，并在请求上游前返回 HTTP 400。
- **Codex CLI**：实测 Codex CLI 0.145.0 的自定义 Responses provider 请求可能同时包含 `function`、`namespace` 和 `web_search`。前两者可用；`web_search` 没有等价微软上游能力，仍会在请求上游前返回 HTTP 400。使用本代理时需关闭 Codex Web Search：

  ```toml
  web_search = "disabled"
  ```

  单次运行可传 `-c 'web_search="disabled"'`；不要使用 `--search`。Codex 0.145.0 的 `tools.web_search=false` 是旧式工具配置，不保证移除默认的 cached Web Search，不能替代顶层开关。
- **只读保护边界**：Responses 复用现有的大小写不敏感名称启发式，只把 `Read`、`Grep`、`Glob`、`ls`、`SearchCodebase` 视为只读工具。它无法判断任意自定义函数的真实副作用，因此不是安全边界；实际工具执行器仍须独立实施权限、审批和沙箱限制。
- **流式生命周期**：成功流从 `response.created`、`response.in_progress` 开始。文本项依次发送 `response.output_item.added` → `response.content_part.added` → `response.output_text.delta` / `done` → `response.content_part.done` → `response.output_item.done`；函数调用项发送 `response.output_item.added` → `response.function_call_arguments.delta` / `done` → `response.output_item.done`，最后以 `response.completed` 结束。上游失败以顶层 `error` 后接 `response.failed` 结束；不发送 `[DONE]`。
- **M365 续接**：把上一轮返回的 `response.id` 作为下一轮 `previous_response_id`，代理会恢复同一条服务端 M365 会话；下一次 `input` 只需携带本轮新增内容。该续接是线性且单次的，只接受最新 `response.id`，不支持从旧 ID 分叉、成功后重放，或在终止帧丢失后的幂等重试。若上一轮并行返回多个函数调用，必须在一次续接中提交全部对应的 `function_call_output`；工具输出目前只支持文本。
- **Consumer 续接**：Consumer 是无状态桥接，每轮都会新建上游对话，`previous_response_id` 不会恢复服务端历史。调用方必须在下一次 `input` 中重发完整的 `input` 历史，包括相关消息、`function_call` 和 `function_call_output`。Consumer 返回的 `resp_...` 只是当前响应标识，不是服务端续接句柄。
- **资源 API 不支持**：方案 A 只注册 `POST /v1/responses`，不提供响应存储、`GET /v1/responses/{response_id}`、`DELETE /v1/responses/{response_id}` 或 `POST /v1/responses/{response_id}/cancel`；`store` 不会创建可供后续读取的响应资源。
- **非函数与托管工具不支持**：OpenAI 托管的 `web_search`、`file_search`、`computer_use`、`code_interpreter` 等工具，以及 `custom`、`shell`、`local_shell` 等非 function 工具，会在请求上游前返回 HTTP 400。Function tool 的 `allowed_callers`、`defer_loading:true`、`output_schema` 也因缺少等价执行语义而明确返回 HTTP 400；namespace 内的 `custom` 子项同样不支持。

## 多账户刷新与保活

应用启动时会一直运行账户池保活调度器，逐账户检查并按 `provider` 分流；它不因 API 空闲而暂停。Docker 入口只有在 `ENABLE_ADMIN_CDP=true` 且 `AUTO_REFRESH=true` 时才启动旧的共享 admin Chromium（9222），否则会关闭这条共享浏览器路径。独立运行 CLI 时，`--no-auto-refresh` 只停旧自动刷新线程，浏览器启动另由 `--no-launch-edge` 控制。**这些共享路径开关都不会关闭每账户 M365 保活，也不会关闭 Consumer Camoufox 保活**。

1. **请求前检查** — 每个 `/v1/` 请求先由 API Key 找到绑定账户，再调用 `ensure_fresh(account.id)`。M365 Token 有效时是廉价 no-op；到期时先尝试 RT，失败再回退到账户专属 Chromium。Consumer Token 不透明，普通请求先使用现有凭据；若上游明确返回需要重新认证，Consumer gate 才对该账户重铸一次并重试当轮。
2. **后台逐账户保活** — 调度器按配置间隔扫描整个账户池。M365 的 CDP 账户在 Cookie 临近过期时刷新，Cookie 已失效但仍有快照时尝试自愈；Consumer 凭据达到年龄阈值后，用该账户专属 Camoufox profile 重铸 Token 与 Cookie。
3. **账户级隔离** — M365 与 Consumer 都有独立账户锁、凭据快照和失败退避。Consumer profile 还包含 Microsoft 主体哈希，重铸结果只有在账户主体及快照仍匹配时才会写回。
4. **全局串行浏览器锁** — M365 Chromium 与 Consumer Camoufox 共用一把浏览器锁，避免多个账户同时拉起浏览器；串行的是资源占用，不是凭据或 profile。
5. **手动刷新** — `/admin` 的账户刷新按钮同样按 provider 分派：M365 强制走账户刷新，Consumer 强制走 Camoufox 重铸。

### M365 两级刷新链路：RT 优先 → CDP 回退

刷新到期（或强制刷新）时，按以下顺序取新 Token：

1. **RT 快速刷新（首选，无浏览器）** — 账户若持有已验证签发上下文的 OAuth2 `refresh_token`，直接向原 AAD authority 做纯 HTTP 交换，换回新的 substrate access token。**不拉起 Chromium、不消耗 Copilot 配额**，速度快、开销低。油猴脚本只接收目标 M365 client + substrate scope 的响应，并把 authority、tenant、object id 一并推送；服务端与当前账户主体匹配后才入库。
2. **CDP 刷新（长期保活与回退）** — SPA refresh token 通常有约 24 小时绝对寿命，轮换不会延长原始到期时间，因此 RT 只是短期快速路径。RT 明确过期/撤销时会停用；`AADSTS40016`、HTTP 失败等会进入退避。两者都立即回退到该账户专属 Chromium profile（独立 CDP 端口 9322+），由顶层登录会话续期 Token 与 Cookie。

> media / designer（图片、Designer）Token 不经 RT 产生，由 CDP 媒体捕获路径按需懒保活。

> **注意：请求命中到期的 M365 Token 时，首轮回复需要等待刷新完成**；RT 路径通常只需一次 HTTP 往返，明显快于 CDP 拉起浏览器。

```
/v1/ 请求 → API Key → 绑定账户 → provider 分流
                                ├─ M365 → Token 有效：直接请求
                                │          Token 到期：RT → 失败则账户专属 Chromium/CDP
                                └─ Consumer → 先用账户 Token/Cookie
                                             明确认证失败时：该账户 Camoufox 重铸一次并重试

后台保活 → 扫描账户池
           ├─ M365：Cookie 临期刷新 / 无效快照自愈
           └─ Consumer：凭据满龄后重铸 Token + Cookie
                        两种浏览器刷新由全局锁串行
```

## 环境变量

### 服务配置（`.env` / pydantic Settings）

| 变量 | 必需 | 默认值 | 说明 |
| ---- | ---- | ------ | ---- |
| `API_KEY` | **是*** | — | 全局/快速启动 API Key；若一个 Key 都没注册且此项留空，`/v1/` 端点无认证开放 |
| `ADMIN_PASSWORD` | 否 | — | `/admin` 总控台密码，未设置时回退使用 `API_KEY` |
| `M365_ACCESS_TOKEN` | 否 | — | 单账户 Substrate Token，留空则由脚本推送或自动捕获（多租户按账户管理，一般不用） |
| `M365_TIME_ZONE` | 否 | `Asia/Shanghai` | 发送给 Copilot 的时区 |
| `M365_MODEL_ALIAS` | 否 | `m365-copilot` | 响应 JSON 中的 `model` 别名（**不是** `/v1/models` 列表里的对话模式 ID） |
| `TOKEN_DIR` | 否 | `/home/app/token` | 令牌/账户/Key/会话等持久化目录（挂载卷） |
| `IDLE_TIMEOUT_MINUTES` | 否 | `30` | 空闲多少分钟无 `/v1/` 请求后暂停自动刷新 |
| `LOG_LEVEL` | 否 | `INFO` | 日志输出等级（DEBUG/INFO/WARNING/ERROR/CRITICAL），Web 轮询与 `/healthz` 始终过滤 |

\* 多租户推荐在 `/admin` 创建 per-user Key，全局 `API_KEY` 仍建议设置以保护未绑定时的接口。

### 日志与安全开关

| 变量 | 必需 | 默认值 | 说明 |
| ---- | ---- | ------ | ---- |
| `LOG_USER_VERBOSE` | 否 | `true` | 账户/刷新的普通进度日志（可在 `/admin` 运行设置里改） |
| `LOG_USER_ERRORS` | 否 | `true` | 账户/刷新的失败/异常日志（可在 `/admin` 运行设置里改） |
| `SUPPRESS_ACCESS_LOG` | 否 | `true` | 屏蔽高频 uvicorn 访问日志（轮询/健康检查/favicon 等） |
| `ALLOWED_ORIGINS` | 否 | — | CORS 允许来源（逗号分隔），留空按内置策略处理 |
| `ADMIN_COOKIE_SECURE` | 否 | `0` | 管理会话 Cookie 是否加 `Secure`（HTTPS 部署置 `1`） |

### 浏览器刷新层（Dockerfile / entrypoint.sh 消费，非 pydantic Settings）

> 以下变量在容器入口脚本读取并转成 serve 的 CLI 参数。**除 `ENABLE_ADMIN_CDP` 外，其余仅在 `ENABLE_ADMIN_CDP=true` 时才生效**——默认多租户部署下共享 9222 浏览器不启动，刷新由每账户独立 Chromium（9322+）承担。

| 变量 | 必需 | 默认值 | 说明 |
| ---- | ---- | ------ | ---- |
| `ENABLE_ADMIN_CDP` | 否 | `false` | 是否启动共享 admin Chromium（9222）并注册其依赖端点 |
| `AUTO_REFRESH` | 否 | `true` | 共享 CDP 开启时，是否自动刷新 Token |
| `REFRESH_BEFORE_SECONDS` | 否 | `300` | 共享 CDP 开启时，Token 过期前多少秒开始刷新 |
| `CHROME_CDP_PORT` | 否 | `9222` | 共享 Chromium CDP 端口 |
| `CHROME_BIN` | 否 | 自动探测 | Chromium 可执行名（chromium/chrome 系列） |

## 客户端配置

| 设置 | 值 |
| ---- | -- |
| Base URL | `http://your-server:8000/v1` |
| API Key | `/admin` 下发的 Key，或全局 `API_KEY` |
| Model | 见 [可调用模型目录](#可调用模型目录)，推荐直接选列表中的 ID |

### Claude Code

```bash
export ANTHROPIC_BASE_URL=http://your-server:8000
export ANTHROPIC_API_KEY=YOUR_API_KEY
# Consumer 个人版
claude --model copilot-reasoning
```

> Anthropic 兼容走 `POST /v1/messages`。`model` 按 API Key 绑定的 provider 解析：M365 对应对话 tone，Consumer 对应「个人版模型 / Mode」中的兼容别名。

个人版使用 Claude Code 工具时推荐 `copilot-reasoning`。当前已实测 Claude Code 默认 25 个工具、约 31.8K 字符输入压缩至 8000 字符后，能够完成 `Read → tool_result → 最终答复` 的完整循环。`copilot-thinking`、`copilot-research`、`copilot-coco` 可作实验性备选；`copilot-search` 工具行为不稳定；`copilot` / `copilot-smart`、`copilot-chat`、`copilot-study` 仅建议用于文本对话。

### Cherry Studio / OpenWebUI / 其他 OpenAI 兼容客户端

```text
Base URL: http://your-server:8000/v1
API Key:  YOUR_API_KEY
Model:    Copilot_自动
          或 gpt-5.5_Chat / claude-sonnet-4-6 / gpt-5.5-持续 等
```

也可在客户端「刷新模型列表」拉取 `GET /v1/models` 后点选。若客户端忽略 vision 能力字段，请手动开启图片上传；M365 与 Consumer 两类账户都能识图。

## 认证

### API Key

`/v1/` API 请求需携带 API Key，两种头都接受（`Authorization` 优先）：

| 请求头 | 用途 |
| ------ | ---- |
| `Authorization: Bearer your-key` | OpenAI 兼容客户端的标准形式 |
| `x-api-key: your-key` | Anthropic 官方 SDK / Claude Code 的标准形式 |

**仅当 `API_KEY` 为空且 `/admin` 里一个 API Key 都没注册时，`/v1/` 端点才无认证开放**（此时启动会打印警告）。两种方式任选其一即可保护接口：设置全局 `API_KEY`，或在 `/admin` 创建 per-user Key。多租户推荐后者。

```bash
curl -H "Authorization: Bearer YOUR_SECRET_KEY" http://localhost:8000/v1/models

# Anthropic SDK 形式
curl -H "x-api-key: YOUR_SECRET_KEY" -H "anthropic-version: 2023-06-01" \
  http://localhost:8000/v1/models
```

### Web 管理页面

访问 `/admin` 运营总控台时需输入管理密码。密码通过 `ADMIN_PASSWORD` 环境变量设置；未设置则使用 `API_KEY` 作为密码。登录后 Cookie 有效期 7 天。

## 多租户使用

分层界面：

- **`/admin` 运营总控台**（管理密码登录）：管理包含 M365 与 Consumer 的账户池及所有 API Key。可新增账户、推送/刷新对应凭据、新建 Key 并绑定账户、设置各 Key 的默认对话模式、随时启用/停用或删除 Key；运行设置里可编辑全局对话模式列表。
- **`/` 用户自助页**（用自己的 API Key 登录）：普通使用者用分到的 Key 登录，管理自己的默认对话模式、提示词增强、系统提示词，并可自助推送/更新绑定账户的 M365 Token 或 Consumer 凭据（未绑定账户时自动创建并绑定）。

典型流程（每个 API Key 绑定一个账户；账户可以是 M365 或 Consumer）：

1. 运营方在 `/admin` 添加账户（可当场粘贴 M365 Token 或留空稍后推送；Consumer 账户由用户推送个人版凭据）
2. 新建 API Key 并绑定到某账户，把 Key 发给对应使用者
3. 使用者在 `/` 用自己的 Key 登录，按需自助推送 M365 Token 或 Consumer 凭据、调整默认对话模式与提示词
4. 在 OpenAI 兼容客户端里填入 Base URL（`http://<host>:8000/v1`）、自己的 API Key，以及 [模型目录](#可调用模型目录) 中的模型 ID

数据持久化：账户池 `accounts.json`、Key 表 `keys.json`、会话 `sessions.json` 以及 Consumer profile 等均写入 `TOKEN_DIR`（挂载卷），容器重启不丢。凭据、刷新状态和浏览器 profile 按账户隔离；Consumer 的账户级出站代理也随账户保存。不同 Key 即使开场白相同也不会串 M365 会话。

### 内存与刷新（按 provider 分流）

采用**浏览器非驻留 + 串行**策略，刷新状态按账户保存：

- **M365**：Token 临近过期且有请求时刷新，优先走 RT 纯 HTTP 交换；RT 缺失或失效时回退到账户专属 Chromium profile（独立 CDP 端口）抓取新 Token。
- **Consumer**：同一保活调度器按凭据年龄逐账户扫描，使用账户专属 Camoufox profile 静默重铸 ChatAI Token 与 Cookie；详见[凭据与 Cookie 自动保活](#4-凭据与-cookie-自动保活camoufox可选)。

两条路径共用同一全局浏览器锁，始终最多一个刷新浏览器存活；因此多个账户可以并存，峰值内存仍接近单租户，而不会把每个账户的浏览器同时常驻。

## 速率限制

微软不返回任何速率限制响应头，所以这是**自己给自己设的闸门**：拦住跑飞的自动化客户端，别让一个 Key 把大家共用的账户打到上游限流。

采用令牌桶：桶最多装 `突发容量` 个令牌，按 `速率限制 ÷ 60` 每秒回填，每个 `/v1/` 请求花掉一个。所以短时连发可以用满突发容量，长期均值被压在每分钟上限。超限返回 `429`，并带上 `Retry-After` 头（秒）。

- **全局默认** — `/admin` → 端口与日志 → 「速率限制（次/分）」与「突发容量」，默认 `60` 次/分、突发 `15`。填 `0` 表示**全局关闭限流**。
- **单 Key 覆盖** — `/admin` 用户管理里点「设置登录」展开行内编辑，填「速率继承」框。留空＝继承全局；填正数＝该 Key 用自己的上限；填 `-1` ＝该 Key 完全不限速。
- 限流按 **Key 独立计算**，一个人跑满不影响别人；未配置 Key 表时按全局身份统一计。
- 检查发生在按需 Token 刷新**之前**，被限流的请求不会白白拉起一次 Chromium。
- `/admin`、`/user`、`/healthz` 与 `/v1/m365-media` 不限流——前者会把运营者自己锁在门外，后者由签名 URL 鉴权、没有可计量的身份，且客户端加载历史图片时天然是批量请求。

> 桶状态存在进程内存里，容器重启即清零；改动上限会立刻重建对应的桶，无需等旧桶排空。

## 出站代理

给**服务器直连不到 M365 或 Consumer 上游的部署**用（例如放在中国大陆的机器）。在 `/admin` → 运行设置 → 「出站代理」填写全局默认出口，留空即直连。

支持 `http://`、`https://`、`socks5://`、`socks5h://`、`socks4://`、`socks4a://`，**必须写明端口**（`socks5h://127.0.0.1:1080`）。填错格式保存时会返回 400 并在按钮旁提示，不会静默存成直连。

全局默认覆盖所有未单独覆盖的出站流量，包括 M365 的 `wss://substrate.office.com` WebSocket、Consumer 聊天、媒体/Token HTTP 调用及刷新浏览器。实现方式是把地址写进标准的 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 环境变量，`httpx`、`websockets` 与 Consumer 传输会继承它。

- **本地 CDP 永不走代理**。`localhost` / `127.0.0.1` / `::1` 始终被钉进 `NO_PROXY`，**即使没配代理也照样钉**：`websockets` 15 默认会读环境变量代理，若部署自己设了 `HTTPS_PROXY`，浏览器控制通道会被代理吞掉，Cookie 刷新与 Token 抓取全线失败。
- 保存后**下一次**上游调用即生效，不用重启；已经跑着的刷新浏览器要等下次拉起才换代理。
- 清空该设置会**还原**部署自带的 `HTTPS_PROXY`（如果 `docker-compose` 里设过），而不是抹掉它。
- socks5 支持来自 `python-socks`（WebSocket）与 `httpx[socks]`（HTTP），已在依赖里，无需额外安装。

> Consumer 账户可在 `/` 用户自助页设置账户级出口；该设置优先于全局默认，清空后恢复继承全局。不同 Consumer 账户因此可以使用不同代理。M365 对话与 Chromium 刷新当前使用全局出口。

## 媒体 / Designer 授权抓取

图片、语音等媒体内容与 Designer（PPT/图像生成）走的是**独立于 substrate 的授权**，这两个 token **不在 MSAL 缓存里**，也不由 RT / CDP 的 substrate 刷新产生——它们只在页面打开含媒体的对话时，作为 `teams.microsoft.com` / `officeapps.live.com` 等域请求的 `Authorization` 头短暂出现。因此需要油猴脚本在浏览器侧嗅探并推送。

**抓取方式**：油猴脚本 hook 页面的 fetch/XHR，当检测到发往下列域的带 `Authorization` 头请求时，捕获并**自动静默推送**到代理（也可用面板按钮手动推）：

| 类型 | 触发域 | Token 形态 | 推送端点 |
| ---- | ------ | ---------- | -------- |
| media-auth | `*.teams.microsoft.com` | `Bearer <JWT>`（存储时剥离 `Bearer` 前缀） | `POST /user/account/media-auth` |
| designer-auth | `*.officeapps.live.com` | 裸 JWE（**无 `Bearer` 前缀**） | `POST /user/account/designer-auth` |

**使用步骤**：

1. 在 M365 Copilot 中**打开一个新会话，在当前会话发送生成图片然后发送生成音频的消息**，必须是同一条会话记录里包含两种。
2. 油猴脚本面板点击一键推送。
3. 之后经代理请求媒体时，服务端用存储的凭据回放；由 `/v1/m365-media` 媒体代理（HMAC 签名 + 主机白名单）对外提供。
4. 推送 Cookie 时脚本会附带 `media_seed_url`（当前对话 URL），刷新流程可回访该对话**重新触发媒体请求以保活**这两个 token。

> 这两个 token 有效期短、且只能在浏览器打开相应内容时抓到，属于**尽力而为**的懒保活；若媒体链接失效，重新打开一次含媒体的对话再推送即可。media / designer 授权与 substrate token 相互独立，缺失时**不影响**普通文本对话。

## 持久会话与上下文优化

会话键的解析**按以下优先级**（高到低）取第一个命中的：

1. **Header 模式（固定会话 ID，最高优先级）**：请求头 `X-M365-Session-Id: my-session`。客户端自定义任意字符串，同一字符串即同一 M365 会话，最稳定可控——推荐需要精确控制会话边界的场景（如多智能体、并行会话）。
2. **模型后缀 / 持续模型**：使用模型名带 `-持续`（或底层 `:persist`），例如 `Copilot_深度思考-持续`、`Reasoning:persist`。同一 Key 下按该模型键复用固定会话。
3. **自动检测（默认）**：普通模型 ID（如 `Copilot_自动`、`gpt-5.5_Chat`）按首条用户消息的哈希自动分组；同一对话的连续轮次复用同一个 M365 会话，在客户端新建对话则自动开启新会话。

> **租户隔离**：所有会话键都会自动加上 `tenant` 前缀（该请求 API Key 的 id，未绑定则用账户 id / `global`）。因此**不同 Key 即使推送相同的 `X-M365-Session-Id` 值或相同开场白，也不会串会话**。
>
> 对于 M365 Responses API（`/v1/responses`），代理签发的 `resp_...` 可作为一次性的 `previous_response_id` 线性续接，无需显式 Header。Consumer 返回的 `resp_...` 不恢复任何服务端历史，调用方仍须重发完整历史。

### 增量上下文优化

当复用一个已有历史的持久会话时，M365 服务端已经记住了之前的轮次，代理只发送**最新一轮的新增内容**（最新用户消息 + 本地工具结果），不再每次重发完整对话历史。

这能节省上下文窗口、加快响应、避免 M365 聊天记录里堆积冗余历史文本。普通模型与 `-持续` 模型在复用会话时均启用此优化。

> M365 Copilot 按账号许可证授权、非按 token 计费，此优化不影响费用，但能提升长对话质量与速度。

### 会话持久化

会话映射（会话键 → 对话 ID、客户端会话 ID、轮次计数）会落盘到令牌存储目录，并在启动时恢复。

因此**容器重启后继续旧对话也能正确续接**：恢复同一个对话 ID、轮次计数大于 0，增量优化照常生效，不会把旧对话当成新会话、不再在 M365 侧产生多条重复记录。

> 持久化主要解决**容器/进程重启**导致的内存会话丢失问题。

### 新对话检测

自动检测按首条用户消息的哈希分组会话。为避免**相同开场白反复新开对话**时哈希碰撞到同一会话（导致复用旧 M365 线程、模型拿到错乱上下文而幻觉），代理会判断请求是否为对话首轮：**首轮（消息中没有任何 assistant 回复）会重置会话、开启全新的 M365 线程**，续接轮次才复用。`-持续` / `:persist` 与 Header 模式靠显式会话键，不受影响。

## 提示词增强与兜底重试

「强制调用 Tool」依赖系统提示词引导模型输出 `tool_call` 块。Web 管理页面提供两级可编辑、持久化的提示词，以及针对 M365 原生行为的服务端兜底：

- **提示词增强**：追加在工具调用提示词之后的自定义指令，用于微调 tool_call 行为，留空则不追加。
- **系统提示词（高级）**：覆盖工具调用的基础系统提示词（定义 tool_call 格式与规则）。默认折叠，需解锁并确认警告后才能编辑；动态工具列表始终自动追加、不可编辑；留空则用内置默认。两者都带「恢复默认」。
- **服务端兜底重试**：M365 Copilot 有原生「生成文件」功能，会把文件托管到自己的对象存储并返回下载链接，而不走 `tool_call`。当代理检测到响应「声称生成了文件（含托管附件链接或"已生成"等措辞）却没有任何 tool_call」时，会用纠正指令对同一会话自动重试一次，逼模型交出真正的 `tool_call`。命中兜底的调用在 Web「API 调用记录」中标记为 `retried`。
- **散文兜底（内联输出救援）**：当模型不输出 ```` ```tool_call ```` fence、但正文里包含「反引号绝对路径（如 `` `C:/temp/file.bat` ``）+ 语言标签匹配的代码块（如 ````bat）」时，代理会自动合成 Write `tool_call`。服务端内建的提示词引导会推动模型往这个形态输出（明确禁止生成托管附件、指定内联输出的格式），以提高在 M365 拒绝 fence 时的救援率。此机制保持解析器的严格性（避免把示例代码块误识别为写文件指令），测试中 `.bat`/`.html` 等文件类型的成功率约 60%；`.py` 等部分类型因 M365 倾向用 markdown 链接展示文件名（而非反引号路径）仍可能失败。

> 提示词只能降低模型幻觉概率，无法根除（底层模型指令遵循问题）。若工具调用仍不稳定，可尝试切换到深度思考（`Copilot_深度思考` / `Reasoning`）模式，或新开会话。

### tool_choice

两种 API 的 `tool_choice` 都会被解析并生效（此前该字段被接收后静默忽略）：

| 客户端传入 | 行为 |
|---|---|
| 不传 / `"auto"` / `{"type":"auto"}` | 默认。注入全部工具，模型自行决定 |
| `"none"` / `{"type":"none"}` | **完全不注入工具契约**，且响应侧不解析 tool_call |
| `"required"` / `{"type":"any"}` | 追加指令要求本轮必须调用某个工具 |
| `{"type":"function","function":{"name":"X"}}`（OpenAI）<br>`{"type":"tool","name":"X"}`（Anthropic） | 只把 `X` 一个工具告诉模型，并要求调用它 |
| `parallel_tool_calls: false`（OpenAI）<br>`disable_parallel_tool_use: true`（Anthropic） | 追加指令要求本轮最多一个 tool_call |

只有 `none` 是**完全可靠**的——它靠「不发送工具契约」实现，是本地决策，同时关掉提示词注入、响应解析、散文兜底和纠正重试，所以即使模型自发吐出 fence 也不会有 `tool_use` 漏给客户端。其余模式是提示词层面的引导，受与上文同样的依从性限制。

指定工具名时会把工具列表**收窄到那一个**，因此模型即使照做也无法挑错工具。名字不存在时保留完整列表而非清空——客户端要的东西我们看不到，清空会让请求看起来像压根没带工具。

### 运行权限（只读 / 完全）

拦住模型主动交出的**改写类**工具调用。判定用工具名的大小写不敏感启发式：`Read`、`Grep`、`Glob`、`ls`、`SearchCodebase` 算只读，其余（写文件、跑命令等）在「只读」下被丢弃，并在回复里附一句说明本轮为什么没有 tool_call。它判不了自定义函数的真实副作用，**不是安全边界**，工具执行器那边仍要自己做权限与审批。

- **全局默认** — `/admin` → 运行设置 → 「运行权限」。
- **单 Key 覆盖** — 使用者在 `/` 用户自助页的「默认配置」里自己改，选「继承全局」＝跟随全局。
- **全局值是上限，不是默认值**：per-key 值只能**收紧**。全局设成「只读」时，Key 上钉「完全」也照「只读」执行——这一栏唯一的写入口是用户自己的页面，若按普通覆盖解析，一条 `POST /user/tone` 就把运营方的策略解掉了。反过来全局「完全」时，Key 可以自己钉「只读」。
- 想只给某个 Key 放宽，只能把全局放到「完全」再让其余 Key 各自钉「只读」：同一个字段里分不出「管理员批的例外」和「用户自己钉的」，所以放宽这件事刻意不做成可表达的。

## 个人版（消费者版 Copilot）账户

除 M365 企业版外，本项目也支持把 **个人微软账号的 `copilot.microsoft.com`** 接进同一套 `/v1` 接口。不需要 M365 订阅，代价是能力受限（见下方[限制](#限制)）。ChatAI Token 与 Cookie 可以自动保活（需 `-camoufox` 镜像，见[凭据与 Cookie 自动保活](#4-凭据与-cookie-自动保活camoufox可选)），也可以手动重推。

一个账户要么是 M365，要么是个人版，由账户的 `provider` 字段决定。个人版账户仍属于同一套多租户账户池：每个 API Key 绑定一个账户，多个用户可以分别绑定多个 Consumer 账户；各账户的 Consumer Token、Cookie、出站代理、Camoufox profile 和保活状态彼此隔离。推送个人版凭据会把该账户**切到 `consumer`**，并将它移出 M365 的刷新链路（RT 换取、Cookie 回放、Chromium CDP 抓取对它都没有意义），改由独立的 Camoufox 路径处理。旧共享 admin Chromium 及其 `--no-auto-refresh` 开关不控制账户池保活，因此个人版保活仍会运行。`/admin` 账户表会给这类账户打上标记。

同一个已绑定账户切换到另一个 Microsoft 个人主体前，需要先在用户页「登出 Microsoft」或解绑；不同账户不会共用 Consumer profile。隔离单位是账户，不是 Key：如果管理员故意把多个 Key 绑定到同一个账户，这些 Key 会共享该账户的凭据、代理、profile 和保活状态；要做到一人一号，应给每人创建并绑定独立账户。这里的“移出 M365 刷新链路”只表示 provider 刷新实现不同，不表示个人版失去多租户隔离或账户级保活。

### 1. 配置出站代理（仅在服务器直连不到 Copilot 时）

`copilot.microsoft.com` 在部分网络下会被 SNI 阻断。Consumer 账户默认继承 `/admin` → 运行设置中的全局出口；也可在 `/` 用户自助页为当前绑定账户设置独立代理，因此多个 Consumer 账户不必共用同一出口。

> **写 `socks5h://` 而不是 `socks5://`。** 两者的差别是 DNS 在哪解析：`socks5` 在本地解析域名，解析结果又会撞回被阻断的路径；`socks5h` 把域名交给代理远端解析。实测同一个代理端口，`socks5://` 失败、`socks5h://` 成功。`http://` 同样可用。

### 2. 推送凭据

1. 安装同一个[油猴脚本](#方式二油猴脚本一键推送m365-与个人版都可用)（`@match` 已覆盖 `copilot.microsoft.com`，无需另装）
2. 在 `/` 用户自助页拿到自己的 **API Key**，填进油猴面板的「用户 API Key」框；「代理地址」填**本代理服务**的地址（如 `http://localhost:8000`）
3. 打开 [copilot.microsoft.com](https://copilot.microsoft.com) 并登录个人微软账号
4. **发送一条消息** —— ChatAI token 只出现在聊天 WebSocket 的 URL 里，不发消息就抓不到
5. 面板「个人版 Copilot」一栏变绿显示 `✓ ChatAI Token 可用` 后，点 **一键推送个人版**

> 脚本按域名自动切换面板内容：在 copilot.microsoft.com 上只显示「个人版 Copilot」，企业版那套（`Token` / `Media Bearer` / 模式抓包）收进底部的「其他产品」折叠抽屉里 —— 它们是企业版专用的，个人版账户不需要。反过来在 m365.cloud.microsoft 上，个人版一栏同样收进抽屉。登录域（`login.live.com` 等）两栏都显示，因为登录中途无法判断你要用哪个产品。
>
> 抽屉只是折叠、没有移除，因为 M365 的 **Cookie 推送**查的是绝对域名，在任何标签页都能用；其余按钮仍需在各自的站点上才能抓到凭据。

### 3. 凭据过期后手动重推

个人版的 ChatAI token 对本服务是**不透明**的（不是可解码的 JWT），因此管理页只能显示「有没有存」，无法显示剩余有效期。过期的表现是 `/v1/` 请求返回 **502**，消息体里带上游的失败原因。

**手动恢复方式就是重复上面第 2 步**：回到 copilot.microsoft.com 发一条消息，再点一次推送按钮。若用了下面的 `-camoufox` 镜像，多数情况不需要走到这一步。

### 4. 凭据与 Cookie 自动保活（Camoufox，可选）

用 `ghcr.io/<repo>:fox-camoufox` 这类带 `-camoufox` 后缀的镜像，服务端就能定期重铸个人版 ChatAI Token 与 Cookie 快照，不需要人反复点推送按钮。默认精简镜像不含 Camoufox，因此只能手动重推。

原理是让 **MSAL 的静默 SSO 重新铸一整套凭据**：容器里起一个使用持久 profile 的 Firefox（Camoufox），加载 copilot.microsoft.com，等页内 MSAL 用 profile 里的微软账号会话换出新的 ChatAI Token，导出新的 Cookie 快照，然后关掉浏览器。它不是延长或反复回放最初推送的 Cookie。服务端只接受与旧值不同的新 Token、同一个 Microsoft 账号主体及可复用 Cookie，随后原子写回账户存储。全程不点任何东西、不发聊天消息，所以**没有任何 Turnstile 环节**。实测冷启动到拿到 Token 约 **6.7 秒**，之后进程即退出，不是常驻浏览器。

这是真「铸新的」而不是「读缓存」：把 localStorage 里的 MSAL Token 清空再刷新，拿到的是一个**不同**的新 Token 且没有跳转登录页，因此旧 Token 过期后仍可恢复。

四个触发点：

- **推送后初始化** —— 用户成功推送个人版凭据后，后台立即强制重铸一次，尽早把用户浏览器抓到的凭据换成与服务端 Camoufox 指纹一致的快照
- **定时保活** —— 调度器默认每 5 分钟扫描；凭据距上次成功捕获满 1 小时后重铸。个人版 Token 不透明、读不到 `exp`，只能按年龄调度
- **请求内自救** —— `/v1` 请求在输出任何内容前收到明确的 `ClearanceRequired` 时，自动重铸一次并重试当轮。普通请求不会额外承担这约 7 秒的浏览器启动
- **管理页刷新按钮** —— 手动触发时立即重铸

前提是 profile 里那个**微软账号会话本身还活着**。每次重铸都会顺带保持会话活跃，但它真失效了（换密码、被撤销、长期没动），仍需要人工重新登录并推送。重铸失败时旧凭据不会被覆盖；同一账户进入 30 分钟退避，避免失效会话每 5 分钟反复拉起浏览器。

profile 落在 `TOKEN_DIR/profiles/` 下，目录名包含代理账户 ID 与 24 位 Microsoft subject 哈希，例如 `<proxy-account-id>-consumer-<subject-hash>`；默认位于 `token-data` 卷内的 `/home/app/token/profiles/`。**这个挂载必须是持久卷，不能是 tmpfs**，否则容器重建后会丢失 Microsoft 登录会话，需要重新人工登录并推送。

需要从干净状态重来时，请在 `/` 用户自助页点击「登出 Microsoft」。服务端会清除账户凭据，并按实际账户 ID 删除当前及兼容旧版的 Consumer profile；不要手工拼接目录名执行 `rm -rf`。

> 续期时会拉起浏览器，峰值内存约 417 MB。调度用同一把全局锁把它和 M365 的 Chromium 刷新**串行化**，不会两个浏览器同时在跑；`docker-compose.yml` 默认的 2G 内存上限够用。

可用下面的日志判断保活是否真正成功；只有第二行出现才代表新 Token 与 Cookie 已写回：

```text
Keepalive: re-minting consumer <account-id>
Consumer refresh for <account-id>: re-minted <N> cookies
```

若日志只有 `Consumer refresh unavailable`、`failed`、`returned the previous token`、账号不匹配或无可复用 Cookie，则本次没有更新凭据。请检查镜像是否含 Camoufox、`TOKEN_DIR/profiles` 是否持久化、Microsoft 登录会话是否仍有效，以及聊天与重铸是否使用同一个可用代理出口。

> 为什么是 Firefox 而不是镜像里已有的 Chromium：consumer 端点会**按 TLS 指纹判决**，Chromium 指纹拿到的是 `method: null` 的 `challenge`（只能由页内 JS 现场解），Firefox 指纹则根本不触发 challenge。这也是 HTTP 客户端那一侧必须用 curl_cffi 的 `firefox147` 的同一个原因。
>
> 代价：镜像大约 **+936 MB**，每个账户的 profile 再占约 97 MB（落在 `token-data` 卷上，与上面的 profile 路径同一处）。这也是它没进默认镜像的原因。本地自建：`docker build --build-arg WITH_CAMOUFOX=true ...`
>
> **保活只解决凭据老化。** 它不能修复 `challenge method=null`、不匹配的 TLS 指纹、代理出口信誉或地区限制、上游额度与服务故障。遇到这些问题，即使 Cookie 刚重铸也可能继续失败。

### 限制

个人版走的是另一套上游协议。下列能力包含协议硬限制与实验性上游行为，不能按 M365 的表现推断：

| 能力 | 个人版 | 说明 |
|---|---|---|
| Consumer mode | ⚠️ | 通过可配置的 `model → mode` 映射把 mode 原样发给上游；没有 `-持续` 变体，且可用性受账户、地区与 Microsoft rollout 影响 |
| 提示词增强 | ❌ | 该文本在 M365 客户端内部注入（`substrate_client.py`），个人版客户端不经过那条路径 |
| 系统提示词 | ⚠️ | 请求自带的 system 消息会拼进正文；管理页的工具系统提示词只在存在有效工具合同的请求中注入，无工具或 `tool_choice=none` 时不注入 |
| 工具调用（提示词模拟） | ⚠️ | 非原生工具协议；客户端 tools 压缩为签名后随正文发送。`copilot-reasoning` / `copilot-thinking` / `copilot-research` / `copilot-coco` 已完成真实工具循环，其他映射可能忽略工具或断开 |
| 图片输入 | ✅ | 图片经 `POST /c/api/attachments` 上传（需聊天 Token，仅 Cookie 会 403），返回的相对 URL 排在文字之前发给上游，与网页版同形；纯图片消息也能识图。`png`/`jpeg`/`webp` 直接接受，`gif`/`bmp` 改标为 `image/png` 后再试（尽力而为），单轮上限 10 张，上传失败只丢该图并记 WARNING |
| 图片生成 | ✅ | 让它画图会返回 Markdown 图片链接。个人版不需要企业版那套「媒体授权」——链接是匿名可取的（实测无 Cookie、无 token 直接 200） |
| 持续会话 | ⚠️ | 每轮开新对话，完整历史每轮重发，因此上下文不丢；但上游侧不存在长期会话 |
| Token / Cookie 自动保活 | ⚠️ | RT / CDP 两条 M365 链路都不适用。`-camoufox` 镜像用持久 Microsoft 登录 profile 静默重铸新 Token 与 Cookie（见[凭据与 Cookie 自动保活](#4-凭据与-cookie-自动保活camoufox可选)）；默认镜像只能手动重推 |

> **TLS 指纹是硬约束。** 个人版上游按 TLS 指纹判客户端：curl_cffi 的 chrome / edge / safari 全系会被拒（表现为收到 `challenge` 帧后连接被掐断），当前固定使用 `firefox147`。这是实测得到的经验事实，微软调整策略后可能失效。

## 企业版与个人版的差异

本项目同时支持 **`substrate.office.com`（M365 企业版）** 与 **`copilot.microsoft.com`（个人微软账号）** 两条 provider 路径。两者上游协议不同，因此约束差别很大；下表均以本项目当前实现与部署实测为准：

| | 本项目（M365 企业版） | 个人版（消费者版 Copilot） |
|---|---|---|
| 上游 | `wss://substrate.office.com/m365Copilot/Chathub`（SignalR） | `wss://copilot.microsoft.com/c/api/chat` |
| 鉴权 | substrate JWT（RT 纯 HTTP 交换，或 CDP 抓取） | 登录 Cookie + ChatAI access token（WebSocket query 参数） |
| Cloudflare | 上游无 Turnstile | 该站**不签发 `cf_clearance`**（实测：全新 profile 加载后只有 `__cf_bm` / `__cflb`，无 Turnstile iframe）。验证发生在**应用层**——`challenge` 帧的 `method` 为 `null` 时要的是页内 JS 现场铸的 Turnstile token，不在任何 Cookie 里，因此「浏览器过验证、HTTP 客户端重放 Cookie」这条路不存在；能否通过只取决于 TLS/HTTP 指纹与出口信誉 |
| 提示词长度 | 实测 147k 字符仍完整（在首轮埋标记、末轮追问，标记可复述） | 默认截断到 8000 字符，需要压缩历史 |
| 并发 | **支持**。每轮开独立 WebSocket（各自 conv/session id），实测 4 路并发同账号答案互不干扰 | 取决于具体代理实现；复用单 socket 的实现必须串行化 |
| 模型/模式 | 多模式（可在管理页编辑列表），每个模式另有「-持续」变体 | 可配置 `model → mode` 兼容别名，无「-持续」变体；实际可用性随账户、地区与 rollout 漂移 |
| 浏览器依赖 | Chromium + 裸 CDP（仅刷新时按需拉起） | 聊天走 curl_cffi（TLS 指纹）；凭据续期需 Firefox 内核，本项目用 Camoufox，同样只在续期时按需拉起（约 6.7 秒后退出）。镜像里的 Chromium 顶不了这个班——consumer 端点拒 Chromium 指纹 |
| 速率限制 | 令牌桶，默认 60 次/分 | 默认 12 次/分（其文档称单账号约 15 rpm 起开始限流） |

**共同的短板**：两边都没有原生 tool-calling 通道，都靠提示词约定 + 解析文本实现，因此都受模型依从性限制。这不是实现选择，而是两个上游协议都没提供该能力。

**对使用者的实际影响**：个人版不需要 M365 订阅，但每个个人账号都需要维护自己的浏览器登录态；本项目用各账户独立的 Camoufox 持久 profile 承载，并由每次续期自动焐热，只有该账号的登录态真失效时才需要人工介入一次。普通 HTTP/WebSocket 重放同一组 Cookie 仍可能被拒。个人版代理常见的 8000 字符历史上限和串行限制属于具体实现约束，不是消费者协议本身的保证。两种 provider 都支持多账户绑定与隔离；M365 额外提供已经验证的长上下文和真并发。

## 架构

```
容器启动 (entrypoint.sh)
  ├─ [可选] 共享 admin Chromium headless → CDP 9222
  │     仅当 ENABLE_ADMIN_CDP=true 时启动（默认 false，多租户不启动）
  │     用于单账户启动捕获 + /admin/token/auto-capture、/admin/cookie/inject、/admin/chromium/*
  │
  └─ copilot-openai-proxy serve (端口 8000)
      ├─ /v1/* — OpenAI/Anthropic 兼容 API
      │         · M365 model → 对话 tone（+ 可选 -持续 / :persist）
      │         · Consumer model → 可配置上游 mode（无持续变体）
      │         · 按 API Key 解析账户与 provider → 对应模型目录 / 提示词策略
      ├─ /admin/* — 运营总控台端点（账户池 + Key 管理 + 设置/可观测性）
      ├─ /user/* — 用户自助端点（用自己的 Key 管理模式/提示词/账户 Token/Cookie）
      ├─ /admin — 运营总控台页面（管理密码登录）
      ├─ / — 用户自助页面（API Key 登录）
      └─ 账户刷新与保活（按账户隔离、按 provider 分流）
          ├─ M365：RT 优先 → 账户专属 Chromium（CDP 9322+）
          └─ Consumer：账户专属 Camoufox profile → ChatAI Token + Cookie
              两条路径共享全局浏览器锁，同一时刻最多一个浏览器，峰值内存接近单租户
```


## 开发与调试

```bash
uv sync --extra dev
uv run pytest -q          # 全量约 1.8k 条，25s 左右
```

模板里的内联 JS 由多个 `template_*.py` 拼出来，`tests/test_template_inline_js_syntax.py` 用 `node --check` 兜住拼错；单个渲染函数的行为（例如抓包面板对畸形字段的容忍）可以像 `tests/test_admin_capture_options_sets.py` 那样把函数抠出来丢给 node 跑。装了 node 才跑，没装自动 skip。

**临时文件一律放 `.probe/`**，该目录整个在 `.gitignore` 里，不要再散到仓库根目录：

| 目录 | 放什么 |
| ---- | ------ |
| `.probe/` | 协议探针脚本（上游抓包、活体轮次、代理出口扫描等） |
| `.probe/ui/` | 浏览器布局探针（Playwright 必须 `channel="chrome"`，仓库没下 chromium；布局量测 headless 就够，只有 CPU / invalidation trace 需要 headed） |
| `.probe/shots/` | 探针截图 |
| `.probe/out/` | 抓下来的请求 / 响应体、日志 |

值得留下来复用的诊断脚本放 `tests/manual/`（进仓库；文件名不以 `test_` 开头，`pytest` 不会收集它们）；`.probe/` 只放用完就扔的。

## License

Apache License 2.0

## 致谢

- [kuchris/m365-copilot-openai-proxy](https://github.com/kuchris/m365-copilot-openai-proxy)
- [KilimcininKorOglu/M365Bridge](https://github.com/KilimcininKorOglu/M365Bridge)
- [HEXUXIU/M365-Copilot2API](https://github.com/HEXUXIU/M365-Copilot2API)
- [xtekky/gpt4free](https://github.com/xtekky/gpt4free) —— 个人版附件上传端点的字段形状线索（仅端点与字段名，实现与其余行为均自行实测）
- [LINUX DO](https://linux.do/)
