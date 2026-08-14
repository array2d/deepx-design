# key 系统与数组访问新方案

## 背景：分隔符语义化

kvspace 的树形 key 与 XValue 的 kindexp 共用一套分隔符语义，每个分隔符承载固定含义：

| 分隔符 | 语义 | key 层 | kindexp 层 |
|--------|------|--------|-----------|
| `/` | 层级 | `/lib/f/`、`/vthread/1/` | — |
| `.` | 成员 | `obj.x`、`lib.func` | — |
| `[]` | 连续/同构（可 compact，定长） | — | `[10]int32`、`[256,256]uint8` |
| `<>` | 分离/变长（key 寻址） | `arr<3>` 分离元素访问、`<s0,s1>` rwir 槽 | `<10>int32`、`<>int32` |
| `@` | 扩展存储句柄 | — | `@[256,256]uint8` |

## rwir 指令序列的 key：`<s0,s1>`

函数 layout 后的 rwir 指令序列，每个指令槽用 `<s0,s1>` 坐标 key，**不再用 `[s0,s1]`**。

```
/lib/main.add/<0,0>   → Rwfunc(nr=2, nw=1)   ← 函数签名
/lib/main.add/<1,0>   → "+"                   ← 指令 0 opcode
/lib/main.add/<1,-1>  → Rwir("a")             ← 指令 0 读槽 1
/lib/main.add/<1,-2>  → Rwir("b")             ← 指令 0 读槽 2
/lib/main.add/<1,1>   → Rwir("c")             ← 指令 0 写槽 1
/lib/main.add/<2,0>   → "return"              ← 指令 1
```

坐标约定：`<s0,0>` = opcode，`<s0,-j>` = 第 j 个读槽，`<s0,+j>` = 第 j 个写槽。

**为什么是 `<>` 不是 `[]`**：`[]` 表示同构、定长、可 compact（连续打包在一个 body）；而 rwir 指令序列是**分离的二维槽阵列**——每个 `<s0,s1>` 是独立 key，行长度可变（各指令读/写槽数不同），元素（opcode / Rwir 引用）非同构定长。故用 `<s0,s1>`（分离），与 kindexp 的 `<>`=分离一致。

（迁移：现有 `layout.go` / `keytree/frame.go` / `rwir.go` / `vthread.go` 的 `[%d,%d]` 格式串，及各篇文档的 `[s0,s1]`，需统一改为 `<s0,s1>`。）

## kvspace-go 的索引目录符号：`<` 与 `,`

kvspace-go 路径系统现有 `/`（层级目录）与 `.`（成员目录）两个索引目录符号。新增 `<`（分离数组索引）与 `,`（多维下标分隔）作为**数组索引符号**，与 `/` `.` 同级，由后端原生解析。`[]` 不是索引符号——连续数组打包在一个 body，无 key 族。

| 符号 | 位置 | 语义 | 后端处理 |
|------|------|------|---------|
| `/` | 后缀 | 层级目录 | `Index`(children) 路由 |
| `.` | 后缀 | 成员目录 | `DictIndex`(children) 路由 |
| `<` `>` | 后缀访问 | 分离数组索引 | 路由到元素 key（每个元素一个整 XValue） |
| `,` | 中缀 | 多维下标分隔 | `<i,j>` 内分隔各维下标 |

- `arr<3>` = 分离数组 `<N>T` 第 3 元素：后端定位真实 key `arr<3>`。
- 多维：`arr<2,3>` 路由到 `arr<2,3>` key（`,` 分隔各维下标）。

**加速来源**：分离数组元素访问 `arr<i>` 由后端原生解析——`SplitArrayParent` 一步拆出 `(base, index)` 并定位 key，语言层不再做路径拼接/解析。

**后端改动**：
- 新增 `SplitArrayParent`（对标 `SplitDictParent`）：检测 `arr<i>` / `arr<i,j>`，拆成 (arrayBase, index)。
- `validateIndexChild` 已允许 `<` `>` `,`（字面量）；`isDir`/`parentName` 无需改动。
- 分离数组 `List` 返回元素 key 列表。

## kind 与 kindexp 二分

| 概念 | 内容 | 例子 |
|------|------|------|
| **kind** | 基础类型字符串，标量/元素类型 | `uint8` `int16` `int32` `float64` `bool` `charbyte` `dict` `index` `rwir` `rwfunc` … |
| **kindexp** | 完整类型表达式，kind + 修饰符 | `int8` `*int8` `*[]int32` `*[16]uint8` `[256,256]uint8` `[10]int32` `<10>int32` `@[256,256]uint8` |

**kind 是 kindexp 的叶子**：剥掉所有 `*`/`@`/`[]`/`<>` 修饰符即得 kind。kind 仍用于 dispatch / `ElemSize` / 宽容读取器等快路径匹配；kindexp 是完整自描述。

