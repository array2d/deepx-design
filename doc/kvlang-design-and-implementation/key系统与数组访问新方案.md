# key 系统与数组访问新方案

## 背景：分隔符语义化

kvspace 的树形 key 与 XValue 的 kindexp 共用一套分隔符语义，每个分隔符承载固定含义：

| 分隔符 | 语义 | key 层 | kindexp 层 |
|--------|------|--------|-----------|
| `/` | 层级 | `/lib/f/`、`/vthread/1/` | — |
| `.` | 成员 | `obj.x`、`lib.func` | — |
| `[]` | 数组访问（compact 与散 key 统一） | `arr[3]` 数组下标 | `[]int32`、`[10]int32`、`[256,256]uint8` |
| `@` | 扩展存储句柄 | — | `@[256,256]uint8` |

## rwir 指令序列的 key：`[s0,s1]`

函数 layout 后的 rwir 指令序列，每个指令槽用 `[s0,s1]` 坐标 key（与当前实现一致）。

```
/lib/main.add/[0,0]   → Rwfunc(nr=2, nw=1)   ← 函数签名
/lib/main.add/[1,0]   → "+"                   ← 指令 0 opcode
/lib/main.add/[1,-1]  → Rwir("a")             ← 指令 0 读槽 1
/lib/main.add/[1,-2]  → Rwir("b")             ← 指令 0 读槽 2
/lib/main.add/[1,1]   → Rwir("c")             ← 指令 0 写槽 1
/lib/main.add/[2,0]   → "return"              ← 指令 1
```

坐标约定：`[s0,0]` = opcode，`[s0,-j]` = 第 j 个读槽，`[s0,+j]` = 第 j 个写槽。

## kvspace-go 的索引目录符号：`<` 与 `,`

kvspace-go 路径系统现有 `/`（层级目录）与 `.`（成员目录）两个索引目录符号。散 key 数组的元素 key 用 `<i>` 后缀（`arr<0>`、`arr<1>`）作为**内部命名**——这是后端存储约定，不是用户语法；用户访问统一写 `arr[i]`（parser desugar 为 `at`）。`[]` 不是索引符号——compact 数组打包在一个 body，无 key 族。

| 符号 | 位置 | 语义 | 后端处理 |
|------|------|------|---------|
| `/` | 后缀 | 层级目录 | `Index`(children) 路由 |
| `.` | 后缀 | 成员目录 | `DictIndex`(children) 路由 |
| `<` `>` | 内部后缀 | 散 key 数组元素 key 命名 | `arr<i>` 定位元素 key |

- `arr<3>` = 散 key 数组的内部元素 key（存储层），用户写 `arr[3]`。
- 后端 `SplitArrayParent` 一步拆出 `(base, index)` 并定位 key，语言层 `at` 不再做路径拼接/解析。

**后端改动**：
- `SplitArrayParent`（对标 `SplitDictParent`）：检测 `arr<i>`，拆成 (arrayBase, index)。
- `validateIndexChild` 已允许 `<` `>`（字面量）；`isDir`/`parentName` 无需改动。
- 散 key 数组 `List` 返回元素 key 列表。

## kind 与 kindexp 二分

| 概念 | 内容 | 例子 |
|------|------|------|
| **kind** | 基础类型字符串，标量/元素类型 | `uint8` `int16` `int32` `float64` `bool` `char/utf32` `dict` `index` `rwir` `rwfunc` … |
| **kindexp** | 完整类型表达式，kind + 修饰符 | `int8` `*int8` `*[]int32` `*[16]uint8` `[256,256]uint8` `[10]int32` `@[256,256]uint8` |

**kind 是 kindexp 的叶子**：剥掉所有 `*`/`@`/`[]` 修饰符即得 kind。kind 仍用于 dispatch / `ElemSize` / 宽容读取器等快路径匹配；kindexp 是完整自描述。

kindexp 字符串是**显示/讨论形式**，落盘为下方「XValueHead 与 body」的 3 区二进制 head。二者一一对应。

