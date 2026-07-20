# kvlang 深度理解

> 本文档是 kvlang 项目的**根节点设计文档**（仓库根 `DESIGN.md` 软链于此，以本文为准）。
> §0 是设计宪法：定位、地址空间、指令分类、模块职责、禁止项；§1 起逐主题深度展开。
> 语法 BNF：[grammar.bnf](grammar.bnf)——**每次 deep-dive 更新后需验证 BNF 与实现一致**（`tutorial/test.py` + `error_test.py` 全量通过）。

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

- `/lib/` 借鉴 Unix `/lib/`——共享库的标准路径，是函数（编译产物）的单一事实源。`lib name { }` 命名空间块声明包、`import lib`（裸名 kvspace 引用）或 `import "lib"`（引号文件糖，自动找 `lib.kv`）填充包、`import "pkg" as c` 别名在 parse 期还原全路径、`.src` 源码副本与指令树同目录。已加载文件自动去重（Python `sys.modules` 式），循环导入无错误跳过
- `/vthread/` 是运行时栈帧，借用 Unix `/proc/<pid>/` 思想——每 vthread 一棵子树，`.pc`/`.status`/`.fn` 等系统键暴露执行状态
- `/sys/` 是基础设施注册表（VM 心跳、op 算子列表），借用 Unix `/sys/` 伪文件系统思想
- `/dev/` 借鉴 Unix `/dev/`——I/O 边界。`/dev/tty/`（终端输入输出）、`/dev/screen`（屏幕渲染）。外部设备挂载为 kvspace 子树，读写设备 = 读写 kvspace 键
- 四域之外的 `/` 路径（如 `/counter`、`/n0.val`、`/tmp/seen`）完全由用户代码定义——kvspace 不预设 schema，只提供 Write/Read/Watch 原语

kvspace 存储两类数据：**基础数据类型**（int、float、bool、string）和 **tensor 元数据**（shape、dtype、指向扩展存储的句柄）。tensor 完整数据在扩展存储中：

| 扩展位置 | 典型数据 |
|---------|---------|
| 集群节点共享内存 | 大张量、激活值（heap-plat 管理生命周期） |
| GPU 显存 | 计算张量（op-plat 在设备侧持有句柄） |
| 文件系统/对象存储 | 模型权重、检查点、数据集 |

### 0.3 指令分类

指令分三层，执行层只见前两层：

1. **rwir**（kvcpu 直接执行）：`writes <- opcode(reads...)` 或 `opcode(reads...) -> writes`。读参写参由箭头方向决定，无隐式栈、无匿名寄存器。`writes = expr` 是 `<-` 的等价书写（写槽在左）；与其它语言的 `=` 不同，读/写角色仍由指令形态严格约束，`=` 不是表达式、不可嵌套在条件中。存储布局见 §2 二维空间模型。
2. **`def func(ra,rb) -> (wa,wb) { … }` = 自定义复合rwir**，也叫自定义函数。单条rwir `A + B -> C` 是原子rwir（一个操作码 + 读参 + 写参）；`def` 把多条rwir打包成一个命名单元，对外暴露相同的箭头接口——`(ra,rb)` 是读参声明，`-> (wa,wb)` 是写参声明。调用 `add(3,4) -> s` 即把实参绑入读槽、写槽映射回调用方帧。设计与 rwir 一脉相承：箭头方向决定数据流向，无论单条还是复合，契约一致。

**rwfunc——复合 rwir 的深层意义**。`def` 不做传统编译器的"函数体→字节码→调用约定"三级跳——它只是把多条 rwir 装进一个命名单元，对外暴露的仍是 `(reads) -> (writes)` 箭头接口。这意味着 kvlang 的调用栈不是"压栈+跳转+返回"，而是**写参的跨帧映射**：HandleCall 将实参绑入子帧读槽，HandleReturn 将子帧写槽的值搬回父帧——整个过程没有"返回值"这个概念，只有槽位间的数据流动。rwfunc 因此可以看作**带帧边界的 rwir**：原子 rwir 在同一帧内完成读写，rwfunc 跨越父子帧完成读写，而 lib 命名空间（§0.6）则是 rwfunc 的再上一层聚合。从 `A+B->C` 到 `def add` 到 `lib math`，**三层同一范式**——箭头方向决定数据流向，槽位显式声明读写角色，源码即数据流图。
3. **控制流原语**（rwir子集）：`call`/`return`/`br`/`goto`——改变 PC，kvcpu 专门分发。
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
| **kvspace** | `github.com/array2d/kvlang-go`（外部模块） | KV 存储接口 13 方法：Get/Set/Del/DelTree/List/Watch/Notify/Link/ClearAll 等 |
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
| R1 | 任何包 import 高于自身层级的设计包 | 依赖单向：cmd→kvcpu→layoutrwir→keytree/ast |
| R2 | 运行时包 import parser/lower/ast | 编译与执行分离 |
| R3 | 硬编码 kvspace 路径字符串在 keytree 之外 | 所有路径经由 keytree 函数生成 |
| R4 | 破坏单层 IR：新增 HIR/LIR 分层 | kvlang 只一层 IR |
| R5 | 帧销毁用 List+Del 代替 DelTree | DelTree 是原子操作 |
| R6 | 模块间循环依赖 | 编译期杜绝 |

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

## 1. 寻址模型：KV 路径 vs 内存地址

### 传统 VM (Python/Lua/JVM)

```
程序计数器 PC = 0x7fff5fbff830 (64-bit 内存地址)
指令    = 内存[PC] → 1 字节 opcode → 操作数
跳转    = PC = 新地址 (直接修改寄存器)
调用    = push 返回地址 → PC = 函数入口地址
栈帧    = 连续内存 [rbp-8] = 局部变量
```

内存地址是**一维线性整数**，跳转和调用本质是整数算术。

### kvlang

