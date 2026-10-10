# 新会话 RPC 传输的本机前置诊断

fixture 转发先消费 PROVIDER_BUDGET_CONTRACT 的持久化 prepare/dispatch；后端原始完成对象核对同一上限并用于 finish。原请求、forwarded request、SSE 和预算 report 分开保存；不能以本机 fixed counter 的模拟值声称真实 token 硬界。HTTP 元数据只保存非秘密有限字段和认证存在标记，失败请求也登记，原始认证值禁止落盘。

结束时从封存后的完整 stdout bytes 重建事件并再次验真，不只信内存中的首个 completed；重复/跨 thread/跨 turn、遗漏 command/final/usage、追加 error 或未终止进程不能通过。provider 失败请求的有界原字节也保留；attempt 次数与 accepted 次数分开。参数配方在启动前按确切字节冻结，客户端之外的变更不能扩大方法或权限。

thread/start 不传空 environments 列表；本机该字段的 [] 会禁止本地工具。诊断只使用默认本机环境及固定 cwd/权限配置，实际 tool schema 必须包含固定内置 exec_command，否则拒绝；不补任意远端环境。

控制进程显式设置 NO_PROXY/no_proxy 为 127.0.0.1,localhost，仅使 fixture 的 loopback 路由不受系统代理影响；不继承任何代理凭据，工具仍 inherit=none/network off。旧失败保留，实际 provider 请求必须到达本机服务，不把其他代理返回的 502 当作 fixture 请求。

仅此无真实认证/模型的本机诊断允许固定 codex_core/codex_api debug 日志；日志同样受 wire 上界约束并保存失败。未知内部停顿不得通过增加重试或扩大工具权限掩盖。

配置 rpc_role_v0.json 先登记假设。实际 CLI 只用临时空 HOME/CODEX_HOME/file 认证存储、明示禁用的 host discovery 和一个 loopback fixture provider。模型名固定为 memorylab-fixture，不是实际模型。request/stream retry 为零；一个新的 ephemeral thread，一个 turn，禁止 resume/fork/steer/account/login。thread 与 turn 使用同一命名权限 profile，不混用 sandbox 参数。

RPC 客户端在启动前绑定允许方法、参数和次数；唯一 turnId 从实际响应取得并校验实际事件，不信任调用者自填完成。保存有界原始 stdin/stdout/stderr 和解析索引；timeout/EOF/error/重复响应/未知 server request/流超界均保留失败并实际终止进程组，不重新执行。正常响应不等于 turn 完成，完成还需一致的 thread/turn、内置 command item、final、usage 与实际 provider 请求链。

fixture HTTP 服务只监听 IPv4 loopback，只接受 /responses 的两个 POST、有界普通 JSON body，不接收认证 header；响应为固定 SSE。第一响应要求内置 exec_command 执行无敏感信息边界探针，第二请求必须含同一 call_id 的真实 function_call_output，之后返回固定 final。工作区读写须成功，私有 marker 和控制环境须不可读。工具网络关闭；工具进程不能直接连接 fixture provider。provider 记录保存实际请求字节、有限 headers 元数据和返回字节；不保存真实凭据，因为此入口不允许提供凭据。未识别模型、请求重发、请求超界、缺工具输出、错误路径均拒绝。

后端 fixture 不会判断任务质量，也不模拟记忆收益。它只检查实际 Codex RPC→模型请求→内置工具→模型请求→最终事件路径。所有 usage 为 simulated；结果 category 为 infrastructure_acceptance、actual_model_calls=0、formal_admission=false。实际认证来源、硬 token 预算、其他 built-in tool、动态来源服务、生产角色 materialization、正式独立评分和上下文压力仍需后续验收。不能把该配置中的 false 改成 true 启用实际模型。

动态来源/记忆的本机诊断按 RPC_DISCLOSURE_CONTRACT 单独运行，同样不授予正式资格。封存复核检查当前实现身份、配置、唯一实际 prompt、合法 item 类型以及原始字节上界；预算账本在该路径只读打开，拒绝开放 journal 和缺库。复核不能修改封存库或重新派发请求。

官方接口参考：[App Server](https://learn.chatgpt.com/docs/app-server)、[Configuration Reference](https://learn.chatgpt.com/docs/config-file/config-reference)。本机生成 schema 和实际行为优先于假设。
