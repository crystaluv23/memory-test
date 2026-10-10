# 研究依据与变体来源

检索日期：2026-10-07。仅使用作者论文、官方实现/说明；这些来源启发实验设计，不把不同任务/模型的论文分数当本实验排名。

| 来源 | 时间 / 性质 | 流程设计采用的启发 | 限制 |
|---|---|---|---|
| [MemoryArena](https://arxiv.org/abs/2602.16313) | 2026-02，论文 | 跨会话 Memory–Agent–Environment 闭环，经验由实际执行产生，评估后续行动 | 当前任务集不是直接移植该 benchmark，需模型校准与真实任务确认 |
| [AMA-Bench](https://proceedings.mlr.press/v306/zhao26bs.html) | ICML 2026，论文 | 真实轨迹、因果/目标信息；保留实际情境和原始 trace | 它仍含 QA；本测试以代码行为为主，当前不实现因果图策略 |
| [AFTER / Managing Procedural Memory](https://arxiv.org/abs/2606.23127) | 2026-06，论文 | 局部提升与跨任务/角色迁移分开；技能可能过度专化 | 不能据此假设所有经验都可泛化 |
| [ACE](https://arxiv.org/abs/2510.04618) | 2025-10，论文 | 增量经验手册、反思/整理、避免反复全文重写导致细节丢失 | 当前仅保留提案/版本接口，尚未实现其策略 |
| [A-MEM](https://arxiv.org/html/2502.12110v11) | 2025，论文/官方代码 | 原子笔记、结构化上下文、链接与演化；组织可以横跨类别 | 当前不实现该论文机制 |
| [Letta Context-Bench V2](https://www.letta.com/blog/evaluating-memory-in-production-agents/) | 2026-07，官方实践报告 | 使用/生成分开；泛化与 hygiene；混乱与清理后记忆比较 | 私有评测、厂商结果，只作为设计依据 |
| [Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode) | 当前官方文档 | 独立 exec、JSONL 事件、structured final、usage | 以安装版本的 help 和隔离探针再核对 |
| [Codex 配置位置](https://learn.chatgpt.com/docs/config-file/config-advanced) | 当前官方文档 | config/state 与项目规则分别控制 | workspace-write 本身不能隐藏评分答案，需要外层隔离 |

本地故障来源（只读核对，不修改）：Recommandation 的 `reports/self-evolution-retrospective-20260927.md`、`memory/experiences/self-evolution-convergence-and-acceptance.md`、`memory/experiences/incremental-repair-and-sourced-memory.md`。采用其中生产形状与简化 fixture 不一致、staged/committed 混淆、越权修复、恢复和后继读取、字节/token 混淆等失败类别。任务中的名字、数据、合同与种子重新生成，不将这些旧笔记直接塞给受测 Codex。

本阶段不据此决定候选机制。后续可利用这些资料讨论组织、读取、维护和反思的实验变量，先由当前环境证明任务与过程可被完整、独立地观察。