```
程序计数器 PC = "[0,0]/entry/[0,0]" (KV 路径字符串)
指令    = kv.Get("/vthread/tid/[0,0]/entry/[0,0]")
跳转    = PC = "[0,0]/merge/[0,0]" (字符串拼接)
调用    = PC = "[0,0]/then/[0,0]" (路径嵌套)
栈帧    = /vthread/tid/[0,0]/ 子树 (KV key 层级)
```

KV 路径是**树形层级字符串**，跳转和调用本质是路径拼接 + 子树导航。

| 维度 | x86/ARM | Python | Lua | kvlang |
|------|---------|--------|-----|--------|
| PC 类型 | `uint64` | `*PyCodeObject + offset` | `Instruction*` | `string` (KV path) |
| 指令获取 | `mov rax, [rip]` | `_PyEval_EvalFrameDefault` 循环 | `luaV_execute` 循环 | `kv.Get("/vthread/tid/" + pc)` |
| 跳转 | `jmp 0x400100` | `next_instr += oparg` | `pc++` | `pc = new_path` |
| 调用 | `call 0x400200` | `call_function` 压栈 | `luaD_precall` | `pc = pc + "/[0,0]"` |
| 栈帧 | `push rbp; sub rsp, N` | `PyFrameObject` (堆分配) | `CallInfo + L->stack` | `/vthread/tid/<pc>/` KV 子树 |
| 作用域 | 栈偏移 | `f_localsplus` 数组 | 寄存器索引 | KV key 子路径（裸名 `x`, `y`） |

## 8. 变量名即指针

kvlang 没有 `&` 取址运算符——**代码中对象的变量名，本身就是这个变量的指针**（kvspace 路径）。指令槽里存的从来不是值，而是指针文本（`[0,-1] = "A"`、`[0,1] = "C"`），求值永远经过一次指针间接。

指针分两种形态：

| 形态 | 写法 | 语义 | 解析时机 |
|------|------|------|---------|
| **相对指针** | 裸标识符 `x` | 相对当前栈帧的偏移 | 运行时与栈路径拼接 |
| **绝对指针** | `/counter` | kvspace 全局绝对路径 | 零拼接，直接 Get/Set |

**局部变量的变量名就是相对指针**。运行时解析公式：

```
绝对路径指针 = FrameRoot(PC) + "/" + 相对指针

例：PC = /vthread/7/[3,0]/.fn/[1,0]
    FrameRoot(PC) = /vthread/7/[3,0]      ← 截断最后一个 /.fn/
    x → /vthread/7/[3,0]/x
```

栈路径（帧根）不需要单独的寄存器——PC 本身是帧内路径（帧的代码区在 `帧根/.fn/` 下），`FrameRoot(PC)` 截取即得。这与 C 的 `rbp + offset` 同构：

| | C/x86 | kvlang |
|--|-------|--------|
| 帧基址 | `rbp` 寄存器 | `FrameRoot(PC)`——从 PC 截取 |
| 局部变量地址 | `rbp - 8`（基址 + 偏移） | `帧根 + "/x"`（栈路径 + 相对指针） |
| 全局变量地址 | `.data` 固定地址 | `/` 开头绝对指针，零拼接 |
| 指针变量 | 存整数地址 | 存路径字符串：`"/n0" -> ptr`，`ptr.val` 解引用 |

`/lib/` 下的函数模板中只有相对指针，因此天然可重入：每次调用创建不同的帧路径，同一份相对指针拼接出互不干扰的绝对指针——递归、TCO 无需任何额外机制。

这解释了为什么全局变量 `/counter` 零成本——绝对指针不经过帧前缀拼接。也解释了为什么数组能作为参数传递——`flattenNestedCalls` 将 `[1,2,3]` 展开为临时变量，再将临时变量（持有 XValue）作为普通参数传递。

**参数不得同名（fix-032）**：变量名即指针——同一帧内两个同名参数将指向同一个 kvspace 位置。读参列表内部、写参列表内部、以及读写列表之间均不可同名。`def f(A:int) -> (A:int)` 签名本身非法——A 不能同时是读参和写参。parser `checkParamDup` 阻断源码路径，VM `checkDupParams` 兜底 agent 直写 KV 构造的非法签名。error_case 锚点：`tutorial/error_cases/read_only/dup_param.kv`。

## 2. 指令的二维空间模型：`[s0, s1]`

### 2.1 两个轴的含义

每条指令在 KV 树中占据一个**二维坐标** `[s0, s1]`：

```
s0 轴（横轴） — 执行顺序轴：第几条指令
s1 轴（纵轴） — 参数轴：该槽的角色

        s1 < 0           s1 = 0         s1 > 0
      (读参，输入)        (操作码)       (写参，输出)
           ←─────────────── 0 ───────────────→
s0 = 0  │  [0,-2] [0,-1]  [0,0]  [0,1] [0,2]
s0 = 1  │  [1,-2] [1,-1]  [1,0]  [1,1]
s0 = 2  │  [2,-1]         [2,0]  [2,1]
  ...
```

**铁律**：
- `[s0, 0]` **永远是 opcode**（操作符或被调用函数名）
- `[s0, -1], [s0, -2], ...` **读参**（read slots），负号代表"消费数据"
- `[s0,  1], [s0,  2], ...` **写参**（write slots），正号代表"产出数据"

---

### 2.2 具体示例

```kv
def add(A: int, B: int) -> (C: int) {
    A + B -> C
}
```

lower + layoutrwir 后写入 KV：

```
/lib/main.add/[0,0]   = "+"      ← s1=0: opcode
/lib/main.add/[0,-1]  = "A"      ← s1=-1: 第1个读参（left operand）
/lib/main.add/[0,-2]  = "B"      ← s1=-2: 第2个读参（right operand）
/lib/main.add/[0,1]   = "C"      ← s1=1:  第1个写参（result destination）

/lib/main.add/[1,0]   = "return" ← s1=0: opcode（隐式 return，appendReturn 补充）
```

图示为一张二维表格：

```
       s1=-2   s1=-1   s1=0      s1=1
s0=0 │  "B"    "A"    "+"      "C"
s0=1 │                "return"
```

