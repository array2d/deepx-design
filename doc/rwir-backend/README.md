# RWIR Backend 委托机制

> kvlang runtime 将 rwir 指令委托给外部进程组件执行的统一机制。

## 三类委托场景

| 类别 | rwir 示例 | 后端 | 特征 |
|------|----------|------|------|
| **计算委托** | `tensor.matmul(a,b) -> c` | op-gpu（GPU kernel 编译器） | 单次请求-响应，毫秒级延迟 |
| **API 封装** | `llm.chat(prompt) -> resp` | LLM worker（HTTP 代理） | 外部服务调用，秒级延迟，需重试/限流 |
| **Agent 封装** | `agent.search(query) -> r` | sub-agent（完整 agent 循环） | 长时运行，可能自 spawn 子 vthread |

三类场景共享同一套委托基础设施：**kvspace 是唯一的协调平面**，后端通过 Watch/Notify 与 kvcpu 交互，不建立 TCP/gRPC 直连。

## 核心公理

1. **rwir 自描述**：每条 rwir 指令的 opcode + 读写参已包含执行所需的全部信息。后端无需知道帧上下文。
2. **kvspace 全局可见**：`/lib/`（指令树）、`/vthread/`（运行时状态）、`/sys/`（后端注册）对所有进程平等可见。
3. **后端被动消费**：后端不主动调用 kvlang，只响应命令队列中的任务。
4. **PC 即状态**：委托期间 vthread PC 不变，后端完成后 kvcpu 推进 PC。

## 文档索引

- [01-后端注册与发现](01-后端注册与发现.md) — `/sys/rwir-backend/` 注册表、能力声明、负载上报、心跳
- [02-委托协议](02-委托协议.md) — 命令队列、任务格式、完成信号、超时与重试
- [03-三类场景专述](03-三类场景专述.md) — 计算委托/API封装/Agent封装的各自考量
