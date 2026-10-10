# 请求发送前的整轮预算合同

finish 仅接收 object=response 的完成对象，token-count 对象或受测者自写 usage 不能代替后端响应。当前调用者是固定 SSE fixture；正式调用者还须绑定实际独立后端原始流。

根合同还绑定预算层及其控制依赖的实现 hash，每次操作重验；代码变化不能复用旧账本的发送许可。cost_unknown 独立于 settled/breach：缺失、错误身份或重复响应的费用仍未知；已知超界量至少按 observed 与 reservation 的较大值计入，不把 breach 或缺 receipt 从分母删除。

本模块是控制端预算层，不是受测工具。先登记 configs/provider_budget_v0.json 中的假设。唯一控制账本保存不可变根合同、注册角色槽位和各层最大 requests/gross input/output；全部槽位预留上界不能超过根护栏。账本重开核对同一合同，不允许换 root 名称、改额度或添加槽位重置预算。

prepare 固定 call_id/slot/model/完整原请求 hash。禁止 conversation、previous_response_id、background、自动 truncation 等隐藏输入；计数器看到注入输出上限后的完整转发体。当前唯一计数器为 fixed_fixture_100，单位不是实际模型 token。输出上限不得低于 API 最小 16，原请求已有更小上限可保留，更大上限按剩余额度限制。max_output_tokens 包括可见和 reasoning output；gross input 不扣缓存。

同一事务核算已完成的真实 usage 和未确认请求的全部预留，再登记不可变 reservation/receipt。dispatch 只消费已登记的同一 body/ref，一次性记录 sent，之后才能交给后端；不能把缓存 receipt 当再次发送许可。dispatch 前后崩溃均保留原 reservation，缺 usage 不释放。调用次数保留，包括已经预留但未完成的请求。没有自动重试或取消即退款。

finish 必须对应同一已 dispatch call，模型/响应 id/terminal status/usage 和响应 hash 绑定。输入 count 不符、负数/bool、output 超界、错误 total 或身份错误都持久化 breach，不能回滚成不存在的失败；根 scope 停止后续发送。合法响应才按 gross input/output 计账并释放未用 output，响应缓存不增加样本或调用许可。两个角色、维护、研究和 shadow 都需同一预算根，当前独立 fixture 根只证明此原语。

接入现有本机 Codex fixture：原始请求与注入上限后的 forwarded request 分别保留，后端确认收到绑定上限，原始 SSE 的完成对象用于 finish。HTTP headers 仅保存有限非秘密元数据和 Authorization 是否存在，永不记录认证值；失败 path/header/body/attempt 留 bounded metadata。此入口只监听 loopback，无凭据、外部 API、计数请求或真实模型。fixture 用量/上限单位均 simulated；不能证明实际 tokenizer、服务端履约、全阶段硬预算或收益。正式认证及预算门槛保持关闭。

真实资格仍需固定可验证模型/计数器、完整含 tools 的输入计数、模型服务端上限履约、计数调用的成本和费用未知恢复。官方依据：[输入计数](https://developers.openai.com/api/docs/guides/token-counting)、[Responses 创建参数](https://developers.openai.com/api/reference/python/resources/responses/methods/create)。官方语义不代替本机实际 provider 资格。
