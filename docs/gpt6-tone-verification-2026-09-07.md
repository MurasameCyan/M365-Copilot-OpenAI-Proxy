**实测结论：`Gpt_6_Astra` 能完成实际任务，但底层 GPT-6 身份未证实；`Gpt_6_Reasoning` 在当前 M365 账号的两次普通直连新会话中均失败。**

测试时间：2026-09-07 21:28、21:37（Asia/Shanghai）。对象为 [Issue #3](https://github.com/MurasameCyan/Ciallo-Ms-365-OpenAI-Proxy-Docker/issues/3) 中的两个 tone。Issue 正文仅列出名称，没有附请求、响应或型号验证材料。

| 实际发往 M365 的 tone | 本轮结果 | 耗时 | 原始协议证据 |
| --- | --- | --- | --- |
| `Gpt_6_Astra` | 1 次成功，返回 364 字符的任务答案 | 7.165 秒 | 17 个更新帧，`turnState=Completed`、`result.value=Success` |
| `Gpt_6_Reasoning` | 2 次失败，没有任务答案 | 2.335 / 3.499 秒 | 两次均为 `turnState=Failed`、`result.value=InternalError` |
| `Gpt_5_6_Chat`，正常对照 | 2 次成功，答案符合预期 | 4.875 / 4.332 秒 | 两次均为 `Completed / Success` |
| `Gpt_99_Invalid_Control`，无效名称对照 | 2 次没有答案或完成帧 | 0.235 / 0.206 秒 | 仅 SignalR type 3；第二轮记录到 `Failed to invoke 'Chat' due to an error on the server.` |

所有请求使用同一道与本项目有关的实际任务：分析流式代理在“尚未向调用者输出任何字节”和“已经输出部分答案”两种情况下能否透明重试，要求返回 JSON。成功回复均给出 `retry_A=true`、`retry_B=false` 及理由。没有用模型自我介绍作鉴定依据。

验证固定到 `api.txt` 中第二组密钥实际绑定的 **M365** 账号。先通过服务现有刷新流程更新该账号登录状态，再在现有目标容器内读取凭据；测试脚本只接收密钥摘要来定位绑定，不导出访问令牌。每次调用都直接使用 `SubstrateCopilotClient._chat_stream_for_turn`，创建独立新会话，并记录实际发送的 `tone` 和 `isStartOfSession=true`。没有经过本地 model 名称解析或工具规划，也没有自动重试。使用该账号的现行协议配置，本次为内置 profile。

这一步有必要：本项目的 `/v1/models` 是配置生成的列表，`resolve_tone()` 对未配置名称可能回退到默认值，OpenAI 兼容响应中的 `model` 也不是上游模型身份证明。实测时线上已配置这两个新 tone；本轮只验证它们，没有新增模型映射。

证据支持的范围：

- **Astra tone 的可调用性已得到一次正向验证。** 无效名称对照走了不同的失败路径，可以排除“本地忽略传入名称，所有请求都成功回退”这一解释。它仍不能排除 Microsoft 在服务端把该 tone 映射到其他模型。
- **Reasoning 的普通直连在本次账号和时间窗口内不可用。** 两次失败前后，正常对照均成功，因此不能把失败归因于所有请求共有的登录失效或连接故障。`InternalError` 本身没有说明是账号权限、灰度配置还是该路由的暂时故障，不能据此判定模型不存在或永久不可用。
- **实际底层型号仍无可靠证明。** 回包没有模型 ID、模型版本或部署标识。匹配到的版本字段只有 Adaptive Card 的 `1.0` 和 `item.result.serviceVersion=1.0.03535.51799`，分别是卡片格式与服务版本，不能解释为 GPT-6 的模型版本。

随后按用户提示，复核了容器的 `call_log.json`、同一时间段的 Docker 日志和正在运行的路由源码，确认 **Reasoning 名下确实有一条返回了答案的记录，但实际执行的是 Studio 的 `Magic` 模式**：

| 时间（北京时间） | 请求与记录 | 实际路径与结果 |
| --- | --- | --- |
| 2026-09-07 18:36:05 | `model=gpt-6`、`tone=Gpt_6_Reasoning`，声明 `fs_read`、`read_file`，返回 249 字符 | 最终 `tool_planning=studio`；Studio 客户端固定使用 `Magic` |
| 2026-09-07 18:36:29 | 同一 model/tone，没有 tools | 普通模型调用，`InternalError`；18:36:31 的 Docker 日志也记录了该上游错误 |

第一条的 `incremental=false`、`turn_count=0`，所以不能把它解释成普通会话复用。核查时版本的 `routes_api_chat.py:159` 先把请求解析出的 tone 写入日志，`:200` 给 Studio 客户端设置 `STUDIO_TONE`，而当时 `studio_planner.py` 定义 `STUDIO_TONE="Magic"`。规划阶段切换时 `routes_api_chat.py:321` 会更新 `tool_planning`；该成功记录最终仍是 `studio`，且没有 `studio_fallback` 或 `router_fallback`。核查时容器与本地这两个文件在统一换行后 SHA-256 一致。后续跟随所选 tone 的代码改动见[实现与工具流程验证](studio-selected-tone-2026-09-07.md)，不用于改写这条历史记录的执行路径。

因此，“gpt-6 名下成功返回了答案”属实，但该记录不验证 `Gpt_6_Reasoning` 的直连可用性。旧回复自称 GPT-5 chat 也不参与型号判断。脱敏日志与源码定位保存在`.probe/issue3-gpt6-20260907/reasoning-log-audit.json`（容器日志复核记录，仅本地保存）。

22:36–22:40 的后续隔离实验确认：绑定现有 Studio agent 后，可以原样发送多个非 `Magic` tone，包括 `Gpt_6_Reasoning` 和 `Claude_Sonnet` 并得到答案；同一时间的 Reasoning 普通直连仍失败。这补充了 Studio 接受度的事实，不改变上述历史调用被业务代码固定为 `Magic` 的判断。详见 [Studio tone 实测](studio-tone-verification-2026-09-07.md)。

另外检查了 Microsoft 公开前端入口：未登录请求只得到空壳或登录页，未取得应用 bundle，因此没有可用于补充验证的当前官方 UI 映射。这里的“未取得”不构成名称不存在的证据。

结合后续实验，`Gpt_6_Astra` 可用于已验证的普通直连和 Studio 流程；`Gpt_6_Reasoning` 仅在有效 Studio agent 路径下验证成功，普通直连与完整 Router 工具流程仍失败。两者底层型号均未确认，不能宣称“两者都已经验证为真正 GPT-6”。

2026-09-08 代码更新：按用户要求同时加入 `Gpt_6_Astra | gpt-6_Chat`、`Gpt_6_Reasoning | gpt-6`，各自提供持续会话变体。加入目录不表示所有路径可用；Reasoning 的有效 tools、Studio agent、规划模式与续轮限制详见 [README](../README.md#astra-与-reasoning-的区别及使用限制)。仅与历史默认列表完全相同的配置会自动迁移，其余自定义列表保持原样。本轮 GitHub 新 tone 检索见[检索记录](github-tone-survey-2026-09-07.md)。

本地证据与复现入口：

- `.probe/issue3-gpt6-20260907/round1.jsonl`（第一轮逐请求记录，仅本地保存）：两个候选及正常、无效名称对照。
- `.probe/issue3-gpt6-20260907/round2.jsonl`（第二轮逐请求记录，仅本地保存）：Reasoning 复测、正常对照，以及无效名称的终止帧错误。第一轮脚本尚未保存 type 3 的 `error` 内容，第二轮已补充；不能把第一轮的客户端 `error=""` 当作成功。
- `.probe/issue3_tone_live.py`（最小实测脚本，仅本地保存）：通过 SSH 标准输入在目标容器执行，每条结果即时落盘，输出文件采用独占创建，避免续跑覆盖已有证据。
- `.probe/issue3-gpt6-20260907/public/evidence.json`（公开来源核查，仅本地保存）：Issue 原文、公开入口响应以及此前 release 链接的核对。HEXUXIU 对应版本的正确 tag 为 [v0.6.5](https://github.com/HEXUXIU/M365-Copilot2API/releases/tag/v0.6.5)，该 release 不提供这两个 GPT-6 tone 的身份依据。

实测记录只含本次任务答案、所选协议字段与字段路径，不含 API 密钥、M365 令牌或账号身份。`.probe/` 是本地忽略目录；上表与关键错误已保存在本文，便于独立阅读。

项目检查：`node --check get_token.user.js` 通过；`python -m pytest tests/ -q --no-header` 为 **1837 passed、3 skipped**。这些检查验证项目回归状况，不参与远端模型身份判断。

可随仓库查看的[脱敏验证摘要](evidence/model-tones-2026-09-08.json)包含关键出站参数、完成状态及工具流程结果；原始 `.probe/` 文件仅本地保存。
