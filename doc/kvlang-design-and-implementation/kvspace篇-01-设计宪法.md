# Chapter 1: Design Constitution（设计宪法）

import kvlang-design-and-implementation

## 0. 设计宪法

### 0.0 程序 = 数据结构 + 函数 + 数据

Niklaus Wirth 的经典公式「程序 = 数据结构 + 算法」统治了半个世纪——算法是主动的变换，数据结构是被算法操作的类型框架。Wirth 时代，程序员首先设计数据结构，然后为之编写算法。

kvlang 的主张：**程序 = 数据结构 + 函数 + 数据**。数据结构不再由用户自定义——kvlang 的全部数据结构都是 builtin：`struct ≡ dict`（键族前缀），`array`（TLV 或键族整数键），`link list`（`/n0.val`、`/n0.next` 平坦键，`"/n1"` 存路径字符串即指针）。agent 编程时不需要"设计数据结构"——只需要声明类型（`lib name { }`）和写函数（`def`），数据在 kvspace 四域（`/lib/` `/vthread/` `/sys/` `/dev/`）里自然落地。

就像 DNA 的 AT/GC 碱基对机制设计成功后，后续三十亿年的生物演化不再使用其它复杂的碱基对设计——最基础的数据结构一旦锁定，上层全部收敛为函数（蛋白质）与数据（基因表达产物）的组合。kvlang 同理：键族是它的碱基对，剩下的全是函数与数据。

**kvlang 是 agent-native 的训推一体自迭代强人工智能计算架构。** 以 kvspace 树形路径为统一地址空间，同一语法同时承担 VM 指令、高级语言、编译器 IR、人类可读源码四种职能。

### 0.1 设计目标

| 目标 | 含义 |
|------|------|
| **单层 IR** | 不分 HIR/LIR/MIR，同一 AST 贯穿解析→lower→执行 |
| **路径即语义** | PC 是 KV 路径字符串；调用栈深度=路径深度；帧是 kvspace 子树 |
| **底座分布式，语言单线程** | heap-plat 管理 shm、op-plat 消费 GPU 指令、VM 多 worker 并行——程序员只写数据流箭头 |
| **rwir（读写码）** | `<-`/`->` 显式命名读参写参，kvcpu 直接执行；高级语法由 lower 降级为 rwir |
| **可观测性** | 所有执行状态在 kvspace 路径中实时可读 |
| **agent-native** | 推理、训练、RL、agent 任务流统一在 kvspace 执行模型上完成 AI 自我迭代 |

与工业界架构的对比：

| | LLVM | JVM | kvlang |
|--|------|-----|--------|
| IR 层数 | C→IR→MIR→MC | Java→Bytecode→JIT | **单层**：源码即 IR |
| 地址空间 | 虚拟内存 | 堆+栈 | **kvspace 树形路径** |
| 数据流 | SSA (phi/alloca) | 操作数栈 | **rwir** `<-`/`->` 显式绑定槽 |
| 调用栈 | 内存栈段 | Stack Frame 链表 | **路径深度=栈深度** |
| 并发 | 多线程共享内存 | JVM 线程+GIL | **多 worker+路径所有权** |
| 崩溃恢复 | 全失 | 全失 | **重启继续**（PC 已持久化） |

### 0.2 地址空间

kvspace 树形路径分四个系统域，借用 Unix 文件系统思想；**其余 `/` 路径全部自由，由用户定义**：

```
/lib/{pkg}.{name}         编译后函数（签名 + 指令树）+ .src 源码副本
/vthread/{vid}/           虚线程栈帧（运行时）
/sys/                     系统基础设施（VM/op-plat）
/dev/                     外部 I/O 设备（/dev/tty 终端、/dev/screen 屏幕）
```