## kindexp 文法

**全前缀**：从左到右读，每个前缀修饰其右侧的类型；最左符号即最外层构造器，无优先级歧义（对标 Go/Rust 的 `*[]int`、`*[10]int`）。

```bnf
kindexp ::= kind                   # 标量
         |  '*' kindexp            # 指针/软链接（body = 目标 key 路径）
         |  '@' kindexp            # 扩展存储句柄（body = 位置信息，data 在扩展存储）
         |  '[' ']' kindexp        # 变长数组（字面量初始化→compact；裸声明→散 key）
         |  '[' dims ']' kindexp   # 定长/多维数组（一个 XValue，连续 body）
dims    ::= INT (',' INT)*         # 维度列表，如 16 或 256,256
kind    ::= uint8 | int8 | int16 | uint16 | int32 | uint32 | int64 | uint64
         |  float32 | float64 | bool | char/utf32 | char/utf8 | char/ascii | time | duration
         |  dict | index | extindex | rwir | rwfunc | scope
```

示例：`*int8`、`*[]int32`、`*[16]uint8`、`[256,256]uint8`、`[10]int32`、`[4,4]float64`（连续 2D）、`@[256,256]uint8`（扩展存储句柄）。

## `[]`：一种访问语法，两种存储形态

同一个"数组"概念，两种存储形态，**访问语法统一为 `arr[i]`**（不再有 `<i>`）：

| 写法 | 存储形态 | 元素位置 | XValue 数 | 判定 |
|------|---------|---------|----------|------|
| `int32` | 标量 | — | 1 | — |
| `[10]int32` | **compact**（定长） | 连续打包在一个 body | **1** | 类型标注定长 |
| `[]int32 = [1,2,3]` | **compact**（字面量定长） | 连续打包在一个 body | **1** | 字面量初始化 |
| `[]int32`（裸声明） | **散 key**（变长） | `arr<0>`… 各自 key | N+1 | 无字面量 / append 增长 |
| `[256,256]uint8` | compact 多维数组 | 连续打包在一个 body | **1** | 类型标注定长 |

- **compact**：`[N]T` 或字面量初始化 `[]T = [...]`——元素连续打包在一个 XValue，`raw_len = ∏dims × ElemSize`。对齐 C `int[10]`、Go `[10]int`、Rust `[i32;10]`。
- **散 key**：裸声明 `[]T`（无字面量）或 `array.append`/`array.slice` 触发——元素分散在 `arr<0>..arr<N-1>` 各 key。对齐"泛型容器/动态"语义（`Vec`/`List`/`Array`）。

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
- **约束**：`@` 只组合 `kind` 与 `[dims]kind`（compact 形态）；外部分散数组无意义，拒绝。

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
| `arr_flag` | 1B | 0=标量 1=compact`[]` 2=散 key |
| `ndim` | 1B | 0=变长（无固定维），N=定长 N 维 |
| `dims` | ndim×4B | 各维长度 LE（取代单一 arraylength） |
| `raw_len` | 4B | body 字节数 LE |

`HeadLen() = 8 + kind_len + ndim*4`；body 在 `value[HeadLen : HeadLen+raw_len]`。

**body 是数据本体，不是 head 的成员**，由 head 的 `ref`/`arr_flag`/`kind` 决定语义：

- 内联标量/连续数组（ref=0）：body = 类型化数据（`[10]int32` 的 body = 40B 连续元素）。
- 软链接（ref=`*`）：body = 目标 key 路径字符串。
- 扩展句柄（ref=`@`）：body = 位置描述符（device + address）。
- 散 key 数组（arr_flag=2）：body = arraykey 描述符（可空），元素在 `arr<0..>` 各 key。

派生字段：`isptr=(ref==1)`、`isext=(ref==2)`、`arraylength = ∏dims`（定长）/ 0（变长）/ 1（标量）。

