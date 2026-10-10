# 独立角色输入和传输合同（S2/S3/S5）

RPC_ROLE_CONTRACT 的前置诊断已连接真实独立 ephemeral thread 和内置命令事件；只用 loopback 固定 Responses fixture，不使用真实认证/模型。它验证 provider 实际输入中的工具输出与封存事件链，usage 明确 simulated，不能接入正式 RoleAdmission 或从旧 exec canary 继承批准。正式角色 materialization、真实认证来源、硬预算与全部工具/上下文资格仍待接通。

当前认证状态为 `isolated_auth_not_qualified`。fake-home 实测发现原外层未拒读 auth 路径，现已拒读 Codex auth 和 宿主配置目录（`~/.config`，含第三方工具凭据），并统一过滤 transport 与生产评分环境。只继承必要非秘密 OS 字段，CoreFoundation 字段按 uid 生成；认证、代理及 controller/session 变量不继承。真实 canary 即使有 packet 批准也在启动前拒绝，直到不暴露凭据的独立认证传输完成资格验证。不能以放宽 worker 读权限解决 CLI 登录。两份历史 canary 包均已撤销，旧批准不转移。

实际 CLI 无模型诊断进一步否定旧双层 native sandbox 的直接执行路径：自身 pidinfo 受限导致 SIGTRAP，允许自己查询后内部 sandbox_apply 仍失败。新的 APP_SERVER_PROBE_CONTRACT 用临时可信 app-server 和明确工具 permissionProfile 验证了命令目录/环境/socket/网络边界；不启动角色 turn、不读取真实认证，也不证明全部内置工具或模型上下文隔离。正式角色须改接并验收可信 RPC 传输与认证/预算；不得仅修改 authentication_policy 布尔值启用旧 canary。

配置为 `configs/role_v0.json`。执行器使用 pinned Codex exec，每个 packet 一次独立 ephemeral 会话，忽略用户配置和 rules，禁用 memories/apps/plugins/browser/computer/multi-agent/skills discovery。solver/acquisition/oracle 的任务来自独立 workspace 和固定 Scenario；extractor 只拿合法公开来源的 metadata 与渐进 read_source；consolidation/reflector 拿当前 cards、支持链、eligible 集和同一截止点的来源句柄。研究角色只拿自己的有限历史/注册字段和 meta runtime；shadow 使用独立 store 的同历史输入。worker 不拿 controller DB、评分器、答案、讨论来源或其他臂的文件。

角色输入按 schema 生成，存储 canonical packet/hash、角色、namespace、截止点、固定 prompt/schema/model/CLI 身份与具体预算。packet 同时绑定控制端全部源码/注册资源的 implementation_sha256，执行器权限修改也改变批准身份，不复用旧批准。来源全部先经 EvidenceStore.read 验证事件/receipt/namespace/cutoff，再形成控制端 immutable cache，socket 线程不能改 canonical store。agent 只可用已声明 source id 请求字符区间；每次返回原始 source_ref/text hash、实际区间和完整性标志。不把未知部分当读过，不截短后伪装整条来源。支持 Unicode，响应连同 JSON envelope/newline 计完整 byte；当前 byte 预算不是模型 token 公平性的证明。错误和重复读取同样计量，先持久化再披露。只有 extractor/consolidation/reflector 可以创建来源服务。

Role transport 使用有界 stdin/stdout/stderr，wall time 到限或流超额终止整个进程组。JSONL 严格解析，保留限额内原始 bytes、部分行、stderr、退出和停止原因。唯一 thread.started、一轮 start/completion、真实非负 usage 和 final 原始消息必须一致；未知/重复/缺失 usage 不记零，不从 worker 自写 final 文件推断模型已完成。cached input 不重复加进 total；reasoning 子项单独保留。命令数按真实 command_execution id 计，超过上限停止。发现已公布的 token 超额时停止后续执行，但这是 observed/post-response guard，不证明当前请求的硬上限。

prepare/probe/scripted transport 不发模型请求。canary packet 用独立内容 hash，执行命令必须指定针对该 hash 的明确批准参数；该参数只防止误跑，真实授权来自用户消息，不能由 proposer 或自动 goal 延续伪造。当前模型配置为空时只能 prepare 验证角色输入，不能启动 canary。未来已获授权的 canary 必须先通过实际读写隔离 probe；保留每个已开始调用和未知费用。canary 的 requested model、CLI binary/version 有实证身份；resolved backend、模型 token 硬界和实际模型完整输入没有证据时保持 unqualified。

任何 canary 或 scripted transport receipt 都不能由本模块作为正式效果、正式 epoch proof 或 K0 freeze receipt 接收。接口用真实独立进程验证传输/失败处理；可控子进程只用于协议故障注入，不能证明真实记忆收益。全阶段 driver 还必须将该 transport 与任务图输入解析、role-specific materialization、固定 extractor admission、独立 grade 及原子提交连接起来。

RoleAdmission 按 ADMISSION_CONTRACT 将原始记录与 extractor/L2/研究提案连接起来。它从实际输入重新校验角色视图，重放 source audit 与 JSONL，用同一事务提交 materialization 和 completion。真实 Codex 的外层 profile 现在启用 write_isolation，与 probe 的读写界限一致；probe 的网络关闭与 Codex 认证连接的网络开启分别声明，受测工具的网络仍关闭。原 60ae3a... 单包发现该写边界不一致，已在 runs/role-canary-20261008/revocation.json 撤销资格，未发模型请求，不可按旧批准执行。

`role-canary prepare` 生成一份自包含、按内容 hash 冻结的单 solver 诊断包，参数为 `configs/canary_v0.json`：1 个会话、独立构造任务、空记忆 runtime、60 秒/8 个命令、结束时用量监测，无 provider 硬 token 保证。模型显式来自 prepare 参数，CLI binary/version 在准备时绑定。模板/源码/输入 DB 留控制端；每次执行从模板重建临时 worker，评分器留父进程。`execute` 需确切 packet hash 的批准参数和控制端已取得真实用户授权；配置的默认 blanket authorization 始终为 false，批准 scope 另存 invocation。原包不可覆盖，原 started attempt 不自动重试；未完成恢复为 interrupted/unknown，同样在单槽分母中。结果另存包外。

自包含包可用其中 bootstrap 执行，不依赖已经改变的 cwd 代码。报告区分 raw transport 成功、独立行为评分、预算的 observed 状态、身份/硬预算 qualification 和正式 admission。canary 不调用 extractor 来更新任何正式 canonical corpus，不提升 M/e。`scripted` 接口只对明确 Python fixture 开启、外层禁止网络，结果类别为 infrastructure_acceptance/model_calls=0，不能伪装成那一次 Codex。
