# 小规模 Codex 记忆实验协议 v1

## 原生受测会话的环境经验污染审计（2026-10-08）

用户选择保留 subagent 路线后，configs/context_preflight_v0.json 限定先关闭本聊天记忆，再进行一个仅返回就绪值、零任务工具的真实探针。可用的本机控制接口仅允许 initialize 与当前 thread 的 memoryMode/set=disabled，不调用 thread/start、turn/start、认证或全局配置写入；原返回保存，接口不可达或空返回不证明已禁用。逐聊天开关对新子角色是否生效仍须检查实际 rollout，不能以设置 ack 代替输入验收。探针不获得任务或评分答案；污染或未知即停止，不重复采样直到偶然通过。

新假设先登记于 configs/context_audit_v0.json。fork_turns=none 只证明没有主动复制父聊天，不能证明没有自动注入全局经验。控制端只读核对实际受测 rollout 的 session identity、parent、agent_path、记录输入与 world_state；记录完整文件 hash、确切命中行及输入 hash，不复制全局记忆正文、凭据或私有推理到仓库。全局记忆块或未批准的输入为 contaminated；缺输入、加密且无法核对的输入、没有冻结输入 allowlist 或身份不符为 unverified，均不能获得干净上下文资格。无已知 marker 不是完整 provider 输入或 OS 隔离的证明。

发现污染即撤销该有限 run 的后续派发资格，保留其原计划、真实任务成绩、提取卡片、失败和缺失分母；其经验不得进入后续合格实验的初始语料。新实现对已有 Loop 对象的继续派发、绑定、放行和服务执行都重查 revocation，不能通过已打开连接或已有 dispatch 绕过。只读报告、实际交付确认和真实 interrupt/terminate 仍可用于收尾；不伪造终结。

rollout 的 token_usage_record 单独报告为 recorded_client_usage，原字节、唯一 response identity 和累计一致性须可核对。它不是已核验 provider 账单、费用、完整上下文或发送前硬额度，不把旧 null 写成零或回填为预算合格。新路线先用明确的 per-session memories.use_memories=false、memories.generate_memories=false 和有限公开配置准备诊断；不修改全局记忆，不复制认证文件、不默认发模型请求。清洁会话须重新冻结并核验，旧会话不能靠一句忽略记忆修复。

状态：环境搭建；尚无正式效果结果。预注册日期：2026-10-07（Asia/Shanghai）。

## meta meta 实施与冻结边界

### Codex controller 驱动的真实 collaboration 路线（2026-10-08）

用户明确要求不用外部 API，由本 Codex controller 启动 fresh subagent 充当实验角色，并先保证全流程正确，再有限迭代后交付可用机制。新增路线为 `collaboration_observed`，不是 fixed response、scripted patch 或旧 canary。任务、固定 extractor、条件 L2、Evaluator、Memory Proposer 均由真实独立 subagent 产生输出，fork_turns=none；只发送冻结的公开 packet，不带本聊天、评分器/答案、其他实验臂记录、控制端文件路径。角色不得自行使用工具或读取共享文件；场景 inspect/check、engine.py 修改、open_memory 与渐进来源请求由 controller 验证、执行、计量、记录，然后通过同一 agent 的后续 turn 返回。该指令界限不冒充 OS 隔离，通用 subagent 的系统上下文、工具限制和完整原始轨迹未核验时，隔离资格仍为未通过。

新假设与有限计划先存 configs/collaboration_v0.json。每个阶段先固定输入和完整计划，再登记 dispatch intent，启动返回的 agent ID 绑定唯一 packet；缺 handle 不自动重发，缓存不构成新样本。每条实际返回原文、身份、controller 服务请求/回复、代码前后快照、独立 production grade 和提交 receipt 持久化。重复服务请求只返回原回复；改变相同 request 身份的原文拒绝。工具、轮次、消息字节和 wall deadline 有上界；实际 provider token/cost/resolved model 不可见时保留 null，不能写 simulated token、零费用或预算已合格。未知用量不支持同 token、公平成本或正式晋升结论。

真实流程先走独立学习，按已注册 Scenario 编译学习/干扰/目标/反例；全部阶段与失败保留。固定 extractor 消费实际可见事件，canonical 卡片和研究历史按现有版本/namespace/cutoff 规则提交，解题者只获得预算内 brief/open_memory。研究者只消费自己的有限公开研究摘要与 own-memory。单字段提案由 Registry 校验；accepted 仅表示合法并可进入下一实验分支，不能因此晋升 champion。noop、无效、超时和来源拒绝同样记录，不能充当改善、plateau 或收益。需要实际 outcome、独立重复、回归、pressure、历史外部验证、独立复核及完整资格后，才授予正式结论与交付适用范围。旧诊断入口继续保存历史，不作为本路线的执行结果。

控制端接入先用真实任务→真实提取→后继读取→独立评分→真实研究提案→新冻结分支验证闭环。完整验收仍按本协议所有关卡，未完成不得以小规模演示改写成整体验收或机制已可交付。

受测 workspace 必须在独立持久的临时根中，既不在 controller/run 根内，也不在 Project 拒读根中。prepare 将该私有 root 和隔离策略绑定在计划里；每个 actor 的确切 workspace 由 controller 登记，不进入模型 packet，不由 actor 选择。check、production verifier、snapshot 与恢复均使用同一登记路径，整个 controller 与其他 worker 仍拒读，不能为了让正确代码通过而把 controller 路径从沙箱里放开。正控制必须使用实际冻结 actor 的 workspace 与实际 grader 命令；只在另一个临时目录跑参考实现不足以证明该真实入口可用。工作区路径错误的旧包保留并撤销执行资格，不搬文件后继续沿用旧身份。

报告核验与 run 开始时对既有完成项的独立核验，各自可在同一 SQLite 写事务锁内复用已经完整核验的 stage input、dependency 和 snapshot，不能跨报告、事务或 actor 调用复用。缓存以确切引用寻址，有序列化字节上界；数据库发生任何写入或回滚后重新建立事件索引和依赖缓存，退出及异常均清除。公开 resolve 返回独立副本。各 publication 的原始文件、child receipt、任务评分和角色输出仍逐项通过原验证器检查，不能用“祖先已通过”跳过文件或跨库核验。嵌套 JSON 解码核验仅在同一预算上界内共享缓存；每次读取仍查询真实 DB 并匹配 exact payload/hash。源码、环境和角色 binding 仍按原操作规则检查。该优化只是待验证假设，报告耗时须另行实测，不能据此宣称 F2 通过。

已提交 task/role 的缓存和报告必须重新比对实际 transport 原字节、持久化 capture、审计 wire 和封存 seal；退出未封存的 interruption 也须保留 exact 原始 prefix，不能在完成后替换。task grade 的磁盘封存须对应已提交 input/capture/grade。role publication 原子保留 seal，防止完成后同时改写 wire 与 seal；实际 native verifier 仍核验 RPC 原字节及 materialization。任何漂移拒绝消费，恢复原文件后可重验同一已提交结果，不新发 actor、不删除原始分母。这是对已有“已完成即可信”缺口的修复，基础设施旧通过记录不覆盖此新边界。

RoleStages/GraphRun 的诊断 actor 可显式选择 Python fixture 或固定本机响应的 native RPC。native descriptor 只允许 pinned CLI 和注册 fixed_role_packet_template，不能执行调用者代码、选择真实 provider 或取得 formal 权限。执行前固定 StageInputs、角色 packet、实际 CLI hash、模板结果、披露身份、输出目录和预算；generated profile 明确拒读 controller 父目录、scope/output 根。native 复用现有 source/snapshot/usage/proposal、child-first/parent-publication 和 StageJournal；只认 committed publication。退出未封存保存有界原始 prefix/audit 为 interrupted，不 materialize、不重试。主库所有发布点与 child→parent 间中断分别验证，旧 CLI/模板/原字节漂移拒绝缓存。disabled/noeligible 不启动 Codex。solver/grade 仍是原显式 Python 路径，组合证据不得称正式全图或真实质量。

尚未创建 transport、thread/turn/calls 均为空且 exact 初始披露仍一致时，可只读恢复初始 gateway，以重交被打断的跨库 start；不重置任何已用额度。有 transport 但无实际句柄仍为 unknown，不能凭缺文件推断未调用或自动重试。

跨库 start 的子 attempt 已登记而父事务回滚时，恢复必须复用同一 child attempt；只能在上述未启动条件下发起首次 transport。已绑定 thread/turn、消费过来源或记忆、初始 audit 变化、已有 transport 的任一情况均拒绝 initial reopen，不能通过重建服务退款。持久化整图诊断须保留 CLI prepare/run/report 原始输出，再由独立进程核验主/子库的原生完成 receipt；该复核仍属于基础设施证据，不是独立质量评审。

账本 schema 初始化只用于尚未初始化的 version 0；DDL 与版本升级同一事务，失败关闭连接并回滚，不能留下 version 1 半库。已支持的 version 1 打开时验证真实表/列合同，不能再次设置 user_version、补建缺表或修补列。已有封存 WAL 库的无新事实打开/校验保持文件字节不变；只读入口禁止转换 journal 或初始化。未知版本或损坏结构明确拒绝，不用自动修复掩盖原始缺失。报告与缓存不应因重复初始化改变主/子库。

typed RPC 的 committed 复核只读打开已封存 controller 库，必须找到原 K，不能补建 K/表/receipt。重新核验 begin/completion/materialization 的精确生产 request hash，原生 raw final 与卡片、派生 raw/batch 或单字段 candidate 保持一致；不能用一份其他合法 receipt 替换该结果。started/rejected/materialized RPC 槽均进入 RoleAdmission 分母。rpc_materialization_fixture 的 run/verify 入口为固定本机响应的持久化诊断，verify 不启动受测者、不产生写入；缺退出句柄保持 started，失败输出不能隐去。新进程复核应保留全目录前后 hash 与原始输出。

