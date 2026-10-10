# 本机 Codex 记忆飞轮结果与可用机制

后续已经补做 [两轮合成有记忆与无记忆配对试验](PAIRED_RETENTION_RESULTS.md)，在项目约定保留上观察到差异，仍选择同一最小机制。下文保留此前历史衍生编码任务的结论，不用新的合成结果改写旧批次。

2026 年 10 月 8 日的小规模运行已经连接任务执行、独立评分、经验提取、Memory Proposer、Evaluator 挑战和下一轮消费。两次机制提案均选择保持原配置，Evaluator 提出了一次规则变更挑战。最后独立续批五个任务全部通过，原始会话审计与新隔离工作区评分复算一致。

可以交付的工程默认是来源可追溯的扁平记忆卡。当前实验没有证明记忆提升任务表现，也没有证明复杂机制优于这个默认；满分任务和没有关闭记忆的对照限制了收益判断。正式晋升与 K 冻结保持关闭。

## 可用记忆机制

[usable_memory_v0.json](../configs/usable_memory_v0.json) 是 registry 可解析的 memory spec，其 config 与实际运行的 initial-memory 完全相同。独立 id 便于使用，原版本和来源 ref 保留在 [机器结果](../runs/lightweight-codex-20261008/result.json)。native 默认初始化也采用同一配方。

| 部分 | 配方与行为 |
|---|---|
| 保存 | 扁平 canonical 经验卡；保留实际上下文、适用条件、例外、来源引用和版本 |
| 检索 | 词法相关度检索；任务、proposer、evaluator 各用自己的 namespace |
| 加载 | 不自动载入全局摘要；每次最多四条局部短摘要，仍受整体读取额度约束 |
| 详情 | 通过 open_memory 按需展开；来源引用可解析，不把引用视为语义真实性证明 |
| 失效 | 显式 supersession 保留旧版本；任务的当前公开合同决定旧经验是否适用 |
| 维护 | 不做树组织、自动抽象合并、反思或信用学习 |

最后一批从空 task corpus 学习；后继实际收到 1、2、3 条摘要，目标与反例使用同一学习截止点和三张卡。五个 solver 的 memory_uses 都为空，详情未展开。因此记录只能证明记忆已交付，不能把成功归因于使用记忆。提取卡的语义质量仍需独立审核。

## 真实迭代及完整分母

所有正式执行任务使用当前 CLI 的既有登录和 gpt-6.1-sol、low effort。每角色独立 ephemeral Codex thread，控制端不调用外部 API 客户端、不复制凭据。只把冻结公开包与其实际服务回复送入受测会话；任务代码通过受限服务执行，评分器在独立隔离进程运行。旧污染 subagent 记录没有作为本轮初始记忆。

| 冻结批次 | seed | 计划任务 | 成功 | 传输终止 | 未执行 | 研究结果 |
|---|---:|---:|---:|---:|---:|---|
| attempt 005 | 801 | 20 | 10 | 1 | 9 | proposer 首次 noop；evaluator 将 rule_change 从 none 改为 registered_revision |
| attempt 009 | 802 | 10 | 4 | 1 | 5 | 新挑战四任务成功；反例发生连接失败，剩余研究与任务未执行 |
| attempt 010 | 803 | 5 | 5 | 0 | 0 | proposer 消费四成功及一传输失败的公开汇总，再次 noop；五任务验证完成 |

这些是不同 seed、源码与传输配置的独立批次。35 个预留任务中 19 成功、2 传输终止、14 未执行是执行库存，不是合并效果估计；失败计零、missing 留在分母，不报告 19/19 成功率。续批仅导入研究者自身的来源与卡片，保留 origin hash/映射；solver 不导入旧批次代码或任务臂记忆。

attempt 004 是校准，计划 20 任务、实际 1 成功、19 未执行。attempt 001 至 003、006 至 008 是资格诊断失败，没有执行任务；各自计划、失败、canary 原字节、退出证明与未执行分母均保留在机器结果。006/007 在模型 turn 前退出；001/002/003/008 已启动 turn 而没有成功完成。诊断不计为记忆收益。

## 核验及实际负担

- 最后一批十个完成角色的 raw stdin/stdout、fresh thread identity、初始公开包、模型请求、生产提交与逐条服务交付确认均通过 [只读传输审计](../runs/lightweight-codex-20261008/attempt-010/independent-transport-audit.json)。工具事件、跨角色或跨 turn 事件会被拒绝。
- 三个执行批次共 19 个完成任务保存了代码快照，并在新的隔离工作区复算独立评分：[首次批次](../runs/lightweight-codex-20261008/attempt-005/independent-regrade.json)、[挑战续批](../runs/lightweight-codex-20261008/attempt-009/independent-regrade.json)、[最后续批](../runs/lightweight-codex-20261008/attempt-010/independent-regrade.json)。结果逐组一致，复算没有新模型调用。
- [62 项相关合同检查](../runs/lightweight-codex-20261008/resilient-validation.json) 通过，覆盖真实交付身份、错误通知、失败分母与依赖跳过、原服务和 corpus 合同。该结果绑定最后运行时的源码，不冒充全库正式验收。

三个执行批次客户端已知累计 1,844,888 total tokens；包括校准与已知 canary 为 1,911,223。total 含重复上下文与缓存输入，不重复加缓存计数。005 的失败会话用量未知，009 的失败会话仅有部分用量，故这些是已知量汇总，费用未知。最后一批十角色耗时之和约 452 秒，客户端 526,320 tokens，canary 另计 6,291。

最后批次的 500,000 token guard 在下一角色启动前检查；最后角色前为 479,579，完成后超过软停止线，之后没有新模型调用。这不是 provider 硬预算。经验提取会话本身消耗显著；下一次规模扩大前，应优先减少重复服务上下文，重新冻结成本假设。

本机观察可核验 memories 显式关闭、instructionSources 为空、无父会话与工具事件。完整 provider 输入没有披露，因此不宣称正式全上下文隔离。任务是历史衍生小项目，真实历史 H2 与真实项目重放没有在本轮执行。

## 运行与复核

新运行先冻结；canary 和 execute 会消耗真实账号用量。以下为操作入口，本交付没有自动启动下一批。

```sh
ML_PY=/Users/l/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
ML_RUN=runs/my-new-lightweight-run
"$ML_PY" -m memorylab.lightweight_codex --run "$ML_RUN" prepare
"$ML_PY" -m memorylab.lightweight_codex --run "$ML_RUN" canary
"$ML_PY" -m memorylab.lightweight_codex --run "$ML_RUN" execute
```

默认新 run 完整预留两次 proposer 与一次 evaluator，共四组五任务；最多 64 会话、3600 秒、1,800,000 客户端观察 tokens，不做自动重试。明确 solver 传输失败须取得实际进程退出证据，计零、跳过对应提取，并继续其他独立任务；未知/合同错误或研究角色失败停止整批。该分支已有反例合同检查，最后批次没有自然触发新失败，因此不额外宣称它已经过真实断网验证。prepare 本身不调用模型。

旧运行的源码、配置和文档快照保持原样；当前仓库已经更新，复核必须使用其冻结版本：

```sh
"$ML_PY" scripts/audit_lightweight_codex.py --run runs/lightweight-codex-20261008/attempt-010 --snapshot
"$ML_PY" scripts/regrade_lightweight_snapshots.py --run runs/lightweight-codex-20261008/attempt-010
```

选择最小机制的依据是它实际可运行，且两次研究提案均没有支持改变配置的证据。这是工程默认选择；是否值得在真实项目保留记忆，需要预先冻结 memory off 配对对照与独立项目任务才能回答。
