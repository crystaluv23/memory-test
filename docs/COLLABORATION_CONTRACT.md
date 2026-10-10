# 真实 Codex subagent 控制接口

用户于 2026-10-08 指定本路线；配置见 configs/collaboration_v0.json。它调用当前聊天的 collaboration 工具，不接第三方 provider、不读取认证文件。模型会话有实际推理和账号用量，当前工具没有公开实际 token/cost/resolved backend，报告保留 unknown/null。它不是零模型调用的基础设施 fixture，也不是已经资格验收的正式实验。

入口为 `python -m memorylab.collaboration --run <新目录> prepare` 与同一目录的 `next`。prepare 保存完整源码/配置/协议快照、环境和初始 M/e；next 只从已提交结果推进，返回下一角色的完整公开 prompt、slot、消息 sequence 和当前 handle。控制端必须先 `dispatch` 登记，再以 fork_turns=none 调用 spawn_agent，立即将实际返回的 agent_id/tool_result `bind` 入库。已有绑定只 wait/followup 同一 handle；dispatch 已登记而 handle 缺失时返回 dispatch_unknown，不重发。角色输出原文通过 receive 提交，controller 服务实际回复随后发送到同一 agent。

实际角色不得读取仓库或直接使用工具，只处理消息包。solver 使用当前 SPEC/初始 engine/公开 sample，并通过 controller 请求 inspect/check、整文件 engine.py 修改、open_memory；评分器、隐藏 input/expected、控制路径和其他臂原始记录不进入 packet。生产 verifier 在已有 OS 沙箱里执行实际提交的代码。instruction-only subagent 权限不冒充受限 OS worker；必须另行核验角色行为与完整上下文，才能关闭整体隔离关卡。

extractor 输入来自真实 task 的公开输入、实际服务、代码差异、final 和独立评分的有限 outcome，不含 hidden cases/expected。渐进 read_evidence 复用 EvidenceReader 的 namespace/cutoff/range/额度合同；未读来源不能提交 citation，部分读取仍不证明未读内容正确。卡片通过 CanonicalCards 原子提交。consolidation/reflector 由 frozen M 配置决定是否启动，调用真实角色；DerivedStage 按 immutable collaboration_capture 验证其输出，不能伪造 scripted 或 RPC 传输身份。任务每个输入先固定 cutoff；target/counterexample 共享学习截止点，target 后的结果不能回流到 counterexample 的经验/credit。

规划中的独立学习分支为 baseline → proposer-0 → candidate-0 → evaluator-0 → challenge-1 → proposer-1 → candidate-1。每个 task 分支运行完整注册链的 learning/interference/target/counterexample，研究角色只收到有限公开摘要及自己 namespace 的经验。每个研究输出再经真实 extractor 进入 own-memory，后继同角色可以读取。Registry 严格校验 parent、单字段变更与来源；合法 accepted 只 materialize 下一实验分支，不晋升冠军。noop/rejected 使用原 spec 继续，但不能计改进或有效学习尝试。这个有限执行验证真实连接；正式交替 epoch、可靠比较、压力、回归和历史验证仍按整体协议验收。

SQLite 保存不可变输入、dispatch intent、绑定、实际消息原文、服务 request/reply、完成 capture、grade、card/proposal 与 receipts。receive 身份精确绑定 actor，消息序号连续；相同身份重复仅返回原 reply，改原文拒绝。副作用前提交原文 intent；controller 中断导致 intent 无 reply 时返回 needs_attention，不猜测 check/grade 已完成，不重新发模型。report 使用 immutable readonly Ledger，逐项重验 hash、capture 与完成工作区；不修改库。未启动、缺失、失败与拒绝保留原计划分母。源码/配置/环境漂移拒绝继续；原快照可作为独立模块入口重放，不篡改历史计划。

系统验收与机制交付仍需：全量回归、所有已知阻塞关闭、实际 role 隔离/用量资格、可靠正负控制、独立重复和明确决策、运行摩擦、真实 H2 及独立复核。当前流程不得把 observed_schedule_complete 当作 kernel_frozen、gain_evidence 或 delivery_ready。

workspace 位于 prepare 登记的私有临时根，必须在 controller/run 和 Project 拒读根之外；actor 内部记录确切位置，check/grade/snapshot/recovery 统一消费。模型不收到路径。正控制须在实际登记 workspace 与实际 grader profile 中通过，同时维持 controller 和 sibling worker 的拒读。旧 attempt-001 因 workspace 位于拒读目录而撤销，原始包/失败反例保留，不能搬目录后沿用旧计划。

