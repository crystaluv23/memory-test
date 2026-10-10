# 固定角色的来源与记忆动态工具

先登记配置假设，再接入实际独立 Codex RPC。本模块复用 EvidenceReader 与 RuntimeSession，不新增第二套检索、来源或额度语义。

extractor/consolidation/reflector 只有 read_evidence(id,start,count)；solver/evaluator/proposer 只有 open_memory(id)。来源只来自该角色 packet 的明确 catalog，记忆只有已自动披露 brief 及合法下层 support。namespace、cutoff、版本、输入 packet 与工具 schema 在 thread/start 前冻结；工具参数不得修改这些界限。acquisition/oracle 或无记忆角色不得取得记忆工具。

RPC 只接受同一实际 thread/turn 的 item/tool/call，request id 与 callId 均唯一；namespace 仅 null/省略。错身份、重复、未知工具或 server request 停止会话。合法工具的非法参数由现有服务返回有界错误；错误同样计额度。每条 pending 调用先持久化，再使用底层服务；完整服务 audit 及精确 reply 持久化后才能发送。无法持久化则不能释放未记录资料。底层服务返回空 bytes 时，工具返回空 contentItems 和 success=false，不另添加未计量内容。外层 RPC 的完整序列化 reply bytes 另受固定上界，不能冒充模型 token。

封存核验从原始 stdin/stdout 重建 server requests/replies，与唯一 call audit 对应；使用初始化时冻结的同一服务状态和原生产方法重算全部合法/拒绝回复与 source/memory audits，不消耗运行时额度或再次执行 Codex。工具 item 的 started/completed、最终事件和模拟 usage 仍须匹配。实际 provider 的后续 input 必须包含同一 callId 与精确输出；仅 controller 声称已读取不算。

初始化、调用和服务审计文件分别受 max_snapshot_bytes 上界，检查实际保存格式的 bytes，读取使用拒绝链接的有界文件读取。只读 verifier 从来源缓存或投影恢复初始生产服务；记忆仍由生产 briefs 自动生成一次，再逐条生产 read/open 重放。初始化 SHA 由传输 recipe 绑定，配置和源码身份必须一致；不能用调用后的额度当作起点。verifier 无 bind/reply 或重发入口，pending、重复、越界或漂移均拒绝。当前只接受明确 offline byte tokenizer，不能据此取得真实 token 计量资格。本机固定 CLI 将工具 inputText 转成 function_call_output 的字符串，封存检查据实际原请求核对这一表示和精确内容。

封存预算核验用只读 immutable SQLite，要求没有 WAL/SHM；缺库、开放 journal 或任何写方法都拒绝。不能在复核时创建预算库或改 schema，且整组 JSON/stdin/stdout/stderr/provider 原字节均按现有边界有界读取。

本机 fixture 可以在一次响应中产生多个固定工具调用，第二次请求包含所有真实输出后才返回固定 final。各角色 packet 必须由现有 RoleInputs 产生；fixture final 不符合正式 extractor/proposer 内容合同，不接入 materialization 或晋升。所有模型调用为零；真实 tokenizer、公平完整 token 计量、认证隔离、正式角色/全图与语义质量仍需后续验收。此诊断不恢复或重试实际会话；未封存尝试保持失败原记录，新尝试独立编号。