- `/lib/` 借鉴 Unix `/lib/`——共享库的标准路径，是函数（编译产物）的单一事实源。`lib name { }` 命名空间块声明包。多文件通过 `kvlang layoutrwir <files...>` 拼接为单一源→parse→lower→写入 `/lib/`，无 `import` 关键字——lib 树即全局命名空间，跨 lib 调用走全路径 `/lib/{lib}.{func}()`。`.src` 源码副本与指令树同目录。已加载文件自动去重（Python `sys.modules` 式），循环导入无错误跳过
- `/vthread/` 是运行时栈帧，借用 Unix `/proc/<pid>/` 思想——每 vthread 一棵子树，`.pc`/`.status` 等系统键暴露执行状态；帧根本身是 extindex 指向 `/lib/` 指令树
- `/sys/` 是基础设施注册表（VM 心跳、op 算子列表），借用 Unix `/sys/` 伪文件系统思想
- `/dev/` 借鉴 Unix `/dev/`——I/O 边界。`/dev/tty/`（终端输入输出）、`/dev/screen`（屏幕渲染）。外部设备挂载为 kvspace 子树，读写设备 = 读写 kvspace 键
- 四域之外的 `/` 路径（如 `/counter`、`/n0.val`、`/tmp/seen`）完全由用户代码定义——kvspace 不预设 schema，只提供 Write/Read/Watch 原语

**kvspace 存储铁律**：Key 必须是字符串路径（`/` 分隔的树形层级）；Value 必须是 XValue 序列化后的字节数组。**严禁**直接写入基础类型的裸值、裸字符串、JSON——所有值必须经 XValue 编解码。违反此铁律的写入在 reader 侧读到非法字节时 behavior undefined。

kvspace 存储两类数据：**基础数据类型**（int、float、bool、string）和 **tensor 元数据**（shape、dtype、指向扩展存储的句柄）。tensor 完整数据在扩展存储中：

| 扩展位置 | 典型数据 |
|---------|---------|
| 集群节点共享内存 | 大张量、激活值（heap-plat 管理生命周期） |
| GPU 显存 | 计算张量（op-plat 在设备侧持有句柄） |
| 文件系统/对象存储 | 模型权重、检查点、数据集 |

### 0.3 指令分类

指令分三层，执行层只见前两层：

1. **rwir**（kvcpu 直接执行）：`writes <- opcode(reads...)` 或 `opcode(reads...) -> writes`。读参写参由箭头方向决定，无隐式栈、无匿名寄存器。`writes = expr` 是 `<-` 的等价书写（写槽在左）；与其它语言的 `=` 不同，读/写角色仍由指令形态严格约束，`=` 不是表达式、不可嵌套在条件中。存储布局见 §2 二维空间模型。
2. **`def func(ra,rb) -> (wa,wb) { … }` = 自定义复合rwir**，也叫自定义函数。单条rwir `A + B -> C` 是原子rwir（一个操作码 + 读参 + 写参）；`def` 把多条rwir打包成一个命名单元，对外暴露相同的箭头接口——`(ra,rb)` 是读参声明，`-> (wa,wb)` 是写参声明。调用 `add(3,4) -> s` 即把实参绑入读槽、写槽映射回调用方帧。**调用必须匹配全部写参**——不想要的用 `._` 丢弃（对齐 Go `_`/Rust `_`，非 Python 约定变量名；`frameSlotKey` 直接返回空路径，不落盘）。设计与 rwir 一脉相承：箭头方向决定数据流向，无论单条还是复合，契约一致。

**rwfunc——复合 rwir 的深层意义**。`def` 不做传统编译器的"函数体→字节码→调用约定"三级跳——它只是把多条 rwir 装进一个命名单元，对外暴露的仍是 `(reads) -> (writes)` 箭头接口。这意味着 kvlang 的调用栈不是"压栈+跳转+返回"，而是**写参的跨帧映射**：HandleCall 将实参绑入子帧读槽，HandleReturn 将子帧写槽的值搬回父帧——整个过程没有"返回值"这个概念，只有槽位间的数据流动。rwfunc 因此可以看作**带帧边界的 rwir**：原子 rwir 在同一帧内完成读写，rwfunc 跨越父子帧完成读写，而 lib 命名空间（§0.6）则是 rwfunc 的再上一层聚合。从 `A+B->C` 到 `def add` 到 `lib math`，**三层同一范式**——箭头方向决定数据流向，槽位显式声明读写角色，源码即数据流图。
3. **控制流原语**（rwir子集）：`call`/`return`/`br`/`goto`——改变 PC，kvcpu 专门分发。

**调用写参 arity 与 `._` 丢弃槽**。kvlang 选 Go/Rust 阵营：调用时必须匹配全部写参——要么接收，要么 `._`。`f() -> s` 对多写参函数是编译错误。

