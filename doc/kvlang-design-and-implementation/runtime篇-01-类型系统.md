# Chapter 8: Type System（类型系统）

import kvlang-design-and-implementation

## 9. 类型系统

kvlang 是**严格类型语言**。所有变量、参数、返回值在编译期必须有确定的类型——不允许无类型变量，不允许运行时类型隐式改变。

### 9.0 类型归属规则

类型的归属分为两级：

| 归属 | 存放位置 | 举例 |
|------|---------|------|
| **函数签名** | `/lib/<pkg>.<name>` — `string:def func(args) -> (rets)` | `def add(A:int, B:int) -> (C:int)` |
| **指令槽引用** | `/lib/<pkg>.<name>/[s0,s1]` — 目前为 `rwir:` 文本引用 | `[0,-1]="A"` `[0,1]="C"` |
| **运行时值** | `/vthread/<vtid>/<frame>/<var>` — 携带 kind 的 XValue | `A → int64:10`, `s → float64:3.14` |

**铁律**：
- `def` 签名中每个参数和返回值**必须声明类型**（`name:type`）
- 签名缺类型的 def **拒绝装载**（parser error）
- 指令槽 `[s0,s1]` 是槽位描述符，类型信息在签名中，不在槽中
- 运行时通过 `kind` 标签自描述，无需查签名
- 与五语言对齐：C必须声明、Rust必须声明、Go必须声明、TS可选但推荐、Python无声明——kvlang 选 C/Rust/Go 阵营

### 9.1 XValue 结构与 kind 家族

**XValue 结构**（`github.com/array2d/kvspace-go`）：

```go
type XValue struct {
    kind        string // vtype name，如 "int64" "float64" "string" "bool" "bytes" "array" "dict" "rwir"
    arraylength int32  // 数组元素数，单值=1，>1 表示数组
    raw         []byte // 类型化原始字节，XValue owned（构造时 copy）
}
```

**kind 完整清单**（以 kvspace-go 实际落盘的 kind 字符串为准）：

| kind 字符串 | 构造函数 | 说明 |
|------------|---------|------|
| `"int8"` `"int16"` `"int32"` `"int64"` | `Int8(v)` `Int16(v)` `Int32(v)` `Int64(v)` | 有符号整数，小端编码 |
| `"uint8"` `"uint16"` `"uint32"` `"uint64"` | `Uint8(v)` `Uint16(v)` `Uint32(v)` `Uint64(v)` | 无符号整数 |
| `"float32"` `"float64"` | `Float32(v)` `Float64(v)` | IEEE 754 浮点 |
| `"bool"` | `Bool(v)` | 1 字节：0=false, 1=true |
| `"string"` | `Str(v)` | UTF-8 原始字节 |
| `"bytes"` | `Bytes(v)` | 二进制原始字节（构造时 copy） |
| `"dict"` | `Dict()` | 零负载类型标记——成员存为平坦键族 `base.名` |
| `"array"` | `Array(elems)` | 定长同类型数组，raw 连续存储 |
| `"rwir"` | `Rwir(v)` | 指令槽文本引用（kvlang 内部） |
| `"null"` | `Null()` | 显式 null 值；`IsNil()` 对 `""` 和 `"null"` 均返回 true |

**kind 铁律——禁止别名**。kvlang **不支持** kind 别名。`"int"`、`"float"` 等短名在任何代码路径中均非法——必须使用全称 `"int64"`、`"float64"` 等上表所列的精确字符串。kind 字符串是跨语言类型契约的一部分（kvspace-go → kvspace-cpp → kvregion shm → op-gpu 张量 dtype），别名会破坏所有 kind-aware 中间件的匹配逻辑。违反此规则的代码（如 `kvspace.Raw("int", ...)`）必须在 code review 中拒绝。

> 历史：`deepx-design/internal/kvspace/DESIGN.md` 中 kind 写作 `"int"` `"float"` 等短名——那是设计草案，**已作废**。`slotValue` 中的 `kind="int"` 也已修正为 `"int64"`（fix-0721）。

**TLV 编码**：

```
[1B kind_len][N B kind_name][4B arraylength LE][4B raw_len LE][M B raw_value]
```

| 字段 | 大小 | 说明 |
|------|------|------|
| `kind_len` | 1B | kind_name 字节数（1~127，0 表示 null） |
| `kind_name` | N B | vtype name，`[a-zA-Z0-9_]` 字符集 |
| `arraylength` | 4B | 数组元素数，uint32 LE，默认=1（单值） |
| `raw_len` | 4B | raw_value 字节数，uint32 LE |
| `raw_value` | M B | 类型化原始数据 |

`IsNil()` 编码为 nil（零字节）。`DecodeXValue` 内部 copy raw bytes（owned 语义，防止与 Redis 读缓冲区共享）。

**访问器分级**：
- **宽容读取器**：`Int64()` 按 kind 实际宽度解码 + 符号扩展（对标 Go `reflect.Value.Int`），`Uint64()` 同理。算术/比较走宽容读取器。
- **精确访问器**：`Int8()` `Float32()` 等严格校验 kind 字符串，不匹配返回零值。

### 9.2 定义变量时指定基础类型（fix-021）

十个数字类型算子，**既是构造器也是转换器**，普通调用形态（parser 零改动）：

```kv
f = float32(3)        # kind=float32 落盘        # = 等价于 <-
i <- int8(0.1)        # 0（float→int 截断向零）
int8(300) -> w        # 44（窄化 = 补码回绕）
uint64(18446744073709551615)   # uint64 上界完整往返
```

`int8/16/32/64 · uint8/16/32/64 · float32/float64`。

| 语义 | 对齐阵营 |
|------|---------|
| float→int 截断向零 | 五语言一致 |
| 窄化 = 补码回绕（`uint8(-1)`=255、`int32(2³¹)`=-2³¹） | Go 转换 / Rust `as` / C |
| (2⁶³, 2⁶⁴-1] 无小数正整数字面量 → uint64 | — |
| nil 输入按 int 0 | fix-017 |

**声明精度是存储/传输类型**：`int16(-2) -> n; n -> /x` 后 `kvspace get /x` 显示 `int16:-2`——
精度进入 TLV kind 落盘，kvspace-cpp / kvregion shm / 张量 dtype 的跨语言类型契约由此成立。

### 9.3 数值运算域

窄类型进入算术后**提升至统一运算域**（C 整型提升风格），三条定理：

1. **int ∧ int → 原生 int64 运算与比较**，绝不经 float64 中转（fix-020：float64 尾数仅 53 位，
   `maxint64 - 1`、`2⁵³+1` 曾经必然丢值/误判相等）；溢出 = 补码回绕（同 C/Go）
2. **任一侧 float → float64 提升**；混合比较为 C 式 double 提升（`3 == 3.0` 为 true）
3. **nil 数值语境 = int 0**（fix-017，与 `nil==0` 比较、`AsBool(nil)=false` 同族）

读取器契约（kvspace-go）：`Int64()`/`Uint64()` 是**宽容读取器**（对标 Go `reflect.Value.Int/Uint`：
按 kind 实际宽度解码 + 符号扩展）；`Int8()`/`Float32()` 等是严格 kind 精确访问器。
消费方（算术/display/String）一律走宽容读取器。

语义回归锚点：`tutorial/01-basics/precision.kv`（提升与精度真相，17 断言）、`numtypes.kv`（十算子，12 断言）。
