# 完整 seed 的两轮小规模比较

这次按用户原始 seed 实现了完整基线：类型目录、brief/detail、真实 Codex 抽象索引及下层依据、全局/关联 brief 必载、渐进披露、使用后重要性自评、公开规则变化后的反思新经验与旧索引失效。关闭 Codex 全局 memory 是会话隔离设置，不限制这套外置经验库。以前的 flat 结果仅证明简单经验保留能力，没有比较完整 seed，不能用来说明简易机制更好。

**交付选择：保留完整 seed。** 完整 seed 筛选6/6、确认3/3，无记忆筛选3/6；相比仅关闭反思的 seed，多完成一次规则更新后的任务。最近时间排序同分，累计分摊客户端成本差约0.13%，未达到预声明10%门槛。抽象索引、两种全局排序和关联检索各自的任务收益仍未判定；两个变体还受到连接失败影响。以下结果只针对合成已教约定，不是一般编码或真实 Recommand 项目收益。

## 实际机制

每条叶经验为一个普通 Markdown 文件，含 brief、详细解释/原始情境、类型、抽象等级、id/version、精确来源 hash。每个符合条件的类型目录有特殊 `_INDEX.md`：由独立 Codex 从至少两条下层经验归纳，仍有 brief/detail，detail 包含具名 few shots，下层经验 refs 可解析到保留的原版本。本轮使用L0叶经验与L1索引，不递归建立更多层。

类型使用冻结树，例如 workflow/testing/triage、workflow/testing/validation、workflow/recovery 和 environment/services。任务类型在公共输入中给出；本轮没有测试自动分类器或分类体系自行演化。目录与不可变对象是控制端外置文件；受测者通过限定 `open_memory` 服务访问，控制端逐字传入结果，不授予它控制端文件访问权。

全局采用堆 top2，权重为重要性0.55、时间0.25、抽象层0.20。时间是冻结的逻辑产出次序，importance是[-1,1]自评的EMA（alpha=0.5），等级为0/1；该组合是本轮预注册配方，不是证明最优的权重。关联经验依公开任务类型由当前目录向祖先扩展，优先近邻，再以抽象等级/重要性/时间取最多8条。两路合并去重；brief预算6000 UTF-8 bytes，detail预算12000 bytes，每角色至多两次展开。预算单位明确是字节，不冒充模型token；本次没有brief预算丢弃。

使用后，受测者只评价自己声明实际用过的已披露经验；EMA、自评原文和独立任务结果分开保存。原始记录显示关闭反思的受测者沿用旧规则后仍给旧索引+1，独立评分判错。这说明自评不能单独证明经验有效。

公开新合同触发独立反思：写入source-backed新叶经验，显式退役旧路由经验和依赖它们的索引，保留仍成立的“顺序/重复项”经验，再由Codex重建索引。旧版本均保留，新增经验区分实际观察例子与公开合同规定的未观察分支。此反思/替换的实验适配器绑定注册的Aster变更案例；接入真实Recommand的通用触发与替换判断留待后续重放。

```mermaid
flowchart LR
  E[观察与公开反馈] --> R[反思生成叶经验]
  R --> T[类型目录与不可变版本]
  T --> I[抽象索引与下层依据]
  T --> B[全局和任务关联brief]
  I --> B
  B --> D[按需展开detail]
  D --> U[任务使用与自评]
  U --> B
  U --> G[独立评分]
```

## 冻结比较与结果

共同 acquisition 是12条明确标为synthetic的历史片段，经4个真实extractor生成12叶经验、一个abstractor生成4索引。六任务依次为重复、字段迁移、当前合同覆盖、无关算术、异项目反例、公开反馈后的新会话保留。同任务公共输入、模型gpt-6.1-sol、low effort、展开上限与字节预算一致。每case内臂顺序提前随机冻结，失败/missing不从分母删除。

| 机制 | 筛选成功/计划 | 模型错误 | 连接失败 | 含共同准备与维护的已知客户端tokens |
| --- | --- | --- | --- | --- |
| 无记忆 | 3/6 | 0（另3次needs_context） | 0 | 39,437 |
| 旧flat/lexical4 | 5/6 | 1（旧规则） | 0 | 46,459 |
| 完整seed | 6/6 | 0 | 0 | 73,866 |
| seed仅关闭索引 | 5/6 | 0 | 1（异项目反例） | ≥55,551 |
| seed仅按最近时间全局排序 | 6/6 | 0 | 0 | 74,059 |
| seed仅采用词汇关联检索 | 5/6 | 0 | 1（无关任务） | ≥59,598 |
| seed仅关闭反思 | 5/6 | 1（旧规则） | 0 | 51,826 |

