# rwir 与 lib 数据：注册布局

> 签名参数/返回值的 `type` 用**签名类型表达式**（并集 `A|B`、通配 `any`、高维 shape `[2,3]`、动态维 `?`），BNF 见 [[runtime篇-07-签名类型表达式]] 与 `grammar.bnf`。

## 三类 KV 内容

kvlang 落在 kvspace 树上的内容，按「是否有指令体」与「是否可读写」分三类，各有固定路径：

| 类别 | 语义 | 有指令体？ | KV Kind | 布局路径 |
|------|------|-----------|---------|---------|
| **rwfunc** | 函数（可执行） | ✅ 指令树 | `rwfunc` | `/lib/<pkg>.<name>/` |
| **lib 数据** | 常量（可读写） | ❌ 单个 XValue | 值类型 | `/lib/<name>` |
| **rwir** | 读写码（空函数体） | ❌ 仅签名 nr/nw | `rwir` | `/rwir/{runtime}/<opcode>` |

## 铁律

- **`/lib/` 下只有两类内容**：可执行（rwfunc，有指令体）或可读写（数据/常量）。
- **rwir 是空函数体**（无指令体，仅签名），不能像 rwfunc 一样注册进 `/lib/`，只能注册在 `/rwir/{runtime}/`。
- **`{runtime}` 反射自可执行文件名**（`filepath.Base(os.Args[0])`，如 `kvlang`），使多个 runtime 共存于同一 kvspace 不冲突。

## rwir：`/rwir/{runtime}/`

native 读写码——core（`+` / `==` / `array` …）与 lib（`sqrt` / `string.len` / `time.now` …）注册到同一 dispatch registry，启动时按 runtime 命名空间写入：

```
/rwir/kvlang/string.len    rwir string.len(S:string) -> (C:int64)
/rwir/kvlang/sqrt          rwir sqrt(A:any) -> (C:float64)
/rwir/kvlang/random.uint64 rwir random.uint64() -> (N:uint64)
```

## lib 数据：`/lib/<name>`（内置 lib 源码 layout + run init）

内置 lib 写成 kvlang 源码，`//go:embed` 打包进 runtime 二进制。启动时先 layout 到 `/lib/`，再 run 各 lib 的 init；init 内用**绝对路径赋值**把常量写入全局（裸名 `Pi = 3.14` 是 `/vthread` 局部变量、跑完回收）：

```
/lib/math.Pi   float64 3.141592653589793
/lib/math.E    float64 2.718281828459045
/lib/math.Tau  float64 6.283185307179586
```

kv 代码直接以绝对路径访问：`print(/lib/math.Pi)`。

## 启动流程

| 步骤 | 内容 |
|------|------|
| 布局 rwir | `WriteRwir(kv, runtime)` → `/rwir/{runtime}/<opcode>` |
| layout 内置 lib | 解析 `stdlib/*.kv` → `WriteFunc` → `/lib/<pkg>.init/` |
| run 各 lib init | 每个 init 独立 vthread 执行，完成后回收 `/vthread/<vtid>/`，vtid 永远递增 |

原生 rwir 由 `builtin.Register(opcode, sig, impl)` 注册；内置 lib 常量不用 Go 注册表，而是 kvlang 源码 + 启动 layout+run。

## 示例

rwir（空函数体）注册在 `rwir/builtin/math.go`：

```go
func init() {
	Register("abs",  "rwir abs(A:any) -> (C:any)", mOp{kind: "abs"})
	registerWord("sqrt", "rwir sqrt(A:any) -> (C:float64)", mOp{kind: "sqrt"})
	// ...
}
```

内置 lib 常量写在 `stdlib/math.kv`（kvlang 源码，lib body 即隐式 `init()`）：

```kv
lib math {
    /lib/math.Pi = 3.141592653589793
    /lib/math.E = 2.718281828459045
    /lib/math.Tau = 6.283185307179586
}
```

## 实现要点（相关文件）

| 文件 | 职责 |
|------|------|
| `rwir/builtin/rwirs.go` | `rwirregistry` + `Register` + `WriteRwir` |
| `rwir/builtin/math.go` | math rwir ops（abs/pow/min/max/sqrt/exp/log/neg/sign） |
| `keytree/sys.go` | `RwirRuntime(runtime, opcode)` = `/rwir/{runtime}/{opcode}` |
| `stdlib/math.kv` | 内置 lib 常量源码（`lib math { /lib/math.Pi = ... }`） |
| `stdlib/embed.go` | `//go:embed *.kv` 打包进 runtime 二进制 |
| `cmd/kvlang/stdlib.go` | 启动时 layout + run 各 lib init（回收 vthread，vtid 递增） |
| `cmd/kvlang/run.go` | `WriteRwir(kv, filepath.Base(os.Args[0]))` |
| `parser/parser.go` | lib body 隐式封装为 `init()` |
| `parser/inst.go` | 绝对路径写槽（`/lib/math.Pi`）不按成员访问反糖 |
