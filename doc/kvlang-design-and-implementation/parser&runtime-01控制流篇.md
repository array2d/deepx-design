# Frame Model & Call Stack（帧模型与调用栈）

# Part 1: Parser & Layoutrwir

## XValue Kind 三层

指令架构三层，`/lib/` 下 XValue kind 直接体现：

| Kind | `/lib/` 示例 | 含义 |
|------|-------------|------|
| `rwir` | `/lib/func/[0,0] = "+"` | 原子指令读写槽 ，kvcpu可直接用buildin解析运行|
| `rwfunc` | `/lib/func` | 函数定义（复合 rwir） ，需要先建立extindex，映射/lib/pkg/func的指令序列|
| `label` | `/lib/func/_then_10` | Label 块 ，需要先建立extindex，映射/lib/pkg/func/label的指令序列|

帧类型不靠路径模式判断——读帧根 extindex target 的 XValue.Kind() 即知。`rwfunc` = 函数帧边界，`label` = label 帧。

## WriteFunc

```go
func WriteFunc(kv kvspace.KVSpace, pkg string, fn *ast.Func) {
    kv.DelTree(keytree.LibFunc(pkg, fn.Sig.Name))
    kv.Set([]kvspace.KVPair{
        {keytree.LibFunc(pkg, fn.Sig.Name), kvspace.Raw("rwfunc", []byte(fn.Sig.String()))},
    })
    WriteBody(kv, pkg, fn.Sig.Name, fn.Body, typeMap)     // rwir 指令槽
    RegisterBlocks(kv, pkg, fn.Sig.Name, fn.Body)         // label 块
}
```

## RegisterBlocks

```go
func RegisterBlocks(kv kvspace.KVSpace, pkg, parent string, body []ast.Stmt) {
    for _, st := range body {
        if b, ok := st.(*ast.BlockStmt); ok {
            key := keytree.LibFunc(pkg, parent+"/"+b.Label)
            kv.Set([]kvspace.KVPair{{key, kvspace.Raw("label", nil)}})
            RegisterBlocks(kv, pkg, parent+"/"+b.Label, b.Body)
        }
    }
}
```

## Lower Pass

Lower 将 `if`/`while`/`for` 降级为 BlockStmt + `br`/`goto`。**变量无编译期改写**——label 帧自身提供作用域，帧路径即前缀。

```
# 源                                       # lower 后
while (found == 0) {                      goto(_while_2)
    s = lo + hi                           _while_2: {
    int(s/2) -> mid                           found == 0 -> _1
    tries = tries + 1                         br(_1, _do_3, _exit_4)
    mid == target -> hit                  }
    if (hit) { ... }                      _do_3: {
    else {                                    lo + hi -> s
        too_low = mid < target                tries + 1 -> tries
        if (too_low) {                        mid == target -> hit
            mid + 1 -> lo                     goto(_if_5)
        }                                 }
}                                         _if_5: {
                                              br(hit, _then_6, _else_7)
                                          }
                                          _else_7: {
                                              mid < target -> too_low
                                              goto(_if_9)
                                          }
                                          _if_9: {
                                              br(too_low, _then_10, _else_11)
                                          }
                                          _then_10: {
                                              mid + 1 -> lo
                                              goto(_merge_12)
                                          }
```

---

# Part 2: Runtime

## 三种路径角色

| 路径 | 角色 | 帧根 extindex → | Kind |
|------|------|-----------------|------|
| `/vthread/1/` | **虚线程根** = 入口函数帧 | `/lib/init` | `rwfunc` |
| `/vthread/1/[0,0]/` | **函数调用帧** | `/lib/B` | `rwfunc` |
| `/vthread/1/[0,0]/_do_3/` | **Label 帧** | `/lib/B/_do_3` | `label` |

虚线程根是 Bootstrap 创建的特例——它兼做入口函数的帧，但本质也是函数帧（kind=`rwfunc`）。通用函数调用帧由 `call` 创建。

## 两种帧

