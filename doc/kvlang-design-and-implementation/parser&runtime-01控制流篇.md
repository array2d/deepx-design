# Frame Model & Call Stack（帧模型与调用栈）

# Part 1: Parser & Layoutrwir

## Lower Pass

Lower 将 `if`/`while`/`for` 降级为 BlockStmt + `br`/`goto`。每个 BlockStmt 即为一个 label。

**变量无编译期改写。** Label 帧自身提供作用域——帧路径即前缀，不需要 `defTracker`，不需要前缀注入。Lower 只做控制流降级，变量名原样透传。

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

## RegisterBlocks

```go
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
| 路径 | `callPC` (`/vthread/N/[K,0]`) | 当前帧下 `/labelName/` |
| 创建者 | `call(func, args...)` | `goto(label)` / `br(cond, t_label, f_label)` |
| `.rparam` | ✅ | ❌ |
| `.wparam` | ✅ | ❌ |
| `.ro` | ✅ | ❌ |
| `.funcframe` | ❌ | ✅ |
| 销毁 | `return` 时 DelTree | `return` 时 DelTree（同函数帧，读 `.returnpc`） |

```
# 函数调用帧
/vthread/1/[3,0]/                   ← 子帧根 = callPC
├── extindex → /lib/callee/
├── .returnpc = /vthread/1/[4,0]
├── .callpc   = /vthread/1/[3,0]/[1,0]
├── .rparam/a → ...   .wparam/c → ...
├── .ro = "a,b"
└── local_vars...

# Label 帧（作为当前帧的子帧压栈）
/vthread/1/_do_3/                   ← label 帧根 = 当前帧 / labelName
├── extindex → /lib/parentFunc/_do_3/
├── .returnpc = /vthread/1/_else_7/[0,0]
├── .callpc   = /vthread/1/_do_3/[4,0]
├── .funcframe = /vthread/1
├── mid = 50, hit = false, s = 101  ← label 局部
└── _if_5/                          ← 嵌套 label 帧（goto 压栈）
    ├── extindex → /lib/parentFunc/_if_5/
    └── .returnpc = /vthread/1/_do_3/[5,0]
```

**Label 帧栈**：`goto` 创建 label 子帧，与 call 创建函数子帧机制一致。label 最后一条指令执行完毕后，隐式 return 读 `.returnpc` 弹栈。

```
/vthread/1/                         ← 函数帧
├── target = 73, lo = 1, hi = 100
├── _while_2/                       ← goto 压栈
│   └── _1 = true
├── _do_3/                          ← br 压栈（父 = _while_2 弹栈）
│   ├── s = 101, mid = 50
│   ├── hit = false
│   └── _if_5/                      ← goto 压栈（父 = _do_3）
│       └── _else_7/                ← br 压栈
│           ├── too_low = true
│           └── _if_9/              ← goto 压栈
│               └── _then_10/       ← br 压栈
```

## 变量作用域：递归向上查找

**写**：`-> name` 写入当前帧。当前帧是 label 帧则写入 label 帧，是函数帧则写入函数帧。无编译期前缀改写。

**读**：`name` 从当前帧开始递归向上查找，**函数帧是硬边界**。每次查找一次 KV Get，时间复杂度 O(d)，d = 当前帧到函数帧的 label 深度。

```go
func resolveRead(name string, frame string) XValue {
    for f := frame; f != funcFrame; f = parent(f) {
        if v := kv.Get(f + "/" + name); !v.IsNil() {
            return v
        }
    }
    // 最后查函数帧
    if v := kv.Get(funcFrame + "/" + name); !v.IsNil() {
        return v
    }
    return XValue{} // NameError
}
```

```
# 在 _then_10 内查询 a：
kv.Get(_then_10/a) → nil
kv.Get(_if_9/a)    → nil
kv.Get(_else_7/a)  → nil
kv.Get(_do_3/a)    → nil
kv.Get(函数帧/a)    → nil  → NameError: "a" not found

# 在 _then_10 内查询 mid：
kv.Get(_then_10/mid) → nil
kv.Get(_if_9/mid)    → nil
kv.Get(_else_7/mid)  → nil
kv.Get(_do_3/mid)    → 50 ✓   ← 停，不再继续
```

```
# _then_10 内读 mid（_do_3 的局部变量）：
_then_10/mid → nil
_if_9/mid → nil
_else_7/mid → nil
_do_3/mid → 50 ✓

# _then_10 内读 lo（函数级变量）：
_then_10/lo → nil
... (各层 label)
函数帧 / lo → 1 ✓

# _then_10 内读 unknown：
全链路 nil → "NameError: unknown not found"
```

### 函数边界 = 铁幕

**不同函数的局部变量不可互相访问。** 递归向上查到函数帧即终止，不跨越函数边界。

```
/vthread/1/                         ← 函数 A 的帧
├── x = 1                           ← A 的局部
├── [3,0]/                          ← call 函数 B 的子帧
│   ├── y = 2                       ← B 的局部
│   └── （读 y → ✓，读 x → ✗）       ← B 不能读 A 的局部
└── [4,0]/                          ← call 函数 C 的子帧
    └── （读 x → ✓，读 y → ✗）       ← C 在同一函数 A 下，能读 A，不能读 B
