# rwir 与 lib 数据：注册布局

> 签名参数/返回值的 `type` 用**签名类型表达式**（并集 `A|B`、通配 `any`、高维 shape `[2,3]`、动态维 `?`），BNF 见 [[runtime篇-07-签名类型表达式]] 与 `grammar.bnf`。

## 三类 KV 内容

kvlang 落在 kvspace 树上的内容，按「是否有指令体」与「是否可读写」分三类，**统一寻址在 `/lib/` 下**：

| 类别 | 语义 | 有指令体？ | KV Kind | 布局路径 |
|------|------|-----------|---------|---------|
| **rwfunc** | 函数（可执行） | ✅ 指令树 | `rwfunc` | `/lib/<pkg>.<name>/` |
| **lib 数据** | 常量（可读写） | ❌ 单个 XValue | 值类型 | `/lib/<name>` |
| **rwir** | 读写码（空函数体） | ❌ 仅签名 nr/nw | `rwir` | `/lib/<opcode>` |

## 铁律

- **三类内容同存 `/lib/`**：可执行（rwfunc，有指令体）、可读写（数据/常量）、扩展读写码（rwir，仅签名）。不再有独立的 `/rwir/{runtime}/` 槽位树，`{runtime}` 命名空间维度已取消。
- **rwir 与 rwfunc 同定义体格式**：值体均为 `[nr:u16 LE][nw:u16 LE][读参+写参 kindexp，"\n" 连接]`（kindexp 与源码类型表达式逐字节相同，详见 [[runtime篇-07-签名类型表达式]] §八）。二者区别仅在 KV kind（`rwir` vs `rwfunc`）与有无指令体子节点（`[i,j]`）。
- **native rwir 不落盘**：core（`+` / `==` / `array` …）与 lib（`sqrt` / `string.len` / `time.now` …）内建在 runtime 的 C 注册表 `builtins[]`（`opcode → bi_fn` 映射），是唯一权威来源；多态，类型处理内建于各 `bi_fn`（由实参 kind 决定输出 kind），**不写 kvspace、运行时不回查**。

## rwir：`/lib/<opcode>`

**扩展 rwir**（宿主注入的非 native 算子，如 `numpy.add`）经 `kvlang_rwirextRegister(c, opcode, nr, nw, sig)` 写入 `/lib/<opcode>`（`<opcode>` 可带包前缀），值 `kind=rwir`、`array_len=1`，raw 即上述定义体格式；写入前对各参 kindexp 做 `kvlang_rwirextTypeValid` 校验（拒绝 `path` 等非 kind）。**用户 rwir 声明**（kvlang 源码 `rwir myop(A:int64) -> (C:int64)`）由 layout 的 `write_rwir_decl` 同样写入 `/lib/<opcode>`。

**native rwir** 数值多类型算子在 `builtins[]` 中**融合为单条**（如 `add` 一条覆盖 int8…float64），派发前 `strip_num_kind` 剥掉 `<numkind>.` 前缀再查表，输出 kind 由实参决定，不再按 `int64.add`/`int32.add` 铺开。

## lib 数据：`/lib/<name>`

`/lib/<name>` 存单个 XValue 常量，任意 kvlang 代码以绝对路径读写：

```
/lib/math.Pi   float64 3.141592653589793
/lib/math.E    float64 2.718281828459045
```

```kv
lib math {
    /lib/math.Pi = 3.141592653589793
    /lib/math.E  = 2.718281828459045
}
```

`lib` body 内的绝对路径赋值即写常量；kv 代码直接以绝对路径访问 `print(/lib/math.Pi)`。裸名（`Pi = 3.14`）是 `/vthread` 帧局部变量、跑完回收，不入 `/lib/`。

> 注：Go/oldhero 曾用 `//go:embed stdlib/*.kv` + 启动 layout+run 各 lib `init` 自举内置常量。当前 Rust layout + C runtime **不内置 stdlib 自举**；常量由用户/宿主显式 layout 写入 `/lib/`。

## 落盘流程（Rust layout + C runtime）

| 步骤 | 内容 |
|------|------|
| 建目录 | layout `init_dirs`：`mkindex("/lib/")`、`mkindex("/vthread/")` |
| 写 rwfunc | `write_func` → `/lib/<pkg>.<name>/`：`[0,0]` 签名（rwfunc）、`<param>` 参数 slot 指针、`[i,j]` 指令（rwir，i≥1）、`.src` 源码副本 |
| 写用户 rwir | `write_rwir_decl` → `/lib/<opcode>`（kind=rwir，无指令体） |
| 注册 native | runtime 静态表 `builtins[]`，不落盘 |
| 注入扩展 rwir | 宿主 `kvlang_rwirextRegister(opcode, nr, nw, sig)` → `/lib/<opcode>` |

## 示例

宿主注入扩展 rwir（各参 kindexp 以 `\n` 连接）：

```c
kvlang_rwirextRegister(c, "numpy.add", 2, 1, "int64|float64\nint64|float64\nint64|float64");
```

用户在 kvlang 源码声明 rwir（无体，仅签名）→ layout 落 `/lib/myop`：

```kv
rwir myop(A:int64, B:int64) -> (C:int64)
```

## 实现要点（相关文件）

| 文件 | 职责 |
|------|------|
| `layout/src/keytree.rs` | `LIB_ROOT`/`RWIR_ROOT` = `/lib`；`lib_func`/`rwir`/`lib_src` 路径构造 |
| `layout/src/code.rs` | `init_dirs`、`write_func`（rwfunc→`/lib`）、`write_rwir_decl`（rwir→`/lib/<opcode>`） |
| `layout/src/kvkind.rs` | `new_rwfunc` / `new_rwir` 定义体编码（`nr`u16 + `nw`u16 + `\n` 连接 kindexp） |
| `runtime/src/keytree.c` | `kt_rwir(opcode)` = `/lib/<opcode>` |
| `runtime/src/builtin.c` | native `builtins[]`（opcode→`bi_fn`，数字多类型融合）、`strip_num_kind` 派发 |
| `runtime/src/rwirext.c` | `kvlang_rwirextRegister(opcode, nr, nw, sig)` → `/lib/<opcode>` |
| `runtime/src/kvcpu.c` | execute 循环 `load_def_reads` + `check_read_types` 内联类型匹配 |
| `runtime/src/type_expr.c` | `kvlang_rwirextTypeValid`（装载校验）+ `kvlang_rwirextTypeMatch`（运行时匹配） |