| | 函数调用帧 (rwfunc) | Label 帧 |
|--|-------------------|---------|
| 路径 | `callPC` (`/vthread/N/.../[K,0]`) | 当前帧下 `/labelName/` |
| 创建者 | `call(func, args...)` | `goto(label)` / `br(cond, t, f)` |
| `.rparam` | ✅ | ❌ |
| `.wparam` | ✅ | ❌ |
| `.ro` | ✅ | ❌ |
| 销毁 | `return` 时 DelTree | `return` 时 DelTree（同函数帧） |

```
# 函数调用帧（B 被 A 调用）
/vthread/1/[0,0]/                   ← 子帧根 = callPC
├── extindex → /lib/B   [rwfunc]    ← 帧类型：函数
├── .returnpc = /vthread/1/[1,0]
├── .callpc   = /vthread/1/[0,0]/[3,0]
├── .rparam/a → ...   .wparam/c → ...
├── .ro = "a,b"
├── local_vars...
└── _do_3/                          ← 此帧内的 label 子帧

# Label 帧
/vthread/1/[0,0]/_do_3/             ← label 帧根
├── extindex → /lib/B/_do_3 [label] ← 帧类型：label
├── .returnpc = /vthread/1/[0,0]/_while_2/[2,0]
├── .callpc   = /vthread/1/[0,0]/_do_3/[4,0]
├── mid = 50, hit = false, s = 101  ← label 局部
└── _if_5/                          ← 嵌套 label 帧
    ├── extindex → /lib/B/_if_5 [label]
    └── .returnpc = /vthread/1/[0,0]/_do_3/[5,0]
```

**Label 帧栈**：`goto` 创建 label 子帧，与 call 创建函数子帧机制一致。隐式 return 读 `.returnpc` 弹栈。

```
/vthread/1/[0,0]/                   ← 函数调用帧（B 的栈帧）
├── lo = 1, hi = 100, target = 73
├── _while_2/       [label]
│   └── _1 = true
├── _do_3/          [label]
│   ├── s = 101, mid = 50, hit = false
│   └── _if_5/      [label]
│       └── _else_7/ [label]
│           ├── too_low = true
│           └── _if_9/ [label]
│               └── _then_10/ [label]
```

## 变量作用域：递归向上查找 + extKind 判边界

**写**：`-> name` 写入当前帧。

**读**：`name` 从当前帧递归向上查。帧类型由帧根 extindex → `/lib/` 的 XValue.Kind() 判定：`"rwfunc"` = 函数帧边界，`"label"` = 继续向上。O(d)，d = label 深度。

```go
func resolveRead(name string, frame string) XValue {
    for f := frame; extKind(f) != "rwfunc"; f = parent(f) {
        if v := kv.Get(f + "/" + name); !v.IsNil() {
            return v
        }
    }
    return kv.Get(funcFrame + "/" + name)
}

func extKind(frameRoot string) string {
    extTarget := getExtIndexTarget(frameRoot)
    return kv.Get(extTarget).Kind()  // "rwfunc" | "label"
}
```

```
# _then_10 内读 mid（_do_3 的局部）：
kv.Get(_then_10/mid) → nil     extKind(_then_10)="label" → 继续
kv.Get(_if_9/mid)    → nil     extKind(_if_9)="label"    → 继续
kv.Get(_else_7/mid)  → nil     extKind(_else_7)="label"  → 继续
kv.Get(_do_3/mid)    → 50 ✓    ← 找到，停

# _then_10 内读 lo：
... (各层 label, kind="label" → 继续)
kv.Get(/vthread/1/[0,0]/lo) → 1 ✓  extKind="rwfunc" → 边界
```

### 函数边界 = 铁幕

**不同函数的局部变量不可互相访问。** extKind 返回 `"rwfunc"` 即终止向上查找，不跨越 call 边界。

```
/vthread/1/                         ← 虚线程根 [rwfunc] = 入口函数 A 的帧
├── x = 1                           ← A 的局部
├── [0,0]/                          ← call B [rwfunc]
│   ├── y = 2                       ← B 的局部
│   │   （读 y→✓, 读 x→✗）          ← B 不能读 A 的局部（跨 rwfunc 边界）
│   └── [3,0]/                      ← B 内 call C [rwfunc]
│       └── （读 z→✓, 读 y→✗）      ← C 不能读 B 的局部
└── [1,0]/                          ← A 内 call D [rwfunc]
    └── （读 w→✓, 读 y→✗）          ← D 不能读 B 的局部
```