RPC 绝对 wall deadline 覆盖消息读取、客户端方法和工具回复发送。持续通知或缓冲区已有消息不能延长期限；到期不发送新请求/回复，保留已产生 audit 与原字节，再终止自己的进程组。cleanup 不受业务发送期限限制。

固定角色的 typed RPC 衔接按 configs/rpc_output_v0.json：turn/start 的 outputSchema 必须是原 RoleInputs 中冻结的 schema，实际 provider 的 text.format.schema/strict 与之相同。固定本机响应仅用于基础设施检查，预设结果留在私有 recipe/provider，不进入受测 prompt；真正 final 从原始 stdout 的唯一 final_answer 读取并校验。预算槽位按实际角色登记，不能把所有角色写成 solver。原始 RPC、输入、配置、provider 请求/回复、披露和预算同一封存；不伪造旧 exec JSONL 事件。RPCAdmission 复用原 RoleAdmission 的来源重算、canonical/derived/typed proposal 原子 materialization，在捕获/提交/receipt 任一中断后只重放同一封存。失败、未封存和重复 thread 保留并拒绝；正式类别、真实模型及 gain/promotion 仍关闭。尚未退出或缺实际进程状态不能提交。

RPC 从真实子进程取得 pid/pgid/start_time，单独保存规范化 handle，复用 production process_status 核对实际终结；不能仅信 exit.json。RPC 整个 initialize→thread→turn→final 共用一个总 deadline，工具和通知也不能刷新此 deadline。衔接的预留数值由 rpc_role/rpc_disclosure 固定 fixture 合同派生，不借其获得真实 token 硬界资格。

已提交 RPC receipt 可在独立进程只读重验：只读 Ledger 的 bind_kernel 仅解析并核对既有 immutable kernel，缺失/变更即拒绝，不能创建 kernel。重验原始捕获、实际 source/memory 重放和 materialization receipt 的提交绑定；结构提交不等于内容真实性或研究质量。

动态来源/记忆 RPC 按 docs/RPC_DISCLOSURE_CONTRACT.md / configs/rpc_disclosure_v0.json。来源角色仅可 read_evidence，solver/evaluator/proposer 仅可 open_memory；acquisition/oracle 无记忆工具。工具声明、固定角色 packet、来源 catalog 或已自动披露 briefs 在 thread/start 前绑定。item/tool/call 的 thread/turn、唯一 request/call id、工具名与参数须一致；未绑定 server request 不答复、不降级。调用 pending 及底层完整 source/memory audit 在回复前持久化；错误和耗尽计入次数，外层序列化 RPC 回复另受 byte 上界。重新验真使用同一冻结服务状态从头重算全部披露，不能以解析索引替代 stdio 原字节；已披露来源或 detail 必须出现在下一次实际 provider input。封存 verifier 在新进程只读恢复有界初始化快照，使用生产 read/open 重算全部内容和额度；它不能 bind、回复、重发或恢复实际会话。快照和调用文件均有独立字节上界，来源仍在控制端。当前仅实际 Codex + 本机固定 fixture，真实模型/认证/tokenizer、正式全图 admission 仍关闭；不能把动态工具布线通过当作收益或 bootstrap 完成。

封存 RPC 的预算复核也必须只读：SQLite immutable/只读连接仅用于已关闭、无 WAL/SHM 的封存库，不能建库、改表或写 checkpoint；发现未封存 journal 则拒绝。prepare/dispatch/finish 在只读对象上明确禁止。原始记录按既有 wire/header 上界使用拒绝链接的有限读取，复核不得借输出索引执行任意路径或创建缺失预算库。

发送前预算层按 docs/PROVIDER_BUDGET_CONTRACT.md / configs/provider_budget_v0.json。控制账本登记唯一整轮合同及全部角色槽位；请求完整内容、模型和输入计数绑定后，原子预留 gross input 与最大 output，再唯一 dispatch。明确注入 max_output_tokens 与 disabled truncation；隐藏 conversation/previous_response_id、改模型、未知槽位、调用重发、超额或费用不明不能通过新 scope 重置。完成只能消费同一 dispatch 的独立后端响应；缺失 usage 保留全部预留，usage 越界/身份不符留 breach 并禁止后续转发。当前计数器和后端均 fixed fixture，所有单位明确 simulated，不接受正式/真实认证或模型配置，不授予 provider/tokenizer 资格。

真实会话传输的前置诊断按 docs/RPC_ROLE_CONTRACT.md / configs/rpc_role_v0.json：实际 pinned Codex app-server 创建新的 ephemeral thread 和单个 turn，唯一 provider 是控制端本机 loopback Responses fixture；无真实认证、外部模型或用户会话历史。fixture 的固定工具调用必须由真实 Codex 内置工具执行，工具输出进入下一次实际 provider request，之后固定 final 与 usage 只能记 simulated。保存完整有界 request/response bytes、CLI/配置/来源身份和实际终止；关联 thread/turn/item 必须一致，禁止 resume/fork、自动重试及未绑定客户端请求。诊断可经 RPCAdmission 接入 RoleStages/GraphRun 的明确 fixture 类别；不授予正式 admission、认证、provider 硬 token、全部工具或收益资格，旧正式入口仍关闭。

认证分离诊断使用 configs/app_server_probe_v0.json：实际固定 CLI 的 app-server initialize/permissionProfile/list/command/exec，不启动 thread/turn、不调用模型、不读取真实认证文件。可信临时 controller app-server 以内部明确 permissionProfile 隔离固定工具探针，server 自身不套第二层 native sandbox。空临时 HOME/CODEX_HOME、file 认证存储和无敏感信息 controller/env marker 不含真实账号/密钥；不使用 keychain、thread/turn/login/account/provider RPC，analytics/feedback 与 host skill/memory/plugin discovery 关闭。工具须允许工作区读写，拒读私有/其他分支标记、拒接可达控制 socket/网络、拒绝外部写入和环境泄露。正控制实际读取同一 harmless marker，以区别权限生效与路径本来不可达。profile 与 legacy sandbox 设置不混用；配置/API/schema 差异不能静默忽略。此 server 是控制进程，不是受测 actor；命令边界通过不能授予真实认证、全部内置工具、模型上下文隔离、token预算或 K 冻结资格。

双层 sandbox 的失败记录保留在 runs/app-server-boundary-20261008/attempt-001–003：app-server 不支持 exec 的 ignore-user-config/ignore-rules flags；路径 canonicalize 需祖先元数据；libdispatch 获取自身 unique pid 遭 blanket process-info 拒绝；self pidinfo 例外允许 initialize 后，内部 sandbox_apply 仍返回 Operation not permitted。当前代码已删除这条不工作的诊断执行路径，未放宽正式 role 的 sandbox/认证门槛。现诊断使用空临时 home 的明示生成配置，不导入现有用户配置/规则；正式 CLI 角色传输须另行替换并验收，不能直接启用旧 canary。

诊断的 stdout/stdin 原字节与 stderr 在 wire 总上界内保存，解析后的 JSON 仅为索引。溢出不能静默截断后标通过；记录实际超界 chunk 长度/hash、已保存前缀、进程终结依据，并拒绝资格。允许 command/exec 的两个固定请求在启动前按确切字节绑定，不接受其他命令/profile/budget 或调用者事后修改；其他 paid RPC 方法在写入子进程前拒绝。

独立整轮初始化和驱动见 docs/GRAPH_RUN_CONTRACT.md / configs/graph_run_v0.json。初始化只使用真实 CorpusStore snapshot 和所选 Registry lineage；task 与两个同名 research corpus 分别导入，全库备份禁止。所有初始移植、重新编译的图、StageInputs binding 与 driver 的 explicit fixture programs 在未发布 SQLite 中完成，再 fsync/non-overwrite link 发布唯一 ledger.sqlite；无 ready receipt 不可执行。已有目的库只可 exact recipe 恢复，不可重置。模型/正式模式仍禁止；原控制库所在父目录和新 round root 一并绑定为全部 task/role/production 的拒读范围，不能只保护新副本。

graph-round 的 prepare/run/report 分别通过相同冻结库工作；run 自动按冻结拓扑图处理 actor/control、disabled、noeligible 和真实失败依赖，既有终结阶段必须重验 typed publication，不把 generic fixture 当实际执行。缺句柄或仍运行的 started 不关闭、不重试；封存结果恢复复用。所有阶段和任务保留原计划分母；未确认进程停止形成 needs_attention，不任意跳过。已提交出版物、来源、binding 或环境完整性漂移直接拒绝 run/report，不能将被损坏的数据包装成可信报告；原始计划与事件保留，不能删失败再运行。整轮预算先检查、各批次 wall 上界、重新启动不重置 StageJournal 额度。此入口的任务仍使用独立 Python transport fixture，角色可显式选择固定本机响应的 native RPC；不能完成真实模型、收益、统计或低摩擦资格验收。

控制器与 task/role 的执行锁目录必须互异，避免嵌套进入同一非重入锁。公开 report/control/grade 的解析也在同一控制账本事务内完成，从实际 immutable receipt 核验；不能把先后读取的可变 head 拼成一个结果。此规则不是完整多进程/正式模型执行资格。

阶段执行冻结身份还须包含实际 Python 可执行文件及版本、系统/架构、native sandbox 可执行文件及摘要、过滤后的 worker 环境摘要。每次 prepare/resolve（包括控制节点、已提交缓存和恢复）先重验不可变 binding、源码/配置及环境；漂移在任何新输入或产物提交前拒绝。这里只证明控制端 fixture 的环境一致性，不授予真实模型/认证/tokenizer 资格。假设和支持策略先登记在 configs/stage_input_v0.json。