### kindexp 字符串 ↔ 二进制 head 的映射

| kindexp 字符串 | ref | arr_flag | ndim | dims | kind |
|---------------|-----|----------|------|------|------|
| `*[]int32` | 1 | 1 | 0 | — | `int32` |
| `[10]int32` | 0 | 1 | 1 | [10] | `int32` |
| `@[256,256]uint8` | 2 | 1 | 2 | [256,256] | `uint8` |
| `int32` | 0 | 0 | 0 | — | `int32` |

`kvspace get` 由 head 重建 kindexp 字符串显示。

解码时 `DecodeXValueHead` 解析 head → 缓存 `kind`/`isptr`/`isext`/`arraylength`/`body 偏移` 于内存结构，热路径不再重复解析。

## 与"整存整取"的关系

kindexp 把形态显式化后，XValue 的存取约束随之收紧为**整存整取**（见 total篇-01）——废除 `arridx` 半读半写：

| 形态 | kindexp | 元素/数据访问 | 是否整存整取 |
|------|---------|--------------|-------------|
| compact 数组 | `[10]int32` | `arr[3]` 整读 body 后内存内切片 | 是（一次整读） |
| 散 key 数组 | `[]int32` | 读 `arr[i]` 这个 key 的标量 XValue | 是（每个 key 一个整 XValue） |
| 扩展存储句柄 | `@[256,256]uint8` | 整读写句柄 XValue；data 访问走 op-plat，不进 kvspace | 是（句柄是整 XValue） |

## 转换与变长：`array.scatter` / `array.compact` / `array.append` / `array.slice`

数组形态转换与变长操作统一收敛到 `array` 命名空间下（dotted rwir，同 `string.slice`）：

| rwir | 方向 | 语义 |
|------|------|------|
| `array.scatter(arr) -> dst` | compact → 散 key | 连续数组 `arr`（一个 packed body）拆成 `dst<0>..dst<N-1>` 标量 key，不动 `arr` |
| `array.compact(arr) -> dst` | 散 key → compact | 读 `arr<0>..arr<N-1>`（顺读到缺席）打包成连续数组 `dst`，不动 `arr` |
| `array.append(arr, elem) -> arr` | 变长 +1 | 追加 `elem`；若 `arr` 为 compact 先自动 scatter |
| `array.slice(arr, lo, hi) -> arr` | 变长截取 | 切片 `arr[lo:hi]`；若 `arr` 为 compact 先自动 scatter |

```
scatter:  arr = [10,20,30]         →  dst<0>=10  dst<1>=20  dst<2>=30  （arr 不动）
compact:  dst<0>=10 … dst<2>=30   →  dst = [10,20,30]                   （item key 不动）
append:   arr = [10,20] (compact)  →  arr<0>=10  arr<1>=20  arr<2>=30  （自动 scatter）
```

- item key 命名 `base + "<" + i + ">"`（散 key 内部实现，一等索引目录符号落地前是普通 key）。
- 长度：`scatter` 由 `arr.ArrayLen()` 定；`compact`/`append`/`slice` 由顺读 item key 到缺席定（dense 散 key）。
- 同构铁律：`compact` 以首个元素 kind 打包，其余元素须同 kind（`packTypedArray` 拒绝混合）。
- 自动 scatter：`array.append`/`array.slice` 这类改长度的方法，遇 compact 数组先就地 scatter（写 `arr<0>..arr<N-1>`、删 `arr`）再操作。

## 待定项

- 散 key 数组 arraykey 描述符的 body schema（shape/元素类型/稀疏索引）细化。
- 连续多维 `[256,256]uint8` 的 row-major 排布约定（与 op-gpu tensor 连续布局对齐）。
- `*[16]uint8`（指向连续数组的指针）的语义与跳数约定。
- 散 key 多维数组是否支持（散 key 的 shape 语义）。
- 扩展存储句柄 body 的位置 schema（device 枚举、shm_name/offset、fd、GPU ptr 的统一编码）。
