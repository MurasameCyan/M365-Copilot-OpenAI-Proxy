# CC / Codex 工具调用修复与客户端验证（2026-09-08）

CC 中显示 `Write` JSON 却没有创建文件，本次已定位为代理的只读误判。真实 CC 与 Codex 流程还暴露了工具续轮的三处处理问题；修复后，两种客户端均完成了真实文件创建、结果回传和最终回答。

## 故障证据与修复

1. CC 把 `CLAUDE.md`、记忆等上下文包装成用户消息中的 `<system-reminder>`。其中“不要修改 `user.name` / `user.email`”只限制 Git 身份配置，旧版代理却对所有用户历史做子串匹配，将整个创建 HTML 任务标成只读。5 条历史失败回复均包含合法 `Write` 和完整 HTML，`run_permission=full`，但调用被代理的 `read_only_guard` 删除。
2. CC 也会在 `tool_result` 后追加 `role=system` 的 token 预算信息。旧版 Messages 入口只看数组最后一项，误将工具续轮当成首轮规划。Studio 正常完成后的文本回答因此触发 Router/普通路径回退，实测出现重复 `Write`。
3. 成功执行工具之后，正常“文件已创建”的确认语仍会命中假文件声明检测，被代理强制重试为新的 Write。现在三个 API 的流式、非流式入口都允许 auto 工具续轮正常结束；首轮未执行工具时的纠错、required/named 约束和显式返回的工具调用保持原有行为。
4. Codex 会重发完整的 `function_call` 和 `function_call_output` 历史。在固定会话 Header、未提供 `previous_response_id` 时，Studio 增量转换先裁掉旧调用，随后又要求它必须存在，误报 HTTP 400。现在只有经过入口完整历史校验的内部增量转换允许省略已裁剪的配对；外部缺失调用、错误 `call_id`、重复结果和非法参数仍被拒绝。两类记录各自的 `id` 不用于替代 `call_id` 配对。

现在，三个 API 从最近的实际用户请求判断任务只读意图，工具结果与上下文提醒不充当新请求；上游仍收到完整原始上下文。明确的只读用户请求、配置权限上限、CC 完整及简短 Plan mode 提醒继续生效；当前系统 Plan 状态不会被历史退出提醒覆盖。Responses 在校验 `previous_response_id` 后，也为“工具结果后附加上下文提醒”的续轮保留原有只读限制。

Messages 入口按最后一个非 system 消息识别工具续轮，与已有翻译逻辑一致。正常最终回答继续使用原 Studio 会话，不再重新规划或追加未调用工具提示。

## 真实 CC 验证

- 客户端：Claude Code **2.1.261**，真实 `claude -p`，保留 `CLAUDE.md` 注入与默认工具列表，使用默认/auto 工具选择。
- 接口：流式 `/v1/messages`，M365 企业账户，现有有效 Studio agent，明确指定 `Gpt_6_Astra`。
- 任务：用 SVG 实现鹈鹕骑自行车的动画，实际写入新的临时 HTML 文件；收到成功工具结果后给出最终确认。
- 结果：**2 轮请求完成**。客户端执行一次 `Write`，文件为 **2327 字节**；实际文件 SHA-256 与工具参数中的内容一致。工具结果 `is_error=false`，最终回答正常，CLI 退出码为 0，没有额外纠错重试。
- 两轮出站 tone 都为 `Gpt_6_Astra`，都附带 Studio agent，复用同一个上游会话，均返回 `Completed / Success`，没有 Router/普通路径回退。

文件 SHA-256：`c8d5b92e091f3d19dc5aadca5394ca22a7ed2591cfef78491e0ef1dab3a9c5b9`。

历史失败回复也经过离线 API 重放：修复前 5/5 丢失工具调用；修复后 5/5 返回结构化 `Write` 和 `stop_reason=tool_use`，全部参数及 HTML 内容保持一致。重放不产生新的上游请求。

## 真实 Codex 验证