数据流方向清晰：**从负轴读入，在零轴执行，向正轴写出**。

---

### 2.3 多参数指令与扇出

```kv
print("hello", x, y)
```

```
/lib/main.foo/[3,0]   = "print"   ← opcode
/lib/main.foo/[3,-1]  = "\"hello" ← 第1读参（字符串字面量，" 前缀编码）
/lib/main.foo/[3,-2]  = "x"       ← 第2读参
/lib/main.foo/[3,-3]  = "y"       ← 第3读参
（无写参，副作用是输出）
```

写参扇出（同一结果写入多个槽）：

```kv
a + b -> sum, backup
```

```
/lib/main.foo/[2,0]   = "+"
/lib/main.foo/[2,-1]  = "a"
/lib/main.foo/[2,-2]  = "b"
/lib/main.foo/[2,1]   = "sum"    ← 第1写参
/lib/main.foo/[2,2]   = "backup" ← 第2写参（扇出）
```

拷贝指令（叶表达式 → 写槽）编码为显式操作码 `=`，值引用放读槽：

```kv
a -> b        # [s0,0]="="  [s0,-1]="a"   [s0,1]="b"
42 -> x       # [s0,0]="="  [s0,-1]="42"  [s0,1]="x"
```

opcode 位**永远是操作码**，从不放变量引用——`=` 使拷贝与零参函数调用（`greet() -> x`，opcode="greet"）在 KV 层无歧义。

---

### 2.4 `op.Decode`：沿 s1 轴扫描

kvcpu 解码时沿 s1 轴向两侧扩展，直到遇到空 key 为止：

```go
// 读参：s1 = -1, -2, -3 ... 直到 kv.Get 返回空
// 写参：s1 = +1, +2, +3 ... 直到 kv.Get 返回空
```

这意味着参数数量是**隐式编码的**：紧凑分配，无需在 opcode 中存储 arity。

**WriteFunc 的正确性约束**：写新函数前必须先 `DelTree` 清除旧数据。  
若旧函数在 `[0,-1]` 有遗留值，新函数的 Decode 会把它当成真实读参——这正是之前 `a -> b` 被错误解读为 `a(1, 2) -> b` 的根因。

---

### 2.5 与传统字节码的对比

| 维度 | 传统字节码 | kvlang `[s0, s1]` |
|------|-----------|-------------------|
| 指令存储 | 线性数组 `code[PC]` | KV 树 `kv.Get(prefix + "[s0,0]")` |
| 操作数位置 | opcode 后紧跟操作数 | `s1<0`（读）/ `s1>0`（写）分离 |
| 参数数量 | opcode 内编码 arity | 隐式：扫描到空 key 停止 |
| 数据流方向 | 单向（操作数 → 结果） | 符号编码（负=读, 正=写） |
| 可观察性 | 字节不可独立寻址 | 每个槽是独立 KV key，可单独 Get/Watch |
| 调试 | 需要反汇编器 | `kv.List("/lib/main.add")` 即可 |

---

### 2.6 为什么负数表示读参

这不是约定俗成，而是数轴对称性的自然选择：

```
写（产出）→   0  ← 读（消费）
+1 +2 +3 … [opcode] … -1 -2 -3
```

- **正数轴**：数据**流出**（写入目标槽，可类比 "加法方向"，产出新值）
- **负数轴**：数据**流入**（读取源槽，可类比 "减法方向"，消费已有值）
- **零点**：执行中心（opcode 本身不消费也不产出，只定义操作语义）

同一条指令的二维坐标在整个 vthread 生命周期内唯一不变，既是地址，又是数据流图的节点描述。

---

## 3. 函数无返回值：只有读参与写参

### 3.1 核心原则

kvlang 函数**没有返回值**。这是和绝大多数语言的根本区别。

传统语言：
```python
result = add(a, b)        # 函数"返回"一个值，赋给 result
```

kvlang：
```kv
add(a, b) -> result   # -> result 是写参映射：被调方写参 C 落到本帧 result 位置
add(a, b)             # 不接收：写参 C 只写入子帧（纯副作用）
```

kvlang 函数只有两种参数：

| 参数类型 | KV 槽位 | 方向 | 说明 |
|---------|---------|------|------|
| **读参**（Read Params） | `[s0, -1], [s0, -2], ...` | 调用方 → 被调方 | 函数的输入，调用时绑定值 |
| **写参**（Write Params） | `[s0, +1], [s0, +2], ...` | 被调方 → 调用方 | 函数的输出，return 时写回父帧 |

---

### 3.2 写槽即位置

```kv
def add(A: int, B: int) -> (C: int) {
    A + B -> C            # 写参 C 在被调方帧中写入
}
```

函数签名中的 `-> (C: int)` 是**写参声明**，不是"返回值类型"。

写槽（`->` 右侧 / `<-` 左侧）必须是**位置**（指针，§8）：

```kv
# ✅ 位置的三种形态
add(a, b) -> s            # 裸名 s —— 本帧位置（帧根 + "/s"）
add(a, b) -> /global/s    # 绝对路径 —— 全局位置
add(a, b) -> obj.prop     # 成员表达式 —— 键族成员位置（§10）

# ✅ 多写参映射：逗号分隔，与签名写参一一对应
divmod(17, 5) -> q, r      # q←商, r←余数
addmul(x, y) -> s, p       # s←x+y, p←x*y

# ❌ 字面量不是位置
add(a, b) -> 42
add(a, b) -> "s"
```

`-> s` 不意味着"函数返回值赋给 s"——它是**写参的跨帧路径映射**（§3.3）：被调方帧的写参 C，
由 HandleReturn 写到调用方帧的 s 位置。语义上仍然没有"返回值"这个东西。

> 历史：早期设计要求写槽显式写 `./s` 以区分"位置"与"值"。`./` 前缀已全面废除
> （避免 `.`、`/` 在 parser/VM 中的多次拼接变换引入 bug）——裸名本身就是位置（变量名即指针）。

