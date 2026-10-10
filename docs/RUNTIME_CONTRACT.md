# V0 共享记忆运行时合同（S2/S3）

运行时由固定组件与 M 配置生成。新假设及具体数值先声明在 `configs/runtime_v0.json`；所有行为参数、tokenizer 身份与 INDEX 生成程序进入指纹。当前离线可使用 UTF-8 byte 计数器检查预算布线，该计数器明确不是模型 tokenizer，不能用于正式 token 公平性或晋升。

输入仅为验证过的 canonical corpus、M spec、本 namespace 的使用事件、明确反思结果和固定运行时合同；不能复制旧派生视图。corpus 的 history 保留所有版本；活动叶子由最新版本、withdrawn 与 supersedes 得到。`invalidation=none` 只忽略显式 supersedes（用于测试陈旧记忆伤害），不允许被 supersede 的下层卡片支持活动 INDEX。revision 仍只取最新版本，withdrawn 永远不加载。

默认离线 INDEX 是确定性导航条目，不生成新经验规则。flat 在根聚合，fixed tree 在固定 taxonomy 的共同祖先聚合。每条至少两条当前有效下层 card refs，绝不支持 INDEX，最多一层；brief/detail 明示下层 brief、适用细节须逐条展开。id 绑定完整 support hash，支持更新/失效后 rebuild 自然换 id；旧 snapshot 不改。确定性生成没有模型成本，其时间与 tokenizer 工作量单独记账。默认反思只添加触发 off/failure/feedback 的 L2 导航注解。

显式生成模式遵守 `docs/DERIVED_CONTRACT.md` / `configs/derived_v0.json`：允许有来源支持和适用边界的单层抽象 INDEX 与反思注解，保留原始输入/响应/usage/receipt，不修改 L1。RuntimeBuilder 必须收到每个应调生成角色的已提交 batch（合法空结果也提交）；生成模式不混入默认导航。其语义仍未验证，不能因为结构合法宣称规则正确。目前只准受控 fixture，真正模型调用与传输尚未通过 S5/S8。

检索 query 来源是当前公开任务文本，route 是固定公开路由器（taxonomy 词表），不能由隐藏 verifier、gold 或记忆用量结果选择。Unicode NFKC/casefold 后按拉丁词与汉字二元组做本地重合检索。tree 用共同祖先作为先验，hybrid 允许 lexical fallback；没有匹配可返回空。credit 是关联 EMA（alpha=0.2），只能使用真实展开/行为关联与独立 outcome，加载和自评 useful 不直接升分；无成本/无结果保持 unknown，不能等同因果贡献。global/local 去重，按 score、时间、稳定 id 排序；global 可以加载无任务匹配的高信用条目，这是 M 的候选行为。

brief 返回固定 `{items:[{id,version,type_hint,brief,level}]}`；detail 返回 `{memory:{id,version,type_hint,detail,level,support_ids}}`。接口仅自动 brief 与 `open_memory(id)`，id 只能来自已披露 brief 或已展开 INDEX 的 support_ids。来源/hash/评分材料不披露；不提供任意版本、search/list/source、其他 namespace 或 caller 自选预算。INDEX 列出的支持仍需单独展开且累计扣费，不能一次返回整库。

预算对每次完整规范化 JSON 加换行计量，包括 envelope、错误、重复读取与 INDEX，不切断条目。brief 与 detail 各有累计 token 上限，同时保留 byte 上限。超大条目可跳过并留控制端 omission 原因；空结果也计量。拒绝若连错误 envelope 都无法容纳则返回空 bytes，记录预算耗尽。wire 在发出前写入控制端持久化日志并扣费，传输失败也不能回滚成没发生。服务只在独立工作区放 socket 和最小 client，真正的 corpus/store/log 保留控制端。

使用事件分为 auto_load、open、claimed_use、behavior_link、independent_outcome，各自记录，不能用 final 自报替代真实行为。EMA 根据有实际成功展开的条目与独立结果确定，行为关联须有注册来源引用。会话日志是 S5 trial staging，未有 trial receipt 不计入正式 P 或研究收益；不能以离线 socket 测试声称 live 模型已实际消费。

行为/结果来源的 episode 必须与使用记录相同；behavior_link 必须发生在对应条目成功展开记录之后，不能把另一任务的结果借给本任务。独立 outcome 每 episode 只允许一次。后续 full reflector/抽象摘要的模型生成与传输验收仍未通过；目前确定性导航只证明 support 失效与接口布线，不代替讨论中的抽象经验或真实反思收益验收。

legacy 适配只生成可校验迁移提案与原始 snapshot hash：仅 level 0、无 links/support 的叶子，显式 supplied 时间/outcome、严格源文本 hash 与可见 source refs；`content`、conditions、limits 合成 detail，不省略适用边界。原 record/snapshot 随迁移工件保留，版本按顺序转换，不能猜测缺失元数据或将旧 abstraction 当 canonical 事实。不可转换记录明确拒绝，不维护两个活动合同。

研究 namespace 的 brief/detail 现在随内容给 exact immutable evidence_ref，用于提案引用；完整序列化 metadata 计入同一预算，整条不适配则拒绝/遗漏，不提供免费引用映射。task namespace 维持 id-only；研究者不能将 INDEX ref 当 card 引用，需展开支持卡。所有 producer、服务与 readonly replay 统一从冻结 projection.namespace 和 runtime policy 推导，来源可解析不等于收益。
