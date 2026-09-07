**当前 Studio 并非只能携带 `Magic`。同一已绑定 agent 下，7 个 tone 请求都返回了实际任务答案和 `Completed / Success`。** 这验证了这些 agent/tone 组合的请求可用性，尚不能证明服务端底模按 tone 切换。

测试时间：2026-09-07 22:36–22:40（Asia/Shanghai）。使用 `api.txt` 中 M365 密钥绑定的同一账号，以及账号库里已经存在、通过身份核验的 Studio agent。没有创建或发布新 agent。

| 发送的 tone | Studio 请求结果 | 耗时 |
| --- | --- | --- |
| `Magic` | `Completed / Success`，返回任务答案 | 6.656 秒 |
| `Chat` | `Completed / Success`，返回任务答案 | 5.914 秒 |
| `Reasoning` | `Completed / Success`，返回任务答案 | 3.884 秒 |
| `Gpt_5_6_Chat` | `Completed / Success`，返回任务答案 | 4.168 秒 |
| `Gpt_6_Astra` | `Completed / Success`，返回任务答案 | 5.841 秒 |
| `Gpt_6_Reasoning` | `Completed / Success`，返回任务答案 | 6.694 秒 |
| `Claude_Sonnet` | `Completed / Success`，返回任务答案 | 6.952 秒 |

所有请求使用同一道流式重试策略分析题，核心问题是“尚未输出任何字节”和“已输出部分答案”两种情况下是否允许透明重试。七条回复的核心判断均为 `retry_A=true`、`retry_B=false`。每个 tone 测一次，未据此宣称长期稳定性或工具规划能力已经验证。

实验绕过业务路由中的 `studio_client._tone = STUDIO_TONE`，直接构造带 `studio_agent_id` 和指定 `tone` 的 `SubstrateCopilotClient`。逐条检查实际序列化的出站请求：

- `tone` 与该条候选完全一致，`isStartOfSession=true`。
- `threadLevelGptId.id`、`gpts[0].id` 与同一已验证 agent 绑定一致，来源为 `MOS3`，普通 `plugins` 已移除。
- 每条只有一次发送，直接调用 `_chat_stream_for_turn`，不经过普通/Router/Studio 回退链，也没有空响应自动重试。

另外两条对照使用相同任务：

| 对照 | 结果 | 含义 |
| --- | --- | --- |
| Studio + `Gpt_99_Invalid_Control` | 0.271 秒，仅 type 3，`Failed to invoke 'Chat' due to an error on the server.`；无答案、无成功完成帧 | 不能用“所有随意字符串都能成功”解释前面七条结果 |
| 普通直连 + `Gpt_6_Reasoning` | 2.456 秒，`Failed / InternalError` | 同一时间窗口内，携带 agent 与普通直连的行为不同；Studio 成功不能外推为普通直连可用 |

因此，代码中固定 `Magic` 是项目当时的兼容策略，不是已经证实的 Studio 协议唯一值限制。此前关于当前租户 Studio 拒绝 Claude 模式的代码注释属于历史依据，至少 `Claude_Sonnet` 在这次普通问答实验中成功了。上述实验时业务路由仍固定发送 `Magic`；后续已按用户要求改为跟随所选 tone，并继续验证真实工具流程，见[实现与工具流程验证](studio-selected-tone-2026-09-07.md)。

证据边界：出站请求带有 agent 和 tone，并不等于已验证服务端采用该 tone 选择底模。回包未提供实际模型 ID、模型版本或部署名称；`gptIdentifiers[].version=1.0.7` 是 agent 标识结构中的版本，另有 Adaptive Card 的 `1.0` 与服务版本 `1.0.03535.51799`，均不能用于认定底模。此次未保存响应 agent ID 的值或与请求 ID 比较的布尔结果，也没有核验该 agent 的完整服务端配置。不能仅凭成功、生成风格或模型自称认定切换到了 GPT-6 或 Claude。

这与之前的历史日志结论不矛盾：18:36:05 那条请求在业务路由中确实被改为 Studio/`Magic`；本次隔离实验则直接发送不同 tone。参见[原始 GPT-6 实测与日志复核](gpt6-tone-verification-2026-09-07.md)。

原始本地记录：

- `.probe/issue3-gpt6-20260907/studio-tones-round1.jsonl`（七个 Studio tone，仅本地保存）
- `.probe/issue3-gpt6-20260907/studio-tone-invalid-control.jsonl`（Studio 无效 tone 对照，仅本地保存）
- `.probe/issue3-gpt6-20260907/ordinary-reasoning-studio-control.jsonl`（同一时间的 Reasoning 普通直连，仅本地保存）
- `.probe/issue3_tone_live.py`（实测脚本，仅本地保存），通过 `--studio` 使用现有绑定；凭据留在容器内，记录中不包含令牌、密钥或 agent ID。

可随仓库查看的[脱敏验证摘要](evidence/model-tones-2026-09-08.json)包含关键出站参数、完成状态及工具流程结果；原始 `.probe/` 文件仅本地保存。