**读参只读公理（fix-027）**：读参是「调用方 → 被调方」的输入绑定，函数体内把读参裸名放进写槽
（含子函数调用的写参映射、`for` 迭代变量）即破坏rwir数据流方向——双阶段拦截：
parser error 级诊断拒绝装载；kvcpu 写槽前置检查（帧 `.ro` 名单，Bootstrap/HandleCall 写入）SetError 终止。
成员写（`A.x = v`，脱糖为 set 本体回写）不写本体，豁免。
注：五语言按值传参均允许参数重赋值——kvlang 更严格，因为读参槽位是 IR 数据流分析的方向锚点（§2.6），
这是 kvlang 作为单层 IR 的自我一致性要求，非行为对齐项（p7 例外，显式记录）。

**推论：一个参数，即使读参也是写参，那它就是写参。** 写参体内可读可写，写参角色覆盖读参。
公理的真正作用是**逼迫签名诚实**——如果你在函数体内写了某个参数，它就不是纯输入，签名必须把它放到写参侧：

```kv
# ❌ 签名撒谎：acc 被写但放在读参侧 → 报错
def sum(arr, acc:int) -> (r:int) { acc + arr[0] -> acc }

# ✅ 签名诚实：acc 是写参 → 可读（首读 nil=0，fix-017）也可写
def sum(arr) -> (acc:int) { acc + arr[0] -> acc }
```

写参 ≡ Go 命名返回值 `func sum(arr []int) (acc int)`（零值起步、体内读写、return 带出）；
读参只读 ≈ Fortran `intent(in)` / Ada `in`。纯工作变量（调用方不需要最终值）才拷贝局部：

| 变量类型 | 签名位置 | 写法 |
|---------|---------|------|
| **累加器/输出**（调用方要最终值） | 写参 | `acc + x -> acc`（体内可读可写） |
| **纯工作变量**（调用方不需要） | 不出现 | 首行拷贝局部：`A -> a` 后用 `a` |
| **跨层传递**（递归下层输入） | 读参 | 本层只读：`rsum(arr, i+1, acc + arr[i]) -> r` |

教学锚点：`tutorial/02-func/accumulator.kv`。

---

### 3.3 为什么没有返回值

传统语言的"返回值"本质是**调用栈上的一块内存**——函数执行完毕，这块内存的值被拷贝给调用者。

kvlang 没有线性调用栈，每个函数调用创建一个 KV 子树：

```
/vthread/run/               ← 调用方帧根
/vthread/run/.fn/           ← 调用方代码区（软链接）
/vthread/run/[3,0]/         ← 被调方帧根（由调用 PC 决定）
/vthread/run/[3,0]/C        ← 被调方的写参槽 C
```

"返回值"在 kvlang 里的正确表述是：  
**HandleReturn 把被调方帧的写参槽值，写回到调用方帧的指定路径**。

这个"指定路径"通过调用时的写槽声明传递：

```
# 指令级别（lower 后的 KV 表示）：
[3,0] = "add"              ← opcode（函数名）
[3,-1] = "a"               ← 读参 A
[3,-2] = "b"               ← 读参 B
[3,1] = "sum"              ← 写参 C 的目标路径（HandleReturn 时写入）
```

HandleReturn 经子帧的 `.callpc` 定位调用指令，读取其写槽 `[3,1]`：

```
kv.Get(childFrame + "/C")  →  value
kv.Set(parentFrame + "/sum", value)
```

这不是"函数返回值"，是**写参的跨帧路径映射**。

---

### 3.4 `->` / `<-` / `=` 与写槽

三种指令书写形态，写槽约束完全一致：右侧/左侧的写槽必须是**位置**。

| 形态 | 写槽位置 | 例 |
|------|---------|-----|
| `expr -> writes` | 右 | `A + B -> C` |
| `writes <- expr` | 左 | `C <- A + B` |
| `writes = expr` | 左（≡ `<-`） | `C = A + B`、`p.val = 8` |

`=` 与其它语言的赋值不同：它只是 `<-` 的别名，读/写角色由指令形态约束——`=` 不是表达式，
不能出现在条件里（`if (x = 5)` 这类错误类在语法层就不存在），源码与 KV 层的拷贝操作码 `=` 同形（§2.3）。

| 位置 | 含义 | 合法写槽 |
|------|------|---------|
| 普通指令 `A + B -> C` | 表达式结果写入位置 | 裸名、`/abs`、`base.名` |
| 函数调用 `add(a,b) -> s`（含隐式 return 映射） | 被调方写参目标位置 | 裸名、`/abs`、`base.名` |
| ~~`f() -> 42`~~ / ~~`f() -> "s"`~~（错误） | 字面量不是位置 | — 不合法 — |

**铁律**：`->` 右侧 / `<-`、`=` 左侧必须是位置（指针）；字面量（数字、引号串）在写槽位置是语法错误，Parser 拒绝并报警。

---

### 3.5 Parser 写槽校验现状

`collectWriteList` 已做写槽合法性校验：

- 字面量写槽（数字/引号串开头）→ warning「unexpected token in write slot position」
- 写槽后紧跟 `(` → warning「function call on same line as write slot」（同行第二条指令）
- `./x` 已废除：`.` 起始的写槽 token 同样触发 warning，只有裸名、`/abs`、`base.名` 通过

---

## 4. 控制流的 KV 寻址优势

### 4.1 label 即路径

```
def 分支示例(flag, X) -> (R) {
    entry: { X + 1 -> a; br(flag, then, else) }
    then:  { a * 2 -> b; goto(merge) }
    else:  { a * 3 -> b; goto(merge) }
    merge: { b + 10 -> R; return }
}
```

label `then` 不是符号表条目，是 KV 路径段：

```
/vthread/tid/[0,0]/entry/[0,0]  = "+"
/vthread/tid/[0,0]/entry/[0,-1] = "X"
/vthread/tid/[0,0]/entry/[0,1]  = "a"

/vthread/tid/[0,0]/then/[0,0]   = "*"
/vthread/tid/[0,0]/merge/[0,0]  = "+"
```

