# runtime篇-05: rwirext 扩展运行时

> 状态：与实现同步。代码：`rwir/ext`（Go 框架）+ `rwirext/term`（Go term）、`runtime-rwirext/go/json`（Go json 标杆）、`runtime-rwirext/rust/term`（Rust term）。
> C runtime 侧的实现（Rust term 扩展 + `rwext_*` ABI）见 [[runtime篇-06-C运行时与后端抽象]]。

## 一、核心模型

kvlang runtime 是**中央调度 runtime**，默认执行全部 native rwir / rwfunc / 控制流。某些 rwir（tensor 计算、LLM 调用、agent 循环）不由它求值，而是交给**扩展运行时**。

**扩展运行时 = 只解析己方 opcode 的迷你 kvcpu**：从中央 runtime 拿到 PC，连续执行己方 rwir，直到遇到非己方指令，把最终 PC 交还中央 runtime。

| 类别 | rwir 示例 | 扩展运行时 | 特征 |
|------|----------|-----------|------|
| 计算委托 | `tensor.matmul(a,b) -> c` | op-gpu | 单次 kernel，毫秒级 |
| API 封装 | `llm.chat(prompt) -> resp` | llm worker | 外部服务，秒级，需重试/限流 |
| Agent 封装 | `agent.search(q) -> r` | sub-agent | 长时运行，可 spawn 子 vthread |

三类共享同一套 handoff 基础设施。**term/json 是标杆参考实现**（`rwirext/term`、`rwirext/json`），livebyte 的 agent 扩展照此模板。

**核心公理**：

1. **rwir 自描述**：opcode + 读写参已含执行所需全部信息；扩展运行时无需帧上下文（PC 即路径）。
2. **注册即签名**：扩展运行时只写 `/lib/<opcode>`（kind=rwir 签名），不再有 `/sys/`。
3. **批量执行**：扩展运行时从 `.todo` 拿 PC，连续执行己方 rwir 到非己方指令，把最终 PC 写回 `/vthread/<vtid>/pc`。
4. **被动消费**：扩展运行时只监控己方 `/lib/<opcode>/.todo<vid>`，不主动调用 kvlang。

## 二、注册与发现

### 签名注册

扩展运行时把己方每个 opcode 的签名写到 `/lib/<opcode>`（kind=rwir，空函数体，仅签名）：

```
/lib/json.to  → rwir(nr=1, nw=1, "rwir json.to(rootkey:charbyte) -> (dest:[]charbyte)")
/lib/print    → rwir(nr=1, nw=0, "rwir print(A:any, ...) -> ()")
```

与 native builtin 的区分：native 签名写在 `/lib/{runtime}/<opcode>`（`{runtime}` 反射自可执行文件名，如 `kvlang`），扩展签名写在 `/lib/<opcode>`（无 runtime 段）。二者同 kind=rwir，路径分层。

### 全局标记

`ext.RegisterGlobalRwir(opcode)` 标记 opcode 为**全局 rwir**——layout 不补 `pkg.` 前缀。裸 opcode（`print`/`input`）依赖此标记；dotted opcode（`json.to`）本就不被补前缀。

term 在**包导入时**注册全局：它与中央 runtime 同进程，`vet`/`format` 也会 layout 用户代码但不调 `Register`，必须在导入期注册，否则裸 opcode 会被补 `pkg.` 前缀。

### 幂等重注册

`Ext.Register` 幂等；`Ext.Serve` 每轮循环重调 `Register`，兜底外部 FLUSHALL 清空 `/rwir` 后签名丢失。

### 无 Select

不选后端实例：每个 opcode 的扩展运行时只监控**自己** `/lib/<opcode>/.todo<vid>`。谁注册了该 opcode，就由谁消费它的 handoff——注册即路由。

## 三、handoff 协议

### 键布局

```
/lib/<opcode>/.todo<vid>  = "pc|id"   # 中央 runtime 写，扩展运行时消费
/lib/<opcode>/.done<vid>  = id        # 扩展运行时写，中央 runtime 等
```

### 时序

```
kvlang runtime                        扩展运行时
─────────────                        ──────────
1. isUserRwir(opcode) 命中
2. id = handoffSeq++（全局原子递增）
3. Set .todo<vid> = "pc|id"
                                     4. List 到 .todo<vid>
                                        → 拆 "pc|id"
                                        → RunSeq(pc)：逐条顺序执行己方
                                          rwir 到非己方指令 → finalPC
                                        → WriteFinalPC：Set /vthread/<vid>/pc = finalPC
                                        → Set .done<vid> = id
                                        → Del .todo<vid>
5. Watch .done<vid> == id（30s 超时）
6. 继续（PC 已由扩展运行时写回）
```

关键：中央 runtime **不在此推进 PC**——扩展运行时批量执行后已把最终 PC 写回 `/vthread/<vtid>/pc`，中央 runtime 从 `.done` 返回后直接读新 PC 继续。

### RunSeq（顺序执行）

扩展运行时拿到 PC 后，`rwir.Decode` 逐条解码，只要 opcode ∈ 己方集合就执行，遇第一条非己方指令即停，返回其 PC 作为 finalPC。

