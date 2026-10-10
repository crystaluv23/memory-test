# 实际 CLI 的无模型认证边界诊断

configs/app_server_probe_v0.json 登记诊断假设和上界。当前 online App Server 文档与本机 0.160.1 的生成 schema 存在差异：本机 SandboxPolicy 未声明 readOnlyAccess/access，不能把不支持的字段静默忽略当隔离成功。permissionProfile 字段另有实际 schema 支持，须在 CLI 内部执行实际正负探针。

真实 app-server 使用临时私有 CODEX_HOME/HOME、空 file 认证存储、无敏感信息 controller/env marker；不使用真实 auth 或 keychain。原双层 native sandbox 路径在 initialize/内部 sandbox_apply 处实际失败，保留 attempt-001–003。当前 server 是临时可信控制进程，不套外层 native sandbox；其工具由实际内部 permissionProfile 隔离。没有模型、登录、account/token 请求、thread/start、turn/start 或自动批准。只允许 initialize/initialized、permissionProfile/list、command/exec；工具输入只含固定 boundary probe，不接收用户模型任务或任意 RPC。raw request/response/stderr、CLI hash/version、生成配置、每条探针和失败均保存；同目录不覆盖。

stdin.bin/stdout.bin/stderr.txt 保存总 wire 上界内的原字节，wire.json 为解析索引，exit.json 保存其 hash、实际进程退出和溢出状态。超界 chunk 留真实长度、hash 和保留前缀；不能静默截断后通过。两个 command/exec 请求在启动前按确切 canonical 字节绑定，拒绝付费方法、其他命令/profile/budget及事后修改。临时目录在实际 server 终止后清理；记录不依赖目录还存在。config/module 纳入自包含 implementation 资源，仅作诊断重放，不改变正式许可。

权限配置使用 root deny、minimal runtime read、明确 Python runtime read、workspace write 与 private deny、network off；不与 sandbox_mode/sandbox_workspace_write/--sandbox 混用。先确认实际 profile 存在且 allowed，再给 command/exec 精确 permissionProfile id。工作区读写须成功，私有 controller/auth-like marker、同级目录和控制 socket不可读，外部写入和工具环境标记不可泄露。读取正控制使用同一实际 server 与同一 marker，唯独 profile 允许该无敏感信息文件，必须真的读到。

本诊断只证明某个已固定 CLI 的命令执行入口及测试边界。真实认证来源、实际模型角色调用、内置所有工具、tokenizer/provider硬上界、每阶段成本和正式 freeze 仍需验收。不能把本诊断生成的 bool 或假 credential 改成 role_v0.authentication_policy 的正式许可。

官方来源：[App Server](https://learn.chatgpt.com/docs/app-server)、[Permissions](https://learn.chatgpt.com/docs/permissions)。本地行为和接口以实际 binary/schema/probe 为准。
