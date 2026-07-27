# Frame Model & Call Stack（帧模型与调用栈）

# Part 1: Parser & Layoutrwir

## Lower Pass: Label 帧与变量作用域

Lower 将 `if`/`while`/`for` 降级为 BlockStmt + `br`/`goto`。每个 BlockStmt 即为一个 label。

**变量首次写入位置决定其作用域。** Lower 维护 `defTracker`：`varName → definingLabel`（`""` = 函数级）。

```
函数体内首次写入 lo       → defs["lo"] = ""          → 函数级，永久
_do_3 内首次写入 mid      → defs["mid"] = "_do_3"     → _do_3 label 级
_do_3 内更新 tries        → defs["tries"] = ""（已有）→ 函数级更新
_else_7 内首次写入 too_low → defs["too_low"] = "_else_7"
```

```go
type defTracker struct { defs map[string]string }

func (d *defTracker) define(name, label string) {
    if _, ok := d.defs[name]; !ok { d.defs[name] = label }
}
```

### 指令改写规则

```
# 写槽：-> name 在 label X 内
  defs 无 name → defs[name] = X，改写 ".label/X/name"
  defs[name] == "" → 裸名（函数级更新）
  defs[name] == X → 裸名（本帧解析）

# 读槽：name 在 label Y 内
  defs[name] == Y    → 裸名（本帧）
  defs[name] == X    → 改写 ".label/X/name"（跨 label 显式路径）
  defs[name] == ""   → 裸名（函数帧回落）
  无记录              → 裸名（外部变量）
```

```go
func scopeInst(inst *ast.Instruction, curLabel string, t *defTracker) {
    for i, w := range inst.Writes {
        t.define(w, curLabel)
        if s := t.scope(w); s != "" { inst.Writes[i] = ".label/" + s + "/" + w }
    }
    for i, r := range inst.Reads {
        if isLiteral(r) || r[0] == '/' || r[0] == '"' { continue }
        if s := t.scope(r); s != "" && s != curLabel {
            inst.Reads[i] = ".label/" + s + "/" + r
        }
    }
}
```

编译器临时变量（`_1`, `_2`...）由 `labelGen.tmp()` 生成，自然落入当前 label 帧，不再污染函数帧。

### 改写前后对比（guess_number）

```
# 函数体                                   # lower 后
73 -> target                              73 -> target
lo <- 1                                   lo <- 1
hi = 100                                  hi = 100
while (found == 0) {                      goto(_while_2)
    s = lo + hi                           _while_2: {
    int(s/2) -> mid                           found == 0 -> .label/_while_2/_1
    tries = tries + 1                         br(.label/_while_2/_1, _do_3, _exit_4)
    mid == target -> hit                  }
    if (hit) { ... }                      _do_3: {
    else {                                    lo + hi -> .label/_do_3/s
        too_low = mid < target                tries + 1 -> tries
        if (too_low) {                        mid == target -> .label/_do_3/hit
            mid + 1 -> lo                     goto(_if_5)
        } else { ... }                    }
    }                                    _if_5: {
}                                           br(.label/_do_3/hit, _then_6, _else_7)
                                        }
                                        _else_7: {
                                            target < .label/_do_3/mid -> .label/_else_7/too_low
                                            goto(_if_9)
                                        }
                                        _if_9: {
                                            br(.label/_else_7/too_low, _then_10, _else_11)
                                        }
                                        _then_10: {
                                            .label/_do_3/mid + 1 -> lo
                                            goto(_merge_12)
                                        }
```

## RegisterBlocks

```go
// 旧: blockKey → "def _then_10() -> ()"
// 新: blockKey → "label _then_10"
func RegisterBlocks(kv kvspace.KVSpace, pkg, parent string, body []ast.Stmt) {
    for _, st := range body {
        if b, ok := st.(*ast.BlockStmt); ok {
            key := keytree.LibFunc(pkg, parent+"/"+b.Label)
            kv.Set([]kvspace.KVPair{{key, kvspace.Str("label " + b.Label)}})
            RegisterBlocks(kv, pkg, parent+"/"+b.Label, b.Body)
        }
    }
}
```

---

# Part 2: Runtime

## 两种帧

kvlang 运行时有两种帧：函数调用帧和 label 帧。

