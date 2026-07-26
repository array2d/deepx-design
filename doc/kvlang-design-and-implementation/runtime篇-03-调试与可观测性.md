# Chapter 11: Debugging and Observability（调试与可观测性）

> **TODO**: 本章的 tree 输出和 `.funclib` 引用需同步更新为 extindex 模型（帧根 extindex → /lib/）。
> 待代码侧 refactor 完成并 `kvspace tree` 输出稳定后逐段修正。

import kvlang-design-and-implementation
import ch10-system-variables

## 11. 调试器实战：用 kvspace 静态观察栈与源码

本节以 `tutorial/03-debugger/chain_array.kv` 为例，演示如何用 `kvlang --debug` 暂停程序，再用 `kvspace tree/list` 静态观察 vthread 栈帧（`/vthread`）和编译后源码（`/lib`）。

### 11.1 启动调试

```bash
cd kvlang
kvlang run --debug tutorial/03-debugger/chain_array.kv
# VM 在 debugger() 处暂停，进程阻塞等待 agent 命令
```

source:

```
def f3() -> (a:int64, s:int64) {
    debugger()          # ← 在此暂停
    at(a, 0) -> v0
    at(a, 1) -> v1
    v0 + v1 -> s        # 30 + 40 = 70
}

def f2() -> (a:int64, s:int64) {
    a:int64 = [30, 40]  # f2 创建数组放写参
    f3() -> (a, s)      # 传给 f3 的写参，不拷贝，同路径直达
}

def f1() -> (s:int64) {
    f2() -> (_, s)      # 接收 s，丢弃数组
}

def init() -> () {
    f1() -> r
    print(r)            # 70
}
```

### 11.2 观察栈帧：`kvspace tree /vthread`

暂停后，`kvspace tree /vthread` 输出（仅保留 `run` vthread 和相关函数）：

```
/vthread/run
├── .funclib → def init() -> ()
│   ├── [0]  rwir:f1  rwir:r
│   ├── [1]  rwir:r   rwir:print
│   └── [2]  rwir:return
├── .rootfunc  string:init
├── .pc        string:/vthread/run/[0,0]/[0,0]/[1,0]/[0,0]
├── .status    string:running
├── .debugger  string:break
└── [0,0]                                    ← f1 调用帧
    ├── .funclib → def f1() -> (s:int64)
    │   ├── [0]  rwir:f2  rwir:_  int64:0
    │   └── [1]  int64:0  rwir:return
    ├── .rootfunc  string:f1
    ├── .rparam/s  → /vthread/run/r          ← 读参 s → init 的 r
    ├── .wparam/s  → /vthread/run/r          ← 写参 s → init 的 r
    ├── [0,0]                                ← f2 调用帧
    │   ├── .funclib → def f2() -> (a:int64, s:int64)
    │   │   ├── [0]  int:30  int:40  rwir:array  int64:0
    │   │   ├── [1]  rwir:f3  int64:0  int64:0
    │   │   └── [2]  int64:0  int64:0  rwir:return
    │   ├── .rootfunc  string:f2
    │   ├── .rparam/a  → /vthread/run/[0,0]/_
    │   ├── .rparam/s  → /vthread/run/r
    │   ├── .wparam/a  → /vthread/run/[0,0]/_
    │   ├── .wparam/s  → /vthread/run/r
    │   └── [1,0]                            ← f3 调用帧（当前暂停位置）
    │       ├── .funclib → def f3() -> (a:int64, s:int64)
    │       │   ├── [0]  rwir:debugger       ← PC 在此
    │       │   ├── [1]  int64:0  int:0  rwir:at  rwir:v0
    │       │   ├── [2]  int64:0  int:1  rwir:at  rwir:v1
    │       │   ├── [3]  rwir:v0  rwir:v1  rwir:+  int64:0
    │       │   └── [4]  int64:0  int64:0  rwir:return
    │       ├── .rootfunc  string:f3
    │       ├── .rparam/a  → /vthread/run/[0,0]/_
    │       ├── .rparam/s  → /vthread/run/r
    │       ├── .wparam/a  → /vthread/run/[0,0]/_
    │       └── .wparam/s  → /vthread/run/r
    └── _  int64[2]:30                       ← f1 的丢弃槽，存放 f2 产出的数组
```

### 11.3 关键发现

**PC 字符串即调用栈**。PC = `/vthread/run/[0,0]/[0,0]/[1,0]/[0,0]`，逐段解读：

| 路径段 | 含义 |
|--------|------|
| `/vthread/run/` | vthread 根帧（init） |
| `[0,0]/` | init 指令 [0,0] 发起的调用 → **f1 帧** |
| `[0,0]/` | f1 指令 [0,0] 发起的调用 → **f2 帧** |
| `[1,0]/` | f2 指令 [1,0] 发起的调用 → **f3 帧** |
| `[0,0]` | f3 指令 [0,0] = `debugger()`，当前执行位置 |

这与传统 VM 的"栈深度=帧数"完全同构——区别只在于 kvlang 用路径深度而非整数偏移。

**`.funclib` 软链 = 零拷贝共享指令树**。四个帧的 `.funclib` 均 Link 到 `/lib/<name>`。关键：f3 帧出现在两个位置——`/vthread/run/[0,0]/[0,0]/[1,0]`（f2 的子帧）和 f2 `.funclib` 展开树中——这是因为 `kvspace tree` 跟随软链展开了 `/lib/f3` 的内容。实际上 KV 存储中只存一份指令树，所有帧通过 Link 共享。