kindexp 字符串是**显示/讨论形式**，落盘为下方「XValueHead 与 body」的 3 区二进制 head。二者一一对应。

## kindexp 文法

**全前缀**：从左到右读，每个前缀修饰其右侧的类型；最左符号即最外层构造器，无优先级歧义（对标 Go/Rust 的 `*[]int`、`*[10]int`）。

```bnf
kindexp ::= kind                   # 标量
         |  '*' kindexp            # 指针/软链接（body = 目标 key 路径）
         |  '@' kindexp            # 扩展存储句柄（body = 位置信息，data 在扩展存储）
         |  '[' ']' kindexp        # 变长连续数组（slice，一个 XValue）
         |  '[' dims ']' kindexp   # 定长/多维连续数组（一个 XValue，连续 body）
         |  '<' '>' kindexp        # 变长分离数组（元素分散 key）
         |  '<' dims '>' kindexp   # 定长分离数组（元素分散 N 个 key）
dims    ::= INT (',' INT)*         # 维度列表，如 16 或 256,256
kind    ::= uint8 | int8 | int16 | uint16 | int32 | uint32 | int64 | uint64
         |  float32 | float64 | bool | charbyte | time | duration
         |  dict | index | extindex | rwir | rwfunc | scope
```

示例：`*int8`、`*[]int32`、`*[16]uint8`、`[256,256]uint8`、`[10]int32`、`<10>int32`、`[4,4]float64`（连续 2D）、`@[256,256]uint8`（扩展存储句柄）。

## `[]` 与 `<>`：两种数组形态

这是 kindexp 的核心区分——同一个"数组"概念，两种存储形态：

| kindexp | 存储形态 | 元素位置 | XValue 数 | body 内容 |
|---------|---------|---------|----------|---------|
| `int32` | 标量 | — | 1 | 4B 标量 |
| `[10]int32` | **连续数组**（定长） | 连续打包在一个 body | **1** | 40B 连续元素 |
| `[]int32` | 连续数组（变长 slice） | 连续打包在一个 body | **1** | N×4B 连续元素 |
| `<10>int32` | **分离数组**（定长） | `arr<0>`…`arr<9>` 各自 key | **11**（1 描述符 + 10 元素） | 描述符 body 空 |
| `<>int32` | 分离数组（变长） | `arr<0>`… 各自 key | N+1 | 描述符 body 空 |
| `[256,256]uint8` | 连续多维数组 | 连续打包在一个 body | **1** | 65536B 连续元素 |

- **`[]` = 连续**：`[10]int32` 存在**一个** XValue，元素连续，`raw_len = ∏dims × ElemSize`。对齐 C `int[10]`、Go `[10]int`、Rust `[i32;10]`。
- **`<>` = 分离**：`<10>int32` 存在 **10 个 key** 中，每个 key 一个标量 XValue；`arr` 本身是 arraykey 描述符。对齐"泛型容器/动态"语义——运行期长度 + 间接存储（`Vec`/`List`/`Array`）。

## `@`：扩展存储句柄

`@` 标记"这是一个元 key 的 XValue，真实数据在扩展存储内，body 只记录位置"。

| 符号 | 语义 | body 存什么 |
|------|------|-----------|
| （无） | 内联值 | 数据本体 |
| `*` | kvspace 内软链接 | kvspace key 路径 |
| `@` | 扩展存储句柄 | 扩展存储位置（device + address） |

- `@[256,256]uint8` = 256×256 uint8 tensor 的句柄，data 在 SHM/GPU，body 存 `{device, shm_name/offset}`。
- dtype 与 shape 已在 kindexp 中，body 只补物理位置。
- `*` 与 `@` 的差别是**目标命名空间**：`*` 指向元存内另一个 key，`@` 指向元存外（SHM/GPU/文件）的一块连续数据。
- **约束**：`@` 只组合 `kind` 与 `[dims]kind`（连续形态）；`@<>`（外部分离数组）无意义，拒绝。

## XValueHead 与 body

XValue 存储分两层：**XValueHead（头，元数据）** 与 **body（体，数据本体）**。key 指向完整 XValue 字节，body 位于 head 之后，由 `raw_len` 定长。

```
XValue      =  XValueHead + body
XValueHead  =  [1B kind_len][N B kind][1B ref][1B arr_flag][1B ndim][ndim×4B dims][4B raw_len]
body        =  [M B raw]        M = raw_len，offset = HeadLen()
```

**XValueHead 字段**：