跨函数访问变量必须**显式传参或传指针**——读参/写参（`.rparam`/`.wparam`）或绝对路径 `/`。

### 与 Python 对齐

```python
x = 10           # 全局 / 入口函数帧

def outer():     # 函数调用帧
    y = 1        # outer 局部
    def inner(): # 函数调用帧
        z = 2    # inner 局部
        print(z) # → inner 帧 ✓
        print(y) # → outer 帧 ✓（闭包）
        print(x) # → 全局 ✓（LEGB）
```

| 读变量 | Python | kvlang |
|--------|--------|--------|
| 当前帧 | ✅ 局部变量 | ✅ 当前帧 |
| 父帧（嵌套） | ✅ 闭包捕获 | ✅ 递归向上（extKind="label"→继续） |
| 函数帧 | ✅ LEGB 直到 builtins | ✅ extKind="rwfunc"→终止 |
| **跨 call 边界** | ❌ 不可访问 | ❌ extKind="rwfunc"→截断 |

## 系统变量

| 变量 | 位置 | 更新 | 职责 |
|------|------|------|------|
| `.pc` | vthread 级 | 每 op | 外部视图 |
| `.callpc` | 每帧 | 每 op | 本帧执行进度 |
| `.returnpc` | 每帧 | 创建时一次 | 返回地址 |

```
/vthread/1.pc = /vthread/1/[0,0]/_do_3/[4,0]

/vthread/1/[0,0]/.callpc = /vthread/1/[0,0]/[3,0]
/vthread/1/[0,0]/.returnpc = /vthread/1/[1,0]

/vthread/1/[0,0]/_do_3/.callpc = /vthread/1/[0,0]/_do_3/[4,0]
/vthread/1/[0,0]/_do_3/.returnpc = /vthread/1/[0,0]/_while_2/[2,0]
```

## return：显式与隐式

**显式**：opcode `return`。**隐式**：空 opcode。统一读 `.returnpc`。

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
1. frameRoot = callPC                     # /vthread/1/[0,0]
2. kv.ExtIndex(frameRoot+"/", "/lib/B/")  # extindex → /lib/B [rwfunc]
3. 写 .returnpc = NextPC(pc)
4. 写 .callpc   = EntryPC(frameRoot)
5. 读参零拷贝：.rparam/<name> → 调用方值位置
6. 写参零拷贝：.wparam/<name> → 调用方写目标位置
```

**没有返回值**。`f(args) -> s` 是写参的跨帧路径映射：被调方 `.wparam` 直写调用方帧。

### Bootstrap

虚线程根 `/vthread/1/`，extindex → 入口函数 [rwfunc]。无父帧，不写 `.returnpc`。

## HandleLabel（goto/br）

```go
func HandleLabel(kv, pc, labelName string) string {
    labelFrame := FrameRoot(pc) + "/" + labelName + "/"
    kv.DelTree(labelFrame)
    kv.ExtIndex(Stack(labelFrame), LibFunc(pkg, parent+"/"+labelName)+"/")  // [label]
    kv.Set([]kvspace.KVPair{
        {labelFrame + ".returnpc", Str(NextPC(pc))},
        {labelFrame + ".callpc",   Str(EntryPC(labelFrame))},
    })
    return EntryPC(labelFrame)
}
```

## 与传统 VM 的关键差异

| | 传统 VM | kvlang |
|--|---------|--------|
| 代码传递 | copy 字节码 | **ExtIndex**（共享 /lib/ 指令树） |
| 帧模型 | 一种帧 | **两种帧**：rwfunc + label |
| 帧类型判定 | — | **extindex target XValue.Kind()**：rwfunc/label |
| 变量作用域 | 词法作用域 | **递归向上查找**：extKind="label"→继续，"rwfunc"→停 |
| 返回地址 | 硬件栈 | **`.returnpc`** 显式记录 |
| 崩溃恢复 | 内存栈，死即全失 | PC + frameRoot 落 KV——重启续跑 |
