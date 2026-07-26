# Chapter 10: System Variables（系统变量）

import kvlang-design-and-implementation

## 12. 系统变量——`X/.var` 影子键

> **⚠️ 本节是全部 `/.` 系统键的唯一事实源。**
> 任何代码变更（新增、修改、删除 `/.` 键）**必须先更新本节**，再改代码。
> `grep -rn '"\.' internal/ cmd/ --include="*.go"` 可审计当前全部 `/.` 键。

VM 运行时会为它管理的对象生成**内置变量（系统变量）**，以 `{对象key}/.var` 形式存放：`/` 下探一层、键名以 `.` 开头。kvlang 标识符不能以 `.` 开头，因此所有 `.` 前缀键均为引擎保留，用户代码无法直接读写——类比 Unix 隐藏文件：默认视图不显示，引擎可见。

**与用户成员键的区分**：`X.attr`（`.` 平坦键，如 `obj.prop`、`lib.func`）是用户 kv 代码可访问的正常 struct 成员；`X/.attr`（`/` + `.` 前缀）是引擎专属。`keytree.Member(base, name)` 产 `base + "." + name`（用户侧），`FuncLib`/`VThreadPC` 等产 `path + "/.name"`（系统侧），零交集。

### 12.1 全量清单（按宿主对象分类）

**vthread 对象**（宿主 = `/vthread/<vtid>`，生命周期与调度；代码 `keytree/vthread.go`）：

| 键 | 机制 | 代码位置 | 语义 |
|----|------|---------|------|
| `.pc` | String | `VThreadPC` / `vthread.Set` | 当前执行 PC（绝对路径） |
| `.status` | String → Notify | `VThreadStatus` / `vthread.SetDone/SetError` | 运行中 `init`/`running`/`wait`；终态 Del + Notify(retVal) |
| `.<status>/msg` | String | `VThreadStatusMsg` | 终态附加信息（`.error/msg`、`.timeout/msg`，路径动态生成） |
| `.debugger` | String | `VThreadDebugger` | 调试控制：`""` 正常，`"step"` 单步；`debugger()` builtin（fix-031）读此键 |
| `.debugger.pause` | Notify 队列 | `VThreadDebuggerPause` / `debugNotifyPause` | CPU → agent 暂停事件（JSON `{"pc","func","frame","op"}`） |
| `.debugger.resume` | Notify 队列 | `VThreadDebuggerResume` / `debugWaitResume` | agent → CPU 恢复命令：`step`/`continue`/`abort` |

**帧对象**（宿主 = frameRoot，调用协议；vthread 根即顶层帧；代码 `keytree/frame.go` + `layoutrwir.go`）：

| 键 | 机制 | 代码位置 | 语义 |
|----|------|---------|------|
| *(帧根 extindex)* | extindex | `Stack` / `HandleCall:ExtIndex` / `HandleReturn:UnLink` | 帧根本身是 extindex → `/lib/<pkg>.<name>` 只读指令区；局部变量存帧根下 |
| `.rootfunc` | String | `HandleCall:Set` / `Bootstrap:Set` / `resolveLabel:Get` / `debugFuncName:Get` | 帧对应函数名；TCO 复用帧时**不更新**（保持根函数名，供 `resolveLabel` 裸标签解析） |
| `.ro` | String | `FrameRO` / `HandleCall:Set` / `Bootstrap:Set` | 只读参数名单（逗号分隔），kvcpu 写槽检查用（fix-027；无参函数不写） |
| `.rparam/<name>` | String | `RParam` / `HandleCall:Set` / `Bootstrap:Set` | 读参重定向：存调用方值的绝对路径；CPU 读参时从此路径直读，零拷贝 |
| `.wparam/<name>` | String | `WParam` / `HandleCall:Set` | 写参重定向：存调用方写目标的绝对路径；CPU 写参时直写此路径，HandleReturn 不再搬运 |

**lib 对象**（宿主 = `/lib/`，编译产物元数据）：

| 键 | 机制 | 代码位置 | 语义 |
|----|------|---------|------|
| `.srcmap` | JSON Bytes | `cmd/kvlang/layoutrwir.go:cmdLoad` | 源码行号→文件路径映射，多文件拼接后供错误定位 |

**数据对象**（宿主 = 变量键，规划中）：

| 键 | 机制 | 语义 |
|----|------|------|
| `.shape` | — | kvspace 数组的形状（todo-009 键族数组落地后） |
| `.gc` | — | 垃圾回收引用计数（未来） |

**语法层保留名（不落盘，`frameSlotKey` 拦截）**：

| 键 | 语义 |
|----|------|
| `._` | 丢弃槽——`frameSlotKey` 遇到 `.xxx` 返回空路径，不写入 kvspace |

**内联暂停指令**：`debugger()`（fix-031，对齐 V8/TypeScript `debugger;` 语句）——源码内联暂停点；非调试模式下 no-op，调试模式下暂停 vthread 等待 agent 的 step/continue/abort。暂停/恢复协议走 `.debugger`、`.debugger.pause`、`.debugger.resume` 三个键。

注意区分：`/sys/`（vm 心跳、op 注册）是独立的系统**域**（顶层树），与对象随身的 `/.var` 系统**变量**是两种机制。`/vthread/<vtid>/term`（终端绑定名）是普通结构键（非 `/.` 前缀），用户代码可读写。

### 12.2 三种键形态：一眼判型

成员键 `.` 分隔（todo-009）落地后，任意 key 的形态唯一确定其性质：

| 形态 | 例 | 性质 | 所有权 |
|------|----|------|--------|
| `X/名`（`/` + 普通名） | `/vthread/7/[3,0]`、`…/then/[0,0]` | 结构：帧、指令槽、label 块 | VM |
| `X.名`（`.` 平坦键） | `/c0.next`、`frame/obj.prop` | 用户数据成员（键族） | 用户 |
| `X/.名`（`/` + dot 名） | `/vthread/7/.pc`、`arr/.shape` | 系统变量（影子元数据） | VM |

### 12.3 设计结论：系统变量维持 `/` 分隔（`X/.var`）

系统变量**应该用 `/` 分隔**、与用户成员的 `.` 分隔形成正交，理由有三：

1. **零冲突的专属命名空间**。用户成员语法只产生 `.` 平坦键（`a.b` → 键 `a.b`），标识符禁止 `.` 开头；动态键 `h.*k` 即使 k 的值以 `.` 开头，`.` 拼接产出的是 `h..xxx`——用户侧永远造不出 `/.` 序列。反之若系统变量也用 `.` 拼接（`arr..shape`），与动态键注入撞车，还需维护保留字表。
   ⚠️ 现状（`/` 拼接成员）做不到这一点：`at(h, ".pc")` 即可命中 `h/.pc` 系统键——这是 todo-009 的又一论据：`.` 拼接天然堵住系统键注入。
2. **生命周期绑定**。`X/.var` 在 X 的 `/` 子树内，`DelTree(X)` 连带清除全部系统变量（帧销毁已依赖此语义）；用户键族 `X.*` 由前缀删除管理。两个删除平面各归其主：结构树归 VM，数据平面归用户。
3. **与帧系统键统一为一条公理**。帧本就是对象，`.pc`、`.rootfunc`、`.ro` 已是 `{对象}/.var` 形态。公理：**任何 kvspace 对象 X——VM 元数据在 `X/.名`（引擎保留），帧根 extindex 指向 `/lib/` 代码区，用户数据在 `X.名`（成员），子级在 `X/名`（结构）**。数组 `.shape`、未来 GC 计数器直接套用，无需新机制。
