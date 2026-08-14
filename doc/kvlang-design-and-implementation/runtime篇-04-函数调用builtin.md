# 函数调用（builtin 视角）

## 调用形态总览

用户自定义函数的调用有三种源码形态，最终都解析为同一 KV key `/lib/<pkg>.<name>`：

| 源码形态 | 示例 | pkg 可为多级路径？ |
|---------|------|-------------------|
| 全路径 | `/lib/math.sum(3, 4) -> s` | ✅ `/lib/a/b.add(3, 4) -> s` |
| 点号/斜杠限定 | `math.sum(3, 4) -> s` | ✅ `a/b.add(3, 4) -> s` |
| 裸名 | `sum(3, 4) -> s` | ❌ 同 lib 内 |

**核心结论**：全路径比点号限定仅多 4 个字符（`/lib`），两者在 HandleCall 阶段完全等价。差异全在 Scanner/Parser 的 tokenization 路径不同，但最终都收敛到同一个 KV key。

---

## 前置：Scanner tokenization

理解"全路径 vs 点号限定"差异的关键在 Scanner 对 `/` 和 `.` 的处理。

### `/` 的双重人格

Scanner 中 `/` 不是 token delimiter（`parser/scanner.go:375-384`，`isTokenDelim` 不含 `/`）。`/` 的 tokenization 取决于上下文：

```go
// scanner.go:331-352
if c == '/' {
    // // 行注释 → 跳到行尾
    if i+1 < len(src) && src[i+1] == '/' { ... }

    // / 后跟字母/数字/下划线 → 路径字面量（读至真正 delimiter）
    if i+1 < len(src) && isAbsPathStart(src[i+1]) {
        start := i
        i++
        for i < len(src) && (!isTokenDelim(src[i]) || src[i] == '.') {
            i++  // '.' 不在路径中作为分隔符——/lib/a/b.add 是单个 token
        }
        tokens = append(tokens, Token{Kind: Literal, Value: src[start:i], Pos: p})
    // / 单独出现 → 除法算子
    } else {
        tokens = append(tokens, Token{Kind: Ident, Value: "/", Pos: p})
    }
}
```

**关键**：路径字面量读取循环中 `.` 被排除——`!isTokenDelim('.') || '.' == '.'` = `false || true` = `true`，所以 `/lib/a/b.add` 是**单个 Literal token**。

### `/` 在标识符中

由于 `/` 不在 `isTokenDelim` 中，标识符读取循环（scanner.go:355-358）会把 `/` 当作普通字符读入：

```
a/b.add(10, 20)
→ 标识符读取：从 a 开始，/ 和 b 都不是 delimiter → 读到 a/b
→ . 是 delimiter → 停止
→ Token: IDENT("a/b")
```

**隐含约束**：多级 pkg 路径中的段分隔符必须是 `/`（如 `a/b`），不能是 `.`（`a.b` 会被识别为 pkg.func 的 func 部分）。

---

## 阶段 1：Parser（源码 → AST opcode）

文件 `parser/inst.go` 中的 `parsePrimaryExpr`。

### 全路径 `/lib/a/b.add(args)`

Scanner 产出：`LITERAL("/lib/a/b.add") LPAREN ...`（路径字面量整体，`.` 非 delimiter）

Parser 走**常规函数调用分支**（`inst.go:288-307`）：

```go
// peek = LITERAL("/lib/a/b.add"), peekAt(1) = LPAREN → 命中
name := p.advance().Value  // "/lib/a/b.add"
// 结果 opcode = "/lib/a/b.add"
```

### 点号/斜杠限定 `a/b.add(args)`（多级 pkg）

Scanner 产出：`IDENT("a/b") DOT(".") IDENT("add") LPAREN ...`

Parser 走**点号函数调用分支**（`inst.go:310-326`）：

```go
// 触发条件：(IDENT|LITERAL_PATH) . IDENT LPAREN
opcode := p.advance().Value     // "a/b"
p.advance()                     // skip Dot
opcode += "." + p.advance().Value // "a/b.add"
// 结果 opcode = "a/b.add"
```

### 点号限定 `math.sum(args)`（单级 pkg，无 `/`）

Scanner 产出：`IDENT("math") DOT(".") IDENT("sum") LPAREN ...`

同上述点号函数调用分支：`opcode = "math.sum"`。

### 裸名 `sum(args)`

Scanner 产出：`IDENT("sum") LPAREN ...`

Parser 走常规函数调用分支：`opcode = "sum"`。

