# 地址空间

> 本章讲述 kvlang **使用** kvspace 的过程设计与约束，而非 kvspace 自身的设计实现。
> kvspace 的接口定义、XValue 类型系统、存储模型等见 [kvspace 设计与实现](../kvspace-design-and-implementation/)。

## 系统域

kvspace 树形路径只保留两个系统域，借用 Unix 文件系统思想；**其余 `/` 路径全部自由，由用户定义**：

```
/lib/{pkg}.{name}         编译优化后函数（签名 + 指令树）+ .src 源码副本；扩展 rwir 也注册于此
/vthread/{vid}/           虚线程栈帧（运行时）
```

> 代码里只有 `LIB_ROOT="/lib"` 与 `VTHREAD_ROOT="/vthread"` 两个前缀（`runtime/src/runtime_internal.h`）。曾设想的 `/sys/`（基础设施注册表）与 `/dev/`（I/O 设备域）**已废弃**：基础设施与扩展 rwir 统一落在 `/lib/`，I/O 不再经设备域（见下"没有 /dev 设备域"）。

### `/lib/`

借鉴 Unix `/lib/`——共享库的标准路径，是函数（编译产物）的单一事实源。`lib name { }` 命名空间块声明包。多文件通过 `kvlang layoutrwir <files...>` 拼接为单一源→parse→lower→写入 `/lib/`，无 `import` 关键字——lib 树即全局命名空间，跨 lib 调用走全路径 `/lib/{lib}.{func}()`。`.src` 源码副本与指令树同目录。已加载文件自动去重。

/lib/的命名，之前曾经考虑过使用/func/，毕竟主流语言定义函数都用func这个关键字，但考虑到/func（/lib）是存放的layourwrir后的产物，更像c++/go的.so,.a等产物，虽然kvlang可以热更新，所以借鉴并确定使用unix的/lib目录习惯。

### `/vthread/`

运行时栈帧，借用 Unix `/proc/<pid>/` 思想——每 vthread 一棵子树，`.pc`/`.status` 等系统键暴露执行状态；帧根本身是 extindex 指向 `/lib/` 指令树。

### 没有 /dev 设备域

KV 世界里没有终端、没有设备文件，只有 key 和 value。`print`/`println`/`cerr`/`input` 这类 I/O **不是**地址空间的一个域，也不是 C runtime 的 builtin rwir——它们是**扩展 rwir**：`term` 扩展运行时把签名注册到 `/lib/<opcode>`（kind=`rwir`），执行时直接写宿主进程的 `stdout`/`stderr`（Rust `print!`/`eprintln!`），不经任何 `/dev/*` 键。与 `json.to`/`json.from`、tensor 算子同一套 rwir 扩展机制（见 [runtime篇-05](runtime篇-05-rwirext扩展运行时.md)）。

### 两域之外

`/` 路径（如 `/counter`、`/n0.val`、`/tmp/seen`）完全由用户代码定义——kvspace 不预设 schema，只提供 Write/Read/Watch 原语。

## 存储铁律与扩展存储

**kvspace 存储铁律**：Key 必须是字符串路径（`/` 分隔的树形层级）；Value 必须是 XValue 序列化后的字节数组。**严禁**直接写入基础类型的裸值、裸字符串、JSON——所有值必须经 XValue 编解码。违反此铁律的写入在 reader 侧读到非法字节时 behavior undefined。

kvspace 存储两类数据：**基础数据类型**（int、float、bool、string）和 **tensor 元数据**（shape、dtype、指向扩展存储的句柄）。tensor 完整数据在扩展存储中：

| 扩展位置 | 典型数据 |
|---------|---------|
| 集群节点共享内存 | 大张量、激活值（heap-plat 管理生命周期） |
| GPU 显存 | 计算张量（op-plat 在设备侧持有句柄） |
| 文件系统/对象存储 | 模型权重、检查点、数据集 |

指令分类见 [parser篇-01](parser篇-01-指令架构.md)。
