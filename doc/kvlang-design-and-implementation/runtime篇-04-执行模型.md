# Execution Model（执行模型）

import kvspace篇-03-代码指令的布局格式

## CLI 装载与执行模型

### lib 树与装载（fix-033/034/039）

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

### 函数调用机制：HandleCall / HandleReturn

kvlang 没有"返回值"——传统"返回值"的正确表述是：**HandleReturn 把被调方帧的写参槽值，写回到调用方帧的指定路径**。

**调用时（HandleCall）**：

```
1. kv.Get("/lib/main.add") → 签名 → 解析参数名
2. frameRoot = callPC              # /vthread/42/[3,0]
3. kv.ExtIndex(frameRoot+"/", "/lib/main.add/")  # extindex：帧根本身 → /lib/ 指令树
4. 读参零拷贝：存储调用方值的绝对路径到 .rparam/<name>，CPU 读参时直接从此路径读取
5. 写参零拷贝：存储调用方写目标的绝对路径到 .wparam/<name>，CPU 写参时直接写入此路径
6. kv.Set(frameRoot+".rootfunc", funcName)    # 入口函数名
7. kv.Set(frameRoot+".ro", paramList)          # 只读参数名单（fix-027）
8. 返回 frameRoot+"/[0,0]"                     # 子帧第一条指令 PC
```

这个"指定路径"通过调用时的写槽声明传递：

```
# 指令级别（lower 后的 KV 表示）：
[3,0] = "add"              ← opcode（函数名）
[3,-1] = "a"               ← 读参 A
[3,-2] = "b"               ← 读参 B
[3,1] = "sum"              ← 写参 C 的目标路径（HandleReturn 时写入）
```

**返回时（HandleReturn）**：

```
1. 写参已在子帧执行期间经 .wparam 直写父帧，无需搬运
2. frameRoot 即 callPC，NextPC(frameRoot) 恢复父帧下一条指令
3. kv.UnLink(frameRoot+"/")                           # 移除 extindex
4. kv.DelTree(frameRoot)                         # 清整个子帧
```

**为什么没有返回值**。传统语言的"返回值"本质是调用栈上的一块内存——函数执行完毕，这块内存的值被拷贝给调用者。kvlang 没有线性调用栈，每个函数调用创建一个 KV 子树：

```
/vthread/run/               ← 调用方帧根
/vthread/run/                             ← 帧根 extindex → /lib/（指令查找）
/vthread/run/[3,0]/         ← 被调方帧根（callPC，extindex → /lib/）
/vthread/run/[3,0]/C        ← 被调方的局部变量 C（帧根 extindex 写层）
```

HandleReturn 经 frameRoot（即 callPC）定位调用指令，读取其写槽 `[3,1]`：

```
kv.Get(childFrame + "/C")  →  value
kv.Set(parentFrame + "/sum", value)
```

这不是"函数返回值"，是**写参的跨帧路径映射**。

**TCO（Tail Call Optimization，尾调用优化：goto/br 不建子帧，仅 Unlink + ExtIndex 换 extindex 指向目标块，.rootfunc 保持不变）**。
**顶层调用（Bootstrap）**：frameRoot 即 callPC，直接 ExtIndex frameRoot → funcKey。

### 与传统 VM 的关键差异

| | 传统 VM | kvlang |
|--|---------|--------|
| 代码传递 | copy 字节码到新栈帧 | **ExtIndex**（所有帧共享 /lib/ 下同一份指令树） |
| TCO（尾调用优化） | 需特殊优化（复用帧 + 重定向参数） | Unlink + ExtIndex，已有的 extindex 机制天然支持 |
| 崩溃恢复 | 栈帧在内存，进程死即全失 | PC=路径字符串、frameRoot=返回点落 KV——重启续跑 |
| 可观测 | 需调试器 attach | `kvspace tree /vthread/…` 看 extindex 指向、frameRoot 在哪，局部变量直读帧根 |

**`=` 操作码是值拷贝，不是函数调用**：`a -> b` 编码为 `[s0,0]="="`（值拷贝），
函数调用是 `call(name, args…) → writes`——opcode 位是 `call`，ExtIndex 发生在 HandleCall 内部。
二者在 KV 层无歧义，opcode 位永远不放变量引用。
