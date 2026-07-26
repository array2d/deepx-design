# Chapter 1: Design Constitution（设计宪法）

import kvlang-design-and-implementation

## 0. 设计宪法

核心哲学与设计目标详见 [总篇-01](总篇-01-存算控制流严格分离的kv树计算架构.md)。

### 0.1 地址空间

kvspace 树形路径分四个系统域，借用 Unix 文件系统思想；**其余 `/` 路径全部自由，由用户定义**：

```
/lib/{pkg}.{name}         编译后函数（签名 + 指令树）+ .src 源码副本
/vthread/{vid}/           虚线程栈帧（运行时）
/sys/                     系统基础设施（VM/op-plat）
/dev/                     外部 I/O 设备（/dev/tty 终端、/dev/screen 屏幕）
```

- `/lib/` 借鉴 Unix `/lib/`——共享库的标准路径，是函数（编译产物）的单一事实源。`lib name { }` 命名空间块声明包。多文件通过 `kvlang layoutrwir <files...>` 拼接为单一源→parse→lower→写入 `/lib/`，无 `import` 关键字——lib 树即全局命名空间，跨 lib 调用走全路径 `/lib/{lib}.{func}()`。`.src` 源码副本与指令树同目录。已加载文件自动去重（Python `sys.modules` 式），循环导入无错误跳过
- `/vthread/` 是运行时栈帧，借用 Unix `/proc/<pid>/` 思想——每 vthread 一棵子树，`.pc`/`.status` 等系统键暴露执行状态；帧根本身是 extindex 指向 `/lib/` 指令树
- `/sys/` 是基础设施注册表（VM 心跳、op 算子列表），借用 Unix `/sys/` 伪文件系统思想
- `/dev/` 借鉴 Unix `/dev/`——I/O 边界。`/dev/tty/`（终端输入输出）、`/dev/screen`（屏幕渲染）。外部设备挂载为 kvspace 子树，读写设备 = 读写 kvspace 键
- 四域之外的 `/` 路径（如 `/counter`、`/n0.val`、`/tmp/seen`）完全由用户代码定义——kvspace 不预设 schema，只提供 Write/Read/Watch 原语

**kvspace 存储铁律**：Key 必须是字符串路径（`/` 分隔的树形层级）；Value 必须是 XValue 序列化后的字节数组。**严禁**直接写入基础类型的裸值、裸字符串、JSON——所有值必须经 XValue 编解码。违反此铁律的写入在 reader 侧读到非法字节时 behavior undefined。

kvspace 存储两类数据：**基础数据类型**（int、float、bool、string）和 **tensor 元数据**（shape、dtype、指向扩展存储的句柄）。tensor 完整数据在扩展存储中：

| 扩展位置 | 典型数据 |
|---------|---------|
| 集群节点共享内存 | 大张量、激活值（heap-plat 管理生命周期） |
| GPU 显存 | 计算张量（op-plat 在设备侧持有句柄） |
| 文件系统/对象存储 | 模型权重、检查点、数据集 |

指令分类详见 [parser篇-01 — 指令架构](parser篇-01-指令架构.md)。

### 0.2 模块职责

| 模块 | 路径 | 职责 |
|------|------|------|
| **ast** | `internal/ast/` | 单层 IR 类型体系：Operand/FuncSig/Stmt/Instruction/File，Walk/Visitor |
| **parser** | `internal/parser/` | Scan→Token→递归下降→`*ast.File`，含 Diagnostic 错误收集 |
| **lower** | `internal/lower/` | 同类型变换 pass：IfStmt/WhileStmt → BlockStmt+br |
| **keytree** | `internal/keytree/` | 路径系统：将运行时概念映射到 kvspace 键路径 |
| **layoutrwir** | `internal/layoutrwir/` | Linker：WriteFunc(编译期写入) + HandleCall/Return(运行时帧管理) |
| **kvcpu** | `internal/kvcpu/` | 执行引擎：Fetch-Decode-Execute+调度器+控制流 |
| **kvspace** | `github.com/array2d/kvspace-go`（外部模块） | KV 存储接口 14 方法：Get/Set/Del/GetMany/MSet/List/DelTree/Notify/Watch/Link/Unlink/ClearAll/DisConn |
| **vthread** | `internal/vthread/` | vthread 状态管理：Get/Set/SetDone/SetError/Create/WaitDone |
| **vtype** | `internal/vtype/` | 可扩展算子类型注册：str/tensor 命名空间 |
| **builtin** | `internal/op/builtin/` | 标量内建算子：算术/比较/逻辑/cast/IO |

模块依赖图：

```
cmd/kvlang
  ├── parser ──► ast
  ├── lower ──► ast
  ├── layoutrwir ──► keytree + kvspace + ast
  ├── kvcpu ──► layoutrwir + keytree + vthread + vtype + builtin + op
  ├── vthread ──► keytree + kvspace
  └── kvspace (接口)
```

### 0.3 禁止项

| 编号 | 禁止 | 理由 |
|------|------|------|
| R1 | 任何包依赖高于自身层级的设计包 | 依赖单向：cmd→kvcpu→layoutrwir→keytree/ast |
| R2 | 运行时包 import parser/lower/ast | 编译与执行分离 |
| R3 | 硬编码 kvspace 路径字符串在 keytree 之外 | 所有路径经由 keytree 函数生成 |
| R4 | kvspace 直接写入裸值（int/float/string/JSON） | 所有 Value 必须经 XValue 序列化为字节数组；Key 必须是字符串路径 |
| R5 | 破坏单层 IR：新增 HIR/LIR 分层 | kvlang 只一层 IR |
| R6 | 帧销毁用 List+Del 代替 DelTree | DelTree 是原子操作 |
| R7 | 模块间循环依赖 | 编译期杜绝 |
| R8 | `fmt.Fprint*` 直接写 stderr 做诊断 | 所有诊断必须经 `internal/logx`（详见 parser篇-05）；usage/help/格式化除外 |

CLI 装载与执行模型详见 [runtime篇-04 — 执行模型](runtime篇-04-执行模型.md)。诊断输出规范详见 [parser篇-05 — 诊断输出](parser篇-05-诊断输出.md)。
