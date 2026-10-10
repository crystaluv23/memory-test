# Codex 记忆实验控制端

最新完整seed比较已封存：类型目录、brief/detail、source-backed抽象索引、两路brief加载、重要性自评、反思新经验与显式失效均已实际运行。七机制筛选中完整seed6/6、off3/6、关闭反思5/6；完整seed与最近时间排序确认均3/3，成本差0.13%，保留完整seed。两变体各1个连接失败记零，原准备失败批次48missing保留。详见 [完整seed结果与边界](docs/SEED_VARIANT_RESULTS.md)、[可用seed配置](configs/usable_seed_memory_v1.json)、[示例记忆包](runs/seed-variants-20261008/seed-memory-pack/pack.json)。这是有限合成机制结论，真实Recommand接入/重放后续；此前flat配对结果保留在 [历史报告](docs/PAIRED_RETENTION_RESULTS.md)，它没有比较完整seed。

目标是固定一个低摩擦、能识别记忆收益与伤害的 meta-meta 外层，再在其中迭代 Evaluator 和 Memory Proposer。完整讨论已读取，并保存在控制端 [来源副本](docs/sources/agent-memory-discussion-20261008.pdf)。逐步实施见 [计划](docs/META_META_PLAN.md)，真实完成范围与剩余关卡见 [验收状态](docs/META_META_ACCEPTANCE.md)。

**当前实施中，K 尚未冻结，尚无正式记忆收益结果。** 已有声明式 M/e、不可变证据与卡片、共享 runtime、六类 interactive 场景、冻结/恢复、配对判断和受控交替 epoch。L2 生成与 Recommandation 历史采集可做离线合同验证；正式独立 Codex 的完整学习链、H2、辨别力和独立复核尚未通过。

仓库是控制端，不是受测工作区。实验遵守 [AGENTS.md](AGENTS.md) 与 [协议](docs/PROTOCOL.md)：先写合同/配置，再实现，再验证；不修改 Recommandation 或全局 Codex 记忆，不默认发模型请求。无需 Python 第三方包，发布验收使用 Python 3.12（系统 Python 3.9 的 SQLite 只读封存兼容检查未通过）；实际工作区隔离依赖 macOS sandbox-exec。

## 本机 Codex 飞轮入口

`memorylab.lightweight_codex` 使用当前 Codex CLI 登录，为每角色启动独立 ephemeral 会话；生产服务执行公开工具请求并独立评分，无外部 API 客户端。`prepare` 只冻结，`canary` 与 `execute` 才调用真实模型。每个新 run 都核对 fresh identity、显式关闭记忆与工具发现；完整 provider 输入仍未可见，因此结果仅限观察模式。失败、未执行与未知用量保留，明确 solver 传输失败计零并继续其他独立单位。

完整重跑和旧快照审计步骤见 [轻量结果](docs/LIGHTWEIGHT_RESULTS.md)。机制配置与 native 默认一致，旧 run 必须用各自源码快照读取。

## 可运行的离线入口

此前的真实 subagent 路线保留供追溯，另见 [控制接口](docs/COLLABORATION_CONTRACT.md)：`python -m memorylab.collaboration --run <新目录> prepare` 冻结，`next` 返回待派发真实角色；当前 Codex controller 先登记 dispatch，以 fork_turns=none 启动角色，绑定实际 handle，核对原生 ready，登记 start 后放行并保存实际工具返回。角色随后只执行自己的固定 IPC client，从已登记的服务获取公开包、读取和提交；控制端运行独立评分。它不用外部 API，但有真实模型推理；不会伪造 CLI/RPC 用量，也不把未知 token/cost 记零。代码、经验、研究提案及后继读取都复用生产服务；全流程资格与收益仍须单独验收。

