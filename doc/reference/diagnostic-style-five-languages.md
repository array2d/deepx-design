# 五大语言诊断输出风格对比

> 实测日期 2026-07-19。GCC 13.3 / Clang 18.1 / Go 1.25 / Python 3.12 / Node v22.23。
> 供 kvlang 编译/运行时诊断格式设计参考。

## 一、C（GCC 13.3）

### 编译错误

```
/tmp/err_syntax.c:3:1: error: expected expression before '}' token
    3 | }
      | ^
```

**格式**：`file:line:col: error: message`，下一行源码，再下一行 `^` 定位符。

### 编译警告

```
/tmp/warn_unused.c:2:9: warning: unused variable 'x' [-Wunused-variable]
    2 |     int x = 42;
      |         ^
```

**格式**：`file:line:col: warning: message [-Wflag]`。`[-W...]` 标注警告选项名，可直接用于 `-Wno-...` 抑制。

### 多错误

```
/tmp/err.c:2:5: error: 'x' undeclared (first use in this function)
    2 |     x = 42;
      |     ^
/tmp/err.c:3:5: error: 'y' undeclared (first use in this function)
    3 |     y = "hi";
      |     ^
```

每条错误独立一行 `file:line:col` 起头，顺序列出。类型不匹配在 GCC 下仅发 warning 不阻塞编译（`-Wint-conversion`）。

### note 辅助信息

```
/tmp/err.c:2:5: note: each undeclared identifier is reported only once
```

`note:` 前缀，提供上下文但不计入错误数。

### 链接错误

```
/usr/bin/ld: /tmp/link_err.o: in function `main':
link_err.c:(.text+0x9): undefined reference to `does_not_exist'
collect2: error: ld returned 1 exit status
```

格式来自 binutils ld：`file: in function 'func':` + `file:(section+offset): message`。

### 运行时 SIGSEGV

```
Segmentation fault (core dumped)
```

Shell 报告，程序自身无堆栈输出。

---

## 二、C（Clang 18.1）

### 编译错误

```
/tmp/err.c:2:5: error: use of undeclared identifier 'x'
    2 |     x = 42;
      |     ^
1 error generated.
```

末尾 `N errors generated.` 汇总。与 GCC 差异：消息措辞不同（"undeclared identifier" vs "undeclared"），无 `note:` 的冗余内容。

### 编译警告

```
/tmp/warn.c:2:9: warning: unused variable 'x' [-Wunused-variable]
    2 |     int x;
      |         ^
1 warning generated.
```

格式与 GCC 一致：`file:line:col: warning: message [-Wflag]`。

---

## 三、Go 1.25

### 编译错误

```
# command-line-arguments
/tmp/err.go:3:9: declared and not used: x
/tmp/err.go:3:17: cannot use "hello" (untyped string constant) as int value
```

**格式**：`file:line:col: message`。无 `error:` 前缀，无 `^` 定位符。第一行 `# package-name` 标注包名。

### 多错误

```
/tmp/err_multi.go:3:5: undefined: x
/tmp/err_multi.go:4:5: declared and not used: y
/tmp/err_multi.go:4:10: invalid operation: "hi" + 1 (mismatched types...)
```

逐行列出，每条 `file:line:col:`。未声明变量和使用未使用变量**都是编译错误**（无 warning 概念）。

### go vet

```
# command-line-arguments
/tmp/vet.go:4:23: fmt.Printf format %s reads arg #1, but call has 0 args
```

格式与编译错误完全一致。Go 无传统意义的"编译警告"——unused 是 error，go vet 是独立的静态分析工具。

### 运行时 panic

```
panic: runtime error: invalid memory address or nil pointer dereference
[signal SIGSEGV: segmentation violation code=0x1 addr=0x0 pc=0x46fb82]

goroutine 1 [running]:
main.main()
        /tmp/run_panic.go:4 +0x2
exit status 2
```

**格式**：`panic: message` → 信号信息行 → goroutine 栈。栈帧格式 `pkg.Func()` + 缩进 `file:line +offset`。

---

## 四、Python 3.12

### 语法错误

```
  File "/tmp/err.py", line 1
    def f()
           ^
SyntaxError: expected ':'
```

