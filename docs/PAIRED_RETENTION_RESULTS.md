# 两轮 Codex 配对记忆试验结果

本文是此前flat机制的历史配对结果。用户完整seed现在已有独立实现与比较，见 [完整seed结果](SEED_VARIANT_RESULTS.md)；本文不构成flat优于完整seed的证据。

来源可追溯的扁平记忆机制在本次合成任务中保留了先前的项目约定，并正确服从新的显式规则。两轮各五个任务对，有记忆均为 5/5，无记忆均为 3/5；四个差异都出现在需要先前约定的重复和迁移任务。无记忆角色诚实回答需要上下文，两臂均没有错误断言。

保留 [usable_memory_v0.json](../configs/usable_memory_v0.json) 作为工程默认。两次合法 Memory Proposer 提案均为 noop，没有机制参数改动；Evaluator 的规则变更挑战已在第二轮实际使用。当前结果支持合成项目约定的跨会话保留，不证明一般编码收益或复杂机制优越。

## 配对任务与结果

共享学习记录有 Aster 和 Boreal 两个项目的不同路由约定。独立 Codex extractor 从这些公开记录生成两张 canonical 卡，包含确切来源版本、项目范围与当前规则优先的条件。每个解题角色使用 fresh ephemeral Codex 会话，不继承学习聊天。有记忆通过生产 runtime 加载摘要和按需展开，无记忆关闭加载及展开。

| 条件 | 两轮有记忆完成 | 两轮无记忆完成 | 信息不足及保护结果 |
|---|---:|---:|---|
| 重复先前 Aster 约定 | 2/2 | 0/2 | 无记忆两次 needs_context |
| 字段迁移并沿用约定 | 2/2 | 0/2 | 无记忆两次 needs_context |
| 当前规则覆盖旧规则 | 2/2 | 2/2 | 两臂均按当前合同执行 |
| 不需要项目记忆的算术 | 2/2 | 2/2 | 没有错误套用记忆 |
| 其他项目的显式当前规则 | 2/2 | 2/2 | 没有混用项目或旧版本 |

20 个解题会话都完成，没有传输终止或缺失。完成能力与诚实性分别记录：needs_context 不算任务完成，也不算错误断言。两臂公开任务、模型和 effort 相同，发起顺序在冻结计划中交错平衡。使用 gpt-6.1-sol、low effort、本机 CLI 既有登录，没有外部 API 客户端或全局记忆修改。

前两个任务有意只在先前学习记录披露项目默认规则，这使本试验适合检查保留能力。它不能回答完整说明已给出的普通编码任务是否同样获益。两轮重复同一组五种合成模板，没有把它们算作十个独立真实项目，也没有计算显著性。先前历史衍生编码任务的满分和连接失败仍见 [原观察报告](LIGHTWEIGHT_RESULTS.md)，不合并为本次收益样本。

## 迭代及结构拒绝

第一轮后，Proposer 认为现有配置已保留约定且保护任务通过，选择 noop。Evaluator 将 rule_change 从 none 改成 registered_revision，第二轮的当前规则阈值与路由标签因此改变；有记忆角色仍遵循新规则。

第二轮后的原 proposer 输出 kind=proposal，生产 Registry 要求 kind=memory，因 role_kind_mismatch 被拒绝。原批次保持 halted，原答、费用、拒绝 receipt 和一个未执行的最终 extractor 保留。缺口来自公开角色 grammar 未明确写出 kind 的值。

补充确切类型示例后，新冻结尾批仅执行资格 canary、最终 proposer 和自己的 extractor，不重跑任何任务。只通过 CorpusStore 移植 proposer 自身研究来源、旧卡版本和 tested memory lineage，原 ledger hash 不变。尾批合法 noop 及新研究卡提交均成功。原失败不改写成成功，最终合成接收以原二十任务独立复算及单独尾批的真实收尾证据共同成立。

## 可使用的机制与示例

机制保持八个字段的最小配方：flat、lexical、全局摘要 0、局部摘要最多 4、显式 supersession；自动合并、反思与信用学习关闭。卡片详情保存实际上下文、适用条件、例外和来源版本。当前公开合同优先于旧经验；没有所需信息时保留 needs_context。

[可移植示例包](../runs/paired-retention-20261008/portable-memory-pack.json) 保存两条合成学习来源、两张实际模型提取的卡及其 refs，不包含任务 gold 或其他臂的输出。示例中的项目规则是合成数据，不应当作真实项目事实导入。原 spec 与 native 默认一致，生产 RuntimeBuilder、RuntimeSession、CanonicalCards 已实际消费此配方。

本轮证明摘要已实际交付并与任务表现差异一致。模型对 memory_uses 的声明保留为自评，不能替代行为或因果证据；显式 supersession 的通用实现有合同检查，本次主要实际检查的是当前规则覆盖，而非卡片自动失效判断。提案没有支持增加树、反思或抽象维护，故保留当前工程默认。

## 核验和负担

[主批次原始会话审计](../runs/paired-retention-20261008/attempt-001/independent-audit.json) 核对 27 个角色的 fresh identity、原始输入/输出、实际 turn 确认、usage 与生产 receipt，并在新 grader 进程复算全部 20 个评分；结构拒绝也在审计输出中。另有 [尾批三角色审计](../runs/paired-retention-20261008/completion-001/independent-audit.json)。当前 [104 项相关合同检查](../runs/paired-retention-20261008/closure-validation.json) 通过，覆盖规范示例、配对隔离、未知记忆声明拒绝、信息不足与错误区分、完整任务要求和已有 runtime/registry/store 反例。全库所有潜在问题不由此被排除。

| 调用类型 | 实际会话数 | 客户端 total tokens |
|---|---:|---:|
| 解题 | 20 | 132,454 |
| 研究提案含原拒绝 | 4 | 32,171 |
| 研究经验提取 | 3 | 24,895 |
| 学习记录提取 | 1 | 7,628 |
| 两个资格 canary | 2 | 12,562 |
| 合计 | 30 | 209,710 |

角色耗时之和约 5 分 28 秒。total tokens 包含重复与缓存输入，缓存没有另行重复相加；所有角色的客户端累计用量可从原字节核验，provider 账单与费用未知。主批次预算 240,000、尾批 40,000，均未触发观察停止线。本轮只用 JSON 任务与少量公开服务回合，不能与此前编码任务直接比较单位成本。

完整 provider 输入仍不可见；本机可观察的 memories 关闭、instructionSources 为空、无父会话和工具事件已经核对。正式 proof、K 冻结、真实历史 H2 和真实项目收益仍未取得。本交付后已停止模型调用。

## 复核和新运行入口

[机器结果](../runs/paired-retention-20261008/result.json) 分别保存原 halted 批次、尾批、任务与研究分母。旧 run 用各自源码快照复核，不重新执行已开始的角色：

```sh
ML_PY=/Users/l/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
"$ML_PY" scripts/audit_paired_codex.py --run runs/paired-retention-20261008/attempt-001
"$ML_PY" scripts/audit_paired_codex.py --run runs/paired-retention-20261008/completion-001
```

新试验可以冻结新目录后执行；execute 会消耗真实账号用量，当前没有自动启动下一批。

```sh
"$ML_PY" -m memorylab.paired_codex --run runs/my-new-paired-study prepare
"$ML_PY" -m memorylab.paired_codex --run runs/my-new-paired-study execute
```

选择扁平机制的明确依据是它完成了本试验要求的保留任务，并通过保护条件；本次没有观察到需要增加机制复杂度的理由。一般项目是否值得使用它，仍需独立真实任务的配对检查。
