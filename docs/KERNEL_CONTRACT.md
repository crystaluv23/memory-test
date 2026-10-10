# V0 Kernel 类型与证据合同

本合同实现 META_META_PLAN 的 S1/S2，并为后续 runtime 提供唯一输入形状。状态为实现中，未冻结。`configs/kernel_v0.json` 保存注册字段、默认配置、约束和上界；未知字段拒绝，不从说明文字推导权限。

## 规范化与身份

所有合同对象仅含严格 JSON 类型，拒绝 NaN/Infinity、非字符串键、tuple 和隐式类型转换；bool 不等于 int，1 不等于 1.0。canonical JSON 使用 UTF-8、排序键、无多余空白和禁止非有限数；sha256 对 canonical bytes 计算。原始文本/文件的 source sha256 对原始 UTF-8/文件 bytes 计算，两者明确区分。

引用形状固定为 `{kind,id,version,sha256,namespace}`。id 只允许 ASCII 字母起始的字母数字、下划线、短横线，长度不超过 96；版本为正整数。namespace 为 `control`、`task/<chain>`、`research/evaluator` 或 `research/proposer`，不由模型自选读权限。controller-only 对象不能通过普通来源读取进入 task/research 视图。

M/e spec 固定为 `{schema_version,kind,id,version,parent,config}`。M config 是注册的 dotted-field map；e config 包含固定 family 与全部注册难度字段。只有 controller 注册初始 spec。子 spec 的行为 diff 必须恰为一个注册字段；子版本为 1，子 id 由父引用与规范化 config hash 决定。hypothesis 仅属于 proposal，不改变 spec，不解释为可执行命令。

提案固定为 `{schema_version,kind,parent,mutation,hypothesis,evidence_refs}`。mutation 为 null（有效空提案）或 `{field,old,new}`。old 必须与父快照值严格同类型相等；new 同类型落在注册枚举；相同值为 noop。空/noop 不生成新 spec，也不能计为改善。父 hash、namespace、字段和组合同时校验；root/snapshot/model/budget/verifier 变更不能作为提案。

每次提案都有独立 receipt，accepted/rejected/noop 都留存。提案 evidence_refs 必须为在该角色与时间截断中确实可见的 source/card/event 版本，拒绝隐藏答案和其他参赛臂来源。异常仅可产生拒绝，不留下部分 spec。

## 不可变存储与事务

控制端采用 SQLite 单库事务保存 canonical object、原始 blob、append-only event 和 receipt。journal/同步策略明确为 WAL/FULL；注册、提案及相关事件在同一事务提交。对象只插入，不更新；同身份同内容幂等，不同内容拒绝。读取重算 hash 防止工件漂移；返回独立副本。

事件顺序由持久化 sequence 决定；预声明 request_id 支持幂等，重复 request_id 的不同内容拒绝。commit receipt 与最终持久化对象在同一个事务，无“写对象但没写决策”的中间状态。可重建索引不能成为 completion 的唯一依据。未来 trial/memory/payoff/epoch 必须扩展同一事务合同，S1/S2 不提前声称覆盖 live 执行的进程恢复。

## 可观察来源与固定卡片

source 为 `{schema_version,kind,id,version,namespace,event_type,episode,sequence,timestamp,visibility,text,sha256}`。event_type 只允许 user_input、agent_output、tool_call、tool_result、file_diff、explicit_feedback、visible_outcome、explicit_hypothesis。独立 hidden grade/gold、完整讨论和其他臂轨迹必须保持 controller_only，不能伪装 visible_outcome。

可见来源以角色、namespace 和 sequence cutoff 三者同时裁剪。source 注册时 hash 检查原始 text；immutable object hash 额外绑定来源包。source 的 timestamp 必须是 timezone-aware ISO 时间；时间相同仍靠 sequence 排序，来源不能跨 cutoff。

来源同时绑定注册 input event 和 source receipt 中的精确引用；不能复制已有 sequence 给另一 source id。空 corpus/active 也检查 namespace 与 cutoff，不因无条目而跳过权限/未来边界。注册空间的 family、verifier、可执行字符串原语必须与实现支持表一致；配置不能声明实现不存在的行为。所有严格 JSON 入口将递归/编码异常转换为明确拒绝。

固定 extractor v0 采用显式、可追溯的 extract proposal 合同；接受 `{cards:[...]}`，每卡含 type_hint、brief、detail、evidence_refs、outcome、supersedes。extractor 使用版本固定的 prompt，从公开可观察输入形成提案；验证器检查结构和来源，语义不自动成立。离线 fixture 只验证本合同，不代表真实模型抽取。空 cards 合法，提案必须全批原子通过；版本冲突拒绝整批。

卡片持久化字段为 `{schema_version,kind,id,version,namespace,type_hint,brief,detail,evidence_refs,timestamp,outcome,status,supersedes,extractor}`。outcome 明确为 success/failure/unknown；status 为 active/withdrawn；supersedes 绑定不可变 card refs，同 namespace 且此前 active，不能自引用。旧 card 不修改，新 revision/supersession 使活动视图改变，历史引用仍可解析。

brief/detail 均非空且有限字节，不切断卡片以合格；type_hint 来自固定 taxonomy 表，不是 evaluator 的隐藏答案。evidence 至少一条可见 source 引用，全部早于生成 cutoff，不能用 fabricated source 或 controller_only 来源。extractor fingerprint 绑定 schema/prompt/model；任何变化都需新实验条件。

从 legacy reference-v1 适配时只做字段映射和版本化，不能宣称旧内容重新通过语义验真。旧 `content` 映射 detail；缺失或不可恢复的來源使转换失败，而非补写。既有 v1 记录保留作历史，自新 kernel 接口起只用 canonical card。

## 独立验证和后续范围

S1/S2 验证包括合法组合、枚举类型、父版本与内容漂移、越权字段、伪装 metadata、无副作用拒绝、并发事务、幂等冲突、source hash、角色/namespace/cutoff、超长和非有限值、批原子性、supersession 及可读旧版。

这些证明合法的合同与证据流；没有证明经验语义正确、模型预算公平、测试有辨别力或整体系统无问题。S3–S8 的 runtime、interactive 场景、执行调度、真实历史与 live 验收仍按原计划推进。