**格式**：`File "path", line N` → 源码行 → `^` 定位符 → `ErrorType: message`。无堆栈跟踪（因为还没开始执行）。

### 运行时异常（单层）

```
Traceback (most recent call last):
  File "/tmp/run_type.py", line 1, in <module>
    x = "hello" + 42
        ~~~~~~~~^~~~
TypeError: can only concatenate str (not "int") to str
```

**格式**：`Traceback (most recent call last):` 起头 → 栈帧列表（`File "path", line N, in name` + 源码 + 表达式下划线 `~~~~^~~~`）→ `ExceptionType: message`。

Python 3.12 新增了表达式级 `~~~~^~~~` 下划线，精确定位到出错子表达式。

### 运行时异常（多层）

```
Traceback (most recent call last):
  File "/tmp/run.py", line 4, in <module>      ← 最外层调用
    a()
  File "/tmp/run.py", line 3, in a
    def a(): return b()
                    ^^^
  File "/tmp/run.py", line 2, in b
    def b(): return c()
                    ^^^
  File "/tmp/run.py", line 1, in c              ← 实际出错位置
    def c(): return 1 / 0
                    ~~^~~
ZeroDivisionError: division by zero
```

栈帧顺序：**调用方在上，出错方在下**（自顶向下 = 调用链方向，exception 在最底部）。每帧标注函数名、源码、行号。

### 警告

```
/tmp/warn.py:2: DeprecationWarning: this is deprecated
  warnings.warn("this is deprecated", DeprecationWarning)
```

`file:line: WarningType: message` → 源码行。不阻断执行（exit code 0），需 `-Wd` 显式开启。

---

## 五、JavaScript（V8 / Node v22.23）

### 语法错误

```
/tmp/err.js:1
function f( { return 1; }
                     ^

SyntaxError: Unexpected number
    at wrapSafe (node:internal/modules/cjs/loader:1713:18)
    at Module._compile (node:internal/modules/cjs/loader:1755:20)
    ...
```

**格式**：`file:line` → 源码 → `^` 定位 → 空行 → `ErrorType: message` → Node 内部模块栈。空行分隔用户代码和内部栈。

### 运行时异常（多层）

```
/tmp/run.js:1
function c() { return null.foo; }
                           ^

TypeError: Cannot read properties of null (reading 'foo')
    at c (/tmp/run.js:1:28)
    at b (/tmp/run.js:2:23)
    at a (/tmp/run.js:3:23)
    at Object.<anonymous> (/tmp/run.js:4:1)
    ...
```

**格式**：`file:line` → 源码 + `^` 定位 → 空行 → `ErrorType: message` → `    at Func (file:line:col)` 栈帧列表。栈帧缩进 4 空格，格式 `at Func (path:line:col)`。

栈的顺序：**出错方在上，调用方在下**（与 Python 相反！）。`Object.<anonymous>` 是顶层模块代码。

### 异步异常

```
/tmp/run_async.js:1
setTimeout(() => { throw new Error("async boom"); }, 10);
                   ^

Error: async boom
    at Timeout._onTimeout (/tmp/run_async.js:1:26)
    at listOnTimeout (node:internal/timers:585:17)
```

格式不变，但无调用方栈（异步 callback 从 event loop 直接触发）。

---

## 六、Rust（rustc 1.8x，未测，格式标准化极高）

### 编译错误

```
error[E0382]: borrow of moved value: `x`
 --> src/main.rs:4:5
  |
3 |     let y = x;
  |             - value moved here
4 |     println!("{}", x);
  |     ^^^^^^^^^^^^^^^^^ value borrowed here after move
  |
  = note: move occurs because `x` has type `String`, which does not implement `Copy`
help: consider cloning the value
  |
3 |     let y = x.clone();
  |              ++++++++
```

**格式**：`error[E0000]: title` → `--> file:line:col` → source snippet with `|` gutter → label lines → `= note:` / `help:` 建议。错误码可搜索（`rustc --explain E0382`）。

### 编译警告

```
warning: unused variable: `x`
 --> src/main.rs:2:9
  |
2 |     let x = 42;
  |         ^ help: if this is intentional, prefix with an underscore: `_x`
  |
  = note: `#[warn(unused_variables)]` on by default