---

## 阶段 2：writeStmt（AST → KV 指令槽）

文件 `layoutrwir/layoutrwir.go:44-58`。

```go
opcode, reads := s.Flat()
if pkg != "" && !builtin.IsNativeOp(opcode) && !op.IsControlOp(opcode) &&
    !strings.Contains(opcode, keytree.FuncPathSep) &&  // "."
    !strings.HasPrefix(opcode, keytree.LibRoot+keytree.PathSegSep) &&  // "/lib/"
    opcode != "=" {
    opcode = pkg + keytree.FuncPathSep + opcode
}
```

补齐条件：opcode 必须同时满足——不含 `.`、不以 `/lib/` 开头、非 builtin/控制流/`=`。

| 源码形态 | 进入 writeStmt 的 opcode | 含 `.`？ | 以 `/lib/` 开头？ | 补齐？ | KV `[s0,0]` 写入 |
|---------|------------------------|---------|------------------|-------|-----------------|
| `/lib/a/b.add(3,4) -> s` | `/lib/a/b.add` | ✅ | ✅ | ❌ | `/lib/a/b.add` |
| `a/b.add(3,4) -> s` | `a/b.add` | ✅ | ❌ | ❌ | `a/b.add` |
| `math.sum(3,4) -> s` | `math.sum` | ✅ | ❌ | ❌ | `math.sum` |
| `sum(3,4) -> s`（在 lib math 内） | `sum` | ❌ | ❌ | ✅ → `math.sum` | `math.sum` |
| `sum(3,4) -> s`（顶层，无 lib） | `sum` | ❌ | ❌ | ❌（pkg==""） | `sum` |

---

## 阶段 3：Execute 分发（opcode → CALL）

文件 `kvcpu/execute.go:136-163`。

Dispatch 优先级：
1. `IsControlOp` — call/return/br/goto
2. `IsNativeOp` — builtin 算子（`registry` map）
3. `vtype.Lookup` — tensor.* 等命名空间
4. `isCopyOp` — opcode="=" 且有写槽
5. **default → 用户定义函数**

```go
// default 分支：opcode 不在前四个优先级 → 改写为 call 指令
inst.Reads = append([]string{inst.Opcode}, inst.Reads...)
inst.Opcode = op.OpCall
execErr = handleControl(ctx, c.kv, vtid, pc, inst)
```

opcode 移到 `inst.Reads[0]` 作为被调函数名。

---

## 阶段 4：HandleCall（函数名 → KV key）

文件 `layoutrwir/layoutrwir.go:87-105`。

```go
funcName := inst.Reads[0]

libPrefix := "/lib/"
if strings.HasPrefix(funcName, libPrefix) {
    // "/lib/a/b.add" → rest = "a/b.add"
    rest := funcName[len(libPrefix):]
    if dot := strings.LastIndex(rest, "."); dot > 0 {
        pkg = rest[:dot]       // "a/b"
        funcName = rest[dot+1:] // "add"
    } else {
        funcName = rest
    }
} else if dot := strings.LastIndex(funcName, "."); dot > 0 {
    // "a/b.add" → pkg = "a/b", funcName = "add"
    pkg = funcName[:dot]
    funcName = funcName[dot+1:]
}
// else: "sum" → pkg = "", funcName = "sum"

funcKey := keytree.LibFunc(pkg, funcName)
// "/lib/" + pkg + "." + funcName → "/lib/a/b.add"
```

`keytree.LibFunc` (`keytree/entry.go:5-8`)：
```go
func LibFunc(pkg, name string) string {
    if pkg == "" { return "/lib/" + name }
    return "/lib/" + pkg + "." + name
}
```

**所有形态最终收敛到同一个 KV key**，多级 pkg 的 `/` 原样保留在 pkg 段中：

| 源码 | funcName in HandleCall | pkg | 最终 KV key |
|------|----------------------|-----|-----------|
| `/lib/a/b.add(3,4)` | `a/b.add` (strip `/lib/`) | `a/b` | `/lib/a/b.add` |
| `a/b.add(3,4)` | `a/b.add` | `a/b` | `/lib/a/b.add` |
| `/lib/math.sum(3,4)` | `math.sum` (strip `/lib/`) | `math` | `/lib/math.sum` |
| `math.sum(3,4)` | `math.sum` | `math` | `/lib/math.sum` |
| `sum(3,4)` (in lib math) | `math.sum` (writeStmt 补齐) | `math` | `/lib/math.sum` |

---