`goto(merge)` → `PC = funcRoot + "/merge/[0,0]"` → **零查表，零计算，纯字符串拼接**。

### 4.2 label = 无参 call

```
goto(merge)  ≡  call(父函数/merge)   ← 相同语义，不同语法
```

block 就是无参函数。控制流统一为 `call` + `return`，无需 `jmp`/`br`/`goto` 等额外原语。

### 4.3 与传统对比

| 操作 | x86 | Python | kvlang |
|------|-----|--------|--------|
| 条件跳转 | `cmp; je label` | `POP_JUMP_IF_FALSE` + offset | `br(cond, t, f)` → `call(t)` or `call(f)` |
| 无条件跳转 | `jmp label` | `JUMP_ABSOLUTE` + offset | `call(then)` |
| 函数调用 | `call addr` | `CALL_FUNCTION` | `call(funcName)` |
| 返回 | `ret` | `RETURN_VALUE` | `return` (PC 回父路径) |

kvlang 不区分"跳转"和"调用"——label block 就是无参函数，控制流就是 `call`/`return`。

## 5. 编译器/解释器架构对比

### Python

```
源代码 → tokenizer → parser → AST
  → symtable (符号表分析, 作用域)
  → compile (AST → 基本块 → 字节码)
  → marshal (字节码 → .pyc)
  → ceval (解释器主循环: 取字节码 → 分发 → 执行)
```

关键特征：
- 基本块由编译器构建（`flowgraph.c`），包含跳转偏移
- 字节码操作数携带 PC 偏移量（整数）
- 解释器在连续字节码数组上递增 PC

### Lua

```
源代码 → lexer → parser → AST
  → codegen (AST → 寄存器指令)
  → luaV_execute (寄存器 VM: 取指令 → 分发 → 执行)
```

关键特征：
- 寄存器式 VM（非栈式），指令携带寄存器索引
- 控制流通过 `JMP`/`TEST`/`FORLOOP` 等指令 + 偏移量
- 无独立的基本块构建阶段

### kvlang

```
源代码 → lexer → parser → AST (if/while/for → IfStmt/WhileStmt/ForStmt)
  → lower  (结构化控制流 → BlockStmt + br/goto)
         (br/goto 又简化 → call(block_label))
  → layoutrwir (AST → KV 结构化 key-value)
         (WriteBody: 递归写入 /lib/<pkg>.<name>/[i,j] KV 指令树)
  → kvcpu (执行循环: Decode → 分发 → 执行)
         (call = HandleCall: 软链接函数指令树到子帧 .fn)
         (return = HandleReturn: 回传值, 清理子栈, 恢复父 PC)
```

关键特征：
- **PC 是 KV 路径字符串**，不是整数
- **指令在 KV 树中**，通过 `kv.Get` 获取，不是内存数组
- **调用 = 软链接**（HandleCall 通过 kv.Link 将子帧 .fn 指向 /lib/<pkg>.<name> 只读指令树）
- **返回 = 子树删除**（HandleReturn 清理子栈, 回传值）
- **label block = 无参函数**，控制流统一为 call/return

### 5.1 编译器前端流水线

kvlang 编译器前端走标准流水线：**`Source → Scanner.Scan() → []Token → Parser → *ast.File`**。
核心设计决策：**块结构由消费 LBrace/RBrace Token 自然追踪，杜绝 `strings.Count/Index` 做语法判断**。
换行是语句分隔符（`Newline` token）而非块结构标记（`{ }` 负责）。`parser` 结构体以 `tokens[]+pos+peek/advance/expect` 递归下降驱动，
文件单向依赖链 `file.go → stmt.go → inst.go → scanner.go`。错误收集不首错即止——`parser.errors []Diagnostic` 累积全量诊断。


### 5.2 AST 类型标记——Quote 字段

`Expr.Quote` 区分字符串字面量和变量名，替代旧的 `"` 前缀 hack。parser 将 scanner 的 token Quote 信息保留到 AST，`Flat()` 在 KV 传输层加 `"` 前缀，`stringPrec` 用 `escapeString` 还原源码形式。数字字面量（如 `-5`）不再被误引号包裹。


## 6. layoutrwir 的设计原理与函数调用 Link 机制

传统 VM：编译器产线性字节码，call = push 返回地址 + 跳转到函数入口。kvlang 不用字节码拷贝——**函数体永不被复制，调用 = kv.Link 创建软链**让子帧指向 `/lib/` 下的指令树。

layoutrwir 在五语言中的对标：

| 语言 | 阶段名 | 做什么 | 产物 |
|------|--------|------|------|
| **C (GCC)** | codegen + assemble + link | AST→GIMPLE→RTL→asm→.o，三个独立工具（cc1/as/ld） | 线性机器码 |
| **Go** | compile (walk + SSA) | AST→SSA→机器码，`gc` 一个二进制全包 | 线性机器码 |
| **Rust** | codegen | MIR→LLVM IR→机器码 | 线性机器码 |
| **Python** | compile | AST→bytecode，`compile()` 内置函数 | 线性字节码 `.pyc` |
| **V8** | bytecode gen (Ignition) + optimizing compile (TurboFan) | AST→Ignition bytecode→TurboFan 机器码 | 线性字节码→机器码 |
| **kvlang** | **layoutrwir** | AST→KV 结构化键值 `[s0,s1]` 二维铺入 kvspace | **树形 KV 键**，可逐槽寻址 |

五种语言都在生成线性序列。kvlang 的 layoutrwir 不是序列化——是**空间布局**：每条指令展开为一组 `[s0,s1]` 坐标，读参负轴、写参正轴、opcode 零点。产物可逐槽 `kv.Get`/`kv.List`，无需反汇编器。

### 6.1 函数调用 Link 机制