成本按真实共同extractor用量在六个有记忆臂间分摊，初始索引用量在四个索引臂间分摊，之后每臂反思与重建单独计入；研究角色/canary另列于全run成本。两个失败角色用量未知，相关成本是已知下界，不能以其较低数字宣称更省。客户端usage含缓存/重复输入，不是provider账单或费用。

Proposer提名recent_global，Evaluator从冻结near/far目录选择far，两项提案都已消费。确认轮采用完整seed与recent_global各3项，均3/3：深层迁移使用新输入，并检验反思前未观察过的severity=1和severity=2分支；异项目反例使用新输入；算术复用原对照。因此是两项新输入加一个重复对照，不是三个完全独立holdout。完整seed筛选+确认分摊成本103,434，candidate103,565，相差131（约0.127%）；不满足成本优势门槛，保留seed。

各研究角色把本轮公开观察与自己的提案通过真实反思存成经验，第二次研究会话披露自己的第一轮经验。末轮evaluator的未激活理由误称实际far确认任务为shallow：原文保留，没有用于机制选择。已为下一版本补充研究公开汇总中的实际candidate/challenge、机制配置与public任务，22项相关合同检查通过；没有再调用模型，不能声称模型研究质量修复已验证。最终未测提案不激活。

## 失败分母、核验与成本

attempt-001 在共享准备阶段连接失败后终止：3个native会话，13,516已知tokens、1个未知用量会话、48个solver均missing，原状态未修改。尚无solver输出时才另冻结attempt-002；旧卡/研究/臂轨迹没有移植。

attempt-002 69个native角色中67生成完成、2个solver明确连接失败；48个计划solver为46完成/2失败/0missing。69原始传输和实际交付逐项核验、46评分在新的独立进程重算一致、51不可变对象及20个提取/抽象/反思/研究输出到落库文本逐项对应；5个solver实际展开detail。两个研究者第二次只读取自己的研究记忆。前后两次22项相关验收是基础设施检查，不是收益证据。

两批工程总分母96：41成功、46生成完成、2个solver连接失败、48missing；另外1个准备角色连接失败。共72 native会话、543,873已知客户端tokens和3个未知用量会话；新批次530,357 tokens，actor耗时之和950.564秒（约15分51秒，非整轮墙钟）。费用未知，未作预算硬上界或完整provider输入证明。完整隔离只核对可见native recipe、空instructionSources与新thread；formal_admission=false。

保护任务完整seed均通过，合成保留/迁移/公开反馈保留相比off均有增益；反思关闭后出现1次实际过期规则错误。记忆库很小、类别和任务类型固定、排序/检索没有经过大库容量压力，原始学习记录是合成教师规则。这些局限使“完整seed有效”只限上述可核验场景；不能宣称索引优于无索引、树检索优于词汇或组合排序优于最近排序，更不能把网络失败算作机制缺陷。

## 交付与复核

[可用seed配置](../configs/usable_seed_memory_v1.json) 与 [可搬运记忆包](../runs/seed-variants-20261008/seed-memory-pack/pack.json) 保存同一实际测试机制。包内31个不可变对象、11条当前叶经验、4个当前索引、历史版本、显式替换记录与仅自评的重要性；不含其他臂轨迹、评分器或隐藏gold。[实际类型目录](../runs/seed-variants-20261008/seed-memory-pack/experience-tree/manifest.json) 可解析每个文件；Markdown另外提供原始来源的本地导航链接。全部例子标为合成，不自动导入Recommand或全局memory。

[机器结果](../runs/seed-variants-20261008/result.json)、[原始传输/检索/评分审计](../runs/seed-variants-20261008/attempt-002/independent-audit.json)、[来源到产出审计](../runs/seed-variants-20261008/attempt-002/source-production-audit.json) 与 [交付验收](../runs/seed-variants-20261008/delivery-validation.json) 可复核。旧批次单独保留在attempt-001。

使用工作区Python运行以下只读审计，不产生模型请求：

```sh
python3 runs/seed-variants-20261008/attempt-002/source-snapshot/scripts/audit_seed_variants.py --run runs/seed-variants-20261008/attempt-002
python3 runs/seed-variants-20261008/supplemental_source_audit.py runs/seed-variants-20261008/attempt-002
```

新实验可用 `python3 -m memorylab.seed_variants prepare --run <新目录>` 冻结，只有显式 `execute` 才调用真实Codex。旧运行须使用其冻结源码读取；不能用当前源码改写旧结论。下一次真正需要检验的是更大且类别有歧义的经验库、多个相互冲突反馈、未见项目及真实项目重放，本轮不继续扩大任务到某个变体胜出。