全图控制阶段按 docs/CONTROL_STAGE_CONTRACT.md / configs/control_stage_v0.json 执行：实际 snapshot/rebuild、有限公开研究 view、typed proposal 反馈与完整任务分母报告均由 controller 从已绑定 receipt 重算。未执行的 blocked task 只能生成 unavailable grade，不能伪造 workspace 或成功；运行中任务不能提前评分。shadow 结构检查必须记录 quality unavailable，不授予晋升。所有 raw/control artifact、公开 source 与 stage finish 原子发布；独立 shadow rebuild 使用可恢复 child receipt 后父发布，不导入母本。控制与评分仍使用显式 Python 路径；角色可绑定诊断 native RPC，不能形成正式收益或 K 冻结证据。

S7 的真实历史采集先遵守 `docs/HISTORY_CONTRACT.md` / `configs/history_v0.json`。目录、报告或历史测试通过不能构成 H2：必须绑定原始用户事件、学习前代码、当时环境、后续目标和完整生产验真。只读审计与恢复包最多得到 staged 状态；没有独立 Codex 因果链执行及评分 receipt，不得标为 accepted。

S2/S3 的组件行为与披露预算见 `docs/RUNTIME_CONTRACT.md` 和 `configs/runtime_v0.json`。离线计数器、迁移提案和 socket 自测不能形成正式 token 公平性或记忆收益结论。

受限 L2 生成按 `docs/DERIVED_CONTRACT.md` 接收分角色的固定输入和严格结果。单层抽象允许从当前叶子归纳有适用边界的规则；结构合法不代表语义正确。生成输入/原始响应/成本须绑定同一 corpus、M 与 prompt。显式 fixture 的派生结果不冒充正式模型生成。

S4 的编译、公开工具响应、条件反馈与独立验真合同见 `docs/SCENARIO_CONTRACT.md`；实际使用的后端与压力噪声参数先固定在 `configs/scenario_v0.json`。离线 production 检查仍不代替真实会话辨别力。

生产验真子进程的 wall time、CPU、完整 stdout/stderr 和打开文件数均有固定上界。超时/输出超额立即终止该进程组，保存限额内已收到的输出与截断原因，不能解析被截断的数据为成功。具体参数登记在 scenario 配置，不以无限缓存或忽略超时输出代替隔离。

相似但不适用的上下文按真实后端分别注册，不能把时点规则作为所有后端的同一干扰。响应超过整个工具预算时仅给明确的超额错误，完整受限原始结果留控制端，不能把未传出的结果记为模型已经看见。

离线检查用 `scripts/verify_offline.py` 一次运行全部合同/故障/组合测试，并保留原始日志、源/配置/测试/文档 hash、环境和真实结果；结果目录按内容及尝试编号保留，不覆盖失败。输出固定为 infrastructure_acceptance，不触发模型调用或 K 冻结。

控制端延迟先按 `configs/friction_probe.json` 预声明的 8 个 M、24 张卡片、3 条 repeat、120 个已终结 missing 记录作十次测量。prepare 各次使用新输出目录，避免把缓存速度冒充生成速度；report 重算全部分母。该检查仅衡量声明规模的离线控制端开销，不覆盖真实模型等待或 formal 数据全规模，不单独完成 F1/F2。

S5 冻结包、精确缓存、预算预留与整次提交见 `docs/EXECUTION_CONTRACT.md` / `configs/execution_v0.json`。离线 executor 明确标为 scripted_acceptance；正式 live 条件未补齐前不会启动。

全阶段任务图与角色预算另登记在 `docs/SCHEDULE_CONTRACT.md` / `configs/schedule_v0.json`。共享 acquisition 与各臂独立学习分别编译，目标只消费学习截止时冻结的 corpus；目标/反例的未来结果不反馈给同一组目标。模型角色、固定验真、extractor、L2、research、oracle、同历史 shadow 和报告都必须列入图。一次 Codex 会话不等于一次 provider 请求，预算表的 calls 明确指会话启动数；真实总 token、provider 请求和模型身份仍需执行证据。条件不满足的维护步骤留下可审计 skip，失败/中断/超时不重发。分批只限制单批 wall time，不能重置整轮额度。

数据分支按 `docs/STORE_CONTRACT.md` / `configs/store_v0.json` 只复制指定截止点的公开 source、canonical 全版本及已提交 usage，不能整库备份。共享 acquisition 可重定位到任务臂；研究 shadow 保持同一角色 namespace、使用独立 SQLite store。旧 INDEX 的使用记录保留为可解析的历史引用，不复制旧抽象进活动 L2，不把 INDEX 效用擅自分摊给叶子。重定位差异与原来源映射留控制端，逐项核对经验内容、引用和 usage 顺序。

阶段输入按 `docs/STAGE_INPUT_CONTRACT.md` / `configs/stage_input_v0.json` 由真实 registry、图、snapshot 和 stage receipt 解析。重新编译图并逐项比较，重新签名的 bindings 不成为许可；模型 packet/资源和 action 在 actor 前原子冻结。来源 episode 使用实际任务/提案 slot id；grade/proposal_validate 的来源沿用其被评分/校验对象的 episode，另用该生产阶段的 begin/finish 验证其真实提交时间。公开来源必须在生产阶段完成前实际提交。跨臂引用仅限图中声明的 acquisition，shadow 只从提案前的研究 corpus 分叉。目标/反例复用同一投影与学习 cutoff，未来 outcome/usage 不重算进本组目标。派生无 eligible 输入时绑定实际 no-op，不调用模型。当前 stage input 接入仍是 infrastructure acceptance，不授予正式调用、晋升或研究收益资格。

任务执行与独立评分按 `docs/TASK_STAGE_CONTRACT.md` / `configs/task_stage_v0.json` 接入。执行前固定 stage input 与受限 transport，退出后封存原始流、工具/记忆记录和安全工作区快照；task capture、公开来源、usage 与 stage finish 原子提交。grade 在新的隔离工作区重建同一完成快照，真正运行 production verifier，再原子提交私有 grade、有限公开 outcome、同任务 episode 的 independent_outcome usage 和 grade finish。缺失进程句柄不推断退出；已封存结果恢复只重交不重发，缺封存的已退出执行保留 interrupted。当前仅显式独立 Python transport fixture，模型费用始终零，不证明记忆收益。

任务评分必须使用 stage input 绑定的 scenario contract，不回退当前默认值。评分 seal 绑定确切 grade input、task capture 和评分内容 hash；已退出但未封存的任务保留可用原始流和 hash。非 UTF-8 修改保留原始字节身份、拒绝当作源码正文；不能让公开 diff 解码错误阻止失败提交和恢复。

所有独立 worker 的外层 sandbox 拒读 Codex auth 文件和本机外部 agent 凭据目录。无敏感信息 fake-home 探针发现原 profile 未拒读该路径；修复不授予 CLI 放宽权限。当前 `role_v0.authentication_policy` 为 `isolated_auth_not_qualified`，真实 canary 在启动子进程前拒绝；需另行资格验证不会把凭据暴露给受测工具的认证传输。不得因 CLI 需要认证而允许 worker 读取 auth 文件或把认证/代理秘密传入工具环境。

受测 transport 和新/旧生产评分共用过滤环境，仅继承 PATH/HOME/USER/TMPDIR/LANG/LC_ALL。macOS CoreFoundation 必需的 `__CF_USER_TEXT_ENCODING` 按当前 uid 生成，不继承任意原值；Python user-site 与 bytecode 固定关闭。凭据、代理、agent/controller 接口及会话身份不进入 worker 环境。fake-home 的目录身份先规范化，避免 `/var` 与 `/private/var` 别名使拒读规则失效。

角色阶段接入按 `docs/ROLE_STAGE_CONTRACT.md` 和 `configs/role_stage_v0.json` 执行。StageInputs 在调用前绑定角色 packet；extractor/L2 只开放声明来源，research/shadow 只开放同角色历史及自己的 memory runtime。显式 Python fixture 是当前唯一 actor 模式，无自动重试或正式收益资格。沙箱拒读控制账本整个父目录、全部 scope store 根及输出根，不只拒读 capture 子目录。主库角色的 raw/materialization、source/usage、snapshot 与 stage finish 同事务提交；独立 shadow 库先提交确切结果，父库发布绑定 receipt。中断恢复重用 child receipt，不重复执行；父发布前结果不能作为完成阶段输入。跨库不声称单个 SQLite 事务原子性，必须用可重放发布协议并验证两个提交间 crash。

独立角色输入、渐进来源读取与 Codex exec 原始传输按 `docs/ROLE_CONTRACT.md` / `configs/role_v0.json` 实现。准备 packet 不调用模型；模型调用的 canary 必须针对确切 packet 另行授权，category 始终是 qualification_canary，不能凭结束时 usage 或自填布尔值转成正式比较。正式入口仍需完整隔离、tokenizer、硬预算和 proof 验收。

角色结果接入遵守 `docs/ADMISSION_CONTRACT.md` / `configs/admission_v0.json`：执行前登记 exact packet、预算和角色；结束后从原始 JSONL 重算结果，不信调用者的 success/usage。extractor、L2 和研究提案按当时实际输入视图校验并与原始记录在一个事务中提交。诊断数据保留 qualification/infrastructure 类别，不能进入正式 epoch 或充当冻结证明；来源读取只证明实际披露区间，不证明语义。

实际 Codex 命令的外层 write isolation 必须与隔离探针一致，不能只在 probe 上禁止外部写入。诊断记录绑定控制目录及 exact sandbox profile/CLI 参数；发现旧包使用不同写权限时撤销该包的执行资格，保留原包，不以旧批准执行修订条件。

角色 packet 的授权身份同时绑定控制端源码和全部注册资源的内容 hash。只有 prompt/config hash 不足以区分执行器权限修订；任何执行器修订都必须改变 packet 身份，不能让旧批准自动用于新源码。