```
调用 add(3,4) -> s 的完整链路：

编译期（load 时，WriteFunc）：
  AST → KV 结构化写入 /lib/main.add/:
    /lib/main.add/[0,0] = "+"      /lib/main.add/[0,-1] = "A"
    /lib/main.add/[0,-2] = "B"     /lib/main.add/[0,1] = "C"
    /lib/main.add/[1,0] = "return"
    /lib/main.add                 = "def add(A:int,B:int)->(C:int)"  (签名)
    /lib/idx/add                  = "main"                           (反向索引)

调用时（HandleCall）：
  1. FuncIdx("add") → "main"                        # 函数名 → pkg
  2. kv.Get("/lib/main.add") → 签名 → 解析参数名
  3. frameRoot = ChildFrameRoot(callPC)              # /vthread/42/[3,0]
  4. kv.Link("/lib/main.add", frameRoot+"/.fn")     # ★ 软链：子帧代码区 → 函数指令树
  5. 绑定参数：caller 帧取实参值 → kv.Set(frameRoot/形参名)
  6. 写 .callpc（返回地址）、.rootfunc（函数名）、.ro（读参名单，fix-027）
  7. 返回 frameRoot+"/.fn/[0,0]"                    # 子帧第一条指令 PC

返回时（HandleReturn）：
  1. 读 .callpc → 定位调用指令的写槽 [callPC_addr0, i+1]
  2. 逐写槽：kv.Get(childFrame/读槽) → kv.Set(parentFrame/写槽路径)  # 写参跨帧映射
  3. kv.Unlink(frameRoot+"/.fn")                     # 摘链
  4. kv.DelTree(frameRoot)                          # 清整个子帧（params/.callpc/.rootfunc）
  5. 返回 op.NextPC(callPC)                          # 父帧下一条

TCO（goto/br）：不建子帧，仅 Unlink + Link 换 .fn 指向目标块（.rootfunc 保持根函数名）。
顶层调用（Bootstrap）：无 .callpc，直接 Link funcKey → vthreadRoot/.fn。
```

### 6.2 与传统 VM 的关键差异

| | 传统 VM | kvlang |
|--|---------|--------|
| 代码传递 | copy 字节码到新栈帧 | **Link 软链**（所有帧共享 /lib/ 下同一份指令树） |
| TCO | 需特殊优化（复用帧 + 重定向参数） | Unlink + Link，已有的 .fn 机制天然支持 |
| 崩溃恢复 | 栈帧在内存，进程死即全失 | PC=路径字符串、`.callpc`=返回点落 KV——重启续跑 |
| 可观测 | 需调试器 attach | `kvspace dump /vthread/…` 看 `.fn` 链向谁、`.callpc` 在哪个坐标 |

**`=` 操作码是值拷贝，不是函数调用**：`a -> b` 编码为 `[s0,0]="="`（值拷贝），
函数调用是 `call(name, args…) → writes`——opcode 位是 `call`，Link 发生在 HandleCall 内部。
二者在 KV 层无歧义，opcode 位永远不放变量引用（§2.3）。

## 9. XValue 的 kind 系统与数字类型算子

### 9.1 kind 家族

运行时每个值携带 `kind` 标签（int8~int64, uint8~uint64, float32, float64, bool, string, bytes, array, dict）。`IsNil()`（kind==""）即 null，无需额外字面量。`dict` 是零负载的类型标记（§10.3）。TLV 编码 `[1B kind_len][N B kind_name][4B raw_len][M B raw_value]` 嵌入每个值。

### 9.2 定义变量时指定基础类型（fix-021）

十个数字类型算子，**既是构造器也是转换器**，普通调用形态（parser 零改动）：

```kv
f = float32(3)        # kind=float32 落盘        # = 等价于 <-
i <- int8(0.1)        # 0（float→int 截断向零）
int8(300) -> w        # 44（窄化 = 补码回绕）
uint64(18446744073709551615)   # uint64 上界完整往返
```

`int8/16/32/64 · uint8/16/32/64 · float32/float64`；`int`/`float` 是 int64/float64 别名。

| 语义 | 对齐阵营 |
|------|---------|
| float→int 截断向零 | 五语言一致 |
| 窄化 = 补码回绕（`uint8(-1)`=255、`int32(2³¹)`=-2³¹） | Go 转换 / Rust `as` / C |
| (2⁶³, 2⁶⁴-1] 无小数正整数字面量 → uint64 | — |
| nil 输入按 int 0 | fix-017 |

**声明精度是存储/传输类型**：`int16(-2) -> n; n -> /x` 后 `kvspace get /x` 显示 `int16:-2`——
精度进入 TLV kind 落盘，kvspace-cpp / kvregion shm / 张量 dtype 的跨语言类型契约由此成立。

### 9.3 数值运算域

窄类型进入算术后**提升至统一运算域**（C 整型提升风格），三条定理：

1. **int ∧ int → 原生 int64 运算与比较**，绝不经 float64 中转（fix-020：float64 尾数仅 53 位，
   `maxint64 - 1`、`2⁵³+1` 曾经必然丢值/误判相等）；溢出 = 补码回绕（同 C/Go）
2. **任一侧 float → float64 提升**；混合比较为 C 式 double 提升（`3 == 3.0` 为 true）
3. **nil 数值语境 = int 0**（fix-017，与 `nil==0` 比较、`AsBool(nil)=false` 同族）

读取器契约（kvlang-go）：`Int64()`/`Uint64()` 是**宽容读取器**（对标 Go `reflect.Value.Int/Uint`：
按 kind 实际宽度解码 + 符号扩展）；`Int8()`/`Float32()` 等是严格 kind 精确访问器。
消费方（算术/display/String）一律走宽容读取器。

语义回归锚点：`tutorial/01-basics/precision.kv`（提升与精度真相，17 断言）、`numtypes.kv`（十算子，12 断言）。

## 10. `.` 运算符——kvspace 路径的标准成员访问

