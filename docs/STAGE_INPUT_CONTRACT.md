# 阶段输入绑定合同（S5）

binding 同时固定实际 Python/version/executable hash、系统 release/version/架构、native sandbox 路径/hash 和过滤后的 worker 环境摘要。每次 prepare/resolve 重新核验环境和源码/配置，连 cached/control/disabled 分支也不豁免；失败不产生新输入、迁移或 stage 完成。只保留允许环境的摘要，不保存认证或 controller 变量。缺少 native sandbox 会记录 unavailable，不成为 native execution 许可。此身份不等于容器/dependency/model/provider 的完整 formal 资格，后者仍须另行验证。

配置为 `configs/stage_input_v0.json`。输入解析器只运行在控制端，不启动 Codex，不接收正式 proof。它不是字符串 bindings 的解释器：用真实 Registry 重新编译选定图，逐项比较所有 slot、依赖、角色、条件、场景和预算。源/配置身份、初始 task snapshot、两份独立研究 snapshot 和控制端 store 根目录作为不可变 binding 提交。

一次执行图占用独立控制端 store；预览可共用 Registry，但两张执行图不能在同一 store 复用 task namespace。下一轮由执行器从已声明 task/research snapshot 自动初始化新 store，不能在旧臂 namespace 内拼接一轮，也不能要求人工清空资料。该初始化仍须全图 driver 接通。单次输入核验中的只读依赖/snapshot 可按精确 ref 暂存；跨核验重新读取，不能按更新后的 head 重选资料。

单次核验在 SQLite 写事务锁内，可缓存已经验证的 exact payload/hash 的 JSON 解码，缓存的序列化 byte 上界由 ledger_decode_cache_bytes 限定，超额时回到正常核验而非截断证据。每次读取仍查询真实 DB，payload/hash 任一变化都不能命中；返回独立副本，不能让调用者污染缓存。相同字节上界的嵌套核验共用外层解码缓存，其他上界使用独立临时缓存；外层退出释放，不跨事务重用。

GraphRun 的单次报告在同一事务内开启 validation_scope，可暂存已核验的 input/dependency/snapshot 和 stage begin/finish 索引。validation_memo_max_bytes 限定这些对象的序列化字节，超额对象照常核验但不加入缓存。prepare/resolve 仍 preflight，公开 resolve 返回深拷贝；数据库 total_changes 变化时重建事件索引及缓存，回滚造成的变化也不会复用旧 generation。报告结束或异常清空，不传到执行或下一报告；所有原始文件和跨库 receipt 仍由各 publication 的生产验证器核验。

每个阶段的输入对象与 receipt 在 actor 开始前原子保存。输入包括精确前序提交、来源 cutoff、选定 corpus、runtime、角色 packet、当前任务模板及明确 action。任务模板由同一场景重新构造，不从受测者提供的目录取模板；私有 grade/report 和讨论不进入角色 packet。恢复重算相同冻结依赖并核对既有输入，不以新 head 代替旧 cutoff。

前序产物必须能反向解析真实 stage completion、begin、attempt、已冻结 input 和 artifact；不接受 generic put 或任意自签 JSON。公开来源使用同一实际任务/提案 episode，namespace 一致，source 注册及决策在其生产阶段 begin/finish 之间提交。grade/proposal validation 的公开事件沿用对应 solver/proposal slot id，使 open/behavior/outcome 可以关联；该来源属于哪个控制阶段，仍由其不可变 artifact 和 begin/finish 区间核验。隐藏结果只留 artifact。extractor 读取图中声明的生产阶段及其独立公开 outcome/校验来源，保留实际失败，不拿私有期望值替代反馈。来源 cutoff 按生产阶段 receipt，而非文本里的 timestamp 判断。

每个 extractor/corpus/rebuild 产物须带实际 corpus_snapshot；其 capture receipt 也必须早于 stage completion。extractor 的输入 active cards 应与前序 snapshot 完全一致，不能夹入非本链的新提取。rebuild 对确切 snapshot cutoff 和当前 M 调用真实 RuntimeBuilder；启用的派生结果必须来自同一 corpus 输入，关闭或未满足 eligible 的分支有可重算证明。

目标核验投影时，还必须比较 rebuild 产物中的 derived_refs 与该 rebuild 已冻结输入的派生阶段产物；另一份即使结构合法、来自同 corpus/M 的 batch，也不能替换已登记生产阶段，否则成本和结果无法归因。

初始 task checkpoint 在第一次公开事件之前幂等复制到臂/acquisition namespace。共享 acquisition 的目标臂只复制图中对应重复的 corpus；相邻 seed、参赛臂或未来 corpus 不能互换。每个目标及反例使用同一学习末尾投影，后续目标使用事件不进入它。oracle 不复制 checkpoint 记忆，使用当前任务的空记忆输入。

研究角色仅使用同名 namespace 的既有 snapshot。新的研究 view 完成后固定提取，再生成提案前 corpus。current/candidate shadow 都从这份 corpus 克隆独立 store，保留相同角色 namespace、实际原引用映射及所需 spec lineage。后置提案经验可以写主库供下一轮使用，不能进入本组 shadow 的 sources/cards/usage。shadow 的来源引用使用克隆映射，不能因外形相同而读主库的后续内容。

控制节点只得到声明的私有 artifact 引用，不生成 worker packet。提案结构校验和 stage input 通过不构成研究质量验证；shadow 的独立质量实验及预算、全图 actor 驱动、正式执行/评分 proof 仍须另行完成。没有 eligible 输入的派生 action 是 bound_noop，和机制 disabled 分开记录，不补理想经验、不伪造模型 invocation。失败依赖按 graph policy 留待阻断，不从分母删除。