```

格式与 error 相同，`warning:` 前缀，无错误码。

### 运行时 panic

```
thread 'main' panicked at src/main.rs:4:13:
index out of bounds: the len is 3 but the index is 10
note: run with `RUST_BACKTRACE=1` for a backtrace
```

`thread 'name' panicked at file:line:col:` → 消息。需 `RUST_BACKTRACE=1` 开启完整栈。

---

## 七、格式特征对比

### 定位精度

| | 文件 | 行 | 列 | 表达式级 | 二次定位(note) |
|--|------|----|----|---------|--------------|
| GCC | ✅ | ✅ | ✅ `^` | ❌ | ✅ `note:` |
| Clang | ✅ | ✅ | ✅ `^` | ❌ | ✅ |
| Go | ✅ | ✅ | ✅ | ❌ | ❌ |
| Python | ✅ | ✅ | ✅ `^` | ✅ `~~~^~~~`(3.12) | ❌ |
| JS/V8 | ✅ | ✅ | ✅ `^` | ❌ | ❌ |
| Rust | ✅ | ✅ | ✅ | ❌ | ✅ 标签线 + help |

### 错误码系统

| | 错误码 | 可查询 | 警告选项 |
|--|--------|--------|---------|
| GCC | `[-Wflag]` | `gcc --help=warnings` | 每个 warning 标注 flag |
| Clang | `[-Wflag]` | 同 GCC | 同 GCC |
| Go | ❌ | — | ❌ (无 warning) |
| Python | 异常类型字符串 | `help(ExceptionType)` | `-Wd` 开关 |
| JS/V8 | 异常类型字符串 | MDN | — |
| Rust | `E0000` 数字码 | `rustc --explain E0000` | `#[warn(...)]` 标注 |

### 栈帧方向

| | 编译错误 | 运行时栈(调用方→出错方) |
|--|---------|----------------------|
| GCC | 每个错误原地报告 | ❌ 无运行时栈 |
| Go | 每个错误原地报告 | panic 栈：goroutine → 调用方在前 → 出错方在栈顶 |
| Python | SyntaxError 原地 | **调用方在顶部，出错方在底部** |
| JS/V8 | 原地 + 内部栈 | **出错方在顶部，调用方在底部** |
| Rust | 原地 + 丰富上下文 | panic 单行，`RUST_BACKTRACE=1` 补全 |

### 收敛原则

1. **编译错误一律 `file:line:col: message`** ——五种语言+两个 C 编译器全部一致。
2. **`^` 定位符是事实标准** ——Python/JS/GCC/Clang 都用，Go 不用（仅有数字列号）。
3. **运行时异常 = 错误类型 + 消息 + 栈帧列表** ——差异仅在栈帧方向（Python 自顶向下 vs V8 自底向上）和单帧格式（`File "..."` vs `at Func(...)`）。
4. **warning 与 error 格式一致**——仅前缀不同。Go 例外：无 warning，unused 是 error。
5. **辅助信息用 note/help**——GCC/Clang `note:`，Rust `= note:` / `help:`，Python 无此概念。

---

## 八、对 kvlang 的参考

| 设计要点 | 参考来源 | 理由 |
|---------|---------|------|
| `file:line:col: severity: message` | 五语言共识 | kvlang 编译期诊断应沿用此格式 |
| `^` 定位符 | GCC/Clang/Python/V8 | Go 的"仅数字"精度不如 `^` 直观 |
| `N errors generated.` 汇总 | Clang | 编译期结束时用户需要知道总共几个错 |
| 栈帧 `file:line` + 源码片段 | Python/V8 | kvlang 可崩溃恢复，panic 时栈本身就是 kvspace 路径，天然可查 |
| 运行时错误类型 + 消息 | Python/V8 风格 | `ReadParamWriteViolation: function 'add', param 'A' written at [3,1]` |
| 错误码（如 `E0001`） | Rust | kvlang parser 已用 Diagnostic 收集，可扩展错误码 + 单测映射 |
| 无 warning，直接 error | Go | kvlang 的"铁律"（如写槽冲突）适合直接报错，不做 warning |
