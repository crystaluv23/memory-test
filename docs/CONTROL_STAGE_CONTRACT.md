# 全图控制阶段合同（S5）

公开 report/control/grade 解析在同一主账本 mutation 中重验 actual receipt，禁止跨读取竞态拼接结果。controller/task/role 的 output lock 路径必须互异，不允许嵌套申请同一个非重入锁。这是本 controller fixture 的边界，仍不替代全图单执行者及正式多进程资格验收。

配置 configs/control_stage_v0.json。所有输入必须经 StageInputs.prepare/resolve，按已编译图的 exact dependency receipt 重算；调用者不能提供成功率、grade、任意 projection 或研究历史。当前仅 infrastructure acceptance，实际模型调用零，正式资格、收益、晋升与 K 冻结均为 false。

corpus 传递确切已提交 snapshot。rebuild 用当前 scope 的实际 RuntimeBuilder，绑定 cutoff/M/derived_refs/snapshot。shadow 先在子库提交 immutable control receipt，然后父库发布；两者之间 crash 重用子结果，缺失或漂移拒绝消费，不把子库 cards/usage/proposal 导入母库。

grade 对真实 task capture 使用 TaskStages 的独立生产评分。只有已实际关闭为 blocked 的未执行 task 可生成 unavailable grade（full_success/behavior_success=false，independent_outcome=unknown）；已开始或 pending 不能提前关闭。该 grade 留在原计划分母，但不能进入不可观测的失败经历提取。

task report 保留图中每个 solver/acquisition/oracle 及其 grade，以 task 为分母，区分 learning/interference/target/counterexample/oracle 与各 namespace、scenario、repeat。目标报告消费所有登记 target/oracle grade，另外报告已终结的学习阶段；pending/blocked/failed/interrupted/missing 不能消失。报告没有 formal/统计晋升资格。public research view 只含按 family/episode role 汇总的计划数、实际成功/失败/缺失/不可评分数及已声明 M/e 配置；不含 task ids、私有 grade 内容、gold、raw trace、答案或其他臂具体产物。超上界拒绝，不能截断后当完整历史。

proposal validation 解析真实 RoleStages 发布和 role admission/Registry receipt，保存结构 accepted/noop/rejected/transport_failed 与有限反馈；反馈属于原 proposal episode，时间在 validation begin/finish 之间。shadow grade 也验证实际 child publication，但 quality 必须 unavailable，不能以 accepted spec 替代新挑战质量或有效机制发现。真实质量 experiment 和嵌套 solve/oracle 成本仍须单独实现。

控制 publication/input/start/raw/observable_sources/stage finish/end receipt 固定身份。主库写入在一个事务中，控制重算不调用模型；崩溃后相同输入重放，已提交结果严格重验并返回缓存，不形成新的独立样本。控制缓存解析还要验证真实子库 receipt。单独的 StageJournal generic fixture completion 不能成为已实现控制阶段的证明。
