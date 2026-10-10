# V0 冻结与原子执行合同（S5）

执行配置先登记 `configs/execution_v0.json`。默认离线，模型/CLI/tokenizer 未配置；prepare、scripted acceptance 不发模型请求。正式调用必须有独立显式授权、完整模型/工具/推理/tokenizer 身份、每角色隔离探针、实际 token 预算可监测/约束的证据；任意缺失阻止 live。现有 v1 的字节预算不是 V0 token 合格证据。

冻结包保存自包含 memorylab 源码和所有注册资源配置（包括 history/derived/schedule/role/canary）、输入 spec 与 lineage、canonical cards/source/usage cutoff 快照、环境身份、所有阶段任务与预先声明重复/seed。内容 hash 是主身份，不依赖 Git。讨论 PDF、凭据、全局记忆、评分答案与其他臂记录不进入 worker。包内容不可覆盖；报告写在包外。后续 cwd 文档/代码修改不改变旧包，可由包内 bootstrap 独立加载和验证。

fingerprint 包含执行模式（scripted/live）、组件/verifier 与实现 hash、M/e、parent、corpus/usage、生成 task 与 public prompt、环境、requested/resolved model、CLI、reasoning、tokenizer、工具、预算、seed/repeat 和阶段。缓存命中只能复用精确 fingerprint 的 committed receipt；命中不算新独立样本。失败也保留缓存；改变预算/模式不能把旧结果当同条件数据。

原始事件先进入 trial staging。一次完成通过同一 SQLite 事务提交 raw events、独立 grade、卡片提案结果/新 projection、payoff 与 receipt；epoch 指针只从 receipt 生成。内部 mutation 可加入显式控制端 transaction，但不自行提交外层事务。任意位置 crash 均不产生一半经验或正式结果。开始一次尝试在独立 start receipt 中预留预算，完成后按实际 usage 结算；started 未完成不是成功，恢复先记 interrupted 与 unknown cost，不重发原调用。新尝试须预声明新 attempt，原失败保留。所有计划槽（pending/失败/超时/缺失）在报告分母中。

正式 budget 预览包括 acquisition、extractor、derived maintenance、solver、research proposal、oracle、shadow、回归与确认；无隐藏免费调用。每次调用前检查剩余 call/token/时间上界，按最大可耗额度预留；每次完成后结算实际值。usage unknown 或实际超过预留均不晋升，unknown 保留全部 token 预留，不能记零；live 中 provider 中断/超时实际 cost unknown 同样。planned full work 超过整轮护栏时保持 pending/inconclusive，不削减分母。离线 scripted 调用计 simulated_invocations，与 live_model_calls 分开。

本阶段离线 executor 只用于故障注入和组合验证：采用显式的 scripted candidate 文件，仍运行 production verifier、完整 socket 接口和 controller 记录。模型 token/压缩、角色隔离、真实使用、预算中止、全链 live 验收仍须真实独立 Codex；该边界进入 result category，不能当效果结果或 K 已冻结。

全阶段规划按 SCHEDULE_CONTRACT 执行。call 上界指 fresh Codex session 启动数，不能冒充 provider request 上界。schedule 资源一并内容冻结。预览的角色 token 数只是显式假设；任何未来 live admission 必须绑定硬停止与真实 usage 证明。旧包中的 legacy solver-only 预算保留原义，新包追加完整 stage preview，不能把后者标为已执行。

原先未参与执行的 `maintenance_calls_per_episode` 移出当前 execution 配置；维护上界只由注册角色、条件槽和整轮限额共同计算。每个 learning episode 的固定 extractor 与各机制的 consolidation/reflector 分别列明，不保留一个值为 1 却与实际调度无关的外层参数。旧冻结包仍保留其原配置与实现。

离线完成 API 禁止 opaque card callback。可提供显式 `extraction_template`：cards 使用与 canonical proposal 相同字段，但以整数 `source_indices` 代替 evidence_refs，完成事务中绑定本 trial 的确切公开来源；完整模板参与幂等 hash。该映射只用于带明确 infrastructure 标记的离线故障注入，不冒充模型提取。正式 staged extractor 与预算隔离接入仍是 S5 未完成项。

离线单入口 `offline-round` 读取冻结包和版本化 candidate/template 文件，自动隔离 probe、运行 inspect/check 与记忆展开、独立验真、提交及报告。candidate 是明确标记的预写 patch，只检验布线；不会把它伪装成受测 Codex。cache 命中不重跑，started 不完成先记 interrupted；input artifact hash 和源码进入每个 trial fingerprint。该入口目前要求初始 corpus 为空，非空共享 acquisition 比较不能借此冒充已实现。