| | 函数调用帧 | Label 帧 |
|--|-----------|---------|
| 路径 | `callPC` (`/vthread/N/[K,0]`) | `/vthread/N/.label/name/` |
| 创建者 | `call(func, args...)` | `goto(label)` / `br(cond, t, f)` |
| `.rparam` | ✅ | ❌ |
| `.wparam` | ✅ | ❌ |
| `.ro` | ✅ | ❌ |
| `.funcframe` | ❌ | ✅ |
| 销毁 | `return` 时 DelTree | 同 label 重入时 DelTree；函数 return 随函数帧清除 |

```
# 函数调用帧
/vthread/1/[3,0]/                   ← 子帧根 = callPC
├── extindex → /lib/callee/
├── .returnpc = /vthread/1/[4,0]
├── .callpc   = /vthread/1/[3,0]/[1,0]
├── .rparam/a → ...   .wparam/c → ...
├── .ro = "a,b"
└── local_vars...

# Label 帧
/vthread/1/.label/_then_10/         ← label 帧根
├── extindex → /lib/parentFunc/_then_10/
├── .returnpc = /vthread/1/.label/_if_9/[0,0]
├── .callpc   = /vthread/1/.label/_then_10/[1,0]
├── .funcframe = /vthread/1
└── local_vars...
```

## 系统变量

| 变量 | 位置 | 更新 | 职责 |
|------|------|------|------|
| `.pc` | vthread 级 | 每 op | 外部视图：当前执行到哪 |
| `.callpc` | 每帧 | 每 op | 本帧执行进度；父帧的冻结在 call 处 |
| `.returnpc` | 每帧 | 创建时一次 | 返回地址 |

```
/vthread/1.pc = /vthread/1/[3,0]/[1,0]              ← 外部

/vthread/1/.callpc = /vthread/1/[3,0]                ← 冻结
/vthread/1/[3,0]/.returnpc = /vthread/1/[4,0]        ← 返回地址
/vthread/1/[3,0]/.callpc = /vthread/1/[3,0]/[1,0]    ← 活跃中
```

**`.returnpc` 不能从帧根推导**：label 帧根 `/vthread/1/.label/_then_10/` 不是 `[K,0]` 路径，`NextPC(frameRoot)` 无意义。创建时固化。

## return：显式与隐式

**显式 `return`**：指令 opcode 为 `return`，kvcpu 执行 HandleReturn。

**隐式 `return`**：指令序列读到空 opcode（`[s0,0]` 无值），说明已执行完当前帧最后一条指令。kvcpu 自动触发 HandleReturn。

```go
// kvcpu execute loop
inst, _ := op.Decode(kv, linkBase, pc)
if inst.Opcode == "" {
    // 隐式 return：读完最后一条指令，下一 slot 为空
    parentPC, _ := layoutrwir.HandleReturn(ctx, kv, pc, inst)
    vthread.Set(parentPC)
    continue
}
```

**HandleReturn 两种路径统一读 `.returnpc`**：

```go
func HandleReturn(kv, pc) string {
    frameRoot := FrameRoot(pc)
    returnPC := kv.Get(frameRoot + ".returnpc").Str()
    kv.UnLink(Stack(frameRoot))
    kv.DelTree(frameRoot)
    return returnPC
}
```

## HandleCall

```
1. kv.Get("/lib/main.add") → 签名 → 解析参数名
2. frameRoot = callPC                          # /vthread/42/[3,0]
3. kv.ExtIndex(frameRoot+"/", "/lib/main.add/") # extindex：帧根 → /lib/ 指令树
4. 写 .returnpc = NextPC(pc)                    # 返回地址
5. 写 .callpc   = EntryPC(frameRoot)            # 帧入口
6. 读参零拷贝：.rparam/<name> → 调用方值位置
7. 写参零拷贝：.wparam/<name> → 调用方写目标位置
8. 返回 EntryPC(frameRoot)                      # 子帧第一条指令
```

```
# 调用指令的 KV 表示（lower 后）
[3,0] = "add"              ← opcode（函数名）
[3,-1] = "a"               ← 读参 A
[3,-2] = "b"               ← 读参 B
[3,1] = "sum"              ← 写参 C 的目标路径
```

**没有返回值**。传统语言"返回值"是栈上的一块内存，拷贝给调用者。kvlang 没有线性调用栈——`f(args) -> s` 是**写参的跨帧路径映射**：被调方通过 `.wparam` 直写调用方帧，HandleReturn 时无需搬运。整个过程只有槽位间的数据流动，没有"返回值"这个概念。