控制端读取受测工作区也须遵守隔离：逐级使用不跟随链接的目录句柄，只读取有界的普通文件；符号链接、硬链接及特殊文件不能成为 patch、source 或快照。工具 inspect 不能回读可被受测者替换的 SPEC/sample 文件，改为使用会话开始前冻结的控制端副本。评分前后均检查工作区，拒绝非法文件时保存失败与原始传输，不收集其内容，不丢弃该槽的分母。工作区只允许控制端创建的两个 socket 路径暂时存在；异常文件不能以忽略缓存目录的方式绕过检查。参数与假设见 configs/workspace_v0.json。该检查是本机隔离验收，不是完整恶意攻击防护证明。

S6 的配对判断与交替状态机合同见 `docs/EPOCH_CONTRACT.md` / `configs/epoch_v0.json`。受控 fixtures 的 simulated 迁移不算真实进化，尚无 formal proof 接入或整体冻结资格。

2026-10-08 开始按计划实施。S1/S2 的具体合同见 `docs/KERNEL_CONTRACT.md`，执行默认值与注册空间落在 `configs/kernel_v0.json`。本配置只定义控制端合同，不表示 live 授权或 K 已冻结。代码、离线测试和实现状态须分别记录；尚未完成的 S3–S8 不能因局部检查通过而标为验收完成。

2026-10-08 新增实施规划，当前实施中，尚未冻结。实施顺序和验收关卡见 `docs/META_META_PLAN.md`；新假设、预算与待定参数先登记在 `configs/meta_meta_plan.json`。该文件是规划配置，不能交给现有 execute；原 v1 配置与实验记录保留。

本计划以《Agent记忆最佳实践》末尾定稿（讨论来源副本第 213–242 页，前文第 171–212 页修订 context pressure 与历史回放）为边界：系统状态为 K、M population、T population、payoff matrix P、不可变研究历史 Hresearch 与研究记忆 Mmeta。K 固定接口、隔离、验真、变异规则、生命周期、预算、日志与晋升程序。Evaluator 在已注册 Scenario DSL 内提出一个挑战变异；Memory Proposer 在共享 runtime 的已注册配置内提出一个机制变异。两者都不是最终 judge，都不能写任意 Python、修改 verifier/gold/hidden seeds、改变模型或扩大预算。拒绝与空提案保留为有效结果。

V0 固定 observable raw history、canonical ExperienceCard（brief、detail、evidence refs、time、outcome 及版本外壳）和 extractor；允许组织、单层 INDEX、检索、加载、credit、反思/失效策略在注册空间内变化。目标 agent 仅自动获得预算内 brief，并能 open_memory(id) 展开 detail；v1 的任意 list/search/source 不直接成为 V0 解题接口。原始来源由固定 extractor/受限反思阶段读取，不能通过解题工具绕过披露预算。原经验和派生 INDEX 保留不可变版本与可解析支持链。V0 全部从 canonical cards 与 usage events rebuild，不继承其他臂派生内容；write/schema、动态树、递归抽象、新 primitive 与复杂历史模拟留到新版本。

测试以独立 Codex 会话的 interactive scenario 为单位，定义为初始状态、确定性 user/environment policy 与固定 verifier；重跑 agent 动作，历史 CoT 不要求。构造测试主导选择，Recommand（本机项目 `/Users/l/Project/Recommandation`）采用 checkpoint → learning → interference → target 的最小因果链作外部校验；checkpoint 只恢复 state，不泄露 lesson，依赖旧 agent 行为的反馈不能无条件重放。重要场景有 clean/pressure 配对和无相关记忆、旧规则失效、迁移及反例。共享 canonical corpus 的组件对比与各臂独立学习的端到端确认分别报告，不能声称后者的实际经历逐字相同。

Memory epoch 冻结 T，Evaluator epoch 冻结 M；frontier 同时仅一个，mastery 或 plateau 触发换题。已掌握题转入 regression，M/T 从 active 退出后进 archive，历史不删除。挑战反馈采用 validity、oracle solvability、elite success、disagreement、novelty、coverage、learning progress、cost 的向量。筛选不授予晋升，确认要满足独立验真、回归与留出条件。Mtask 冠军不能自动替换 Mmeta；固定同一研究历史，通过独立 shadow 对比后才能晋升。evaluator/proposer 输出、自报告、实际读取和 verifier 结果分别保存。

“快速、小规模”是可验证的运行约束：单字段提案、active/调用/时间/token 上界、自动冻结和报告、精确条件缓存、分层筛选、无需逐轮修环境或搬移记忆、中断恢复与有限人工步骤。“可以明确结论”要求真实执行的增量、独立评分和预注册的好/坏/不可区分/证据不足规则；不能为了快速跳过回归、失败分母或独立确认。具体候选参数见规划配置，须在 live 校准与冻结前核定。

“没有任何问题”的冻结验收定义为：声明支持范围内的所有必需关卡有证据、全部已知阻塞缺陷关闭、独立复核完成、可更换策略组件且外层不变、真实独立会话完整链路通过。有限测试不支持绝对无缺陷承诺；未知、缺失或未核验不得记为通过。记忆收益及真实项目外部有效性单独验收，不用基础设施自测替代。

最终冻结须分别记录 K 的实现/组件注册表/verifier/决策程序/默认超参数、可变 evaluator/proposer/M/e 版本与单次实验快照。K 通过必要关卡后固定；此前用受控提案和已授权的 live canary 验证调度与测试辨别力，此后开始正式迭代 evaluator 和 memory proposer。证明 bootstrap 收益还须实际观察 e0 → M 改善 → e1 → M 再改善，并独立确认研究生产率；不能用受控演示代替。核心缺陷通过明确的新版本升级处理，不修改历史冻结物。当前 v1 的 source_hashes 包含全部 docs 与 memorylab 源码，本次文档更新会使旧计划的当前源码校验失败；旧计划作为历史保留，禁止修改 lock 绕过校验。细分冻结范围与从快照重放属于本计划待实现内容。

## 研究问题与边界

检验“先前执行/反馈形成的经验是否改变后续行动并提高完整任务质量”，同时测量读、写、反思及维护负担。不能用记忆数量、引用次数、自评分或一次任务成功替代收益证明。不能预设分类树优胜。该实验评估工程任务中的 agent 熟练度，不直接评估股票研究收益，也不把记忆当作整个 Recommandation 不收敛的唯一原因。

第一阶段使用依据真实项目故障类别构造的可运行小项目：提交/恢复链、精确授权修复链、时点证据链。它们是**历史衍生的合成案例**，并非生产任务或真实市场标签。每个任务需要修改生产入口使用的代码，输出经过独立行为测试。当前公开合同给出了完成任务所需信息；无记忆臂也能解题，避免设计成强制依赖记忆的问答题。

## 本阶段范围与接口

本阶段不选机制变体，也不开展机制比较。只提供 `off`（无跨任务经验）及 `reference`（用于验收接口的最小顺序存储）两种运行模式。reference 不是候选赢家，也不代表用户提议的树状机制。

后续机制通过 adapter 接口提供：自动 brief 选择、list/search/read、经验提案校验/提交、快照及统计。排序、分类、抽象、评价、反思触发的具体方案均后续决定；控制端不能把策略偷偷写入所有受测 agent 的共同指令。

## 经验合同

每条：稳定 id、version、类型路径、level、brief、content、适用条件、限制/反例、来源引用、support_ids、links、supersedes、产生 episode。任务类型提示由控制端生成，不读取评分答案；经验类型由提案提供。当前只检查路径形式与支持关系，语义分类需独立审查，分类策略留到后续。

原子经验必须引用实际任务/反馈/执行轨迹的 source handle（带 SHA256）。反思器可读取历史原始情境，不能仅获得最后答案。抽象经验必须至少有两条下层来源，层级严格递减，其类别必须是被支持经验类别的祖先或相同类别。跨类别推论放在共同祖先，links 可横跨类别。拒绝悬空引用、支持链循环、自支撑和不合法路径。支持关系绑定下层经验的不可变版本，历史版本可以按 id+version 读取。引用校验只证明出处可解析，不能自动保证语义正确。经验修改生成新版本，旧版保留；只有明确 supersedes 才使旧版退出活动读取。原记录与模型面向的有限视图分开。

接口只规定完整 brief 预算、渐进读取预算、不可变版本、来源与行为关联证据，不规定重要性算法。reference 使用最近 brief，仅供流程自测；任何改进方案都需未来配置和单独冻结。

agent 的 `memory_uses` 自报告与实际读取分别保存。外部任务结果与已读取的经验可关联，但本阶段不计算重要性，不把关联当因果效用。来源校验只证明出处可解析，语义正确性由独立盲审负责。

## 任务与时间安排

三条轨迹，每条 5 个 episode：首次学习、同类再次学习、变形迁移、跨类别/术语迁移、合同变更及旧经验反例。每个 episode 重置代码快照；仅同轨迹的经验延续，避免把上一轮已修好的代码误当记忆收益。每条轨迹完毕不跨臂/跨重复共享记忆。保留首次学习任务结果，但主要效应预注册在 episode 3–5。

完整任务库为 3 轨迹 × 5 episode。流程试跑可选择 1 轨迹或限定单任务；预算清单提前显示解题和可能的反思调用数。本阶段只跑离线环境验收，不启动正式模型实验。后续选定机制后再冻结比较规模、重复次数和入围规则。

先运行无记忆试点校准：若迁移任务基本全过（>90%）或几乎全败（<15%），标记天花板/地板；修改任务后产生新 suite hash，旧数据留存，不能边看赢家边改评分。v1 提供固定种子的隐藏变体、噪声、嵌套/重命名结构、原入口验证及相反授权反例。不能把同一轨迹中的测试断言当作独立统计样本。

## 公平性与隔离