`ptr.val` → `at(ptr, "val")` → `kv.Get(ptr/val)`。Pratt 循环中 `.` 作为后缀运算符，对标 C `ptr->val`、Go `ptr.val`。写侧 `42 -> ptr.val` 展开为 `set(ptr, "val", 42) -> ptr`。Scanner 将 `.` 作为 token 分隔符和独立 Dot token，`at`/`set` builtin 支持字符串字段名做 kvspace 路径拼接。

### 10.1 静态字段：`h.field`

```
h.field  →  at(h, "field")    # field 是字面量字符串
```

解析时 Pratt 消费 `.` 后读到普通标识符 → 作为 `StrLit` 传给 `at`。

### 10.2 动态解引用：`h.*key`

```
h.*key  →  at(h, key)         # key 是变量，取其值作为路径段名
```

解析时 Pratt 消费 `.` 后读到 `*` + 标识符 → 作为裸 `Leaf` 传给 `at`，不做字符串化。这是 kvlang 内置 hash map 的语法基础：

```kvlang
"/tmp" -> h           # h = 路径前缀
2 -> key              # key = 2
h.*key                # at("/tmp", 2) → 读 /tmp/2
```

与传统语言的对比：

| 语言 | 静态字段 | 动态字段 |
|------|---------|---------|
| kvlang | `h.field` | `h.*key` |
| Python | `h["field"]` | `h[key]` |
| Go | `h.field` | `h[key]` (map) |
| JS | `h.field` | `h[key]` |

**与 nil 配合**：`at` 查不到 key 返回 nil。存 `idx+1`（≥1），读时判断 `> 0` 区分"找到/未找到"。O(1) hash map，解锁数百道 LeetCode 题。

详见 `doc/kvlang/design/kvspace-hash-map.md`。

### 10.3 struct ≡ dict：kvspace 中的等价性

kvlang 不区分 struct 和 dict。二者在 kvspace 中是**同一种东西：共享前缀的键族**。

| 语言层视角 | kvspace 层实质 |
|-----------|---------------|
| struct：编译期已知的字段名 | `base` + 字面量成员名，`obj.prop` → `at(obj, "prop")` |
| dict：运行期动态的 key | `base` + 变量值成员名，`obj.*key` → `at(obj, key)` |
| 链表节点：`val` + `next` 指针 | 键族 `{val, next}`，`next` 存下一节点的路径字符串（§8 变量名即指针） |
| 数组：下标索引 `a[i]` | `base` + 整数成员名，`a[i]` → `at(a, i)` |

kvspace 没有类型边界：同一键族可以同时按 struct 用（静态字段）、按 dict 用（动态 key）、按数组用（整数 key）。静态/动态的区别只存在于**语法层**（`.field` vs `.*key` vs `[i]`），到 `at`/`set` 之后完全消失。

**dict 字面量与类型标记**：`a = { attr1="s1"; attr2=2; attr3=null }` 是键族的一等创建语法——
desugar 为 `dict("attr1", "s1", ...)`，base 键 `a` 写入 `kind="dict"` 的零负载标记值，
成员写入平坦键族 `a.attr1`、`a.attr2`；值为 `null`（裸名，运行时解析为 nil）的成员**不写入**——
kvspace 中缺席即 null。dict 标记非 string 值，成员解析自动走按名回退（§10.4）；
`at`/`set` 亦显式识别 `kind=="dict"` 的 base 强制路径模式。键值对分隔符为 `;`、换行或逗号，
对内的 `=` 与赋值算子同形（fix-010）。

**成员分隔符已统一为 `.`**（fix-009）：`at`/`set`/`dget`/`dset`/`kvat`/`kvhas` 的成员拼接全部经 `keytree.Member(base, name)`（`base + "." + name`）。链表节点落盘即 `/n0.val`、`/n0.next` 平坦键，零子树。

### 10.4 成员解析规则：按值优先，按名回退

表达式 `base.名`（读写两侧同规则）中 base 的解析：

1. **按值解引用**：base 持有非空字符串值（路径指针）→ 成员键 = `值(base).名`。如 `"/n0" -> p` 后 `p.next` → `/n0.next`。
2. **按名回退**：base 无值（或非字符串）→ 成员键 = `解析(base).名`，其中 `解析()` 为帧感知：裸名 → `帧根/base`，`/` 开头 → 直通。如局部键族 `chars.0` → `帧根/chars.0`；字面量 `/n0.val` → `/n0.val`。

该规则使"局部 struct"与"指针解引用"共用一套语法：键族的 base 永不赋值（保持按名），指针变量存路径字符串（触发按值）。

**遗留不一致**（待收敛）：`dget`/`dset` 仍纯按名寻址（`帧根/变量名.key`），未走按值优先规则。

## 12. 系统变量——`X/.var` 影子键

VM 运行时会为它管理的对象生成**内置变量（系统变量）**，以 `{对象key}/.var` 形式存放：`/` 下探一层、键名以 `.` 开头。kvlang 标识符不能以 `.` 开头，因此所有 `.` 前缀键均为引擎保留，用户代码无法直接读写——类比 Unix 隐藏文件：默认视图不显示，引擎可见。

### 12.1 全量清单（按宿主对象分类）

**vthread 对象**（宿主 = `/vthread/<vtid>`，生命周期与调度）：

| 键 | 机制 | 语义 |
|----|------|------|
| `.pc` | String | 当前执行 PC（绝对路径） |
| `.status` | String → Notify | 运行中 `init`/`running`/`wait`；终态 Del + Notify(retVal)，Get 会 WRONGTYPE |
| `.<status>/msg` | String | 终态附加信息（`.error/msg`、`.timeout/msg`，路径动态生成） |
| `.debugger` | String | 调试控制：`""` 正常，`"step"` 单步；`debugger()` builtin（fix-031，对齐 V8/TypeScript `debugger;`）读此键 |
| `.debugger.pause` | Notify 队列 | CPU → agent 暂停事件（JSON） |
| `.debugger.resume` | Notify 队列 | agent → CPU 恢复命令：`step`/`continue`/`abort` |