```

跨函数访问中间变量必须**显式传参或传指针**——通过读参/写参（`.rparam`/`.wparam`）或绝对路径 `/`。

### 与 Python 对齐

kvlang 的作用域规则与 Python 一致：

```python
x = 10           # 全局 / 函数帧

def outer():
    y = 1        # outer 局部
    def inner():
        z = 2    # inner 局部
        print(z) # → inner 帧 ✓（当前帧）
        print(y) # → outer 帧 ✓（闭包，递归向上）
        print(x) # → 全局帧 ✓（LEGB，到顶）
```

| 读变量 | Python | kvlang label 帧 |
|--------|--------|----------------|
| 当前帧 | ✅ 局部变量 | ✅ 当前 label 帧 |
| 父帧（嵌套） | ✅ 闭包捕获 | ✅ 递归向上查 |
| 全局/函数帧 | ✅ LEGB 直到 builtins | ✅ 到函数帧终止 |
| **跨函数帧** | ❌ 不可访问 | ❌ 函数帧边界截断 |

```python
def f1():
    a = 1
    f2()           # f2 无法读 a

def f2():
    print(a)       # NameError
```

kvlang 完全一致：`call` 创建的函数帧之间互不可见，必须 `f(args) -> writes` 显式传递。

Label 帧自身即前缀——不需要 `defTracker`，不需要编译期改写，帧路径自然编码作用域。

## 系统变量

| 变量 | 位置 | 更新 | 职责 |
|------|------|------|------|
| `.pc` | vthread 级 | 每 op | 外部视图 |
| `.callpc` | 每帧 | 每 op | 本帧执行进度 |
| `.returnpc` | 每帧 | 创建时一次 | 返回地址 |

```
/vthread/1.pc = /vthread/1/[3,0]/[1,0]

/vthread/1/.callpc = /vthread/1/[3,0]               ← 冻结
/vthread/1/[3,0]/.returnpc = /vthread/1/[4,0]
/vthread/1/[3,0]/.callpc = /vthread/1/[3,0]/[1,0]
```

## return：显式与隐式

**显式**：opcode 为 `return`。**隐式**：读到空 opcode（`[s0,0]` 无值），说明帧内指令已执行完。

```go
// kvcpu execute loop
inst, _ := op.Decode(kv, linkBase, pc)
if inst.Opcode == "" {
    parentPC, _ := layoutrwir.HandleReturn(ctx, kv, pc, inst)
    vthread.Set(parentPC)
    continue
}
```

两种路径统一读 `.returnpc`：

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
1. frameRoot = callPC                          # /vthread/42/[3,0]
2. kv.ExtIndex(frameRoot+"/", "/lib/callee/")
3. 写 .returnpc = NextPC(pc)
4. 写 .callpc   = EntryPC(frameRoot)
5. 读参零拷贝：.rparam/<name> → 调用方值位置
6. 写参零拷贝：.wparam/<name> → 调用方写目标位置
```

**没有返回值**。`f(args) -> s` 是**写参的跨帧路径映射**：被调方 `.wparam` 直写调用方帧，return 时无需搬运。

### Bootstrap（顶层调用）

无父帧，frameRoot = vthreadRoot，不写 `.returnpc`（顶层 return → SetDone）。

## HandleLabel（goto/br）

```go
func HandleLabel(kv, pc, funcFrame, labelName string) string {
    labelFrame := FrameRoot(pc) + "/" + labelName + "/"
    kv.DelTree(labelFrame)
    kv.ExtIndex(Stack(labelFrame), LibFunc(pkg, parent+"/"+labelName)+"/")
    kv.Set([]kvspace.KVPair{
        {labelFrame + ".returnpc", Str(NextPC(pc))},
        {labelFrame + ".callpc",   Str(EntryPC(labelFrame))},
        {labelFrame + ".funcframe", Str(funcFrame)},
    })
    return EntryPC(labelFrame)
}
```

不再走 `HandleCall(tail=true)`。

## 读变量：递归向上查找

```go
func resolveReadValue(kv, framePath, param string) XValue {
    // 字面量、绝对路径照旧
    if isLiteral(param) || param[0] == '/' { ... }

    // 读参重定向照旧
    if r := kv.Get(RParam(framePath, param)); !r.IsNil() { ... }

    // 递归向上查找
    for f := framePath; f != ""; f = parentFrame(f) {
        if v := kv.Get(Stack(f) + param); !v.IsNil() {
            return v
        }
        if isFuncFrame(f) { break }  // 函数帧是终点
    }
    return XValue{}  // 未找到 → error
}

func parentFrame(f string) string {
    // /vthread/1/_do_3/_if_5/ → /vthread/1/_do_3/
    // /vthread/1/_do_3/       → /vthread/1/
    return FrameRoot(strings.TrimRight(f, "/"))
}
```

## 与传统 VM 的关键差异

| | 传统 VM | kvlang |
|--|---------|--------|
| 代码传递 | copy 字节码 | **ExtIndex**（共享 /lib/ 指令树） |
| 帧模型 | 一种帧 | **两种帧**：函数调用帧 + label 帧栈 |
| 变量作用域 | 词法作用域 | **递归向上查找**：当前帧 → 父帧 → 函数帧 |
| 返回地址 | 硬件栈 | **`.returnpc`** 显式记录 |
| 崩溃恢复 | 内存栈，死即全失 | PC 路径字符串 + frameRoot 落 KV——重启续跑 |