**顺序执行，非并行 batch**：ext 的 rwir 之间存在顺序依赖（上一条的写参是下一条的读参），必须逐条按 PC 次序执行，与中央 runtime 执行指令流同构。己方 rwir 是叶算子（不跨帧），linkBase 恒定。

### 超时与幂等

- 超时 30s（`externalRwirTimeout`），超时 → `vthread.SetError`。
- id 由全局 `handoffSeq` 原子递增，保证同一 `.todo<vid>` 反复 handoff 时 id 唯一（循环里同一 PC 也唯一），天然幂等。

## 四、框架（rwir/ext）

`rwir/ext` 提供共享骨架，一个 rwirext 只需三件事：

```go
var rt = ext.Ext{
    Ops:  []ext.Op{{Name: "json.to", Sig: "...", Nr: 1, Nw: 1}, ...},
    Exec: exec,
}
func Register(kv kvspace.KVSpace) { rt.Register(kv) }  // 写 /lib/<opcode> 签名 + 全局标记
func Serve(kv kvspace.KVSpace)    { rt.Serve(kv) }     // 常驻：注册 + 监控 .todo + 批量 + 交还 PC

func exec(_ context.Context, kv kvspace.KVSpace, pc string, inst *rwir.Rwir) {
    // 按 opcode 分发到具体 handler
}
```

框架成员：

| 成员 | 职责 |
|------|------|
| `ext.Op` | 一个 rwir：Name（opcode）+ Sig（签名）+ Nr/Nw（读写参数量） |
| `ext.Ext` | 扩展运行时：Ops 集合 + Exec 回调 |
| `Register` | 写 `/lib/<opcode>` 签名 + `RegisterGlobalRwir`，幂等 |
| `Serve` | 常驻循环：注册 + 监控 `.todo` + 批量执行 |
| `RunSeq` | 从 PC 起逐条顺序执行 opcode ∈ ops 的 rwir（顺序依赖，非并行），返回下一条非己方指令 PC |
| `WriteFinalPC` | 把最终 PC 写回 `/vthread/<vtid>/pc` |

## 五、标杆：term 与 json

| | term | json |
|--|------|------|
| op | print/println/cerr/input | json.to/json.from |
| 进程 | 与中央 runtime 同进程（run.go 导入） | 独立进程（`rwirext/json/cmd`） |
| opcode | 裸名（init 注册全局） | dotted（`json.to`） |
| handler | 直接写 /dev/stdout\|stderr、读 /dev/stdin | 递归 KV↔JSON |

两者结构统一为：`var rt = ext.Ext{...}` + `Register`/`Serve` 两行 + `exec` 分发 + handler。livebyte 的 agent 扩展照此模板：声明 Ops、两行转发、实现 Exec。

## 六、三类场景

### 计算委托（op-gpu）

- **融合编译**在 compile pass 完成（FUSE_DB），handoff 协议不感知融合，只看到单条 rwir。
- **设备亲和**：tensor 落在哪个 storage，其计算路由到附着该 storage 的引擎——由 `/ext/` 拓扑注册表决定（见 [[kvspace篇-05-扩展存储拓扑]]）。
- **编译缓存**：`.so` 缓存共享，扩展运行时按需 dlopen。

### API 封装（llm）

- **必须异步**：扩展运行时独立 worker pool 处理 HTTP，中央 runtime 经 `.done` 等待。
- **session 约定**：`llm.chat` 输入是 session_id（字符串），扩展运行时自行读消息树、调 API、写结果。

### Agent 封装

- **sub-agent = vthread**：agent 扩展运行时收到 handoff 后 spawn 子 vthread 执行 agent 循环，agent 循环是普通 kvlang 函数，由标准 kvcpu 执行。
- **livebyte** 是第一个使用者：`subagent.spawn`/`subagent.await` 拆成两条 rwir，允许主 agent 在等待子 agent 期间继续执行（发起多个并行子 agent）。

## 七、kvcpu 集成

调度链（execute.go）：

```
1. IsControlOp  → handleControl        （call/return/br/goto）
2. IsNativeRwir → builtin.Native       （+/-/print/...）
3. isCopyOp     → ExecuteCopy          （= 赋值）
4. isUserRwir   → handoffExternalRwir  （/lib/<opcode> kind==rwir）
5. default      → rewrite as call      （用户函数）
```

- `isUserRwir(opcode)`：`/` 开头 → false；`/lib/<opcode>` 存在且 kind==rwir → true。
- `handoffExternalRwir`：写 `.todo<vid>="pc|id"`，Watch `.done<vid>==id`，不推进 PC。
- `dispatch` 包已删除：tensor 的 `/sys/op/` 调度不再保留，统一走 rwirext。

## 八、与 /ext/ 拓扑的关系

| 目录 | 回答 | 内容 |
|------|------|------|
| `/lib/<opcode>` | 有什么 op（签名） | rwir 签名（读参/写参/类型） |
| `/ext/` | 在哪、怎么执行（拓扑） | 存储/计算/通信注册（[[kvspace篇-05-扩展存储拓扑]]） |

无状态扩展运行时（term/json）只写 `/lib/`；有状态扩展引擎（op-gpu/heap-plat）写 `/lib/` + `/ext/`。handoff 协议一致，后者多一次 `/ext/` 查表定位引擎。