**帧对象**（宿主 = frameRoot，调用协议；vthread 根即顶层帧）：

| 键 | 机制 | 语义 |
|----|------|------|
| `.fn` | 软链接 | → `/lib/<pkg>.<name>` 只读指令区；TCO 时 Unlink + 重链 |
| `.callpc` | String | 调用指令绝对地址；HandleReturn 据此推算写槽路径并恢复父 PC（仅子帧） |
| `.rootfunc` | String | 帧对应函数名；TCO 复用帧时不更新（顶层帧由 Bootstrap 写入） |
| `.ro` | String | 只读参数名单（逗号分隔），kvcpu 写槽检查用（fix-027；无参函数不写） |

**数据对象**（宿主 = 变量键，规划中）：

| 键 | 机制 | 语义 |
|----|------|------|
| `.shape` | — | kvspace 数组的形状（todo-009 键族数组落地后） |
| `.gc` | — | 垃圾回收引用计数（未来） |

**内联暂停指令**：`debugger()`（fix-031，对齐 V8/TypeScript `debugger;` 语句）——源码内联暂停点；非调试模式下 no-op，调试模式下暂停 vthread 等待 agent 的 step/continue/abort。暂停/恢复协议走 `.debugger`、`.debugger.pause`、`.debugger.resume` 三个键。

**语法层保留名（不落盘）**：`._` 丢弃槽——`frameSlotKey` 对 `.xxx` 槽返回空路径直接忽略。`.w0` 已废弃（写槽路径改由 `.callpc` 推算，仅存于陈旧注释）。

注意区分：`/sys/`（vm 心跳、op 注册）是独立的系统**域**（顶层树），与对象随身的 `/.var` 系统**变量**是两种机制。

### 12.2 三种键形态：一眼判型

成员键 `.` 分隔（todo-009）落地后，任意 key 的形态唯一确定其性质：

| 形态 | 例 | 性质 | 所有权 |
|------|----|------|--------|
| `X/名`（`/` + 普通名） | `/vthread/7/[3,0]`、`…/then/[0,0]` | 结构：帧、指令槽、label 块 | VM |
| `X.名`（`.` 平坦键） | `/c0.next`、`frame/obj.prop` | 用户数据成员（键族） | 用户 |
| `X/.名`（`/` + dot 名） | `/vthread/7/.pc`、`arr/.shape` | 系统变量（影子元数据） | VM |

### 12.3 设计结论：系统变量维持 `/` 分隔（`X/.var`）

系统变量**应该用 `/` 分隔**、与用户成员的 `.` 分隔形成正交，理由有三：

1. **零冲突的专属命名空间**。用户成员语法只产生 `.` 平坦键（`a.b` → 键 `a.b`），标识符禁止 `.` 开头；动态键 `h.*k` 即使 k 的值以 `.` 开头，`.` 拼接产出的是 `h..xxx`——用户侧永远造不出 `/.` 序列。反之若系统变量也用 `.` 拼接（`arr..shape`），与动态键注入撞车，还需维护保留字表。
   ⚠️ 现状（`/` 拼接成员）做不到这一点：`at(h, ".pc")` 即可命中 `h/.pc` 系统键——这是 todo-009 的又一论据：`.` 拼接天然堵住系统键注入。
2. **生命周期绑定**。`X/.var` 在 X 的 `/` 子树内，`DelTree(X)` 连带清除全部系统变量（帧销毁已依赖此语义）；用户键族 `X.*` 由前缀删除管理。两个删除平面各归其主：结构树归 VM，数据平面归用户。
3. **与帧系统键统一为一条公理**。帧本就是对象，`frameRoot/.pc`、`.fn`、`.callpc` 已是 `{对象}/.var` 形态。公理：**任何 kvspace 对象 X——VM 元数据在 `X/.名`，用户数据在 `X.名`，结构子级在 `X/名`**。数组 `.shape`、未来 GC 计数器直接套用，无需新机制。

## 7. 设计决策总结

| 决策 | 理由 |
|------|------|
| PC = KV path string | KV 树寻址天然支持层级，无需整数映射 |
| `[s0,0]` = opcode，`s1<0` = 读参，`s1>0` = 写参 | 符号编码数据流方向；每个槽独立可寻址、可观察 |
| 参数数量隐式（扫描到空 key 停止） | 无需在 opcode 中存 arity，指令布局自描述 |
| label block = 无参 call | 消除 jmp/br/goto 原语，控制流统一 |
| WriteFunc 先 DelTree 再写 | KV 不是内存，覆写不清零旧槽；必须显式删除旧函数树 |
| WriteBody 写结构化 KV | 避免文本往返，直接映射 AST→KV |
| lower 在 write 前执行 | 结构化 → 基本块的转换在 AST 层完成 |
| kvspace 抽象存储 | 存储后端可替换，接口仅 Get/Set/Del/GetMany/SetMany/List/DelTree/Notify/Watch/Link/Unlink/ClearAll/DisConn |
| 函数无返回值，只有写参 | `f() -> s` 是写参跨帧映射到位置 s，不是"返回值赋给 s" |
| `->` 右侧必须是位置 | 裸名=帧内、`/abs`=全局、`base.名`=成员；字面量写槽在 Parser 层报警 |
| `./` 前缀全面废除 | 裸名即位置；消除 `.`、`/` 在 parser/VM 中的多次拼接变换 |
| 拷贝指令显式操作码 `=` | `a -> b` 编码为 `[s0,0]="="`、`[s0,-1]="a"`；opcode 位永不放变量引用 |
| int∧int 原生 int64 运算域 | float64 中转必丢 >2⁵³ 精度（fix-020，对齐 C/Go/Rust）；混合数值 C 式 double 提升 |
| import/init 三区文件 | 参考 Python 去重 + Go 包名规则（fix-033） |
| 数字类型算子 = 构造器 + 转换器 | `int8()/float32()` 等十算子；声明精度即 TLV kind，跨语言类型契约（fix-021） |
