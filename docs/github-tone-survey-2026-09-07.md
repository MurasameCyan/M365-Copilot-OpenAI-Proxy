**本轮 GitHub 检索没有找到除 Issue #3 两个候选以外、可据现有证据确认为新一代 M365 可用模型的 tone。** 找到了几个本项目默认列表没有收录的旧别名或候选值；它们没有在本账号上完成本轮验证，因此没有随 Astra 一并加入默认列表。

检索时间：2026-09-07。使用 GitHub Code Search 与 Issues Search 查询 `Gpt_6_Astra`、`Gpt_6_Reasoning`、`Gpt_5_7`，并结合 `tone`、`substrate.office.com`、`m365` 限定上下文。进一步读取了 6 个 M365 相关仓库的 23 个源码或文档文件，按搜索结果的 blob SHA 固定版本。Code Search 会把不同标点形式的名称一起命中，普通 OpenAI 模型目录和 GitHub Copilot 模型目录不能视为 M365 tone 证据。

| 发现的值 | 来源 | 可以确认的范围 |
| --- | --- | --- |
| `Gpt_6_Astra`、`Gpt_6_Reasoning` | [本仓库 Issue #3](https://github.com/MurasameCyan/Ciallo-Ms-365-OpenAI-Proxy-Docker/issues/3) | Issue 只列名称；本账号的直接调用结果及 Studio 日志区别见[实测报告](gpt6-tone-verification-2026-09-07.md) |
| `Gpt_5_2_Auto` | [HEXUXIU 静态 tone 列表](https://github.com/HEXUXIU/M365-Copilot2API/blob/45130711ef396ff1845f7d2a7f65aa7b920c9749/internal/web/codex_catalog.go#L87) | 名称确实列在代码中；该处没有当前账号可用性证据，也不是 GPT-6 的新型号 |
| `Gpt_5_2_Quick`、`Gpt_5_3_Quick`、`Gpt_5_4_Quick` | [cramt 模型映射](https://github.com/cramt/m365-copilot-proxy/blob/d7c6d8080bf2bb769c1949c2dfbe60bb7ca929c3/packages/core/src/copilot.ts#L35)，mahmoudsallem 的分支也保留了这些映射 | 属于旧版 Quick 命名；仅凭代理映射不能证明现在仍可用或对应独立模型 |
| `Gpt_Quick`、`Gpt_Reasoning` | [uefi2333 静态 tone 列表](https://github.com/uefi2333/m365-native/blob/7a0b09e45954b309215d60bf9d9e3a18f0f120ed/internal/web/codex_catalog.go#L78) | 无版本号的通用旧名称；未发现足以纳入本项目默认列表的新验证 |
| `Claude_Reasoning` | [cramt 探测候选](https://github.com/cramt/m365-copilot-proxy/blob/d7c6d8080bf2bb769c1949c2dfbe60bb7ca929c3/scripts/tone-probe.mjs#L22)、[调查文档](https://github.com/cramt/m365-copilot-proxy/blob/d7c6d8080bf2bb769c1949c2dfbe60bb7ca929c3/docs/m365-copilot-api.md#L200) | 脚本标记为 speculative，文档也不推荐使用；其底模判断依赖自述，不能据此认定新的 Claude 型号 |
| `Claude_Haiku`、`Claude_3_7_Sonnet` | [cramt 调查文档](https://github.com/cramt/m365-copilot-proxy/blob/d7c6d8080bf2bb769c1949c2dfbe60bb7ca929c3/docs/m365-copilot-api.md#L200) | 原作者标记为测试被拒，不能把字符串出现当作支持 |

核对的仓库还包括 [OmniRoute 的 M365 帧构造](https://github.com/diegosouzapw/OmniRoute/blob/d6f315018af6ed59ff0df253f857ca17abad4974/open-sse/executors/copilot-m365-frames.ts)、[M365Bridge 模型映射](https://github.com/KilimcininKorOglu/M365Bridge/blob/6464b99ec1ddf32b66c98730d88e4dd656ae6090/pkg/models/models.go) 和 [mahmoudsallem 分支](https://github.com/mahmoudsallem/m365-copilot-proxy-claude/blob/6377d04ba04d10a617c1214c3b9b83fd24dcd247/packages/core/src/copilot.ts)。这批文件没有提供更新的 GPT-6 M365 tone 实测证据。

一个可复用的调查细节：HEXUXIU 动态提取 tone 的正则只接受 `Gpt_<主版本>_<次版本>_<名称>`，会漏掉 `Gpt_6_Astra` 这种少一段数字的名称。因此不能用它的抓取结果缺少 Astra 来证明 Astra 不存在。本项目已有直接指定 tone 的验证脚本，本轮没有复制第三方实现。

此结论仅覆盖本次检索和已读取文件，不代表 GitHub 上不存在其他未被索引、未公开或针对不同租户的 tone。后续按用户要求，将 `Gpt_6_Astra` 与 `Gpt_6_Reasoning` 一并加入默认列表，后者附有 Studio 路径限制；其他候选保留为待验证线索。默认目录不等于当前账号的无条件可用清单。

原始搜索响应、固定版本源码和逐文件提取结果保存在本地忽略目录 `.probe/issue3-gpt6-20260907/github-scan/source-results.json`（.probe/issue3-gpt6-20260907/github-scan，仅本地保存）。

可随仓库查看的[脱敏验证摘要](evidence/model-tones-2026-09-08.json)包含关键出站参数、完成状态及工具流程结果；原始 `.probe/` 文件仅本地保存。