所有臂固定同一明确模型、reasoning effort、CLI 版本、每会话 timeout、command 停止阈值、解题预算和任务生成 seed。反思成本额外记录，未来若采用同总成本对比需另行冻结。命令超过阈值时停止进程，超额尝试也留在原始记录。配置中未来加入的臂顺序按重复随机打乱，轨迹内部顺序不打乱。解题和可选反思均采用 fresh Codex exec，不继承本聊天，不创建用户侧新聊天。反思器读取本 episode 的原始 trace、解题产物和有限反馈，再输出经验提案；不读取评分期望值。

控制端保留评分器/期望结果/其他臂记录，受测 workspace 位于临时目录。macOS 外层 Seatbelt 禁止读控制端、项目目录、全局 memory/history/AGENTS/skills，禁止探查进程；CLI 内层 workspace-write 禁止写出工作区。忽略用户配置、规则，禁用全局 memories/apps/plugins/browser/multi_agent，关闭 web；不复制或打印 auth 文件。受测 agent 通过 Unix socket 向控制端请求经验，不能直接读全经验库。每次读取内容、字节和延迟都由控制端记录。运行前执行拒读/拒写探针；探针不通过就拒绝正式运行。正式 runner v1 仅支持此 macOS 安全边界，其他平台不能自动降级。

OS 隔离不是恶意攻击基准的完美防护；本实验没有做红队安全证明。代码 grader 也在隔离子进程中运行受测代码，期望答案留在父进程。读当前隐藏测试输入是行为测试正常组成，不能因此看到其他用例的期望答案。

## 指标与决策

1. 完整任务成功：所有隐藏行为组通过，公共入口可运行、保护文件未改、final 格式与行为一致；分母为计划任务，超时/模型失败得 0，尚未执行显示 missing，不混成成功率。
2. 细分质量：生产入口、合法边界、拒绝非法动作、恢复/跨周期、不适用经验下的正确拒绝。保留每个 test-group 分数和失败码。该分数是任务诊断，不扩大统计 n。
3. 学习：后续比较可计算 episode 3–5 相对 baseline 的配对差；当前保存相应分组，同类错误重复次数、规则变更任务错误率、近/远迁移差、首次学习与后续趋势。v1 报告分组失败复发；“卡住”记录重复 command、timeout、修复/调用次数，不猜测内在心理状态。
4. 总成本：Codex 原始 input/cached/output token、wall time、命令/工具次数；solve/reflection 分开并汇总。total_tokens 不把 cached input 再重复相加。无法得到费用时显示 null，不猜美元。字节只标 bytes，绝不冒充 tokens。
5. 记忆负担：自动 brief 字节、list/search/read 返回字节/次数/耗时、生成与拒绝提案数、活动/历史库体积、版本/supersession 与人工去重审查、未读而声称使用的比例、overflow。完整 CLI input token 包含反复序列化上下文，不等于独立经验 token。
6. 语义盲审（评审表已提供；需独立人工完成）：来源是否支持规则、适用条件是否完整、过度泛化、矛盾/陈旧、抽象质量、检索噪声、真正改变了哪一个动作、验证依据是否足够。评审表及证据包使用匿名 trial 编号；原始映射另存，机制组织形式仍可能被猜出，所以属于部分盲审；强结论需用户或另一个独立审阅者复核，受测 agent 自评不是最终 judge。

不做任意加权总分。先看完整成功和严重违规；再画/列成本与效果的 Pareto 比较。后续比较应按轨迹配对，展示所有 seed；小样本和不足的独立轨迹不能输出貌似稳定的区间。成功率同等时偏好负担更低、来源/维护质量更好的机制；无稳定增益则明确“无法区分”。任何后续比较不纳入基础设施自测或 scripted-agent 数据。本阶段报告描述性指标，不实现或选择最终排名规则、显著性检验与胜出阈值。

## 留存与可复现

任务生成 seed 只控制任务数据，不能保证模型推理逐字可复现；后续需要独立重复。CLI 输出若未披露实际后端模型身份，报告保留 requested model，resolved model 记为未知。

prepare 冻结完整配置、protocol/source/suite hash、seed、随机顺序和预期 trial 清单。execute 校验冻结文件，防止测试中换规则。每任务持久化 prompt、自动读取快照、原始 events/stderr、final、原始 workspace 前后 hash、独立 grade、memory requests、reflection proposal/验证/库版本。状态标志只有在上述结果写入后才提交；中断后保留 running，不将其推断为完成。正式运行的失败不自动抹去或重新算为未发生。

## 真实项目外部有效性

环境自测后，要加入至少 2–3 个从未用于制定机制的真实冻结 Recommandation 请求/代码快照，或公开真实 issue，并在独立任务组上确认。必须审核复制范围、保留匿名输入和生产入口、外部 I/O 改为固定 replay，独立验收覆盖后继消费。真实 snapshot adapter 可以复用 runner/隔离/记录；v1 没有把整个生产项目及隐藏凭据搬过来。没做这一步，结论范围只能是本历史衍生小项目。

原生角色缓存核验先经过既有 RPCAdmission，再检查父 publication seal，使原始 RPC 漂移保留精确拒绝码。中断后的当前退出证明必须绑定同一 pid/pgid/start_time；ps 的“已不存在”或“PID 已复用”原因可随时间变化，不要求该衍生原因和历史字符串相同，但不能接受仍运行、缺句柄或不同句柄。

## 确认计划与统计误判范围（待冻结前校准）

每个 memory epoch 的质量判定 family 预留最多三个 task-memory 比较与三组双研究角色 shadow，共九个比较。每个比较报告更好、更坏、质量非劣效三类命题；alpha 在所有预留比较和命题间预先均分，未使用槽位不能在看到结果后重新分配。成本结论只描述本次冻结 cohort 的实际总成本节约，不冒充未来平均成本推断。5% 范围是单个预声明 memory epoch；不宣称无限 bootstrap 历史的全局 5%。

统计确认前先提交不可变 comparison_plan：固定 epoch/focus、candidate/baseline、研究 history（shadow）、各目标的完整有序 unit IDs 和阈值合同。fixture proof 必须在计划 receipt 之后提交，并绑定同一 plan；结果列表必须与计划逐项对应，失败/缺失保留单位，不能延长样本或删行。一个计划只消费一次，换请求 ID 也不能重复；完全相同请求仅重用原 receipt，不产生进展。计划注册即保留单位，跨确认、shadow、mastery 与新 epoch 不释放或改名复用；真实单位的源/seed/cache 身份须由未来 formal receipt 构造器证明，fixture 名字登记不等于真实独立性。

本修订仍是 scripted_acceptance；formal proof 和模型权限保持关闭。7–14 上界与预算/辨别力仍需一致性校准。更严格的 family 下，小样本可能不能得出结论，这属于证据不足，不通过扩大 alpha、降低阈值或选择有利样本强制晋升。mastery 和 challenge 的经验阈值是预声明调度规则，不冒称获得了这些质量假设的统计显著性。

## Collaboration 消息交付和终结修订（2026-10-08）

先冻结实际 spawn/followup 的完整工具参数；服务生成、发送预留、实际工具返回的交付确认分别记录。未确认的来源读取不授权 citation 或 usage；发送预留而返回未知时不得自动重发。下一条角色原文必须有上一条回复的交付确认，错误回复也遵守此规则。场景服务在生成回复时 delivered=false，实际发送确认单独不可变。

绝对 wall deadline 或 turn 上限触发 interrupt_required；controller 使用实际 interrupt_agent 返回创建终结 receipt。终结保留绑定、已收原文、未发送回复和全部计划分母；本次有限资格 run 停止，不重派同槽，不伪造模型 final、空 extraction 或研究提案。未完成 solver 明确失败，研究/维护失败不可 materialize。恢复须使用同一记录且精确幂等；派发/交付不确定保持 needs_attention。

## Collaboration 本机角色服务修订（2026-10-08，资格验收假设）

实际工具返回只含 task_name 或空返回，必须保留原始值；task_name 可作为原生 collaboration 的实际 target handle，规范化不伪造 UUID 或模型 metadata。attempt-003 的人工长回复转发发生原文偏离，实际一次受测会话失败后通过真实 interrupt 返回终结；缺少 exact ack 的代码请求不提交，全部原文和计划分母保留，不能作为记忆收益证据。

为消除人工搬运，研究角色仍由 root 以 fork_turns=none 启动独立 collaboration 会话；受测者仅允许通过登记的本机 role client 请求控制服务，禁止其他工具/文件/global memory/评分端/其他臂。client 位于单角色独立公开目录，不暴露 controller/ledger/gold 路径或 provider 凭据，只有该角色的临时 IPC capability。它把实际模型命令输入原文送给 controller，生产服务与数据合同不改变，stdout 原字节返回同一角色。直接角色工具权限仍是指令约束，不能冒充 OS 隔离资格。

生成、发送预留、认证 client 的确切接收/输出确认分别持久化；确认绑定序号、binding、request 和 reply hash，只有已确认数据可进入后继读取和 usage。client stdout confirmation 不等于模型上下文未截断或理解，provider tokens/cost/resolved model 保留未知。禁止把 IPC receipt 伪造成 collaboration.followup_task 返回。socket 崩溃或发送未知不自动重试；deadline/turn 上限继续用实际 interrupt receipt halted。每个新角色独立 client capability，拒绝跨角色/错误序号/超限/修改源码输入。

本机服务先做实际独立进程的成功、错误、跨角色、掉线、来源读取、提交/恢复与原字节正负控制，再启动真实 canary；全量回归和已有正式隔离/预算/辨别力/H2/独立复核关卡继续保留。此路线只改变资格测试的传输，不用失败和 unknown 换取胜出或冻结。

## IPC producer / observable error feedback 修订（2026-10-08）

