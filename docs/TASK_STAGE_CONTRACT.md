# 任务执行与独立评分提交合同（S5）

`configs/task_stage_v0.json` 固定本模块为显式 Python transport fixture；生产 verifier、原始 JSONL parser、记忆/场景 socket 与实际 Ledger/UsageStore 共用生产路径。该入口不运行 Codex，不接受调用者指定正式类别，不证明记忆收益。完整真实角色执行/研究 driver 和 formal proof 仍是待完成关卡。

每个 task 从 StageInputs 的确切 packet 和当前模板初始化独立临时 worker。开始先原子提交 stage attempt 与 execution identity，fixture 源码 hash、模型零费用、输入、控制端输出路径固定。outer sandbox 限制读写及网络。模型/工具不得访问 controller capture、grader、其他臂或答案。

控制端读边界包括账本整个父目录、StageInputs scope 根和输出根；测试目录或输出位置改变不允许使同级 controller.sqlite 可读。实际执行和恢复时的命令校验使用同一组根。

退出并关闭 socket 后，控制端封存 bounded raw capture、memory/scenario audit 和安全工作区快照。链接/特殊文件拒绝收集并留下失败码。task commit 重新播放原始 JSONL、核对 packet 和 transport 身份、重算记忆披露、核对工具 wire；来源仅含当前用户合同、实际公开工具响应、允许的 command 输出、合法文件 diff 和最终公开输出。reasoning、私有 oversized 工具响应和 grader gold 不作为经验来源。原始私有文件保留，不能把未知 token 当零模型真实用量。

单条事件超过 SourceStore 的完整文本上界时，不截短正文；原始私有 capture 保留全文，公开来源只给明确 unavailable 原因和原文 hash/byte 数。该 metadata 不是已披露原文，extractor 不能据此声称原文支持一个语义结论。

task capture、公开 source、auto_load/open/self-claimed use、stage artifact/finish 和 receipt 在同一外层事务中提交。source 与 usage episode 始终是任务 slot id；被拒绝读取不能记为已展开。socket 返回记录不单独证明后端模型已看到正文，自报告使用也不构成因果收益。没有独立行为关联依据时不自动生成 behavior_link 或给所有已展开卡片分摊 credit。

grade 在新的隔离 worker 恢复封存任务文件，使用固定独立 production verifier，核对生产完整行为、保护文件、final 与严重违规。评分后新增非法文件同样失败。私有 grade 含组、生产输入/输出及 suite identity；公开 outcome 只保留 verification、full_success、behavior_success。公开 source 在 grade 阶段产生，但 episode 是被评分 task，使 independent_outcome 与该任务的 usage 正确关联。grade raw、公开 outcome、usage、grade artifact/finish 和 receipt 原子提交。结构提交成功与任务行为成功分别报告，不能用完成槽数当任务成功率。

外部进程结果在事务提交前已真实发生：中断恢复读取同一 seal，不重发 fixture/model。缺 seal 且确实退出时保存 interrupted 和已有 raw hash；缺句柄/进程仍运行则保持 started，不能据 lock 或目录存在标 terminal。一个 output root 只允许一个 executor。缓存要求输入与 execution identity 完全相同；改变 fixture code 不复用旧结果。所有 planned/started/failed/blocked/missing 仍由 StageJournal 留在分母。

评分 seal 同时绑定 grade input、原 task capture 和 grade hash，恢复时逐项核对；生产入口使用绑定的完整 scenario contract。原始修改若不是 UTF-8，公开 diff 标出 unavailable、byte 数和 hash，保留私有字节，不产生替换字符源码或阻塞原子失败提交。