**`.rparam` / `.wparam` 实现零拷贝跨帧传参**。注意 f3 帧中没有任何局部变量槽——执行尚未到 `v0`/`v1` 的创建。但读写参的路由已经建立：

```
f3 .rparam/a → /vthread/run/[0,0]/_    ← f1 的丢弃槽（数组在此）
f3 .rparam/s → /vthread/run/r          ← init 的 r 槽
f3 .wparam/a → /vthread/run/[0,0]/_    ← 写回 f1 丢弃槽（不保留）
f3 .wparam/s → /vthread/run/r          ← 写回 init 的 r（最终结果）
```

这不是"传值"——是**路径别名**。f3 执行 `at(a, 0)` 时，kvcpu 经 `.rparam/a` 拿到绝对路径 `/vthread/run/[0,0]/_`，直接 Get 该键，零中间拷贝。同理 f3 写 `s` 时经 `.wparam/s` 直接 Set 到 `/vthread/run/r`。

**数组流经整条调用链，落点在对齐的写参槽**。f2 创建 `[30, 40]`，写参 `a` 经 f1 的 `.wparam/a` 路由到 `/vthread/run/[0,0]/_`（f1 丢弃槽）。此时 `kvspace get /vthread/run/[0,0]/_` 返回 `int64[2]:30`（tree 显示首元素 30，实际含两个元素）。f3 的 `.rparam/a` 指向完全相同路径——数组在 kvspace 中只存一份，所有帧通过路径别名共享。

**`.rootfunc` 在 TCO 语义中保持根函数名**。每个帧独立记录 `.rootfunc`（此处 f1/f2/f3 各记自己的函数名），即使 TCO 复用帧也不覆盖——`resolveLabel` 靠它解析裸标签。

### 11.4 观察源码：`kvspace tree /lib`

暂停后 `/lib/` 已包含 chain_array.kv 的全部编译产物：

```
/lib
├── init   string:def init() -> ()
│   ├── [0]  rwir:f1  rwir:r
│   ├── [1]  rwir:r   rwir:print
│   └── [2]  rwir:return
├── f1     string:def f1() -> (s:int64)
│   ├── [0]  rwir:f2  rwir:_  int64:0
│   └── [1]  int64:0  rwir:return
├── f2     string:def f2() -> (a:int64, s:int64)
│   ├── [0]  int:30  int:40  rwir:array  int64:0
│   ├── [1]  rwir:f3  int64:0  int64:0
│   └── [2]  int64:0  int64:0  rwir:return
├── f3     string:def f3() -> (a:int64, s:int64)
│   ├── [0]  rwir:debugger
│   ├── [1]  int64:0  int:0  rwir:at  rwir:v0
│   ├── [2]  int64:0  int:1  rwir:at  rwir:v1
│   ├── [3]  rwir:v0  rwir:v1  rwir:+  int64:0
│   └── [4]  int64:0  int64:0  rwir:return
├── .init.src  string:def init() -> () { … }   ← 源码副本
├── .f1.src    string:def f1() -> (s:int64) { … }
├── .f2.src    string:def f2() -> (a:int64, s:int64) { … }
├── .f3.src    string:def f3() -> (a:int64, s:int64) { … }
└── .srcmap    bytes:{"1":"tutorial/03-debugger/chain_array.kv", …}
```

**KVC 指令的 `[s0,s1]` 二维布局清晰可见**。以 f3 为例，三个读参指令（`at` x2 + `+`）和一个 `debugger` 零参指令：

```
       s1=-2   s1=-1   s1=0          s1=1
s0=0 │                debugger
s0=1 │                at           v0
s0=2 │                at           v1
s0=3 │ v0      v1      +            (int64:0)  ← 临时写槽，类型为 int64
s0=4 │                return
```

读参不在当前帧创建——指令槽中 `v0`/`v1` 是 rwir 文本引用，执行时经 `frameSlotKey` 解析为帧内路径。写参（`v0`/`v1`、匿名临时槽）在执行到该指令时才创建。

**`/lib/.src` 源码副本**。每个函数的完整 lower 后源码以 string 值存入 `/lib/.*.src`——这是 `kvspace tree` 能展示源码的原因。`.srcmap` 记录行号→文件路径映射，供错误定位。

### 11.5 调试器协议要点

| 键 | 方向 | 机制 | 值 |
|----|------|------|-----|
| `.debugger` | agent→CPU（控制） | String | `""` 正常 / `"step"` 单步 / `"break"` 函数入口暂停 |
| `.debugger.pause` | CPU→agent（事件） | Notify | JSON `{"pc","func","frame","op"}` |
| `.debugger.resume` | agent→CPU（命令） | Notify | `"step"` / `"continue"` / `"abort"` |

暂停后 agent 写入 `kvspace notify /vthread/<vtid>/.debugger.resume continue` 即可恢复执行。

### 11.6 常用观察命令

```bash
# 栈帧全貌
kvspace tree /vthread/run

# 当前 PC
kvspace get /vthread/run/.pc

# 当前帧（按 PC 截取 frameRoot，再 tree）
# PC=/vthread/run/[0,0]/[0,0]/[1,0]/[0,0] → frameRoot=/vthread/run/[0,0]/[0,0]/[1,0]
kvspace tree /vthread/run/[0,0]/[0,0]/[1,0]

# 查看某帧的读写参路由
kvspace tree /vthread/run/[0,0]/[0,0]/[1,0]/.rparam
kvspace tree /vthread/run/[0,0]/[0,0]/[1,0]/.wparam

# 查看全部已加载函数
kvspace tree /lib

# 查看某函数的指令树
kvspace tree /lib/f3

# 查看源码副本
kvspace get /lib/.f3.src
```
