# 全阶段调度合同（S5）

这是控制端的冻结计划和预算账本，不是已合格的模型执行器。参数先登记 `configs/schedule_v0.json`；角色的 token 上限是待 live 校准的预留假设，不是 CLI 已证明可强制执行的限制。默认不发模型请求。

输入明确选择 `component_comparison` 或 `independent_learning`，不可混算。component 模式每个场景/重复只做一次非参赛 acquisition，逐 episode 固定提取，冻结一份 corpus；各参赛 M 从同一 cutoff rebuild。independent 模式各臂有独立 namespace 和完整学习/干扰链。每个学习 episode 的 grade 留控制端，只有实际公开的事件进入 extractor。每个 solver/oracle/research/shadow 槽代表一个 fresh 会话，不复用历史聊天。

目标和反例从学习结束的同一个 corpus 截止点独立开始；不从前一个目标的结果补充经验。只有 learning/interference 后调度提取。L2 在每个需消费的 corpus 版本上登记 consolidation/reflector；机制关闭该行为时保留 deterministic disabled receipt；启用但实际没有 eligible inputs 时由维护实现提交 bound no-op，不能由调用者随意写 skipped。失败后无合法 corpus 仍保留所有目标槽，并可完成明确 blocked 结果，不能注入理想经验。

Oracle 在独立 namespace 解目标/反例，用预注册的信息视图，不能读 gold/评分代码或参赛臂的记录。报告后才生成研究者可见的有限历史视图。evaluator/proposer 分 namespace 和 parent；meta memory 必须独立指定，不自动等于 task champion。若提名新的 meta memory，分别为两个研究角色登记 current/candidate 同历史 shadow。shadow 保持已注册的 role namespace，但使用分别克隆同一截止研究历史的独立 store_scope 和独立会话，不把 shadow 的提案或维护写回另一臂。晋升仍需 S6 的双目标独立评分。

图中节点只依赖先前登记节点；输入视图用 frozen bindings 和 committed receipts 解析，不从任意路径读别的臂。每个节点有确定 id、角色、namespace、预算、条件与依赖。模型节点有会话数、input/output token、wall time/command 预留；控制节点也有 wall time。单批按顺序与最坏时间装箱，禁止切断一个节点。总计划超过 round_calls/round_tokens 时保留完整图和超额原因；分批不获得新的整轮额度。编译只是无调用的可审查预览，不授予 live admission。

调度 research 时必须分别指定 evaluator/proposer 的既有 canonical corpus 身份，不能拿 task corpus 替代研究记忆。新增有限研究视图先作为该角色公开来源提取，形成冻结研究 corpus，再 rebuild；shadow 的两臂都克隆这一研究 corpus。实际提案经 typed validation 后，仅该角色公开输出进入后置 extractor，供下一轮使用。这两次研究提取也计入预算；后置内容不能回灌同一组 shadow。

账本先登记不可变图。开始节点时精确绑定 input hash，检查依赖成功终结及整轮剩余额度，原子预留。未终结开始记录恢复为 interrupted，未知成本保留全部预留，不自动重发；只能使用原图中另行预声明的新 attempt（V0 不提供自动 retry）。完成以一个事务提交私有 raw/artifacts/usage 和 receipt。所有槽，包括 pending、blocked、disabled、失败和中断，始终在报告中；cache 返回既有 receipt，不算新独立样本。当前 journal 的 token 与会话结算是明确标注的预算 simulation，实际模型费用始终为零；真实 input 来源许可、独立 store 的 materialize、条件 no-op 证明和模型执行 receipt 仍待正式执行器接入。

目前 journal 只允许显式 `scripted_acceptance` 的受限控制端故障注入，不接受自填 live usage 或 grade 来晋升。预览 model maxima 与实际 scripted 零模型费用分开。模型硬预算停止、角色隔离、独立 grade 与 formal proof 接入仍是 live 前置关卡。

失败任务仍执行独立评分并可提取实际公开的失败经验，不能因解题失败就删除学习来源。grade/report 等控制节点依赖 terminal receipt；extractor 依赖有实际 attempt 的终结事件（成功、失败、超时或中断），真正未执行的 blocked 来源不能被代换。rebuild/solver 则依赖合法完成的 corpus/runtime。成本未知会使整轮不具备晋升资格，但不妨碍保存诊断。依赖策略也是冻结图的注册字段。

`StageInputs` 现按 `STAGE_INPUT_CONTRACT.md` 接通实际来源许可、snapshot、投影、独立 shadow store 及 eligible/no-op 核验。`StageJournal.begin_bound` 从已冻结输入推导预留，只有实际 bound_noop 可预留零调用；普通开始接口仍仅用于明确 simulation。所有完成仍标 infrastructure，不接受自填 live 字段。当前组合测试使用控制端 fixture 生产来源/评分/提案，只证明各阶段消费链和拒绝边界；正式 actor、独立质量实验和 proof 并未因此完成。
