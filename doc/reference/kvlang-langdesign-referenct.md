# 参考语言对照表

> kvlang 设计过程中对标工业界顶级语言的参考维度矩阵。
> 5 种参考语言：C、V8(JavaScript)、Go、Rust、Python。
>
> 使用方式：kvlang 做某个子系统的设计决策时，查阅此表确认各语言的行业标准做法，
> 确保 kvlang 不落后于主流实现。

---

## 零、各语言在 kvlang 设计中的角色

| 语言 | 参考角色 | 为什么选它 |
|------|---------|-----------|
| **C** | 底层内存模型、数值表示 | 所有现代语言的 runtime 底座；IEEE 754、int 溢出行为的事实标准 |
| **V8 (JS)** | JIT 编译、动态类型优化 | 最成熟的动态语言 VM；hidden class、inline cache 是业界标杆 |
| **Go** | 标准库设计、工具链、并发模型 | kvlang 实现语言；`strconv`/`go/scanner` 是可直接复用的标准库 |
| **Rust** | 类型系统、所有权、零成本抽象 | 最先进的类型系统设计；`rustc_lexer` 的 token 分类极其严格 |
| **Python** | 可读性语法、REPL 体验、动态特性 | 最广泛使用的动态语言；`ast`/`tokenize` 模块是 parser 参考 |

---

## 一、词法分析（Scanner / Lexer）

### 1.1 数字字面量扫描

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 整数 | `[0-9]+` | `[0-9]+` | `[0-9]+` | `[0-9_]+` | `[0-9_]+` |
| 浮点 | `[0-9]*.[0-9]+([eE][+-]?[0-9]+)?` | 同 C | `[0-9]+.[0-9]*([eE][+-]?[0-9]+)?` | `[0-9]+.[0-9]*([eE][+-]?[0-9]+)?` | 同 C |
| 科学计数 | `[eE][+-]?[0-9]+` | 同 C | 同 C | 同 C | 同 C |
| 十六进制 | `0x[0-9a-fA-F]+` | `0x[0-9a-fA-F]+` | `0x[0-9a-fA-F]+` | `0x[0-9a-fA-F_]+` | `0x[0-9a-fA-F_]+` |
| 前导 `+` 在数字中? | ❌ 独立一元算子 | ❌ | ❌ | ❌ | ❌ |
| 前导 `-` 在数字中? | ❌ 独立一元算子 | ❌ | ❌ | ❌ | ❌ |
| `1e` 无指数数字? | ❌ 编译错误 | ❌ SyntaxError | ❌ 编译错误 | ❌ 编译错误 | ❌ SyntaxError |
| `1e+` 无指数数字? | ❌ 编译错误 | ❌ SyntaxError | ❌ 编译错误 | ❌ 编译错误 | ❌ SyntaxError |
| `.5` 前导小数点? | ✅ 合法 (0.5) | ✅ | ✅ | ✅ | ✅ |
| 数字中 `_` 分隔? | ❌ (C23 无) | ❌ | ✅ (`1_000`) | ✅ (`1_000`) | ✅ (`1_000`) |
| 类型后缀 | `f/l/u/ll` | 无 (BigInt: `n`) | 无 | `f32/f64/i8/u8..` | 无 |
| 验证方式 | 编译器阶段 | Token 化时 | `strconv.ParseFloat` | lexer 内完整校验 | `ast.literal_eval` |

**kvlang 决策**：
- 数字扫描对齐 **Go/Rust**：分阶段扫描（整数→可选小数→可选指数含 `[+\|-]?`）
- 验证委托给 `strconv.ParseFloat`（Go 标准库）做权威校验
- 无效科学计数（`1e`、`42e+`）→ parser 诊断错误（对齐全部 5 种语言）
- `_` 分隔暂不支持（kvlang 标识符不含 `_`）
- 类型后缀不需要（kvlang 动态类型）

### 1.2 注释语法

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 行注释 | `//` | `//` | `//` | `//` | `#` |
| 块注释 | `/* */` | `/* */` | `/* */` | `/* */` | `""" """` (docstring) |
| 嵌套块注释 | ❌ | ❌ | ❌ | ❌ | N/A |

**kvlang 决策**：`#` 和 `//` 同时支持（对齐 Python + Go 双社区习惯）。

### 1.3 字符串

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 双引号 | `"..."` | `"..."` | `"..."` | `"..."` | `"..."` |
| 单引号 | `'c'` (字符) | `'...'` | `'c'` (rune) | `'c'` (char) | `'...'` |
| 反引号 | ❌ | `` `...` `` (模板) | `` `...` `` (raw) | ❌ | ❌ |
| 转义 | `\n\t\\` | 同 C | 同 C | 同 C | 同 C |

**kvlang 决策**：双引号 `"..."`=字符串字面量，单引号 `'...'`=路径字面量（独有设计）。