| 语言 | 多输出调用 | 丢弃部分输出 | 强制 arity？ |
|------|----------|-------------|------------|
| Go | `q, r := divmod(17, 5)` | `_, r := divmod(17, 5)` | **是** — `x := f()` 对多返回值编译报错 |
| Rust | `let (q, r) = divmod(17, 5)` | `let (_, r) = divmod(17, 5)` | **是** — pattern must match |
| Python | `q, r = divmod(17, 5)` | `_, r = divmod(17, 5)` | 否 — `x = f()` 拿到整个 tuple |
| C | `divmod(17, 5, &q, &r)` | `divmod(17, 5, NULL, &r)` | 否 — 传 NULL，编译器不管 |
| V8/TS | `const [q, r] = divmod(17, 5)` | `const [, r] = divmod(17, 5)` | 否 — `const x = f()` 静默丢弃其余 |

`._` 是语言内置的正式丢弃槽（对齐 Go `_`/Rust `_`）：parser 识别、`frameSlotKey` 对 `.` 前缀 slot 直接返回空路径——不落盘、不占 KV 存储。与 Python/JS 的 `_` 不同：后者只是约定（变量名，仍占内存），kvlang 的 `._` 是引擎语义——从未分配 slot。
4. **高级语法**（lower 后消失）：`if`/`else`、`while`、`for`、`label:`——写入 `/lib/` 前降级为基本块+br，kvcpu 不感知。

### 0.4 模块职责

| 模块 | 路径 | 职责 |
|------|------|------|
| **ast** | `internal/ast/` | 单层 IR 类型体系：Operand/FuncSig/Stmt/Instruction/File，Walk/Visitor |
| **parser** | `internal/parser/` | Scan→Token→递归下降→`*ast.File`，含 Diagnostic 错误收集 |
| **lower** | `internal/lower/` | 同类型变换 pass：IfStmt/WhileStmt → BlockStmt+br |
| **keytree** | `internal/keytree/` | 路径系统：将运行时概念映射到 kvspace 键路径 |
| **layoutrwir** | `internal/layoutrwir/` | Linker：WriteFunc(编译期写入) + HandleCall/Return(运行时帧管理) |
| **kvcpu** | `internal/kvcpu/` | 执行引擎：Fetch-Decode-Execute+调度器+控制流 |
| **kvspace** | `github.com/array2d/kvspace-go`（外部模块） | KV 存储接口 14 方法：Get/Set/Del/GetMany/MSet/List/DelTree/Notify/Watch/Link/Unlink/ClearAll/DisConn |
| **vthread** | `internal/vthread/` | vthread 状态管理：Get/Set/SetDone/SetError/Create/WaitDone |
| **vtype** | `internal/vtype/` | 可扩展算子类型注册：str/tensor 命名空间 |
| **builtin** | `internal/op/builtin/` | 标量内建算子：算术/比较/逻辑/cast/IO |

模块依赖图：

```
cmd/kvlang
  ├── parser ──► ast
  ├── lower ──► ast
  ├── layoutrwir ──► keytree + kvspace + ast
  ├── kvcpu ──► layoutrwir + keytree + vthread + vtype + builtin + op
  ├── vthread ──► keytree + kvspace
  └── kvspace (接口)
```

### 0.5 禁止项

| 编号 | 禁止 | 理由 |
|------|------|------|
| R1 | 任何包依赖高于自身层级的设计包 | 依赖单向：cmd→kvcpu→layoutrwir→keytree/ast |
| R2 | 运行时包 import parser/lower/ast | 编译与执行分离 |
| R3 | 硬编码 kvspace 路径字符串在 keytree 之外 | 所有路径经由 keytree 函数生成 |
| R4 | kvspace 直接写入裸值（int/float/string/JSON） | 所有 Value 必须经 XValue 序列化为字节数组；Key 必须是字符串路径 |
| R5 | 破坏单层 IR：新增 HIR/LIR 分层 | kvlang 只一层 IR |
| R6 | 帧销毁用 List+Del 代替 DelTree | DelTree 是原子操作 |
| R7 | 模块间循环依赖 | 编译期杜绝 |
| R8 | `fmt.Fprint*` 直接写 stderr 做诊断 | 所有诊断必须经 `internal/logx`（§0.7）；usage/help/格式化除外 |