消息回复只有生成事实；controller 必须先 followup 登记完整 target/message，再调用实际 followup_task，将原始工具返回通过 ack 确认。wire 原文（或无 wire 的公开服务 reply）是唯一消息，不发内部 event/receipt/grade gold。只有已确认回复进入后续证据读取与 usage。保留 unknown 发送，不自动重发；下一条原文缺上一条交付确认即拒绝。scenario event delivered=false 与独立交付 receipt 区分，确认工具接受不声称模型理解。

绝对 deadline/turn 上限返回 interrupt_required。先 interrupt 登记，再用实际 interrupt_agent 返回 terminate。本次有限 run 明确 halted，保留未完成 actor、原文、全部 schedule 和失败分母；不造 final、空 canonical batch、L2 或 proposal。不确定 interrupt 不能重发；终结只读复核绑定与工作区，禁止同槽重新执行。

本机 IPC 路线：actor packet 仅附自己的 service_client.command；独立角色允许通过固定 client 执行控制服务，不允许其他文件/工具/全局记忆。root 以 fork_turns=none 启动，bind 接受实际返回的 task_name 作为 target handle，原始返回保留，不造 UUID。每计划派发 task_name 带 plan hash，避免跨 run 重用会话。`python -m memorylab.collaboration_ipc --run <目录> --slot <slot>` 启动本机服务，不发模型请求。

client 使用该角色临时 capability，逐序请求传输真实原文，公共回复原字节输出到 stdout。服务生成、发送预留和 client stdout ack 分开保存，绑定原文、回复、binding、sequence；这不是 provider 费用、上下文完整性或 OS 权限资格。agent 的函数工具外层与命令输出上限均指定 40000；实际截断仍须单独核验。失去连接留下 pending，禁止自动重发；非法身份/客户端漂移拒绝服务。模型提交 finish 的真实 raw 经原有生产 verifier/cards/typed research 提交，所有旧恢复和来源边界继续适用。

IPC producer 修订专项 33 项通过，21.776 秒，源码未变；原始日志及登记源码 hash 已重验，见 runs/collaboration-ipc-20261008/attempt-002/focused-attempt-003/result.json。新增错误来源、中间代码、私有字段拒绝、确切 grammar/native final 检查。全部为基础设施 fixture，不计真实推理或收益。attempt-004 保留一真实 solve 成功及四次请求错误，因来源缺失停止推进；下一冻结资格 run 重新检验真实提取和后继消费。主工作区尚未合入 IPC；正式资格与最终交付均未完成。

初始包原文获取修订：通过身份绑定的 packet 服务返回该 actor 的确切公开包，派发只给短 bootstrap 和 packet hash；不暴露 controller/internal/其他 actor。34 项专项通过，22.931 秒，源码不变，日志与登记源码已重验，见 runs/collaboration-ipc-20261008/attempt-003/focused-attempt-001/result.json。此前 IPC 454 项全量通过，874.586 秒，身份 9473902ecf2504ae；该旧全量不覆盖本修订。新全量与真实闭环仍待执行，未交付、未声称收益。

完整初始调用修订专项 35 项通过，22.301 秒，源码未变；日志与登记源码重验，见 runs/collaboration-ipc-20261008/attempt-004/focused-attempt-001/result.json。真实 shell 执行冻结 heredoc 可得确切公开 packet；空输入不连接、不推进状态。attempt-006 因漏 stdin 终结，planned=5/terminated=1/missing=4，另有 1 次无工具诊断推理，均不计收益。下一独立角色从新冻结版本执行，旧失败不删除。

当前 IPC 启动必须先等待 native ready（仅返回 awaiting_controller_binding 与 packet SHA256，无工具），controller bind 实际 handle；start 保存 actual ready 原文及唯一 CONTROLLER_BOUND message，再实际 followup 同一角色，ack-start 保存原生真实空返回。packet 服务在 start intent 前拒绝；未知放行不重发，完成报告查 start/ack binding。该额外推理记为 startup 回合，不冒充零成本。研究与 solver 的实际 auto_load/open 同样进入 usage；solver claimed_use 去重且绑定公开已披露版本，未展开/未披露单独保留，不产生 behavior_link。研究 namespace 的 exact ref 与 brief/detail 共同计量，以便 typed proposal 合法引用；solver id-only 不变。当前 89 项专项通过，整图模型/全量/正式资格/交付仍未完成。