---

## 二、语法分析（Parser）

### 2.1 表达式解析

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 解析算法 | 递归下降 | 递归下降 | Pratt / 递归下降 | 递归下降 | LL(1) + 优先级表 |
| 优先级层级 | 15 级 | 21 级 | 5 级（极少） | 14 级 | 17 级 |
| 结合性处理 | 语法规则 | 语法规则 | 语法规则 | 语法规则 | 语法规则 |
| 错误恢复 | ❌ 无 | ✅ 启发式 | ✅ 分号插入 | ✅ `rustc` 有 | ✅ `syn` crate | ✅ `parser` 模块 |

**kvlang 决策**：Pratt parser，对齐 Go 表达式解析的简洁性。

### 2.2 语句分隔

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 分隔符 | `;` | `;` (ASI) | `;` (自动插入) | `;` | 换行 |
| 显式分隔 | 必须 | 可选 | 可选 | 必须 | `;` 可选 |

**kvlang 决策**：换行 = 语句分隔，`;` 显式分隔可选（对齐 Python + Go ASI）。

---

## 三、类型系统

### 3.1 核心分类

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 类型范式 | 静态, 弱类型 | 动态, 弱类型 | 静态, 强类型 | 静态, 强类型 | 动态, 强类型 |
| 类型推断 | ❌ | N/A | ✅ (`:=`) | ✅ `let` | N/A |
| 泛型 | ❌ (C11 `_Generic`) | ❌ | ✅ (1.18+) | ✅ trait | ✅ `TypeVar` |
| Null 安全 | ❌ 裸指针 | `null`/`undefined` | `nil` | `Option<T>` | `None` |
| 和类型 | ❌ (union) | ❌ | ❌ (interface) | ✅ `enum` | ❌ (Union 3.10+) |
| 积类型 | `struct` | `{}` object | `struct` | `struct`/`tuple` | `tuple`/`dataclass` |
| 类型别名 | `typedef` | ❌ | `type X = Y` | `type X = Y` | `X = Y` |

**kvlang 决策**：动态类型 + `kvspace.Value{kind, raw}` 运行时类型标记，对齐 V8 hidden class 的 tag 设计。

### 3.2 数值类型

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 整数范围 | 平台相关 | `Number` (f64) | 平台无关 | 平台无关 | 任意精度 |
| int 默认 | `int` (≥16 bit) | f64 | `int` (64 bit) | `i32` | `int` (∞) |
| float 默认 | `double` (64 bit) | f64 | `float64` | `f64` | `float` (f64) |
| 整数溢出 | 未定义行为 | f64 精度丢失 | 回绕 | panic (debug) | 自动升级 |
| 类型转换 | 隐式, 截断 | 隐式 | 必须显式 | 必须显式 `as` | 必须显式 `int()` |

**kvlang 决策**：
- int = 64-bit signed，float = 64-bit IEEE 754（对齐 Go/Rust）
- 类型转换显式（对齐 Go/Rust，拒绝 C 的隐式截断）
- `tryParseNumber`: `ParseInt` 优先 → `ParseFloat` 回退（对齐 JSON 数值解析）

### 3.3 布尔与真值

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| bool 类型 | `_Bool` (C99) | `boolean` | `bool` | `bool` | `bool` |
| 真值规则 | 非零=真 | 6 种 falsy | 无隐式转换 | 无隐式转换 | 8 种 falsy |
| `0` 的真值 | false | false | ❌ 编译错误 | ❌ 编译错误 | false |
| `""` 的真值 | N/A | false | ❌ | ❌ | false |
| `"0"` 的真值 | N/A | true | ❌ | ❌ | true |

**kvlang 决策**：`AsBool(v)`: int/float 非零=真，string 非空且非 `"0"`/`"false"`=真，对齐**动态语言惯例**（V8 + Python 交集）。但 parser 层面 bool 作为类型化值 `kvspace.Bool(true/false)`，避免 string 歧义。

---

## 四、内存与所有权

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 内存管理 | 手动 `malloc/free` | GC (标记-清除) | GC (并发三色) | 所有权+借用 | GC (引用计数) |
| 栈/堆分离 | 显式 | 隐式 | 逃逸分析 | 显式 `Box` | 隐式 |
| 零成本抽象 | N/A | ❌ | ❌ (interface 有开销) | ✅ | ❌ |
| 值语义 vs 引用 | 值语义 | 引用语义 | 值语义 | 值语义 (move) | 引用语义 |
| 别名控制 | ❌ `restrict` | ❌ | ❌ | ✅ 借用检查 | ❌ |

**kvlang 决策**：kvspace 即"堆"——所有变量在 KV 树中持久化，帧 `DelTree` = 栈帧回收。所有权 = 路径前缀（`/vthread/<vtid>/` 归单 worker 所有）。

