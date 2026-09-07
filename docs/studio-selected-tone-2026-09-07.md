**Studio 已在代码中改为发送客户端所选模型解析出的 tone。三个 API 的工具首轮、结果续轮、纠正重试和进入 Studio 的回退均遵循同一选择。** 固定 `STUDIO_TONE="Magic"` 常量及覆盖逻辑已移除。

本次进一步验证了真实工具流程：模型先请求 `Read`，测试程序核对工具名称和精确路径后实际读取容器内的临时 JSON 配置，再把文件内容作为工具结果交回。文件含随机 `change_id`，用于检查最终回答是否引用了真实读取值。

| Tone | 真实 API 形态 | 工具流程及参数 | 最终回答格式 |
| --- | --- | --- | --- |
| `Magic` | Chat Completions，非流式 | 通过 | JSON 正确 |
| `Chat` | Messages，非流式 | 通过 | JSON 正确 |
| `Reasoning` | Responses，非流式 | 通过 | JSON 正确 |
| `Gpt_5_6_Chat` | Chat Completions，流式 | 通过 | JSON 正确 |
| `Gpt_6_Astra` | Responses，流式 | 通过 | JSON 正确 |
| `Gpt_6_Reasoning` | Messages，流式 | 通过 | JSON 正确 |
| `Claude_Sonnet` | Chat Completions，非流式 | 通过，返回数据与实际文件一致 | 两次均附带介绍语和 JSON 代码块，严格 JSON 格式未通过 |

各项都实际完成首轮和续轮，出站 tone 一致、两轮都附带同一已绑定 Studio agent、首轮新建而续轮复用同一上游会话，完成帧均为 `Completed / Success`。最终阶段均为 `studio`，未通过普通路由代替 Studio 获得通过结果。响应中非 user 条目的 agent 标识也在两轮各核对到一次匹配。

因此验收须区分：**7/7 工具流程及 tone 保持验证通过；6/7 严格 JSON 输出格式通过。** Claude Sonnet 的复测保存了预期值与解析后实际值的 SHA-256，两者一致；额外介绍语仍原样保留在记录里，不能把整批严格格式测试宣称为全绿。这不涉及响应格式能力的新增实现。

实现同时处理了两个相关问题：

- Studio 使用版本化的 agent＋tone 会话命名空间，隔离旧版本以 Magic 建立的线程。上下文连续时保留增量发送；客户端提供的历史不连续或 A→B→A 切换时，替换旧会话对象，避免沿用缺少中间历史的线程，也不原地修改在途请求持有的锁和会话。持久化仅增加不含明文历史的上下文标记。
- 修复 Chat/Messages 在 `tool_choice=auto` 的正常工具结果续轮后追加“没有调用工具”说明的问题。该说明曾污染正确 JSON；required/强制工具、空响应和参数拒绝的原有诊断继续保留。Responses 仍先验证 `previous_response_id` 的归属、防重放与消费锁，新 Studio 线程仅恢复已有的已验证私有上下文；非法输入保持 HTTP 400。

实际测试方法与部署范围：候选完整源码通过 SSH 标准输入送入现有目标容器的临时目录，创建独立 FastAPI/ASGI 实例；API 转换和 SSE 解析走候选代码，M365 调用使用真实网络。加密账号文件先复制到容器内临时快照后解密，使用现有 agent，不创建或发布新 agent。测试实例、临时账号文件和配置在 SessionStore 收尾刷新后清理，记录均确认清理成功。**隔离测试不会更新正在运行的服务；使用包含这些改动的镜像时，仍需拉取镜像并重建容器。**

后续 Router 限制复核（2026-09-08）：`Gpt_6_Reasoning` 的普通路由分类先返回 `InternalError`，回退 Studio 后首轮工具调用成功；提交真实工具结果后，当前显式 Router 再次走普通直连并失败。首轮和续轮 HTTP 都可能是 200，不能只据 HTTP 状态判成功。该结果保留为使用限制，本次没有修改 Router 的续轮回退策略，详见 [README](../README.md#astra-与-reasoning-的区别及使用限制)。

这是跨协议和 tone 的代表性矩阵，不是每个 tone 与全部协议组合的全排列，也不承诺长期可用性。它验证代理发送的 tone 与工具流程，仍不把 tone 名称、模型自述或 agent 版本号作为实际底模身份证明。

本地证据：

- `.probe/issue3-gpt6-20260907/studio-tools-magic-control.jsonl`（初始 Magic 工具测试，仅本地保存）：定位到正常最终 JSON 被代理追加无工具提示；该失败记录保留。
- `.probe/issue3-gpt6-20260907/studio-tools-follow-selected.jsonl`（七个 tone 的主要矩阵，仅本地保存）：14 次实际请求；严格格式 6/7。
- `.probe/issue3-gpt6-20260907/studio-tools-final-check.jsonl`（最终源码复核，仅本地保存）：恢复 Responses 的 400 语义后，复测其两种形态及 Claude；严格格式 2/3，工具流程 3/3。
- `.probe/studio_follow_tone_live.py`（验证脚本，仅本地保存）：保存候选源码哈希、实际出站参数、完成帧、字段比对与清理结果；输出统一脱敏。

最终检查：`node --check get_token.user.js`、修改模块编译、`git diff --check` 通过；全量 `python -m pytest tests/ -q --no-header` 为 **1907 passed、3 skipped**。Studio 跟随 tone、会话切换/恢复、并发替换、工具续轮、required/forced 诊断和参数错误转换均有回归覆盖；独立代码审查未发现未解决的重要问题。

可随仓库查看的[脱敏验证摘要](evidence/model-tones-2026-09-08.json)包含关键出站参数、完成状态及工具流程结果；原始 `.probe/` 文件仅本地保存。
