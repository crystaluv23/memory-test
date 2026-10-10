# 独立整轮初始化与执行合同（S5）

configs/graph_run_v0.json 声明支持的诊断 transport；入口 graph-round 支持显式 Python fixture，以及角色槽的 native_rpc_fixed_fixture descriptor。实际模型调用为零，formal/gain/promotion/freeze 全部 false。无 live fallback。程序逐个映射冻结图中所有非 disabled actor slot；拒绝遗漏、其他 slot、未知 descriptor、非法角色、单项/整体超上界。native descriptor 为 {transport,cli_path,cli_sha256,output_policy}，output_policy 只能是 fixed_role_packet_template，CLI 必须实际存在且 hash 相同；结果模板随全体实现冻结。代码、descriptor 与配置首次执行前固定，恢复不能换程序。solver/acquisition/oracle 仍仅 Python，不能把这种混合诊断称为正式原生全图。

prepare 接收原 controller DB、真实 task snapshot、按角色区分的 research snapshots、选定 M/e/meta/shadow refs、seeds、mode 和可选固定 schedule/role/scenario/execution 配置。先验证输入和 prospective guard；只移植选定公开 corpus 及其全版本/usage/support、注册 spec 的必要父 lineage。目标 task/checkpoint 与两个 research namespace 独立导入；其他臂、私有 grade、raw capture、答案、全局记忆不复制。不能用 SQLite backup 把未声明对象搬进去。

准备过程在同文件系统的私有 staging 中完成 task fork、research imports、目标 snapshot、从目标实际 hash/cutoff 重新编译图、初始 StageInputs 与 driver binding、ready receipt。关闭/WAL checkpoint/fsync 后以 non-overwrite link 发布 ledger.sqlite。相同请求在发布前中断可无模型重建；发布后中断重验 actual ready receipt 恢复，不再次复制。已有目的 DB 不属于该 recipe、路径别名、源目标同文件、跨角色 snapshot 或源码/环境 drift 均拒绝，不覆盖或要求手工清库。目标根位置、原控制目录拒读范围与程序也属 frozen identity。子进程与 production grader 同时拒读 origin controller parent、新 round root、scope 和输出；初始化不改原库事件。

已发布初始化须有可解析移植 receipt、嵌入的原公开 snapshot/portable hash、每个目标 cutoff、spec lineage、图和两个 bindings；恢复可从自身工件完成，不要求原 controller 在线。未来原 source 的新事件不进入旧 cutoff，后续轮次显式选择新 snapshot。恢复不得重选最新 head。

run 持有整轮唯一执行锁，按实际 pending 拓扑及已冻结 batch 边界驱动 TaskStages/RoleStages/ControlStages。disabled 与失败依赖使用真实 close_unrun；bound_noop 使用真实零模型派生批次；grade/report/validation 等按各自 dependency policy 消费已终结失败。缺 process handle 或正在运行保持 started，并停止为 needs_attention；不假装完成，不自动重试。已提交 source/receipt/binding/环境完整性校验失败时 run/report 直接拒绝，不将损坏数据报告为可信成功；原始分母和事件不删除。KeyboardInterrupt/故障注入保留原始未完成状态。已提交任务/角色/control/child/noop 全部按实际 typed receipt 重验，缓存不成为新独立样本。

整轮 prospective 超 guard 在执行前拒绝；批次时间和可选 --limit 只控制本次调度，不重置 round calls/tokens。不执行的槽仍 pending，不能 arbitrary skip。report 重新核验所有 finished publication 并保留所有计划阶段和全部 task denominator；stage terminal 不等于任务成功，也不等于研究质量。shadow quality 仍 unavailable。

report 的 input/dependency/snapshot 解析可在单次锁定的事务中复用，按 StageInputs 的有界 validation_scope 管理。原始输出、跨库 publication/child receipt、评分与任务捕获仍逐项核验；缓存只减少同一祖先的重复重组，不代替这些验证器。遇到数据库变更刷新，异常释放，下一次 report/run 从实际状态重新检查。改进是否达到报告时间上界须用完整图实测，局部计时不授予 F2。

默认 run 为一次已冻结 batch；正常小图可一次结束，分批从同库继续。自动迁移和 driver 接通只证明布线，不满足 F1/F2：真实各角色、十次完整运行摩擦、模型/token/provider 硬上界、独立质量与 H2 仍须后续资格验收。

prepare 的 --request 是 JSON 对象，字段精确为 source_db、initial_snapshot、research_snapshots、options、programs。snapshot/ref 来自实际 CorpusStore/Registry；research_snapshots 在启用 meta 时精确为 evaluator/proposer，否则为空对象。options 字段精确为 mode、memory_refs、scenario_refs、seeds、meta_memory_ref、shadow_memory_ref、schedule、execution、role、scenario；最后四项配置可传 null 使用当前默认值，随后完整写入 recipe。programs 为冻结图中非 disabled actor slot id 到明确 fixture 程序文本或已声明 native 角色 descriptor 的完整映射；control 和 disabled slot 不能传程序，任务槽不能使用角色 descriptor。Python 路径无生成正确解法的默认值；native 路径仅运行随实现冻结的诊断模板，不能据此证明真实任务或提案质量。tests/test_graph_run.py 展示实际生产 API 初始化、CLI prepare/run/report、完整运行、拒绝和恢复路径。测试中的预写任务解法只属于基础设施检查，不是正式受测者策略。

run 对既有完成阶段的核验使用另一单次 validation_scope，退出并清空后才调度任何 actor。不能把既有核验缓存带到执行阶段；终了报告另外新建 scope，即使本次没有新 actor，也重新核验原始工件。