---

## 五、并发模型

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 并发范式 | pthreads | 事件循环 + Worker | goroutine + channel | async/await + 线程 | GIL + asyncio |
| 内存共享 | 共享内存 | SharedArrayBuffer | channel 通信 | 所有权防共享 | multiprocessing |
| 调度器 | OS 线程 | libuv 事件循环 | 抢占式 (G-M-P) | 协作式 async | 协作式 asyncio |
| 并发安全 | 无保证 | 单线程安全 | race detector | 编译期保证 | 单线程安全 |
| 并行 Worker | pthread | Worker threads | `GOMAXPROCS` | `rayon` / tokio | `ProcessPool` |

**kvlang 决策**：多 worker 并行 + 路径所有权（一个 vtid 一个 worker），对齐 Go goroutine 的 M:N 调度 + Rust 所有权隔离。

---

## 六、错误处理

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 错误表示 | 返回值 + `errno` | `Error` 对象 | `(value, error)` | `Result<T, E>` | `Exception` |
| 错误传播 | 手动检查 | try/catch | `if err != nil` | `?` 算子 | try/except |
| 不可恢复 | `abort()` | `throw` (未捕获) | `panic` | `panic!` | `sys.exit` |
| 堆栈追踪 | ❌ | ✅ | ✅ `panic` | ✅ `panic` | ✅ traceback |
| 静态检查错误路径 | ❌ | ❌ | ✅ `errcheck` | ✅ 类型系统 | ❌ |

**kvlang 决策**：`vthread.SetError(vtid, pc, msg)` → `Del(.status) + Notify("error")`。对齐 Go 的显式错误返回 + V8 的 Error 对象（`.<statusVal>/msg` 存储错误详情）。

---

## 七、工具链

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 编译器 | gcc/clang | Ignition → TurboFan | `compile` (SSA) | `rustc` (LLVM) | `compile` → bytecode |
| 格式化 | clang-format | Prettier | gofmt | rustfmt | black |
| Lint | cppcheck | eslint | govet / staticcheck | clippy | pylint / ruff |
| 包管理 | 无标准 | npm | go mod | cargo | pip |
| 测试框架 | 无标准 | jest / vitest | `testing` | `#[test]` | pytest / unittest |
| 调试器 | gdb/lldb | Chrome DevTools | delve | gdb/lldb | pdb |
| 基准测试 | 无标准 | Benchmark.js | `testing.B` | `#[bench]` | timeit / pytest-bench |

**kvlang 决策**：`gofmt` 式的强制格式化（`kvlang format`），对齐 Go 工具链哲学（少即是多）。

---

## 八、执行模型

| | C | V8 | Go | Rust | Python |
|--|---|---|-----|------|--------|
| 执行方式 | 编译→机器码 | JIT 编译 | 编译→机器码 | 编译→机器码 | 解释→bytecode |
| IR 层数 | 1 (汇编) | 2 (bytecode→TurboFan IR) | 1 (SSA) | 2 (MIR→LLVM IR) | 1 (bytecode) |
| 栈帧表示 | 内存栈 | V8 StackFrame | goroutine 栈 | 内存栈 | PyFrameObject |
| PC 类型 | 内存地址 (整数) | bytecode offset (整数) | 内存地址 (整数) | 内存地址 (整数) | bytecode offset (整数) |
| 调用约定 | cdecl/systemv | V8 内部 | Go ABI | Rust ABI | CPython 内部 |

**kvlang 决策**：
- PC 是 KV 路径**字符串**（不是整数），跳转 = 字符串拼接
- 栈帧 = kvspace 子树，路径深度 = 调用栈深度
- 崩溃恢复：PC 持久化在 KV 中，重启继续（零语言有此能力）

---

## 九、kvlang 差异化总结

| 维度 | 行业标准（5 语言交集） | kvlang 选择 |
|------|----------------------|-----------|
| Scanner 数字验证 | lexer 内/编译时校验，无效字面量=错误 | ✅ 对齐：parser 诊断 + `strconv.ParseFloat` 权威校验 |
| 类型 | 静态（C/Go/Rust）或 动态（V8/Python） | 动态 + `Value{kind, raw}` 运行时 tag |
| 内存 | 栈+堆分离 | kvspace 统一树（无栈/堆概念） |
| 并发 | 线程（C/Rust）或 事件循环（V8）或 goroutine（Go） | 多 worker + 路径所有权 |
| PC | 整数地址/offset | 字符串路径 |
| 崩溃恢复 | ❌ 全失 | ✅ PC 持久化，重启继续 |
| 可观测性 | gdb/devtools 外挂 | KV 路径直接 GET（内建） |
| 指令形式 | 字节码/机器码 | 读写码（`<-`/`->` 显式读参写参） |
| Agent 交互 | 无标准方式 | KV 接口即 Agent API |
