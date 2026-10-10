# Recommandation 历史采集合同（S7）

本阶段只读原项目与明确选中的历史事件。`configs/history_v0.json` 先固定采集假设、字节上界、禁入路径、时间顺序和验收覆盖。`history` 命令不启动模型、不执行原项目代码、不修改原项目或全局记忆。审计失败也保留目录中的候选分母和具体原因。

历史目录不是经验来源的替代品。一个候选声明完整 commit、学习事件与后续 target 事件。事件只从原 Codex JSONL 的 `response_item/message/role=user/input_text` 或 `event_msg/user_message` 解析，逐行绑定原文件 hash、行号、行 hash、原始时间和完整文本 hash。自动注入的环境/AGENTS 消息不算历史任务。文件大小、事件大小、重复键、无时区、时间倒置、相同事件、其他项目会话或 event 身份漂移均拒绝。不能从事后报告概括出一个所谓真实任务。

checkpoint 必须是原仓库的完整 40 位 commit；校验 Git object 身份、committer 时间不晚于 learning，并逐项读取该 commit 中的常规文件。只接受显式路径清单，不复制当前 checkout 或整个 `.git`。符号链接、submodule、绝对/父路径、凭据、经验库、事后报告、运行目录和控制端评分材料不得进入 solver snapshot。资料按时间截断；学习后形成的代码不得当学习前 state。生产入口依赖是否完整须由独立恢复/执行验收证明，静态存在不等于可运行。

候选可附历史 run contract，审计其原始 hash、契约内 code revision 与 checkpoint 相同、产生时间不晚于 learning。run contract 仅留控制端，不原样复制给受测者。环境身份、固定外部 I/O replay、独立 verifier 的生产入口/恢复/后继消费/非法行为覆盖、规则输入与条件反馈政策均为必需字段；未配置项必须列为 blocker。`none` 只适用于无条件纠错输入；`production_check_failed` 必须绑定可复用的生产检查，实际新 agent 未触发谓词则跳过反馈，历史分支不能决定新分支。

审计输出区分 `rejected`、`incomplete`、`staged_requires_independent_replay`，没有 `accepted` 快捷开关。恢复包只含 checkpoint 文件、原始 task 输入和候选 provenance；不附 lesson summary、历史 patch、隐藏答案或研究者记忆。私有 controller manifest 保留原来源定位、verifier 和 feedback 合同。凭据检查拒绝已识别的密钥/令牌/私钥，并要求独立内容审核；模式匹配不能证明完全无凭据或无答案。未通过独立审核的包不得交给 solver。

内容 hash、完整文件清单、不可覆盖发布、symlink 检查和源文件重读防漂移保持可复核。每次操作前后绑定原项目 HEAD、status/diff 和声明读取源的 hash；改变则拒绝发布。原文件访问是控制端能力，历史源、manifest 与本聊天都不进入独立受测会话。采集包准备与正式 H2 执行分别记录；合成 fixture 的审计测试不算真实链验收。

S7 完成仍要求至少两条真实链的独立执行/评分，以及至少三条未用于机制设计和校准的 holdout。采集过程中读过或用于适配开发的链标为 adapter，不能后来重新命名为 untouched holdout。adapter、history-derived synthetic 与 real H2 分开报告。当前静态审计不得满足 V4，也不得证明记忆收益。
