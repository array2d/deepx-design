# rwir 与 lib 数据：注册布局

## 三类 KV 内容

kvlang 落在 kvspace 树上的内容，按「是否有指令体」与「是否可读写」分三类，各有固定路径：

| 类别 | 语义 | 有指令体？ | KV Kind | 布局路径 |
|------|------|-----------|---------|---------|
| **rwfunc** | 函数（可执行） | ✅ 指令树 | `rwfunc` | `/lib/<pkg>.<name>/` |
| **lib 数据** | 常量（可读写） | ❌ 单个 XValue | 值类型 | `/lib/<name>` |
| **rwir** | 读写码（空函数体） | ❌ 仅签名 nr/nw | `rwir` | `/sys/rwir/{runtime}/<opcode>` |

## 铁律

- **`/lib/` 下只有两类内容**：可执行（rwfunc，有指令体）或可读写（数据/常量）。
- **rwir 是空函数体**（无指令体，仅签名），不能像 rwfunc 一样注册进 `/lib/`，只能注册在 `/sys/rwir/{runtime}/`。
- **`{runtime}` 反射自可执行文件名**（`filepath.Base(os.Args[0])`，如 `kvlang`），使多个 runtime 共存于同一 kvspace 不冲突。

## rwir：`/sys/rwir/{runtime}/`

native 读写码——core（`+` / `==` / `array` …）与 lib（`sqrt` / `string.len` / `time.now` …）注册到同一 dispatch registry，启动时按 runtime 命名空间写入：

```
/sys/rwir/kvlang/string.len    rwir string.len(S:string) -> (C:int64)
/sys/rwir/kvlang/sqrt          rwir sqrt(A:num) -> (C:float64)
/sys/rwir/kvlang/random.uint64 rwir random.uint64() -> (N:uint64)
```

## lib 数据：`/lib/<name>`

lib 包注册的内置 key（常量等可读写数据），启动时写入 `/lib/<name>`：

```
/lib/math.Pi   float64 3.141592653589793
/lib/math.E    float64 2.718281828459045
/lib/math.Tau  float64 6.283185307179586
```

kv 代码直接以绝对路径访问：`print(/lib/math.Pi)`。

## 注册 API

| API | 所在包 | 作用 | 布局 |
|-----|-------|------|------|
| `Register(opcode, sig, impl)` | `builtin` | 注册 native rwir | `/sys/rwir/{runtime}/<opcode>` |
| `RegisterLibData(name, val)` | `lib` | 注册 lib 内置 key（常量） | `/lib/<name>` |
| `WriteSysRwir(kv, runtime)` | `builtin` | 启动时写 rwir 签名 | `/sys/rwir/{runtime}/` |
| `WriteLibData(kv)` | `lib` | 启动时写 lib 数据 | `/lib/` |

## 示例

rwir（空函数体）注册在 `rwir/builtin/math.go`：

```go
func init() {
	Register("abs",  "rwir abs(A:num) -> (C:num)", mOp{kind: "abs"})
	registerWord("sqrt", "rwir sqrt(A:num) -> (C:float64)", mOp{kind: "sqrt"})
	// ...
}
```

lib 常量（可读写数据）注册在 `lib/math/math.go`：

```go
func init() {
	lib.RegisterLibData("math.Pi",  kvspace.NewFloat64(math.Pi))
	lib.RegisterLibData("math.E",   kvspace.NewFloat64(math.E))
	lib.RegisterLibData("math.Tau", kvspace.NewFloat64(2*math.Pi))
}
```

## 实现要点（相关文件）

| 文件 | 职责 |
|------|------|
| `rwir/builtin/rwirs.go` | `rwirregistry` + `Register` + `WriteSysRwir` |
| `lib/lib.go` | `libdata` + `RegisterLibData` + `WriteLibData` |
| `keytree/sys.go` | `SysRwirRuntime(runtime, opcode)` = `/sys/rwir/{runtime}/{opcode}` |
| `cmd/kvlang/run.go` | 启动时 `WriteSysRwir(kv, filepath.Base(os.Args[0]))` + `lib.WriteLibData(kv)` |
| `rwir/builtin/math.go` | math rwir ops（abs/pow/min/max/sqrt/exp/log/neg/sign） |
| `lib/math/math.go` | math 常量（`math.Pi` / `math.E` / `math.Tau`） |
