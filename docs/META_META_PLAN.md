# Meta meta 机制逐步实施与冻结计划

日期：2026-10-08，Asia/Shanghai。状态：实施中，尚未冻结。对应规划配置为 [meta_meta_plan.json](../configs/meta_meta_plan.json)。S1–S6 的控制端合同与离线基础已实现；S7 有只读来源审计与静态恢复包。正式模型全链路、真实 H2 执行、辨别力及独立复核仍未通过。逐关完成情况见 [验收状态](META_META_ACCEPTANCE.md)，局部基础设施通过不代表整个阶段已验收。

目标是先获得一个固定、低摩擦、能够可靠识别记忆收益与伤害的实验外层，再在其中迭代 Evaluator 和 Memory Proposer。最终要得到在声明任务范围内有独立证据支持、成本可接受、能够用于 Recommand 的记忆机制。

讨论来源：[Agent记忆最佳实践](https://chatgpt.com/c/6ac62dd4-cc70-83ee-8b65-de6416ff2ee2)。[完整来源副本](sources/agent-memory-discussion-20261008.pdf)有 242 页；最后定稿在第 213–242 页，context pressure 与历史回放的修正在第 171–212 页。按后文修订采用声明式配置、固定卡片、单层 INDEX、受限接口和 H2 因果链；前文开放任意代码、递归抽象、经验 schema 进化等属于未来版本。来源副本仅供控制端对照，不能进入受测者、正式 proposer 的上下文或经验库。

## 完成和冻结的含义

“快速、小规模”要落实为系统约束：普通一轮只提交一个配置变化，运行和恢复只需一个入口，自动形成完整证据，活跃机制、挑战、调用、时间和 token 都有上界。不能靠每轮人工修环境、搬记忆、改评分或挑成功记录来维持运转。

“可以明确结论”要求实验能测到真实改变，并可靠区分有效、伤害、无关和无法区分的机制。检索到经验、模型说有用、一次成功、scripted 演示都不足以证明收益。证据足够时输出有范围的好/坏结论；证据不足时明确保留不确定，不能强选赢家。

“没有任何问题”作为冻结门槛，具体指：已声明支持范围内，所有必需关卡通过，有完整原始证据，无已知影响正确性、归因、隔离、恢复、预算或运行摩擦的缺陷，无未决职责和隐式参数，并完成独立复核。有限验收不能证明任意未知输入下绝对零缺陷；固定 K 也不能意味着发现缺陷后继续使用错误裁判。发现核心缺陷要停止相关晋升，作废受影响结论，修成新 K 版本，保留原冻结物。

冻结有两道时间上的分界：S8 验收 K 后才正式迭代 Evaluator/Proposer；之后才能证明 bootstrap 的研究收益。冻结前验证的是程序确实允许并检验进化，冻结后要观察实际提出的方案是否带来可确认的改善。

## 固定外层与可变对象

系统状态为 `S = (K, M, T, P, Hresearch, Mmeta)`。K 是规则和执行器；M 是机制 population；T 是挑战 population；P 是逐机制逐场景的完整结果；Hresearch 是不可变研究事件；Mmeta 是研究者使用的记忆机制。

| 对象 | V0 固定什么 | 后续正常迭代什么 |
|---|---|---|
| K | 类型和权限、runtime、组件注册表、verifier、生命周期、预算计量、统计决策程序、冻结和恢复规则 | 正常 M/T 迭代无权改变 K |
| Memory mechanism | 固定共享组件库，M 是配置且每次仅改一个注册行为字段 | flat/fixed tree、无抽象/单层 INDEX、tree/lexical/hybrid、加载、credit、反思和失效策略 |
| Experience | L0 原始事件、L1 卡片 schema、extractor 的模型/prompt/规则与版本 | L2 的组织、排序、派生 INDEX、utility 和活动视图 |
| Evaluator | 输入视图、Scenario DSL、单字段提案格式、校验和接受规则 | 当前挑战的一个注册参数；不能改 verifier 或 gold |
| Task agent | 模型/工具/推理设置、每 episode 独立 Codex 会话、记忆接口和公平预算 | 自己产生搜索、工具调用、代码修改和 reasoning |
| Research agent | Evaluator 与 Proposer 分 namespace，固定可见研究资料和预算 | 分别提出挑战或机制；研究记忆只经过 shadow 规则升级 |
| Scenario | 注册 family 的 user/environment 状态机与 verifier 映射 | 干扰数、迁移距离、规则变化、实际 context pressure 等注册参数 |

模型选择、组件新增、schema/extractor 改变、预算与超参数变化都会产生新的实验条件；不能静默改变已有计划。K0 冻结后默认不再调整这些外层参数。M/e 的配置快照随每轮冻结，不能因为它们可迭代就不记录版本。

接口统一为 runtime 自动选择预算内 brief，再由 agent `open_memory(id)` 展开 detail；支持空结果。来源访问只给固定 extractor 或受限反思阶段。解题者不能通过 raw source、list/search 或直接读存储绕过预算。AGENTS.md 只写稳定控制协议，经验放在隔离记忆存储。

### 经验公平性和真实学习分别验证

两个实验模式必须分别记录，不能混算：

1. **共享经验的组件对比**：独立、不参赛的 acquisition 会话真实完成学习和干扰任务；固定 extractor 形成一份冻结的 canonical corpus。所有候选从同一份 cards、可见来源和 usage events 独立 rebuild，再运行 fresh target。这样可以归因于经验的组织和使用。采集器、语料与 seed 在候选对比前冻结，不能从某个胜出臂挑选有利经历。
2. **独立学习的端到端确认**：各臂从相同 checkpoint、外生事件政策和预算开始，学习、干扰、目标均独立真实执行。extractor 固定，但实际行动和形成的经验内容可能不同。这个模式确认实际闭环可用，不声称所有臂收到逐字相同的 C，也不能把全部差异归因于一个检索组件。

共享原始语料是预先冻结的合法输入；共享其他参赛臂的结果或派生记忆是污染。失败导致未形成经验也是结果，不能补上一条理想经验后继续计为成功。学习内容、目标评价、研究历史都按时间截断，不能在目标前看到目标的未来反馈。

## 仓库基础和缺口

已有 v1 的 runner、工作区隔离、原始事件捕获、独立 grader、来源和版本检查、报告与中断保留可以复用。现有 3 类 15 episode 是真实故障类别衍生的合成小项目；现有 reference store 是流程接口自测，不是已经实现的候选机制。

已实现注册的声明式 M/e 搜索空间、canonical 卡片/来源合同、interactive Scenario DSL、配对决策及受控交替 epoch、完整离线 trial/payoff 提交和受控 Mmeta shadow 规则。全阶段 prospective 图已登记共享 acquisition/独立学习、oracle、条件 L2、研究前后 extractor 和同历史 shadow，并有整轮故障/预算 simulation；它尚未驱动正式模型。单层生成的严格输入/结果可入库并 rebuild，但当前只准显式 fixture；没有正式固定模型 extractor/reflector 调用。正式 proof 接入、全阶段真实预算停止、研究 agent 自主提案、真实 Recommand H2 重跑和 live 辨别力仍缺验收。离线披露计数器是 UTF-8 bytes，预算 journal 的 fixture token 标为 simulation，均不能宣称同 token 公平性。原 v1 全量 docs/source hash 会阻止文档变化后的旧计划继续执行；旧计划必须保留，不改 lock 绕过。V0 离线包已能从自包含快照重放。

本仓库目前不是 Git 仓库。冻结不能依赖一个不存在的 commit；先采用自包含、按内容 hash 寻址的 K/组件/场景/环境快照。后续若加入 Git，commit 作为附加身份，不替代工件内容校验。

## 逐步实施顺序

### S0 固定讨论合同和参数清单

交付本计划、协议修订、规划配置、讨论需求对照表。明确研究假设、固定/可变边界、V0 与 V1、验收证据和所有默认参数的来源。配置中的数值是显式初始选择，不是已验证阈值；live 前校准，S8 前全部定稿。

验收：末尾定稿的每项约束都有实现阶段和关卡；先前建议中被修订的部分不混入 V0；不存在“实现时自由决定”的研究假设。模型、tokenizer、extractor 和 seed 等尚未选定时保持 `null` 并阻止正式计划。

### S1 实现类型合同和不可变注册表

先做统一 schema、ID/version、typed mutation、parent snapshot、可见输入视图、错误码及 registry。M 与 e 都通过声明式提案进入，未知字段、伪装在 metadata 内的行为变更、一次多字段变更、旧 parent、非法组合和越权提案一律拒绝。允许合法 noop/空提案但不能计为改进。

交付固定 memory component 注册表、scenario family 注册表、verifier 映射、预算与角色权限。把现有 `content` 到 canonical `detail` 的转换放在一次性版本适配层，保留原 record；不维护两套相互冲突的活动实验合同。

验收：合法提案可 materialize，所有违规提案有精确拒绝码且无部分修改；同一输入生成同一规范化 spec；evaluator/proposer 不能通过提案改 K、模型、预算、verifier 或隐藏数据。

### S2 实现原始证据和 canonical 卡片

L0 保存输入、可观察输出、工具及结果、文件 diff、明确反馈、独立 outcome、显式 hypothesis/rationale；不依赖历史 hidden CoT。L1 用一个固定 extractor，统一 brief/detail/evidence/time/outcome。L2 是各 M 的派生视图。卡片引用实际可见事件；gold 和独立隐藏检查的期望答案不进入 extractor 输入。

INDEX 最多单层、至少两个下层卡片不可变版本作支持；引用只证明可解析，语义有效仍须独立检查。更新叶子、supersedes 或失效时，依赖的 INDEX 同步失效/重建；拒绝悬空、自支撑、循环、跨 namespace 和未来时间引用。credit 保存原始 usage 事件，区分加载、展开、声明使用、实际行为关联、独立结果；EMA 是关联信号，不能宣称因果贡献。

每个候选统一从同一 canonical source rebuild。派生生成涉及模型时，固定模型/prompt/调用预算，保存原始响应和派生快照；重放已经记录的投影与新随机生成要分别标记。

验收：rebuild 不继承旧派生误规则；旧版仍可解析；重复 ingest 不重复写；错误引用不能提交；卡片/INDEX/usage 任意阶段中断无半条经验或半个活动状态；摘要与原始来源矛盾可被独立反例揭示。

### S3 实现最小共享记忆 runtime

按依赖顺序做 flat/fixed tree → none/single INDEX → lexical/tree/hybrid → global/local loader → outcome credit → reflection/invalidation。先做小规模本地确定性组件，V0 不引入 embedding 服务或外部向量库。组件行为和 prompt 版本都进入 K 的注册表。

tree 是路由先验，hybrid 提供 lexical fallback；相似类型不自动等于适用。全局 top-k、同祖先近邻、重要性排序及反思触发是候选字段，不写成所有臂共同强制指令。反思仅产生 L2 派生内容，不偷偷改变固定 L1 extractor/write policy。

预算计完整序列化后的 brief 与每次 detail 返回；不得半截切断规则后宣称已加载。来源、反思、重建、credit、tokenization 等维护成本都记录。无相关经验时允许零加载，不提醒“想起某条旧经验”。

验收：一个字段改变只改变声明的组件；其余预算/数据/模型一致。类别错放、祖先关系、重复/冲突、陈旧 INDEX、空库、大条目和 Unicode 等都验证。工具不能越过 namespace、版本或披露预算。

### S4 实现 interactive 场景和独立验真

以 `e = (S0, user_policy, environment_policy, V)` 实现真实任务。第一批注册 6 类：feedback retention、workflow reuse、environment gotcha、stale memory、abstraction transfer、context interference。每类都包含学习、独立干扰、target 和至少一个不适用反例；保持无记忆 agent 有完成任务所需当前事实，避免设计成记住密码式必败题。

重要 case 有 clean/pressure 配对，任务语义及验收规则相同。pressure 从实际工具结果和当前任务上下文进入；只增加磁盘上从未读取的文件不算 context pressure。记录实际到达模型的输入、噪声来源、token 数和截断/压缩情况；相关资料位置变化不重新编码正确答案。

固定 verifier 分别检查生产入口、完整行为、非法动作拒绝、错误修改、恢复和后继消费；行为事件不能只靠 final 自报告或匹配命令字符串，必须有真实执行结果和状态变化。正确参考实现与有意义的负例由独立合同导出；不能让同一个错误函数同时生成任务和 gold。

加入四种辨别力控制：已验证正确经验、条件错误/陈旧经验、无相关经验、关闭或移除关键记忆。特权 oracle 只用于诊断可解性，不参加排名；能看答案的 oracle 成功本身不证明记忆收益。

验收：正确参考行为全过，初始缺陷和越权/伪完成负例不全过；所有 case 可解且当前接口不泄露 lesson/gold；压力确实进入当前模型上下文；测试能检查取到以后是否改变行为。六类覆盖是 suite 合同，普通一轮只聚焦一类。

### S5 实现执行冻结、预算和原子恢复

复用隔离 runner，但分别隔离 solver、extractor、derived reflector、Evaluator、Proposer 和 grader。preflight 验证控制端、全局记忆、原 Recommandation、其他臂、隐藏 seed 和评分端拒读；失败就停，不能降级为普通工作目录执行。

执行计划绑定完整 Experiment Fingerprint：K/组件/verifier、M/e/parent、canonical corpus、workspace/env/dependencies、模型 requested/resolved 身份、CLI、推理设置、prompt、tokenizer、工具、预算、seed/repeat、实际上下文与执行模式。将研究结果的公开摘要和私有评分材料分开。隐藏集只终局确认，不逐轮给 proposer 具体失败 trace。

prepare 先列全部阶段的调用数及预算，包括 acquisition、extractor、reflection、rebuild、proposal、oracle、shadow、回归和确认。不能只计解题。token 预算独立于 bytes；若 CLI 无法提供实际值或及时限制，预留界限、监控和 provider 限制是否可靠必须实证确认，否则不能标成预算合格。未知成本记 unknown，不记零。

缓存键用完整 fingerprint，命中才不调用模型。缓存复用不是新的随机重复；任何模型/来源/环境/规则变化都使旧结果不能当成同条件样本。冻结快照可在 cwd 后续变化时独立重放，报告文档变化不能迫使重算无关样本。

工件写入 staging，校验后提交一个 commit receipt，再更新可重建索引。覆盖在原始事件、grade、memory、payoff 和 epoch 指针之间的各个 crash 点；只认 receipt。恢复可继续 pending 工作，已 committed 任务不重复调用；中断任务保留失败和调用成本，新尝试另编号，不能覆盖成没发生。

验收：同条件命中零新调用；条件漂移拒绝混合；单一执行者；任意 crash 点都恢复一致的 trial/memory/P/epoch；原始记录不被 report 覆盖；预算超额尝试留存，超时、失败、中断和缺失不从计划分母消失。

### S6 实现两种 epoch 和晋升规则

固定 T 的 Memory epoch：输入公开 T、当前 e、M/P、经权限过滤的 Hresearch 和本 namespace 的 own-memory；提出一项 M mutation，固定 controller 校验、rebuild、筛选、确认。目标是在 e 上改善且 regression/严重反例不恶化，预算合格。拒绝也保存假设、reason code 和 source。

固定 M 的 Evaluator epoch：收到 mastery 或 plateau trigger，提出一项 scenario mutation；固定 controller 做 validity/solvability/duplicate/coverage 检查，再测 elite。R(e) 保留向量，不用未声明的权重把正确性与难度混成一个分数。太难要求 stepping stone，太简单转作回归诊断；proposal 耗尽则明确 `search_space_exhausted`/`budget_exhausted`，不编造新 family 或无限循环。

T 生命周期为 proposed → validated → frontier → mastered → regression → archive，另有 hidden；拒绝可进入 rejected archive。mastery 看冻结的 elite cohort 和独立确认，不看全历史 population，也不能靠删除失败机制抬高比例。同一批缓存结果不算第二轮 mastery。plateau 看有真实执行的有效尝试，noop 或基础设施失败不能当作无学习梯度。

M 分 active/archive，保持 lineage 和 niche。active 上限与固定 parent/eviction 规则限制运行量。一次只一个 focus frontier；采用配置中明确的边界避免 80% 同时被判成 frontier 和 mastery。缺少独立样本时保持 undecided。

Mtask 的晋升和 Mmeta 的晋升分别记录。Mmeta shadow 固定同一公开研究历史、同模型/prompt/预算，对旧/新记忆的 Evaluator 挑战质量与 Proposer 有效发现率作配对确认。新机制从 canonical 研究 cards/usage 重建，不能复制旧抽象；新 task champion 未通过 shadow 时继续用旧 Mmeta。

验收：受控提案完整驱动 `e0 → M changes → mastery → e1 → M changes`，也驱动 plateau、无效 proposal、预算耗尽、回归拒绝及 shadow 拒绝。M/T 同步修改不可提交。每次前后差异、证据和决策可解释；state 单独损坏能从 committed events 恢复。

### S7 接入 Recommandation 的真实历史因果链

此处 Recommand 指本机 `/Users/l/Project/Recommandation`，不指另一个合成小项目。到本阶段只读采集，选择至少 2 条有真实来源的 H2 链用于 adapter 验收；另外预留至少 3 条未用于制定机制和校准的独立链作为未来终局 holdout。数量不是统计充分性的保证，来源不足时明确 blocked，不拿衍生 fixture 冒充真实链。

每条保留学习前 checkpoint、真实用户输入/明确反馈来源、可恢复代码及环境、真实后续 target、独立验收依据和匿名化记录。历史持久化资料按时间截断；移除当时以后形成的经验、答案和事后复盘。外部 I/O 改固定 replay，凭据、live API 和生产执行不能进入包。

只恢复 state，不用 summary 提前提供 lesson。learning 与 target 都在 fresh Codex 会话真实执行，工具输出响应新行为；原 agent 的错误 patch 和动作不硬灌。中间插入独立/构造 distractors并明确标记。反馈优先选与 agent 分支无关的规则输入；条件纠错只在注册谓词确认新 agent 确实犯错时给，否则跳过，缺少可靠谓词的 case 排除并记录理由。

生产入口、恢复/后继消费与越权反例均由 controller 验证；不能只重跑一个简化函数。合成、历史衍生合成、真实 H2 分表，限制结论范围。未来 live shadow/生产应用需独立授权和上线验收，不在本计划默认启动。

验收：采集包不含凭据/未来经验/评分答案；原项目内容不改；checkpoint 可恢复；学习与目标原始事件完整；分支反馈正确；H2 有实际独立执行和独立结果。本关先证明历史测试合法，收益另由正式对比确认。

### S8 验收辨别力和摩擦并冻结 K0

先做离线合同、负例、故障注入和重复执行，再在明确模型、预算和 live 执行授权下做 canary。当前计划没有授权启动正式模型测试。canary 只校准、验收和识别测量问题，其数据不进入后续机制胜出统计。

live 验收至少覆盖学习 → 提取 → rebuild → brief/detail → target → 独立 verifier → 研究事件 → 受控提案 → 下一个 epoch；包含成功、失败/超时、pressure 和反例。离线 scripted 提案测试规则；真正 Codex solve 测传输、隔离、可观察行为和测试辨别力。不能把预写好的正确 patch 自动替代真实模型再称 live。

完成下面全部冻结关卡。冻结包包含 K0 自包含实现/注册表/verifier、完整参数、canary 与故障证据、历史 adapter 证据、需求覆盖表、独立复核记录、支持范围和 freeze receipt。正常迭代入口随后只允许提交 M/e，不能自修改 K。

验收后先在固定 e0 上启动正式 M epoch，达到 mastery 或有效 plateau 才换到 Evaluator epoch；继续到 e1 上的 M epoch。统计研究生产率和 Mmeta shadow，确认“迭代真的发生”及其收益。若搜索空间没有有效变化，诚实报告空间不足，不把规则绕过当创新。

## 冻结关卡及证据

| 关卡 | 必须证明 | 必须保留 |
|---|---|---|
| K1 合同 | 所有角色权限、字段、默认值、scope 定稿；无越权 mutation | schema/registry hash、合法与拒绝用例、需求对照 |
| K2 证据 | 时间截断、来源、旧版、support 和派生失效一致 | cards/source/usage/INDEX 快照和校验记录 |
| K3 隔离 | 每种角色读写界限有效，其他臂/global memory/评分不可见 | 每角色探针和真实 CLI 配置 |
| K4 恢复 | 跨 trial/memory/P/epoch 原子提交与任意 crash 恢复 | receipt、故障点、恢复前后 hash、重复调用计数 |
| K5 调度 | 单字段、交替冻结、mastery/plateau/回归/archive/shadow 全路径 | 提案、reward 向量、状态转移、拒绝原因 |
| V1 正确性 | 独立参考全过，缺陷/越权/伪完成被拒绝，gold 不自证 | 独立合同及 verifier 正负例 |
| V2 记忆辨别力 | 正确/陈旧/无关/off/消融的行为差异能被测到 | live 配对轨迹、实际读取、独立 outcome |
| V3 压力有效性 | clean/pressure 的实际输入可比，未偷偷注入 lesson | prompt/tool-output/context 证据、token 和截断记录 |
| V4 历史合法性 | 至少 2 条真实 Recommand H2 恢复和独立执行成功 | 来源、checkpoint、分支政策、原始事件、独立验收 |
| F1 操作摩擦 | 常规一轮一个入口，无手改产物、迁移或必须清理步骤 | 从提案至报告的完整操作记录 |
| F2 控制端延迟 | 同机器 10 次，prepare/校验 p95 ≤60s，report p95 ≤10s | 机器/数据规模、原始 timing，明确不含模型等待 |
| F3 扩张约束 | active≤8、focus=1、每 epoch≤3 候选；全阶段预算先算、达到上限停 | 计划估算/实际消耗，预算停止与剩余状态 |
| F4 复用恢复 | 精确缓存零新调用，committed 样本恢复零重复执行，重建零人工迁移 | cache provenance、resume/rebuild 日志 |
| K6 独立复核 | 原始工件足以重算决策，复核者认可 scope 内无未决缺陷 | 不同于实现者的人工/独立审阅记录 |

V2 不要求所有机制必然拉开，但要求测试存在有真实行为证据的有效正负控制，且负面结果不会被写成成功。若 live 机制控制全无差异，检查任务难度、经验是否形成、预算及消费路径；不能只改阈值让它通过。若本轮没有足够证据证明测试能判好坏，不能冻结后再把测试有效性当默认真理。

独立复核不由待迭代 Evaluator 或同一个实现者的自评替代。不能为此擅自启动编码子 agent；仓库要求的编码工作仍由当前执行者完成。独立人工审阅或未来明确授权的独立评审流程提供复核记录。

## 分层测试和结论规则

普通候选先在 focus + 至少两类分层回归上筛选，初始 3 次独立 seed/repeat；筛选只排除明显失败和选择确认对象。入围者在新 seeds 上确认，初始 7 次、预声明最多 14 次，包含祖先 family 全覆盖、严重反例及实际 pressure。终局 hidden 与真实 H2 holdout 仅作独立确认，不反复反馈给搜索者。每个重复的统计单位是独立场景因果链，不是内部断言数量或同一缓存结果。

所有比较先看完整成功、严重违规与回归，再看探索步骤、重复错误、纠正次数、时间和记忆维护成本。主效应使用相同外生事件的配对比较，并固定多重比较规则；小样本中 3% 容差常小于一个样本，不能照搬。配置先采用零严重回归、明确的非劣效界限和独立确认；最终统计程序在 S4/S8 校准定稿，不由胜出结果反推。

| 结论 | 需要的证据 |
|---|---|
| 在声明范围内更好 | 独立确认的任务成功/重复错误改善，效应达到预注册最小幅度且不由随机波动解释；回归、pressure、hidden 和预算均合格 |
| 同质量而更省 | 对任务质量有明确非劣效证据，成本降低达到预注册幅度；不能用“差异不显著”代替非劣效 |
| 更坏或不可用 | 已确认行为伤害/旧规则误用，或严重越权、回归、预算/来源违规；说明具体失败和适用范围 |
| 无法区分 | 有完整可比较数据，但差异没有达到预注册判据 |
| 证据不足 | 缺失、身份漂移、测试未校准、成本未知或独立样本不足，不能作有效好坏比较 |

仅在 benchmark 获益不足以称 Recommand 可用。终局需真实 H2 独立 holdout 确认、真实预算与部署接口可行，之后才有授权的实际使用验证。研究 bootstrap 还要单独证明相同研究历史下 Evaluator 产生更有效挑战或 Proposer 更快发现有效机制。分别画/列 retrieval、agent proficiency、research productivity；第三项不能由前两项推定。

## 低摩擦的一轮操作

实现后的日常入口接收一份 typed proposal，自动完成：校验 → 预算预览 → 新计划 → rebuild → 筛选 → 必要确认 → 固定决策 → 提交 → 报告 → 保留下一步状态。若 live 权限和预算未配置则只生成可审查计划，不能自动发模型请求。上述是待实现行为，不是当前 CLI 已有命令。

研究者只拿当前焦点、固定 regression 摘要、限定 population/payoff/coverage 视图和渐进研究记忆；完整证据仍可按授权来源追溯。旧 M/T 全部 archive，但无需在每轮全部加载。候选淘汰、精确缓存和分层回归降低费用；只有确认才跑完整规定覆盖。缓存、分层采样和早停不能缩减失败分母或改变验收标准。

一轮达到预算上界就停止并形成 partial/inconclusive 报告，后续从明确 pending 状态继续。执行前冻结续跑批次和可选追加样本规则，不能根据看到的分数无限重试。必要维护、人工处理数、拒绝率、重复候选率和单位有效发现成本都进入研究日志，作为系统摩擦指标。

## 讨论需求与执行阶段对照

| 讨论定稿 | 计划位置 |
|---|---|
| 固定 K，M 与 T 共进化；双方无权改裁判 | S1、S4、S6、S8 |
| M 是 config，共享组件，一次一个字段 | S1、S3、S6 |
| brief/detail/evidence；类型树与 INDEX | S2、S3 |
| 固定 extractor/schema，raw source 保留，统一 rebuild | S2、经验公平性两种模式 |
| 相同记忆接口与 budget；retrieval/use/outcome 分开 | S3、S5、结论规则 |
| interactive 构造测试；不要求历史 CoT | S4、S5 |
| H2 因果链、state-only checkpoint、不能硬套反馈 | S7 |
| clean/pressure 与相似但不适用的干扰 | S4、V3 |
| reward vector、oracle/frontier、mastery 和 plateau | S4、S6 |
| regression/hidden；M/T archive 保留 | S5、S6、分层测试 |
| epoch alternating、task/meta champion 区分、own-memory shadow | S6 |
| fingerprint、明确所有 defaults 和摩擦护栏 | 配置、S0、S5、F1–F4 |
| retrieval/behavior/research 三层证据与真正 flywheel | S8 后的正式迭代与结论规则 |
| dynamic tree/write/schema/radical/new primitive 等逐步开放 | 配置 deferred_v1，K0 正常迭代不开放 |

每阶段完成时在独立验收记录中附实现 hash、测试命令、原始证据、实际结论和下一阶段指针。当前离线结果不替代上述 live/历史/独立复核关卡。后续依赖顺序为：角色执行与全阶段预算 → 真实 extractor/L2/research 接入及正式 proof → 共享 acquisition 与独立学习比较 → 历史 adapter → live 辨别力/摩擦/独立复核 → K0 冻结。