### Bootstrap（顶层调用）

无父帧，frameRoot = vthreadRoot。直接 ExtIndex vthreadRoot → funcKey，不写 `.returnpc`（顶层 return → SetDone）。

```go
func Bootstrap(ctx, kv, vtid, funcName, args) string {
    vthreadRoot := VThread(vtid)
    kv.ExtIndex(Stack(vthreadRoot), LibFunc(pkg, name)+"/")
    // 绑定入参（若有）→ vthreadRoot 下
    return EntryPC(vthreadRoot)
}
```

## HandleLabel（goto/br）

```go
func HandleLabel(kv, pc, funcFrame, labelName string) string {
    labelFrame := funcFrame + "/.label/" + labelName
    kv.DelTree(labelFrame)
    kv.ExtIndex(Stack(labelFrame), LibFunc(pkg, parent+"/"+labelName)+"/")
    kv.Set([]kvspace.KVPair{
        {labelFrame + ".returnpc", Str(NextPC(pc))},
        {labelFrame + ".callpc",   Str(EntryPC(labelFrame))},
        {labelFrame + ".funcframe", Str(funcFrame)},
    })
    return EntryPC(labelFrame)
}

func gotoBlock(...) {
    funcFrame := currentFuncFrame(kv, pc)
    newPC := HandleLabel(kv, pc, funcFrame, inst.Reads[0])
    vthread.Set(newPC)
}
```

不再走 `HandleCall(tail=true)`。

## 路径解析

```go
func frameSlotKey(frameRoot, slot string) string {
    if slot == "" { return "" }
    if slot[0] == '/' { return slot }
    if slot == "._" { return "" }
    if strings.HasPrefix(slot, ".label/") {
        return funcFrameRoot(frameRoot) + "/" + slot  // 从函数帧根解析
    }
    if slot[0] == '.' { return "" }
    return Stack(frameRoot) + slot
}

func funcFrameRoot(frameRoot string) string {
    if idx := strings.Index(frameRoot, "/.label/"); idx >= 0 {
        return frameRoot[:idx]
    }
    return frameRoot
}
```

读 `.label/_else_7/too_low` → **路径即数据溯源**：这个值来自 `_else_7`。

## 运行时 KV 全景

```
/vthread/1.pc = /vthread/1/[3,0]/[1,0]

/vthread/1/                                       ← 函数帧
├── .callpc = /vthread/1/[3,0]                    ← 冻结
├── target = 73, lo = 1, hi = 100, tries = 1      ← 函数级变量
├── .label/
│   ├── _while_2/
│   │   └── _1 = true                             ← 条件临时变量
│   ├── _do_3/
│   │   ├── .returnpc = /vthread/1/.label/_while_2/[2,0]
│   │   ├── .callpc   = /vthread/1/.label/_do_3/[4,0]
│   │   └── s = 101, mid = 50, hit = false        ← label 局部
│   ├── _else_7/
│   │   ├── .returnpc = /vthread/1/.label/_if_5/[1,0]
│   │   └── too_low = true
│   └── _then_10/
│       ├── .returnpc = /vthread/1/.label/_else_7/[3,0]
│       └── .callpc   = /vthread/1/.label/_then_10/[1,0]
└── [3,0]/                                        ← 函数调用子帧
    ├── .returnpc = /vthread/1/[4,0]
    ├── .callpc   = /vthread/1/[3,0]/[1,0]
    ├── .rparam/a → ...
    └── .wparam/c → ...
```

沿 `.callpc` 链还原完整调用栈；读 `.label/` 路径直接追踪变量来源。

## 与传统 VM 的关键差异

| | 传统 VM | kvlang |
|--|---------|--------|
| 代码传递 | copy 字节码到新栈帧 | **ExtIndex**（所有帧共享 /lib/ 下同一份指令树） |
| 帧模型 | 一种帧（call/return） | **两种帧**：函数调用帧 + label 帧（goto/br → `.label/`） |
| 返回地址 | 硬件栈/隐含 | **`.returnpc`** 显式记录，隐式 return 读空 opcode 触发 |
| 崩溃恢复 | 栈帧在内存，进程死即全失 | PC=路径字符串、frameRoot 落 KV——重启续跑 |
| 可观测 | 需调试器 attach | `kvspace tree /vthread/…` 看 extindex、`.callpc` 链、label 局部变量 |
