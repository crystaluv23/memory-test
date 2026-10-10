# V0 交替 epoch 与决策合同（S6）

调度参数见 `configs/epoch_v0.json`，与规划阈值一致；所有 proof categories 保留。普通 memory epoch 固定 focus T，memory proposer 只能在合法 active parent 上单字段变异；evaluator epoch 固定整个 active M，不允许同时提交 M 修改。accepted spec 是 materialized，不自动 active、champion 或 used。elite 按有效 payoff 更新，开始 mastery 确认后固定同一 cohort；晋升或 cohort 变化使旧确认作废，不能混用旧弱 cohort 与新强 cohort。

二元成功配对以独立因果链 unit 为单位（seed/repeat/cache identity 都绑定），完整失败/缺失计失败，严重回归直接拒绝。单个 memory epoch 的统计 family 包含最多 3 次任务比较和 3 次双角色 shadow，共 9 次比较；每次比较预留 better、worse、noninferior 三种质量声明，固定 Bonferroni 阈值为 0.05/(9×3)，未用额度不转移。此范围不是无限 bootstrap 的累计错误保证。paired superiority 使用精确 discordant binomial 单侧检验与最小 effect；非劣效采用 paired win/loss 各自 Clopper–Pearson 单侧边界，再取保守差值下界。成本更省须先有非劣效证据；成本声明只指该冻结 cohort 的已观察总成本。筛选不能晋升；7–14 单位上限仍待 power/整轮预算校准，不因新阈值默认扩大。

memory_confirmation 与 shadow_confirmation 必须先通过 plan_confirmation 提交不可变 comparison_plan，绑定 epoch、focus、candidate、baseline、research history（仅 shadow）、按角色排列的完整 unit_ids 和统计配置。计划只接受当时 pending memory 或尚未成为 meta champion 的 task champion，计划数量先占预算。所有计划单位立即在整个 experiment 的持久集合中预留；未执行、失败、epoch 关闭都不释放，mastery 单位也不能与它们重叠。换 focus 仅开始新 family，不能清空全局单位集合。

确认参数和 proof 均引用该 plan。proof 必须有在计划提交之后产生的唯一控制端登记 receipt；结果单位及顺序必须与计划完全一致，候选/基线/history/focus 漂移拒绝。每个计划只有一次确认；新请求重复提交拒绝，同一完整请求仅返回原 receipt，不能添加进度。拒绝原子回滚，不消耗其他计划；已提交的失败或无法区分结果同样消耗当前计划。这里只验证 controller fixture 的发布次序；正式 proof 仍须证明真正执行、原始单位来源/seed/cache 身份及独立性，不能凭 unit 标签证明。

mastery 在固定 elite cohort 上两次不同 unit_ids 的独立确认均 ≥0.8 才成立，同缓存第二次拒绝。frontier 为 [0.2,0.8)，oracle ≥0.9；太难保持 rejected/stepping_stone_required，太容易只作为 regression。plateau 只计真正完成验证的有效单字段尝试，连续三次 gain<0.1 且非基础设施失败；noop/拒绝/失败不冒充无梯度。换入 evaluator 时旧 focus转 mastered/regression 或 plateau/archive，历史不删。

memory active≤8，按可比较公共 payoff 的 Pareto/覆盖约束选择 parent；archive 不作为普通变异 parent。淘汰顺序为已证明 dominated、配置重复、最老非 sole niche，保护当前 champion；若不存在合法淘汰对象则拒绝而非越界。niche 使用固定 organizer/retrieval 组合，不由模型自由写。一次 evaluator只接受一个 focus，duplicate semantic_signature 拒绝，奖励向量保留 validity、oracle、elite、disagreement、novelty、coverage、progress、cost。

Mtask 与 Mmeta 分离。shadow 在同一 research history hash、模型/prompt/预算和独立 unit 上，分别确认 evaluator 挑战质量与 proposer 有效发现率；新 task champion 不自动影响 Mmeta。公开研究事件在 evaluator/proposer 各自 namespace，由 controller 提供有范围摘要；不允许跨 namespace 或读取隐藏评分失败 trace。元经验同样 canonical rebuild。

实现初期受控状态机明确 `mode=scripted_acceptance`，可用带 fixture 标记的验收 proof 驱动 e0→M→mastery→e1→M 及各拒绝分支，所有 transition 标明 simulated。这些不是真实迭代/晋升收益。formal 模式在 S5/S8 attestation 完成前拒绝。真实 epoch 需要由冻结执行和 independent grade receipt 构造 proof，不能直接接受模型传来的 success 数组；这仍是未完成接入项。
