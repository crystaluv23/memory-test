# Interactive Scenario DSL V0（S4）

Scenario family 表示要测试的记忆风险，不等于必须重写一套生产程序。V0 将六类 family 映射到独立验证过的 recovery、repair、evidence 后端，另外增加 service 环境后端。后端、user/environment policy 与 verifier 固定；Evaluator 只能改变注册的五个难度字段。映射和数值先登记 `configs/scenario_v0.json`，不把历史衍生小项目称为真实 Recommand H2。

controller 编译整条因果链：fresh learning episode（abstraction 有两次学习）、0/2/4 个独立干扰、fresh target、fresh 不适用反例。每 episode 重建自己的初始工作区，旧 agent patch 不复制。learning/target 都能从当前 SPEC 和实际工具结果找到解题所需事实。目标提示不写 family、learning/target 标签、旧 lesson 或“请记住”；hidden seed、预期输出和 grader 保留控制端。

feedback_retention 使用权限/原子修复；workflow_reuse 使用断点恢复；environment_gotcha 使用本地服务环境信息；stale_memory 使用 observed/published 新旧时点规则；abstraction_transfer 学习两种可靠性任务后跨字段/层次结构修复；context_interference 使用时点证据与相似但不适用的合同资料。transfer_distance 改变实际字段/结构而非只改标题；rule_change 改当前明确可见合同及 verifier 输入；similar_inapplicable 增加显式不同适用域的真实上下文，不能改变当前 gold。任何字段无法对某 case 生效则编译拒绝或明确 effective=false，不能算有效挑战变化。

环境 policy 的动作是 inspect / check：inspect 返回当前状态与公开规则；check 真正执行工作区 production run.py 和固定公开 smoke input，返回真实 stdout/exit。controller 根据真实执行结果触发至多一次条件反馈；无关行动不触发，无失败不强套原 agent 的纠错。condition 是注册的 `production_smoke_failed`，不使用隐藏期望值给 solver 纠错；隐藏 verifier 仅用于 episode 结束后评分。未实现自由 LLM user simulator。

repair 的注册 revision 明确改变当前 `capability_mode`（both/add_only），而非只改变随机字段；far 将真实状态和所有授权路径一起嵌入新层次。恢复/证据/服务的 far 更换实际 pointer/metric/period/dependency 坐标，保持当前合同可见。工具最多 60 次，响应最多 2MB，超限拒绝且保留失败。公开 smoke 输入与 target hidden suite 的 seed 分离；public smoke 反馈不披露 hidden 输入、答案或具体失败轨迹。

clean/pressure 使用同一 initial task 与 hidden verifier。上下文通过 **实际 inspect 工具响应** 注入：固定从无关服务/操作记录生成的噪声，不能靠未读取文件声称有压力。工具 response 每次记录 wire hash、完整 bytes、tokenizer count、task semantic signature；controller 在真实传输后记录 delivered 才可算实际压力。离线只验证传输字节和计划，模型实际 token 与压缩情况留到 S5/S8，不能把离线 bytes 当 token。语义干扰与 pressure 分开。

verifier 从 production entry 的实际 JSON、全行为组、protected 文件和 completion claim 构成；独立 reference candidate 在测试目录，绝不复制给 solver。gold 仍由控制端固定 verifier 计算。运行时使用只供记忆消费的 brief/open socket，场景 policy 为另一个控制端接口，二者无互访。工具日志在控制端，不能读取 worker 自写成功日志作为事实。

四种 control 的语料来源为独立 acquisition 冻结 artifact 或标明的离线 fixture；correct/stale/irrelevant/off/ablation 必须用统一模型、任务和预算。受测 agent 真的采取行动后才评价辨别力，离线正确 patch 全过只证明 verifier 布线。当前场景正负例是 S4 离线验收，不能替代 V2/V3 live。
