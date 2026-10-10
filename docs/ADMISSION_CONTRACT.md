# 原始角色结果接入合同（S2/S3/S5/S6）

退出未封存可使用独立 interruption 接口：核对已注册 attempt/实际退出，保留可用 raw files、source/memory audit bytes/hash（后者未校验），原子提交失败 capture 和 completion，不运行 materialization。未校验披露不能产生 card/usage/提案；缺句柄或仍运行拒绝终结。资格类别保留 unknown 费用，fixture 的真实模型费用仍零。

参数先登记 `configs/admission_v0.json`。这是控制端角色接入，不是正式效果或 epoch 晋升接口。它接通 extractor、consolidation、reflector、Evaluator 与 Proposer 的原始 transport 和实际存储。solver 独立 grade、全阶段调度 driver 与正式 proof 仍需单独实现；不得把本接入成功写成它们已验收。

执行前登记一次 exact packet、角色配置、预算和类别。packet 必须从实际 registry/cards/source 截止视图重新生成并完全相同；任意加入的 prompt、输出 schema、parent、私有字段、未知 source 都拒绝。相同 attempt 不自动再调用；completion receipt 原子绑定原始记录、实际 usage、来源读取区间和实际 materialization。类别只能是 infrastructure_acceptance 或 qualification_canary；真实授权另由用户消息提供，本接口不授予启动权。

结束时只接受该 attempt 的控制端捕获目录。核对 input/start/budget/进程组退出，完整 bounded stdout/stderr 的 bytes/hash/base64 一致，从原始 JSONL 重算 final/usage/停止状态，与 transport 中保存的结果比较。fixture 身份不能被标成 Codex；不接受 worker final 文件、调用者 success/usage、文本 attestation 或自填 live 布尔值。原始记录留控制端且先注册一次 attempt；它证明这次控制端捕获，不能证明 provider 身份或硬 token 界。

来源 audit 按同一 EvidenceReader、同一注册来源和截止点逐项重放，比较完整响应、字节和实际区间。错误、重复、空返回仍计量；实际有文本的合法区间才能算已披露。extractor 的 evidence_refs 限于已提供且有实际披露文本的来源；版本更新/supersedes 限于输入的 active cards。输出 outcome 须与提供的独立 visible_outcome 相容，否则 unknown 或拒绝；不得凭自评补任务成功。研究提案 parent 固定为 packet parent，证据仅限实际输入历史，注册表仍检查单字段与权限。L2 支持仅来自 prepared active/eligible cards，语义正确性保留未证实。

固定 extractor/L2 的真实调用必须绑定显式模型、prompt 和配置身份；缺模型不能因 transport 已结束就形成 canonical 内容。fixture 只在基础设施类别 materialize。DerivedStage 的诊断接入必须解析已提交的 role_capture receipt，而不是接收调用者自行填写 model_calls 或 usage。旧的显式 fixture 入口保留原类型，不成为 live shortcut。

提交在一项外层事务里保存 raw capture、角色验证/拒绝、canonical/derived/proposal 下层 receipt 和 completion receipt。任何 crash 点都无半条 card、半个提案或仅 raw 的完成标记；进程调用已经发生的外部文件仍保留，恢复只重新提交同一捕获，不重新调用模型。raw/cost 和结构拒绝同时保留。失败、未知成本和未完成保持一个已登记 slot，formal/gain/promotion 一直 false。后续正式执行器必须追溯类别及调用来源，不能凭下层结构合法 receipt 接受诊断经验为正式历史。
