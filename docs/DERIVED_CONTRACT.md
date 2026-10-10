# 单层抽象与受限反思合同

原始来源以 `progressive_handles` 进入当前生成输入：精确 ref、text hash、长度、事件/时间 metadata，不把所有 raw text 塞进 64KiB prompt。固定角色来源服务按 ROLE_CONTRACT 读取实际文本区间并记录披露；完整来源保留控制端，不能用摘要替代。canonical 卡片的 detail 仍完整进入该阶段的有限输入，整份输入超额仍明确失败；未读来源不代表有语义支持。新输入/prompt/实现 hash 与旧版本分开，旧冻结包不覆盖。

讨论中的 L2 不止导航目录。`consolidation=single_index` 可以在固定 prompt 下，从至少两条当前叶子生成有适用条件、限制和反例的抽象 INDEX。`none` 不调生成器。V0 最大一层，支持只引用完整不可变 card refs，不引用 INDEX 或另一个臂的派生内容。组织按 M 决定：flat 的共同根或 fixed tree 的同一顶层分组；类型须是支持叶子的共同祖先。INDEX 专用的合成根 `root` 登记在 derived 配置，允许 flat 跨顶层聚合，不新增 canonical 卡片类别；fixed tree 仍不得跨顶层。来源引用证明可解析，不能证明归纳正确；实际 counterexample 与独立 outcome 检验语义。

`configs/derived_v0.json` 先固定生成输入上界、输出上界、prompt 与模型身份。prepare 仅取指定 namespace/cutoff 下当前 canonical cards 与可解析的公开来源，role 为 consolidation/reflector；不读 hidden grade、研究者聊天或未来结果。一次单字段提案后统一 rebuild，不复制旧抽象。输入 hash 绑定 K、M、canonical corpus、runtime、生成合同、role、cutoff 和 source refs。给模型的是有上限的整个输入；超额拒绝，不无声截掉适用边界。

反思触发只来自当前卡片的 failure/明确 feedback，off 不调用。反思生成对所给当前叶子的 L2 注解，不改 L0/L1，不超出指定 eligible refs。失败本身不允许凭空发明纠正结论；证据不足返回空列表。维护阶段耗时、完整输入/输出、原始响应与实际 usage 分开保存，不能算作免费维护。

结果仅为 `indices` 或 `annotations` 的严格对象。未知字段、重复 support、未来/陈旧/withdrawn/跨 namespace、错误祖先、跨顶层 fixed-tree、递归、超大文本均拒绝。完整结构校验后，同一个 transaction 提交原始响应（私有 control）、有效派生 batch 和 receipt。任何失败不留下半个 batch；空列表保留合法 noop。batch 的 identity 绑定 input、原始响应与 invocation；精确缓存不重复生成，旧 batch 保留，支持叶子改变后不得装入新 projection。

RuntimeBuilder 可显式装入已提交的两个 role batch，并校验每个输入身份与 receipt。生成模式下 INDEX/注解完全来自这些结果，不混入旧的确定性导航；off/none 阻止错误批次加载。默认确定性导航继续用于离线接口自测，报告 `navigation_only`；显式生成结果报告 `unverified_derived_semantics`。两者不混算。直接 invocation 仍只允许显式 scripted_acceptance fixture。RoleAdmission 可以从核验过的原始记录形成 captured_diagnostic batch，必须与 completion receipt 一起原子提交，加载时再次解析原始 capture 与输入/final 绑定；不能凭 model 字符串、自填 usage 或一段 JSON 自证。真实诊断调用须有固定模型身份，formal admission 仍未开放。