attempt-004 首个真实 solver 独立生产评分 full_success=true，protected 违规零；10 次请求包含 4 次接口结构拒绝、2 次 engine 修改与 2 次 check。原实现的 IPC 文案未给完整 JSON 请求形状，且 solver source 生成把无 wire 的错误回复写成空字符串，非 scenario/open 的公开错误未进入 extractor 输入。这是资格验收缺陷，不以任务成功掩盖。

公共请求 grammar 必须与各 producer/dispatcher 的嵌套字段严格一致，所有角色有完整 JSON 示例；finish 包装仅用于 client，native agent final 是 result 对象匹配 output_schema，二者通过真实结果相等核验而非猜测 envelope。经验来源包含每条此前实际提交的公共服务 request 与实际公开 reply（有 wire 时原字节、无 wire 时规范 JSON）；写入的中间代码与拒绝回复也保留。未知私有推理原文不传播；未解析 request 只给已保存的规范拒绝描述，原始 raw 留在控制账本。没有实际交付 ack 的服务不能进入经验来源。

保留 attempt-004 的真实成功与接口错误记录，但存在 source loss 的配置不继续做机制比较；修订后新冻结资格包单独验证。不改写老输入/评分或把旧结果当新源码执行。正式 token/OS/context/统计/H2/独立复核门槛保持未通过。

## IPC 初始包原文交付修订

长初始包同样禁止人工重新编写。冻结派发参数只含该角色固定 client、公开包的 SHA256 以及获取指令；独立会话首先发送 {"op":"packet"}，收到 controller 中冻结的完整公共 packet 原字节。操作受原有身份、序号、预留和 stdout ack 合同限制，只返回自己的公共 packet，不返回 internal、账本、评分答案或其他角色记录。该实际公开输入与后续服务一起保存，不能以文件存在代替接收。首次交付算入原有 12 次请求上限，不增加 budget，manual 历史路线不改变。先验证确切包/身份/额外字段拒绝，再冻结真实运行。attempt-005 未启动任何模型，原派发预留与缺失分母保留并撤销执行资格。

## 真实初始调用空 stdin 反例修订

attempt-006 的真实角色执行 client 时遗漏 stdin，服务收到空 raw 后拒绝，角色遵守不重试；真实 interrupt 后 planned=5/terminated=1/missing=4。原工具调用和输出由角色诊断原文返回，保留，不计 solve 或收益。初始 bootstrap 必须附冻结的完整 exec_command 参数与有引用的 heredoc 请求，不仅给 argv 和自然语言；不依赖角色猜测 stdin 接口。client 在锁/状态写入/网络请求前拒绝空 stdin，明确 no request sent，状态保持 ready；这不是连接未知时可重试许可。专项先验证空输入无发送/无状态推进，以及完整冻结初始命令确切拿到自己的 packet，才能新冻结真实会话。旧全量继续绑定旧代码，不覆盖修订。

## 独立角色启动放行与使用记录修订

attempt-007 已有两次真实任务成功、一张真实提取卡；第二个提取角色在实际 spawn 返回与 bind 之间先请求 packet，30 秒 client 等待超时，随后真实 interrupt halted。完整旧分母与成功/失败/未知发送保留；没有重派该槽、没有继续比较。启动不依赖 controller 能抢在角色工具前完成 bind：每次新角色先不调用工具，只返回含确切 packet hash 的 awaiting_controller_binding；controller 从实际 native 返回保存就绪证据并完成实际 handle 绑定，随后先登记唯一 start intent，再发送确切 CONTROLLER_BOUND 及 packet hash。只有已有 start intent 才能进入 IPC 服务；实际原生 followup 的空工具返回按原样另存 ack，不伪装非空返回。放行未知不重发；已完成的报告必须核对放行确认。额外就绪推理回合明示计入研究负担，仍不宣称 provider token/cost。绑定时起的绝对 deadline 不因放行延长。

solver 与 researcher 统一将真实自动加载和已确认展开保存为 usage；solver final.memory_uses 仅作为 claimed_use。去重且只能关联当前公开已披露的 exact immutable memory ref；未披露声明与未展开声明单独记录，不能获得行为信用。声称有用、展开、独立结果互不代替；无独立行为对应证据绝不生成 behavior_link。该路线的 outcome_ema 若缺行为对应记录，得分保留零且不声称信用学习。原始 final 保留，非法 final 仍由 production verifier 判失败。先做身份/哈希/提前调用/未知放行、声明去重/陌生 id/未展开及真实研究展开记录等反例，再新冻结真实后继读取。

## 研究记忆的引用可表达性修订

解题者保持 id-only、无原始来源访问；研究角色的 namespace runtime 在自动 brief 与实际 open detail 内增加 exact evidence_ref，仍只给已披露的自身卡片/INDEX 版本。完整序列化后的引用 metadata 计入同一读取预算，不另开免费映射；INDEX 不冒充可引用的 canonical card，研究者须展开支持卡并引用该 exact card ref。producer、服务、RPC frozen snapshot 和 readonly replay 均从冻结 projection namespace 与同一个固定 runtime policy 派生此行为，不靠调用者自由布尔授权。普通 task namespace 不披露 refs。提案只能引用其 output_schema 允许的来源类型；引用可解析不等于已证明语义或收益。固定 source prompt 改为登记服务的确切 read_evidence JSON grammar，不再要求未登记的 read_source.py 路径。先验证研究引用可合法提交、solver 无引用泄漏、引用字节预算整体拒绝与重放一致，再冻结。


## 多操作语义与评分覆盖审计（2026-10-08，新独立候选）

在继续机制迭代前，对已经登记成功的真实提交进行独立只读补充审计。原始工作区、分数、会话与来源均不修改；在新的隔离工作区执行确切代码副本，先冻结输入、独立预期、来源 hash 与实验假设，保留所有结果。repair 多项编辑按列表顺序消费当前工作副本，父坐标必须在该项应用时存在，任一后续项失败仍整体回滚；需覆盖祖先替换后子项编辑、子项后祖先替换、新增父项再修改子项、原有父项被替换为标量后子项失败、对象/数组和转义坐标组合。覆盖缺口是资格问题，不能用旧 full_success 或卡片自评作为通用正确性证据，也不得向正在受测的角色传回评分答案。若审计发现缺口，先补协议/配置，再修改真实生产 verifier 的探针与独立正负控制，最后验证，不修补受测者的提交或改写历史成绩。


服务任务的同类多记录合同：请求内 seen 与跨请求 committed 分开。某 id 在本请求首次出现即纳入 seen，即使因金额无效或依赖不可用而暂缓，也不能由后续同 id job 再次处理；暂缓不纳入 committed，下一独立请求可以重试。先处理旧 committed/当前 seen 的 skip，再验证 amount，再验证本次观测服务，不凭旧观测认定新健康。生产 probe 和独立正/负参考都须覆盖暂缓后重复、交错状态顺序、旧提交、下一请求重试与版本条件；不通过修改实际受测提交来消除失败，不把旧参考标为正确后跳过反例。


## 轻量本机 Codex 飞轮（2026-10-08）

用户本轮授权以 Codex 驱动有限真实迭代，关闭记忆后重新开始；不复用污染的 attempt-009。新增 native app-server 观察路线，使用安装的 CLI 与既有登录，控制端不读取、复制或记录凭据，不使用外部 API 客户端。每角色 fresh ephemeral thread；同一角色的服务请求在该 thread 内继续，角色之间无上下文继承。关闭 memories、host skills、apps/plugins/MCP/web、多 agent 与工具执行；空临时 cwd，不读取项目指令。公开包、受限服务、canonical 提取、版本支持链、typed 单字段提案和独立生产评分复用现有 Loop。真实 thread/start/turn/start 返回直接记录为 native receipts，绝不伪装 collaboration spawn/followup。未知交付或进程中断停止且保留分母；不自动重发。

假设和预算先落在 configs/lightweight_codex_v0.json：固定两次 proposer、一次 evaluator、四组五 episode 学习链；正式晋升关闭，质量仅作描述性比较。研究角色可 noop。启动先做一会话 canary，核对 fresh identity、无 instructionSources、显式 memory disabled 和工具禁用；只证明公开的本机配方与实际观察，完整 provider input 未披露时仍标 unverified，不能声明正式隔离或统计收益。原始 RPC、客户端 usage、耗时、任务/记忆快照和独立评分保留。无相关记忆、迁移、干扰、目标和旧规则反例由冻结链覆盖。

最终交付包含可重跑 driver、冻结运行包、经实际运行的机制配置和结论。相同任务满分或有限样本没有可辨增益时选择负担更低的合法机制作为工程默认，标明收益未证实；不宣称协同进化改善、真实历史/项目有效性、K 正式冻结或 Mmeta 晋升。单个失败先留存，修复需新冻结版本，不在旧计划中换规则。

Native canary 修订：userMessage 是真实 app-server 的正常输入事件，允许该类型，不放行工具类型；attempt-001 的拒绝记录保留。控制端固定使用已有离线验收采用的 bundled Python 3.12，系统 Python 3.9/SQLite 的只读 WAL 行为不能作为通过证据。读报告必须在所有 writer 句柄关闭后进行。

Native 连接修订：现有本机环境提供 HTTPS_PROXY，旧 canary 清除代理后停在 turn/started；新版本仅让可信 CLI 继承该连接变量，不记录代理值、不向受测工具传播。明确禁用已配置的 computer-use/node_repl MCP，而非假设空表会清除 TOML 合并项。attempt-002/003 的中断原字节和进程退出保留，不复用其会话。

有限预算校准：attempt-004 的首个真实 solver 独立评分成功，38.49 秒，客户端累计 42,731 tokens（含 repeated context 和 21,888 cached input）。20 个计划任务加提取/研究会话会超过原 600,000 观察阈值；旧校准 run 停在 1/20，原分母保留。新冻结执行包将观察停止阈值预注册为 1,800,000 tokens，最多 64 fresh sessions / 3600 秒，不增任务数或依据赢家改变评分；该上界只是客户端观测护栏，不是 provider 硬 token 限额。配置校准先于机制/挑战提案结果。