`python3 -m memorylab.rpc_materialization_fixture run --role <extractor|consolidation|reflector|evaluator|proposer> --cli <明确 CLI 路径> --output <新目录>` 用真实 fresh Codex 会话、固定本机响应和严格 outputSchema，将原始角色 final 接入现有 canonical/L2/proposal 生产提交。`python3 -m memorylab.rpc_materialization_fixture verify --output <该目录>` 在新进程只读重算披露、原始 RPC、生产 request 与提交结果；不建库、不写 receipt、不启动受测者。失败/pending 保留分母，恢复同一 sealed 输出不重发。该入口无真实模型/认证，usage 模拟，没有正式全图、收益或冻结资格。[输出合同](docs/RPC_OUTPUT_CONTRACT.md)

```sh
python3.12 scripts/verify_offline.py
python3 -m memorylab doctor
python3 -m memorylab kernel --db runs/local.sqlite init
python3 -m memorylab history audit --catalog configs/history_candidates.json --output runs/history-audit/current.json
```

以上不调用模型。doctor 检查本机 CLI 和隔离；history 只读核验真实来源，缺失候选保留在分母。静态来源通过不算 H2 执行通过。

`python3 -m memorylab.app_server_probe --cli <明确 CLI 路径> --output <新目录>` 是实际 Codex 命令边界的无模型诊断。临时可信 app-server 通过明确 profile 执行固定正负探针，核验私有/其他分支目录、控制 socket、网络、写入和环境；保存原字节、版本/配置和退出证明。没有 thread/turn/login 或真实凭据。当前旧双层 native sandbox 路径已被实际检查否定；诊断通过也不能解除 role-canary 的真实认证/预算门槛。[合同](docs/APP_SERVER_PROBE_CONTRACT.md)

`python3 -m memorylab.rpc_role --cli <明确 CLI 路径> --output <新目录>` 创建实际独立 ephemeral Codex thread，但只连接本机固定 Responses fixture。验证内置工具执行、下次实际输入中的工具输出、final/usage 事件及封存原字节重验；发送前持久化整轮/槽位预留、唯一 dispatch、绑定 max_output_tokens，原始和 forwarded 请求分开保存。没有真实认证或模型调用，计数与 usage 为 simulated，不进入正式结果。[传输合同](docs/RPC_ROLE_CONTRACT.md)、[预算合同](docs/PROVIDER_BUDGET_CONTRACT.md)

`python3 -m memorylab.rpc_disclosure_fixture run --role <extractor|consolidation|reflector|solver|evaluator|proposer> --cli <明确 CLI 路径> --output <新目录>` 使用现有 RoleInputs、来源和记忆服务做实际动态工具诊断；控制端的固定合成来源与原始记录留在新目录。`python3 -m memorylab.rpc_disclosure_fixture verify --output <该目录>` 在新进程只读重算封存请求、回复、额度和下次输入，无 Codex 或模型调用。它没有正式接入/晋升资格。[披露合同](docs/RPC_DISCLOSURE_CONTRACT.md)

V0 的冻结与执行分开：`kernel prepare-v0` 生成内容寻址、自包含的离线包；`offline-round --bundle <包> --candidates <显式候选文件> --output <结果目录>` 用同一个入口运行、继续 pending、恢复 interrupted、精确缓存并报告。候选文件必须明示预写代码和 extraction fixture，运行类别固定为 infrastructure_acceptance；不会伪装成模型解题。可复核例子见 `tests/test_offline.py`。当前该入口要求初始 corpus 为空，共享真实 acquisition 比较仍待接入。

L2 有 `kernel derive-input`、`derive-admit` 与 `rebuild --derived-refs`，只接受受控 fixture 的严格输出。普通 solver 只获得自动 brief 和 open_memory(id)，不能读原始库、评分端或别的臂。反思和抽象保持支持链，canonical 卡片不变。