| 字段 | 大小 | 含义 |
|------|------|------|
| `kind_len` | 1B | kind 字符串字节数 |
| `kind` | N B | 基础类型叶子（明文字符串） |
| `ref` | 1B | 0=内联 1=`*`软链接 2=`@`扩展句柄（isptr 的三态扩展） |
| `arr_flag` | 1B | 0=标量 1=连续`[]` 2=分离`<>` |
| `ndim` | 1B | 0=变长（无固定维），N=定长 N 维 |
| `dims` | ndim×4B | 各维长度 LE（取代单一 arraylength） |
| `raw_len` | 4B | body 字节数 LE |

`HeadLen() = 8 + kind_len + ndim*4`；body 在 `value[HeadLen : HeadLen+raw_len]`。

**body 是数据本体，不是 head 的成员**，由 head 的 `ref`/`arr_flag`/`kind` 决定语义：

- 内联标量/连续数组（ref=0）：body = 类型化数据（`[10]int32` 的 body = 40B 连续元素）。
- 软链接（ref=`*`）：body = 目标 key 路径字符串。
- 扩展句柄（ref=`@`）：body = 位置描述符（device + address）。
- 分离数组（arr_flag=2）：body = arraykey 描述符（可空），元素在 `arr[0..]` 各 key。

派生字段：`isptr=(ref==1)`、`isext=(ref==2)`、`arraylength = ∏dims`（定长）/ 0（变长）/ 1（标量）。

### kindexp 字符串 ↔ 二进制 head 的映射

| kindexp 字符串 | ref | arr_flag | ndim | dims | kind |
|---------------|-----|----------|------|------|------|
| `*[]int32` | 1 | 1 | 0 | — | `int32` |
| `[10]int32` | 0 | 1 | 1 | [10] | `int32` |
| `<10>int32` | 0 | 2 | 1 | [10] | `int32` |
| `@[256,256]uint8` | 2 | 1 | 2 | [256,256] | `uint8` |
| `int32` | 0 | 0 | 0 | — | `int32` |

`kvspace get` 由 head 重建 kindexp 字符串显示。

解码时 `DecodeXValueHead` 解析 head → 缓存 `kind`/`isptr`/`isext`/`arraylength`/`body 偏移` 于内存结构，热路径不再重复解析。

## 与"整存整取"的关系

kindexp 把形态显式化后，XValue 的存取约束随之收紧为**整存整取**（见 total篇-01）——废除 `arridx` 半读半写：

| 形态 | kindexp | 元素/数据访问 | 是否整存整取 |
|------|---------|--------------|-------------|
| 连续数组 | `[10]int32` | `arr[3]` 整读 body 后内存内切片 | 是（一次整读） |
| 分离数组 | `<10>int32` | 读 `arr<i>` 这个 key 的标量 XValue | 是（每个 key 一个整 XValue） |
| 扩展存储句柄 | `@[256,256]uint8` | 整读写句柄 XValue；data 访问走 op-plat，不进 kvspace | 是（句柄是整 XValue） |

## 压缩与解压：`<>` ↔ `[]`

`scatter` / `compact` 两个 builtin 在连续与分离两种形态间转换（整存整取，无部分读写）：

| builtin | 方向 | 语义 |
|---------|------|------|
| `scatter(arr)` | `[]type -> <>type`（解压） | 连续数组 `arr`（一个 packed body）拆成 `arr<0>..arr<N-1>` 标量 key，删除 `arr` |
| `compact(arr)` | `<>type -> []type`（压缩） | 读 `arr<0>..arr<N-1>`（顺读到缺席）打包成连续数组 `arr`，删除 item key |

```
scatter:  arr = [10,20,30]        →  arr<0>=10  arr<1>=20  arr<2>=30  （arr 删除）
compact:  arr<0>=10 … arr<2>=30  →  arr = [10,20,30]              （item key 删除）
```

- item key 命名 `base + "<" + i + ">"`（`<>` 字面量；一等索引目录符号落地前是普通 key）。
- 长度：`scatter` 由 `arr.ArrayLen()` 定；`compact` 由顺读 item key 到缺席定（dense 分离数组）。
- 同构铁律：`compact` 以首个元素 kind 打包，其余元素须同 kind（`packTypedArray` 拒绝混合）。
- 返回值：两者均将连续数组值写回首个写槽（无写槽则仅前进 PC）。

## 待定项

- 分离数组 arraykey 描述符的 body schema（shape/元素类型/稀疏索引）细化。
- 连续多维 `[256,256]uint8` 的 row-major 排布约定（与 op-gpu tensor 连续布局对齐）。
- `*[16]uint8`（指向连续数组的指针）的语义与跳数约定。
- 分离多维 `<256,256>uint8` 是否支持（分离数组的 shape 语义）。
- 扩展存储句柄 body 的位置 schema（device 枚举、shm_name/offset、fd、GPU ptr 的统一编码）。