### 0.6 lib 树、CLI 装载与执行模型（fix-033/034/039）

**lib 树**：`kvlang layoutrwir` 将多个 `.kv` 文件拼接为单一源→parse→lower→写入 `/lib/`。
每个 `lib name { }` 块形成一个 lib 节点，每个 lib 有且仅有一个 `init` 函数（init 体 + 顶层代码合并）。
`kvlang layoutrwir` 完成后形成一棵 `/lib/` 下的 lib 树。

**执行模型**：
- `kvlang run`（无参数）→ 执行 `/lib/.init`（匿名 lib 的 init）
- `kvlang run {childlib}.{func}` → 执行 `/lib/{childlib}.{func}`（`/lib/` 前缀可省略，func 默认 `init`）
- `kvlang layoutrwirandrun <files…>` → 先 load，再 run（等价 `kvlang layoutrwir <files> && kvlang run`）
- `kvlang layoutrwir <file|dir>` → 多文件拼接合并为单源→parse→lower→写入 `/lib/`。每个 lib 含该 lib 的全部函数 + 一个 init。文件夹递归收集 `.kv` 并拼接

**跨 lib 调用**：使用全路径 `/lib/{childlib}.{func}()`，kvcpu 经 `LibIdx("{childlib}.{func}")` 查 pkg→`LibFunc(pkg, name)` 定位指令树→Link→执行。无 `import` 机制——lib 树已在 kvspace 中，调用即路径。

**`def init() -> () { }` 初始化函数（fix-036）**：与普通 def 语法一致，支持 `if`/`while`/`for`/赋值/函数调用。裸顶层代码自动封装为隐式 `def init() -> () { }`。

```kv
lib math {
    def sum(A:int, B:int) -> (C:int) { A + B -> C }
}

def init() -> () {
    /lib/math.sum(3, 4) -> s   # 跨 lib 调用：全路径
    print(s)
}
```

- `lib name { }` 借鉴 C++ `namespace` / Rust `mod`。支持嵌套 `lib a { lib b { … } }` 形成 `a.b` 级联包名，也支持扁平 `lib a/b/c { }`。每个 lib 含一个 `init` 函数（该 lib 的入口）
- lib 树中每个 lib 节点注册在 `/lib/<name>.{func}` 下（`/lib/math.sum`、`/lib/math.init`）
- 无 lib 包裹的 def 属于匿名 lib（路径 `/lib/.{func}`，`kvlang run` 无参默认执行 `/lib/.init`）
- 源码存储：`WriteFunc` 写入 `/lib/<pkg>.<name>.src`（fix-034），与指令树同目录
- 无 `import` 关键字——lib 树即全局命名空间，跨 lib 调用走全路径 `/lib/{lib}.{func}()`

### 0.7 诊断输出规范（logx）

**所有 stderr 诊断输出必须通过 `internal/logx` 包，禁止直接调用 `fmt.Fprint*`。** 输出格式对齐五大语言编译器（GCC/Go/V8）：`{level}: {context}: {msg}`，无时间戳、无 key=value。

**logx API 范式：**

| 函数 | 前缀 | 用途 |
|------|------|------|
| `Debug/Info` | 无 | 操作消息、调试追踪（仅 `LOG_LEVEL=debug`/`info` 可见） |
| `Warn` | `warn: ` 自动 | 可恢复警告 |
| `Error` | `error: ` 自动 | 错误信息 |
| `Fatal` | 同 Error | 错误 + `os.Exit(1)` |
| `Diag(d)` | Diagnostic 自带 | parser 诊断单行输出 |
| `DiagWithSource(d)` | Diagnostic 自带 | parser 诊断 + 源码行 + `^` caret |

**明确豁免 `fmt.Fprint*` 的情形：**
- `flag.FlagSet.Usage` 内的 usage 文本（无前缀的说明文字）
- help 命令的输出（完整帮助文档）
- `fmt.Printf` 到 stdout 的成功状态（如 `%s: OK`）
- `ast.Dump` / `ast.Format` 的格式化输出（到 `io.Writer`，非诊断）

**原则：如果你在写诊断，走 logx；如果你在写内容（help / usage / 格式化 / stdout 结果），走 fmt。**