## 轻量批次连接失败后的独立续批

attempt-005 在完成 10 个任务、一次 proposer noop 和一次 evaluator 合法 rule_change 提案后，挑战首个学习会话出现 responseStreamDisconnected / workspace routing discovery failed；客户端内部进行了 reconnect，最终 failed，无模型 final 和 usage。旧失败计 0，其余计划保留 missing，整批 halted，不删除旧原始记录、不重发该槽。新版本在 CLI provider 中显式关闭 request/stream retries 和 websocket 连接；连接错误第一通知即停止。

为完成剩余一次研究迭代，新批次独立冻结：使用 seed=802、已产生的挑战配置，schedule 仅 challenge-1 → proposer-1 → candidate-1；这不是原失败单位的恢复或统计补样。预算为 900,000 客户端观察 tokens / 1800 秒 / 32 sessions，旧已观测用量约 891,515 单独报告。仅通过 CorpusStore initial_checkpoint 将 proposer/evaluator 的各自研究来源与卡片转入对应 namespace，保留原版本、来源库 hash、映射和 cutoff；不导入任何任务臂的记忆、代码、评分器或本聊天。原库只读，snapshot 在私有 SQLite backup 中创建；合法旧提案作为新初始配置的来源，不冒充正式晋升。旧批次 10 成功 / 1 失败 / 9 未完成与新批次分别列出，不能拼成 20/20 或声称收益确认。

MCP 配置诊断修订：attempt-006 在 initialize 前退出，错误为 mcp_servers.computer-use invalid transport，真实 turn 数零。空 mcp_servers 覆盖与随后子字段覆盖的组合不可假定保留 transport；新版本只禁用已经存在的具体 MCP 项，环境工具仍全部关闭。旧失败和未执行分母保留，新 continuation 从同一只读 origin 冻结，不复用其会话。

无模型 initialize 诊断证实本机 CLI 禁止覆盖 reserved built-in provider openai；官方通用 provider 字段不能直接用于该保留 id。续批采用 CLI 原生的独立 named provider（requires_openai_auth=true，不指定外部 URL、不读取 key），同一现有登录与 requested model，显式 websocket=false / request retries=0 / stream retries=0。仅空 mcp_servers={} 的配置初始化通过，移除无 transport 的子字段。实际 provider 解析身份和 canary 另行记录，不声称跨传输成本因果相同。attempt-006/007 均在模型 turn 前退出，未覆盖旧失败。

Native 错误通知校验修订：attempt-008 已建立 fresh thread、requested model=gpt-6.1-sol/provider=memorylab-native，turn/start 后客户端校验器对正常通知的 error=null 做了错误取值，真实进程中断且缺 usage 保留 unknown。校验器仅在 method=error 时读取错误详情，正常空 error 不应导致异常；增加该反例。新会话独立冻结，不重发旧会话。


## 观察型飞轮的单单位连接失败处理

attempt-009 已完成挑战四个任务和三个提取，反例单位出现 SSE decoding response body 错误，willRetry=false；该槽真实退出、失败记零，剩余原计划保留 missing。实际连接不稳定不能作为删除失败或反复重派同一单位的理由；资格 canary 的 whole-run halt 也不适合作为长期观察型飞轮调度。

新配置仅在 native observation 中授权：solver 明确连接错误/超时/模型 turn failure 且进程已退出时，单位保持 terminated/0，跳过其 extractor，后继使用此前已有 corpus；不伪造 final、评分成功、空提取或额外证据。CLI 原始 error、退出、服务回复/未知交付和分母仍保留。未知错误、工具/权限/合同违规、研究或维护角色失败仍整批停止。研究输入允许 completed+terminated=planned，公开区分传输失败与代码失败；受测者不因此获得隐藏答案。正式 proof 与晋升仍关闭。

新有限批次从 seed=803 开始，仅 proposer-1 → candidate-1 五任务；proposer 的研究输入使用 attempt-009 挑战的冻结公开汇总（4 success / 1 transport failure），自身研究经验沿用 attempt-005 的 CorpusStore 同 namespace checkpoint。传递的是已授权研究汇总，不向 solver 导入旧任务臂记忆/代码。本配置上限 500,000 observed tokens / 1200 秒 / 16 sessions；不会重跑旧失败单位，也不合并为完整无失败实验。


## 轻量观察结果与工程默认封存

attempt-010 已完成两个研究角色及五任务/三提取，共十个真实角色；独立原始传输审计与新工作区评分重算均通过。两次 memory proposer 都是合法 noop，evaluator 产生一次规则变更挑战；这证明有限观察流程已经连接，不证明机制进化带来收益。旧失败和 missing 按原冻结批次分别报告，不拼接为无失败试验。

交付 usable_memory_v0 为同一实际运行的 flat/lexical/局部最多四 brief/显式 supersession 配方；不生成未经观察支持的新机制。该文件为 registry 可解析 spec，原始 initial-memory 的 id、版本与源 ref 保留在结果中。原计划及源码快照不改变。主 native 默认配置以后采用已验证的单 solver 传输失败保留零分并继续其他独立单位的政策；修改只适用于下一次新冻结 run，不复写旧运行。结果、使用记录、原始事件、快照、独立复算与机器汇总封存；不发更多模型请求。假设仍是工程可用与因果收益分开，正式 proof、K 冻结、真实历史/项目重放和复杂机制胜出均未获得。

500,000 observed token guard 是下一角色启动前检查的软停止线；attempt-010 最后角色前累计 479,579，最后完成后 526,320，未再发新角色。须披露该超过与重复/缓存输入，不把它当 provider 硬上界或账单。canary 用量另列；失败会话的未知/部分用量不记零。


## 两轮合成保留任务配对试验

用户要求继续快速、小规模实际迭代。本次资格为观察型，先核对并保留上轮全部记录；不由已通过局部合同检查推出系统全部无问题。受测 subagent 仍采用 fresh ephemeral 原生 CLI 会话，避免 collaboration 子会话既往 MEMORY_SUMMARY 污染；不会为编码启动 agent。新假设与固定预算落入 paired_retention_v0.json 后再实现。

新任务类别 synthetic_project_preferences 单独报告，不伪称历史/真实项目。公共学习记录包含项目路由约定和另一个项目的不同约定；由真实 extractor 生成 canonical 卡，有 exact source refs、适用性与例外，不注入评分答案。五种配对任务为重复、跨字段迁移、当前规则显式替换、无需记忆、其他项目反例。前两种有意只在学习记录披露私有约定：memory-off 允许诚实 needs_context，完成率与臆测错误分别评分；该设计仅回答保留先前约定的能力，不回答一般编码收益。两臂公共任务、模型与 effort 相同；共享冻结 acquisition，off 的 runtime.enabled=false 且任何展开拒绝；新会话不继承学习聊天。无外部 API 客户端、无全局记忆写入、无原项目操作。

先冻结两轮十个任务对、固定顺序平衡 on/off、规则变更/迁移的四个有限模板、精确公开输入与控制端 gold。第一轮五对 → proposer → evaluator → 第二轮五对 → proposer，研究角色消费有限公开结果与自身经验。提案复用 Registry 单字段验证，mutable memory 只限 loader.global_briefs、loader.local_briefs、invalidation.type；mutable scenario 只限 transfer_distance、rule_change。修改在第二轮实际输入使用，最终未测试提案只能保存不激活。抽象维护关闭，自己的研究提案另由 extractor 提取，不向 solver 转交研究上下文。

solver 可提交最终 JSON 或最多两次公开 open_memory；控制端逐字传回复，不做 JSON 修复。实际 raw/thread/turn/使用、卡与来源版本、任务/记忆快照、独立 grader 进程及只读 raw 重验均保留。已开始未知角色不重派；solver 明确失败计零并继续独立对，研究/提取失败或合同/权限错误停止、剩余 missing 留分母。全 study 用量 guard 包含资格 canary，固定最大 32 会话、240000 observed tokens、1200秒、每角色90秒/3turns；仍是事后软护栏，未知/部分用量不能记零，不拓展任务到赢为止。

描述性工程接收条件：两轮各两个保留任务 on 成功且 off 无法完成，三个保护任务 on 不比 off 多错误；分别报告需求信息与错误断言。没有显著性/总体效果宣称；条件不满足则拒绝新增收益结论。不删除原先成本，另报本次 shared acquisition、任务与研究成本。此接受只针对合成约定的保留及显式覆盖，正式 proof/K/H2/真实项目 holdout 保持未通过。


## 已知提案结构拒绝后的研究收尾

paired-retention attempt-001 的二十个 solver 和前两研究/提取已完成，最终 proposer 真实 JSON 的 kind=proposal，被 Registry 以 role_kind_mismatch 拒绝。旧状态保持 halted，原 final/生产拒绝/费用不修复，最后提取未执行。原研究提示只列出 kind 键而没有明示其值；补充 public schema example 与该角色的确切 memory/scenario kind；提取同时给固定唯一 id 前缀，避免下一角色看不到既有 id 时发生无意版本冲突。模型输出仍严格解析，不修答、不修 receipt。

新独立尾批只包含资格 canary、一个最终 proposer 与自己的 extractor，固定40,000 observed tokens/4sessions/180秒。已明确 terminal、原始 final 已知且生产拒绝确认为 role_kind_mismatch、原二十任务完整时才允许；拒绝未知进程或不完整单位。通过 CorpusStore private backup/fork 仅移植 proposer 自身公开来源、canonical 版本和 tested memory lineage，原库 hash 必须不变。尾批计划冻结原二十任务的公开 outcome rows/hash，solver计划为零；不再运行任务、不向研究者披露 gold 或其他臂代码。旧研究失败、未执行提取与新研究收尾分别报告。