## 完整 tokenization 对照

### 单级 pkg

| 源码 | Scanner token 流 | Parser 分支 | opcode |
|------|-----------------|------------|--------|
| `/lib/math.sum(3,4)` | `LITERAL("/lib/math.sum") LPAREN ...` | 常规函数调用 | `/lib/math.sum` |
| `math.sum(3,4)` | `IDENT("math") DOT IDENT("sum") LPAREN ...` | 点号函数调用 | `math.sum` |

### 多级 pkg

| 源码 | Scanner token 流 | Parser 分支 | opcode |
|------|-----------------|------------|--------|
| `/lib/a/b.add(3,4)` | `LITERAL("/lib/a/b.add") LPAREN ...` | 常规函数调用 | `/lib/a/b.add` |
| `a/b.add(3,4)` | `IDENT("a/b") DOT IDENT("add") LPAREN ...` | 点号函数调用 | `a/b.add` |

---

## 查找失败

若 `funcKey` 对应的 `[0,0]` XValue 为 None（函数未注册），HandleCall 返回：

```
NameError: func not found: <funcName>
```

线程进入 error 终态。

---

## 阶段 5：Resolve（Ptr 链变量解析）

**文件** `rwir/builtin/resolve.go`。

调用后指令执行时，读槽引用走 Ptr 链解析（3 跳）：

```
resolveReadValue(kv, framePath, param):
  1. literal? → return param.Val
  2. absolute path? → GetOne(kv, "/abs/path")
  3. funcFrameRoot → Find nearest .lib marker
  4. GetOne(rwRoot+"/"+name) → ext→ Ptr("[0,-j]")?     ← name→slot
     yes: GetOne(rwRoot+"/[0,-j]") → Char(path)       ← slot→arg addr
          GetOne(path) → value                         ← arg addr→value
  5. fallback: GetOne(rwRoot+"/"+name) → local var

resolveWriteSlot(kv, framePath, name):
  1. absolute path? → return name
  2. funcFrameRoot
  3. GetOne(rwRoot+"/"+name) → ext→ Ptr("[0,+j]")?     ← name→slot
     yes: return PtrTarget → "[0,+j]" → kv reads arg addr
  4. fallback: rwRoot+"/"+name → local var path
```

**优化**：函数入口时 List 一次 named key → build `name→slotIdx` 内存 map，降为 2 跳。

**不再使用**：`‥rparam/‥wparam` 重定向表（替换为 Ptr 链）。

## 相关文件

| 文件 | 职责 |
|------|------|
| `parser/scanner.go:331-384` | `/` 和 `.` 的 tokenization 规则 |
| `parser/inst.go:288-326` | 解析三种调用形态为 AST Expr |
| `layout/layout.go:44-58` | 裸名补齐 pkg 前缀 |
| `kvcpu/execute.go:156-163` | 用户函数分发 → CALL |
| `layout/layout.go:131-225` | HandleCall：函数名解析 + Ptr 链 arg 写入 |
| `rwir/builtin/resolve.go` | resolveReadValue：Ptr 链变量解析 |
| `rwir/builtin/helper.go` | resolveWriteSlot：Ptr 链写槽解析 |
| `keytree/entry.go:5-8` | LibFunc KV key 构造 |
| `keytree/frame.go` | EntryPC = [1,0] |
| `kvspace-go/xvalue_rwir.go` | Rwfunc body=[nr\|nw]（4 字节） |
| `kvspace-go/xvalue.go` | XValueHead.IsPtr + Ptr 类型 |

---

## builtin 包清单

| 包 | 路径 | 说明 |
|----|------|------|
| `byte` | `rwir/builtin/byte/` | 所有 byte 派生 kind 通用方法（Len, At, Set, Slice） |
| `utf8` | `rwir/builtin/utf8/` | UTF-8 编解码（String, Bytes, Len, At, Set, Slice），仅 charbyte |
| `unicode` | `rwir/builtin/unicode/` | Unicode 码点操作 |
| `random` | `rwir/builtin/random/` | crypto/rand uint64（Uint64, Int63, Intn） |

### kind 继承树

```
uint8                         ← 基类，elemSize=1
├── bool, int8, charbyte   1B
├── int16, uint16             2B
├── int32, uint32, float32    4B
└── int64, uint64, float64    8B
```

`IsByteDerived(kind)` / `ElemSize(kind)` 判定继承关系。`byte.Len(v)` 对所有派生 kind 可用，`utf8.String(v)` 仅 charbyte 可用。
