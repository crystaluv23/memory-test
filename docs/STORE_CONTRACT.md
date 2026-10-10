# 冻结语料与独立 store 合同（S2/S5/S6）

配置为 `configs/store_v0.json`。只在控制端运行，零模型调用，structural copy 不构成收益证据或正式验收。输入来自实际 Ledger/EvidenceStore/CanonicalCards/UsageStore，不能给一份重新签 hash 的任意 JSON 就作为原始语料。snapshot 与 import 均有不可变 receipt。

capture 绑定 namespace/cutoff/kernel/extractor，逐条核验 source 注册、canonical 提取、usage 注册与旧 INDEX 支持。只含该截止点已提交的公开 source、所有 canonical 版本、usage 顺序和必要的旧 INDEX 原始归档；不复制 private grade、其他 namespace、未来事件、控制端聊天或凭据。上界检查整体拒绝，不截短经验。空语料是合法输入。

同一内核下 import 重定位 source/card/support/evidence refs，保留 card id/version、原文本、timestamp/outcome、supersession 和相对 usage 顺序。只有空目的 namespace 能开始 import；幂等命中返回原 receipt，不能再次提取或追加一份相同历史。原 namespace/ref 到新 ref 的映射、原始 snapshot 与结果 portable identity 留控制端。portable identity 检查来源文本/事件类型/时间、卡片完整字段/支持、usage 类型/顺序，不以忽略 namespace 的方式绕过权限；真实对象仍通过目标 store 的注册及版本校验。

shared_acquisition 只允许 task/acq-* 到独立 task/*；research_shadow 只允许原 research/evaluator 到同名 namespace，或 research/proposer 到同名 namespace，并使用不同 store。initial_checkpoint 的研究资料也不能跨研究角色，task checkpoint 不能作研究历史。执行图还须绑定确切原 snapshot/来源阶段，purpose 字符串不是 worker 的读写许可或正式 proof。

引用旧 INDEX 的 raw usage 不丢失、不转换成新机制已经展开过它。目标 store 只建立历史引用壳：原 ref/hash 与重定位后的 canonical supports，不携带原 brief/detail。它可解析，不能进入新 projection 的活动 items。EMA 对旧 INDEX 的关联不会自动转到新 INDEX 或叶子；缺少因果依据时保留未分配记录。活动 L2 全部重新生成，原抽象只在私有来源 snapshot 中归档。

普通 import 的 source/card/usage/历史引用/映射与 receipt 在一个事务中完成。任意中断回滚全部复制，不留下半份活动记忆。fork 不用 SQLite 全库 backup，只写 scope 内重新注册的语料与所需已验证 spec lineage；先写临时 DB，关闭/checkpoint/fsync 后无覆盖发布。旧 store 保持只读，失败不替换已有目标。子 store 的完整证据由 parent driver 显式绑定后才算阶段完成；文件存在本身不是完成指针。

本合同不改变 extractor/write policy，也不为阶段图补造理想经验。复制的是实际已经形成的内容；acquisition/extractor 失败造成无合法 snapshot 时，后续槽仍保留并按失败依赖处理。真实 fixed extractor、全图运行与正式 proof 仍须验收。