组合接收须原批次原字节审计及二十评分复算通过、尾批真实提案结构接入和来源提取通过，再按原预声明的合成保留/保护条件判断。组合状态不改写为原批次无失败；未测试的最终机制修改不得激活。该有限尾批修订解决公开 grammar 缺口，不允许不断重新派发到拿到特定结论。


## 合成配对观察交付封存

原二十 solver 的 raw/评分复算全部核验：每轮 on5/5、off3/5，off 的两个保留任务诚实 needs_context；三个保护任务两臂均通过，错误断言零。尾批三角色完成，新的最终 proposer noop 合法提交与 canonical 提取通过。共三十 fresh native会话、客户端209710 tokens（含原结构拒绝与两个 canary），actor耗时之和328.465秒。按预声明条件给出有限的合成保留机制接收结论；原批次 halted、一次 role_kind_mismatch 和一未执行 extractor 不改变，组合与尾批分开报告。

交付原 usable_memory_v0 的同一 config（没有机制参数变化）及 portable-memory-pack，来源和示例卡均标 synthetic，不写全局记忆。代码/评分器/有限模板未在看结果后改变；新的104项相关合同验收、两份冻结 raw 审计、二十新进程 grader 复算与来源 refs构成证据。读取报告使用冻结快照；不能把同一五模板的两轮当十个独立真实项目，不能声称正式因果收益、复杂机制优越或泛化到真实编码。系统的公开 research grammar 缺口已经实际修正并验证，仍不作所有潜在流程/权限错误均不存在的宣称。结果、快照和缺失分母封存后不再发模型请求。

## 完整 seed 的小规模机制筛选（2026-10-08）

用户澄清：关闭全局 memory 仅用于会话隔离，不授权把 seed 简化成 flat。旧配对结果不证明完整 seed 或复杂变体优劣。本轮以用户 1A/1B/2A/2B/3/4 为完整基线；用户选择先合成机制比较，真实 Recommand 重放后续，不修改原项目或全局记忆。

在实现之前冻结 seed_variants_v0 配置中的假设：每条叶经验含 brief/detail/真实来源情境（本轮来源明确标为合成事件）；按类型目录与抽象等级组织，特殊 _INDEX.md 是真实 Codex 抽象而非拼接导航，含 exact 下层 refs/few shots，保留原版本。类型注册为父类的子路径，禁止任意跨父类重挂。全局 mandatory top2 brief 依重要性/时间/抽象等级组合取堆 topK，局部最多8依任务类型沿祖先查邻近经验，union 去重，whole-brief 字节预算拒绝须记录。细节只允许已披露 id 按需展开，索引展开允许发现其 exact 下层支持；每角色最多2次展开。使用后的自评 EMA 提供 importance，实际独立任务结果另外保存，不把自评当收益。公开规则变化触发独立 Codex 反思，生成 source-backed 新叶经验并显式替换旧规则；失效下层索引退出，剩余有效支持和新经验由真实 Codex 重建，旧版本不删除。

七臂：off、先前 flat 对照、完整 seed、seed 仅关闭抽象索引、seed 仅改全局排序为最近、seed 仅改局部关联为词汇、seed 仅关闭反思。类型分类由冻结公开任务给定，不由 gold 推断；同任务同模型同 effort/预算，统一记忆服务与自评输出语法。十二合成历史片段通过4个独立 extractor 形成共同 acquisition，三卡一组；抽象臂共享一次预处理索引，之后各臂独立维护。六任务依次覆盖重复、迁移、显式新规则、无相关记忆、异项目反例、使用前一公开反馈的新规则；各case内七臂顺序固定随机化。反馈来自公共任务规则，绝不转发 gold/评分器/其他臂轨迹。flat/off 为整体对照，四 ablation 只作单因素比较，不完整组合搜索。

screening 42 solver 后独立 proposer 在冻结候选集中提名一个 ablation，evaluator 从冻结 near/far 挑战选一；控制端验证提名且不自动采纳无效提案。两角色只见公开分数/自评/成本和自己的来源记忆，永不见 gold。confirmation 固定完整 seed 与提名机制各3个新任务（6 solver），实际消费提案/挑战；研究来源由该角色的反思保存，下一研究会话渐进披露自己的记忆，最终未测建议不激活。最多80 native角色/3turns/每角色90秒/全run1800秒/800000 observed tokens软停止；不重派未知或失败会话，不追跑到正结果。

冻结源码、CLI hash、模型配置、来源/记忆/任务全快照、原始事件与各轮输入输出；独立无模型评分器进程，所有48计划solver包括失败/missing作分母。维护/研究合同错误即停，solver错误计零后继续已冻结不同单位；soft guard触发余项 missing。排名先保护任务错误与完成率，再保留/迁移成功，再成本；若同质量且成本差未达10%，不宣布胜出。仅本组案例描述性结论，重叠模板不当独立统计样本。完整 seed 与 off 的配对收益需历史保留/迁移有效且无保护任务退化，明确 private conventions 是任务设计中的已教知识，不推广为真实项目收益。抽象机制的细节生成正确性、检索、失效与评分分别验收；基础设施自测不能证明记忆收益。

### 机制筛选的准备阶段连接失败与独立新批次

seed attempt-001 的 canary/acquire-0 完成，acquire-1 原生 error=Reconnecting... waiting for network 后实际进程已退出；没有 solver 或臂特定维护开始。旧状态 halted、三会话原始事件、部分共享 acquisition、未知失败用量和48 missing 全部保留，冻结源码只读审计已通过。允许一次新的独立有限批次 seed=1104，重新冻结相同机制/来源/任务与不同臂顺序；不移植任何旧实验臂记录，不自动重发原角色，不修旧状态。此选择发生在任何 solver 结果之前，不按成绩筛选。新批次的描述性结果与旧48 missing分开且同时报告，合并工程分母为96，未启动的旧任务不称为模型任务失败。仍不对统计或总体成功率作推断；未知费用不记零。

## 完整 seed 结果封存与研究输入的任务形态补充

attempt-002 已完成全部48计划solver槽：46生成并独立评分复算、2明确原生连接失败记零，没有missing。完整seed与recent_global筛选均6/6，确认均3/3；off3/6，flat/no_reflection各5/6且在旧规则更新后各1错。no_index与lexical_local各5/6，损失分别为counter与unrelated连接失败，不能归因记忆或把未知费用记零。69原生角色 raw、46评分、51 immutable对象、20维护/研究生产输出映射独立核验通过。原attempt-001的48 missing与1准备阶段连接失败保留，合并工程分母96，不合并成无失败运行。

确认轮迁移与反例使用了新输入；算术是复用的无关对照，不能把三项都称为独立新holdout。seed/native维护记录区分观察到的例子与公开规则规定的未观察分支；本轮可以接收合成经验保留、显式失效及反思更新，不证明真实项目、统计泛化、分类器或大记忆库下的检索收益。近期排序与组合排序同质量、客户端成本差不到预声明10%，保留完整seed，不删除类型树/抽象层，也不宣称后三项机制优越。

末轮evaluator的未激活理由把已用far的确认任务称为shallow。该原始措辞保留，不作为机制选择依据；公开汇总原先只有stage/outcome，未明确测试任务形态，属于输入缺口。下一代码版本先登记研究输入假设：研究者需要实际已用candidate/challenge、机制配置与公开任务（仅public，绝无gold），才能知道挑战已经消费。补充公开字段并做合同检查，原运行不得改写或再次评分到好结果；此修复不改变已测试seed运行/评分/语料，不额外调用模型，不声称已验证修复后的模型研究质量。

## 控制端发布范围（2026-10-10）

控制端仅保留独立 Codex 会话路线；移除其他 controller 的专用入口、三份实验配置、专用测试、编辑器忽略文件及对应协议章节。既有 Codex collaboration、轻量 CLI、离线合同与独立评分继续保留。本次不新增研究假设，不启动正式模型测试，也不重写历史实验记录。

共享来源服务保留非空配额耗尽信封 {"error":"role_source_quota_exhausted"}：耗尽回复计入 wire_bytes，不再增加 source_requests；source_prompt 继续披露配额与耗尽形态。该行为是跨传输的服务合同，不能作为某个已移除实验臂的收益证据。

受测者继续拒读 Codex auth 与宿主配置目录；拒读范围统一为 ~/.config，覆盖其中所有第三方工具配置及凭据，不因删除某个入口而放宽隔离。fake-home 探针使用通用工具目录中的无秘密 marker。

GitHub 发布包含剩余源码、配置、测试、脚本和文档；默认创建私有仓库。runs/、Python 缓存、系统临时文件和本机凭据不进入 Git，原始实验记录保留在本地。README 中本地 runs/ 链接只能在完整本机副本中使用；上传版本中的结果摘要不构成完整可复现实验发布。本次验收以离线完整测试与上传后 Git 提交一致性为准，不证明记忆收益或正式冻结资格。

发布验收使用 Python 3.12。系统 Python 3.9 的离线尝试发现 SQLite readonly_ledger_unsealed 等兼容失败，已主动终止并保留日志、退出码与 source_unchanged 记录，不把该尝试列为通过；同两项只读合同在 3.12 下通过。发布不扩展修复旧 Python/SQLite 的兼容边界，完整验收以新源码身份下的 3.12 记录为准。

发布全量离线检查完成 504 项，其中 503 项通过，一项 RPC 测试仍断言旧版请求配额耗尽空回复。按上述共享服务合同修正该断言：错误信封必须原字节经 RPC 交付、保持 success=false、计入 wire 字节并可重验；另行覆盖字节预算不足时的真实空回复。只修改测试，生产服务不变；原全量失败记录保留，修正后的 RPC/来源/认证隔离专项复测单独记录，不把专项复测冒充新的全量通过。