Codex CLI **0.153.4** 通过自定义 Responses provider、流式 `/v1/responses` 和同一 M365 账户请求 `Gpt_6_Astra`。关闭 `web_search` 后，客户端实际声明 4 个顶层 function 和一个包含 5 个 function 的 namespace；请求的 tools 与 input 原样转发。

模型请求内建 `exec_command` 创建全新的临时 HTML；测试只放行精确的新文件命令。真实 Codex 执行器退出码为 0，文件 **129 字节**，内容与预期一致。第二轮携带真实 `function_call_output` 和完整历史，没有 `previous_response_id`，通过固定 `X-M365-Session-Id` 复用 Studio 会话，正常返回最终确认。两轮均为 `Completed / Success`，没有回退或纠错重试，CLI 退出码为 0。

文件 SHA-256：`d9ece82081d2ce1f70f4d2abd032756d78e5cf358df5695441ec5a477bab5d57`。

测试使用独立临时 Codex 配置：`web_search="disabled"`、`sandbox_mode="workspace-write"`、`approval_policy="never"` 和 `windows.sandbox="unelevated"`。最初空白 Windows 配置会把 workspace-write 降为只读；本地 mock 先验证执行器写入权限，再运行 M365 实测，没有更改用户的持久权限配置。

## 与参考项目的差异

核对 [HEXUXIU/M365-Copilot2API 的固定提交](https://github.com/HEXUXIU/M365-Copilot2API/commit/45130711ef396ff1845f7d2a7f65aa7b920c9749)：其工具校验检查声明、tool choice 和 schema，没有本项目这段全文只读意图过滤；工具的只读分类用于并发控制，Write 仍可串行返回客户端。因此，本次失败不需要通过更换模型或移植另一套 JSON 解析器处理。

## 验证范围与本地记录

候选源码通过 SSH 标准输入送入目标容器的临时目录，运行隔离 ASGI 实例；M365 调用使用真实网络。账号存储先在容器内复制为临时快照再读取，使用现有 Studio agent。真实客户端在本地创建全新的测试文件。两种客户端使用相同的源码归档（SHA-256：`9007513e0f9723410653a3e281787a41fb20ce170549e9931052f615c80b3ab7`），106 个源码模块逐一核对一致。测试转发层附加固定会话 Header，以覆盖客户端完整工具历史和服务端会话复用。容器临时目录已清理；未修改运行服务的账号配置或服务器宿主机文件。

本地详细记录保存在 `.probe/cc-write-20260908/`，不随仓库发布：

- `cc-request.json`、`cc-plan-request.json`：loopback 捕获的实际 CC 请求，仅用于分析上下文与 Plan 提醒。
- `saved-write-failures.json`、`replay-before.json`、`replay-after.json`：五条历史失败的输出及重放结果。
- `runs/8f3a8df8222a41b0ac85eca5e84ad805/`：第一次真实写入成功后，发现续轮误回退的记录。
- `runs/e0fecc92f4664da68ecfff64eff449b7/`：定位成功确认语被误判为假文件声明的失败记录。
- `runs/49575fe7074443ab82e03a7886e5b672/`：最终源码的 CC 完整成功流程、源码哈希、API 帧、CLI 结果与测试文件。
- `codex-runs/fb95e38d843b4e0faa42f4bf17fb3e02/`：定位完整历史的 Studio 增量转换误报 400。
- `codex-runs/4128b57991f640eca244cb3fe0a7d189/`：最终源码的 Codex 完整成功流程。

回归覆盖实际上下文、三种 API 的流式/非流式只读判断、工具结果续轮、Plan 状态、Messages 尾部 system 元数据、正常文件确认语，以及 Codex 完整历史与非法配对对照。独立审查已复核修复。可公开的[脱敏证据](evidence/client-tool-calls-2026-09-08.json)保存了客户端版本、文件摘要、源码摘要、出站 tone 与完成状态。此验证证明具体客户端工具流程成功，不据 tone 名称推断实际底模身份，也不将 Studio 实测推广为普通直连的工具能力结论。
