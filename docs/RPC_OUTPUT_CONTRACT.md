# 固定角色输出与原子提交的 RPC 衔接

现有动态工具路径继续复用 EvidenceReader/RuntimeSession。typed 模式的 prompt 使用冻结角色 prompt 和完整 packet，turn/start 另外传同一 outputSchema。实际 provider input 的 JSON schema、strict、prompt 和 model 均验真；固定 fixture 的预设 final 仅在控制端，不能进入第一条输入。所有实际 RPC bytes 保留，不制造 exec JSONL 事件冒充原始来源。

固定 schema 校验覆盖 object/array/string/integer/boolean/null、enum/anyOf、required/额外字段和整数下限；只使用 RoleInputs 的固定 schema，不提供新 schema 或任意执行能力。错误结构、额外字段、错误类型和非法引用由结构及原生产 materializer 分别拒绝。schema 有深度和最终输出 bytes 上界。

RPCAdmission 在调用前登记 packet、CLI 与初始披露身份，在完成时有界读取同一封存并从实际 stdout/独立 source/memory 生产重放取最终结果。捕获与 canonical/derived/proposal 的提交复用 RoleAdmission 的生产方法，在同一事务下写 raw capture、materialization 和 receipt；中断不留下部分卡片，恢复不调用 Codex。缓存复核当前 artifact，不是直接返回旧成功；native thread 不可用于另一 attempt。CLI 实际终止、线程/回合、输出 schema、角色预算槽位和各原始 hash 必须一致。

目前仅 infrastructure_acceptance，usage 模拟、semantic_validation=not_established、formal/gain/promotion=false。此衔接不代替 StageInputs 的正式全图、独立评分、真实认证或预算资格。资格类别不能通过这个固定响应入口。

提交后的 verify 只读打开已有 K 的封存控制账本，重算 begin、completion 和 materialization 的精确生产 request。原 final 必须绑定实际卡片内容、派生 items/raw/invocation 或注册表产生的 candidate；别的合法 receipt 也不能替换。读取前后全部文件保持一致，不建表、不补 receipt、不启动 Codex。started、失败和完成槽进入同一报告分母。持久化 run/verify 入口固定合成来源和响应，失败保留原目录；尚未确认停止的进程不完成提交。

接口依据：[OpenAI Docs App Server](https://learn.chatgpt.com/docs/app-server) 的 turn/start.outputSchema，并核对本机生成 schema。真实计量下一步须结合 [官方 token counting](https://developers.openai.com/api/docs/guides/token-counting) 对完整实际输入和 tools/schema 计量；本机 fixed counter 不提供该证据。当前未调用外部计量或模型。