`kernel schedule-plan` 编译完整阶段依赖与最大预算，明确选择 shared acquisition 或独立学习；可独立指定 meta memory、两个研究 corpus 和待 shadow 的机制。会列出 extractor/L2/research/oracle/shadow，不发模型请求。超出整轮护栏的图保留完整分母，并拒绝把分批当作新额度。冻结包包含同版本 scheduler 与配置，另附 prospective 图；`offline-round` 目前仍执行原有 scripted slots，不是已接通全图的正式执行器。

`graph-round` 是完整拓扑图的诊断入口，任务使用显式 Python fixture，角色可显式选择固定本机响应的原生 Codex RPC；原库只移植选定公开 snapshot 和 spec lineage，发布独立实验库后，自动处理任务、提取、派生维护、评分、报告、研究提案与四条 shadow 分支。封存后恢复不重发，未知进程停止为 needs_attention，已提交证据损坏拒绝报告；完整任务分母保留。用法为：

```sh
python3 -m memorylab graph-round prepare --request request.json --output runs/my-round
python3 -m memorylab graph-round run --run runs/my-round --timestamp 2026-10-08T10:00:00+08:00
python3 -m memorylab graph-round report --run runs/my-round
```

request 的字段、冻结配置和精确程序映射见 [合同](docs/GRAPH_RUN_CONTRACT.md)，可复核实例见 tests/test_graph_run.py。一次 run 推进一个已冻结 batch；恢复使用同一个目录，--limit 仅限制本次调度。选择 native 角色 descriptor 时会启动独立 Codex 会话，但仅使用本机固定响应，不调用真实模型。它不能证明真实学习收益、研究质量或 F1/F2 操作摩擦通过。

`role-canary prepare --cli <明确 CLI 路径> --model <明确模型> --output <目录>` 准备一个自包含诊断包，不调用模型。单 solver 使用独立临时 workspace、socket、原始 JSONL/usage、独立 grade，完整保存失败分母；旧源码可由包内 bootstrap 加 `-B` 重放。实际 `execute --approve-model-calls-for <packet hash>` 仅在用户确切授权后使用，消耗账号用量；诊断不作收益结果。模型 token 的硬界/真实角色全链仍未验收，不能把该单包称为 K0 已冻结。

`RoleAdmission` 接通已登记角色的原始 transport、渐进来源 audit 和 canonical/L2/typed proposal 的原子提交，逐槽保留拒绝、未知和 pending。接口不会启动模型，也不开放 formal proof。原 60ae3a... 诊断包因探针与实际命令写权限不一致已撤销，见 `runs/role-canary-20261008/revocation.json`；修订命令的外层 write isolation 已统一，必须重新准备审查后才可使用。

## 证据与后续依赖

新合同及参数集中在 docs/*_CONTRACT.md 与 configs/*_v0.json；控制端保存 source/card/usage、原始响应、独立 grade、payoff 和 commit receipt。中断、失败、未知成本和未执行槽始终留存。受控 epoch 的晋升标为 simulated，不能当作研究已经取得收益。

下一步依次补齐：真实角色认证/隔离与全阶段模型预算 → 固定 extractor/L2/research 调用、独立质量与正式 proof → 两种模式的真实学习 → Recommandation H2 → live 正负控制、实际 pressure、操作摩擦和独立复核。全部关卡有证据后才冻结 K0。

历史 v1 runner 与其原记录保留供追溯；v1 的 reference adapter 和字节预算不满足 V0 完整合同，不能用它绕过 V0 正式 admission。configs/meta_meta_plan.json 是规划配置，不能传给 v1 execute。任何正式模型调用都需先生成具体可审查的冻结计划。

## GitHub 源码发布

上传版本包含 Codex 控制端源码、配置、测试、脚本和文档。`runs/` 原始实验记录与快照仅保留在本机，不随 Git 上传；文档中的本地记录链接需要完整本机副本，结果摘要不能替代正式结果所要求的原始事件和独立评分。克隆后可直接运行 `python3.12 scripts/verify_offline.py`，生成新的本地离线验证记录；不会启动正式模型测试。
