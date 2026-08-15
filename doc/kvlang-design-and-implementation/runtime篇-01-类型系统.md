# Type System（类型系统）


## 类型系统

kvlang 是**严格类型语言**。所有变量、参数、返回值在编译期必须有确定的类型——不允许无类型变量，不允许运行时类型隐式改变。

### 数字类型：无 float/int，只有具体位宽类型

**kvlang 没有 `float`、没有 `int`。** 不存在名为 `float` 或 `int` 的类型，只存在携带具体位数的数字类型：

| 类别 | 类型 |
|------|------|
| 有符号整数 | `int8` `int16` `int32` `int64` |
| 无符号整数 | `uint8` `uint16` `uint32` `uint64` |
| IEEE 754 浮点 | `float32` `float64` |

**设计理由**：位数是类型契约的核心部分。`int64` 跨 kvspace-go（Go）→ kvspace-cpp（C++）→ kvregion shm（C struct）→ op-gpu tensor dtype 传播时，每一层都依赖精确的位数信息。`float` 和 `int` 这类模糊名称在不同语言/平台上映射到不同位数（C `int` 在 ILP32 为 32 位、LP64 为 32 位；Python `int` 是任意精度），破坏跨语言类型契约。

**实现约束**：XValue kind 字符串只接受上表所列的 10 个精确名称。`"int"`、`"float"` 等短名在任何代码路径中均为非法 kind，code review 必须拒绝。

### 类型归属规则

类型的归属分为两级：

| 归属 | 存放位置 | 举例 |
|------|---------|------|
| **函数签名** | `/lib/<pkg>.<name>` — `string:rwfunc func(args) -> (rets)` | `rwfunc add(A:int, B:int) -> (C:int)` |
| **指令槽引用** | `/lib/<pkg>.<name>/[s0,s1]` — 目前为 `rwir:` 文本引用 | `[0,-1]="A"` `[0,1]="C"` |
| **运行时值** | `/vthread/<vtid>/<frame>/<var>` — 携带 kind 的 XValue | `A → int64:10`, `s → float64:3.14` |

**铁律**：
- `def` 签名中每个参数和返回值**必须声明类型**（`name:type`）
- 签名缺类型的 rwfunc **拒绝装载**（parser error）
- 指令槽 `[s0,s1]` 是槽位描述符，类型信息在签名中，不在槽中
- 运行时通过 `kind` 标签自描述，无需查签名
- 与五语言对齐：C必须声明、Rust必须声明、Go必须声明、TS可选但推荐、Python无声明——kvlang 选 C/Rust/Go 阵营

### XValue 结构与 kind 家族

**XValue 结构**（`github.com/array2d/kvspace-go`）：

```go
type XValue struct {
    kind        string // vtype name，如 "int64" "float64" "char/utf32" "bool" "array" "dict" "rwir"
    isptr       bool //是否是指针
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
| `"char/utf32"` | `Char32(v)` | 码点，4B×N，**默认字符串**，定宽（可索引）|
| `"char/utf8"` | `CharByte(v)` | UTF-8 字节串，1B×N，变宽（存储/交换，禁索引）|
| `"char/ascii"` | `CharAscii(v)` | ASCII 字节串，1B×N，定宽（可索引）|
| `"dict"` | `DictIndex(children)` | dict 成员目录（尾 `.` 的 key），children 为字段 key；旧零负载 `Dict{}` 兼容（raw 为空） |
| `"index"` | `Index(children)` | 通用目录索引（尾 `/` 的 key），children 为子节点 key |
| `"extindex"` | `ExtIndex(children, extpath)` | 扩展索引，写留在上层，读回落 extpath |
| `"rwir"` | `Rwir(v)` | 指令槽文本引用（kvlang 内部） |
| `"rwfunc"` | `Rwfunc(v, al)` | 函数定义（复合 rwir） |
| `"None"` | `None()` | None 值；`IsNone()` 返回 true |
| `"char/utf32"` (ptr) | `Ptr(kind="char/utf32", target)` | 软链接：Set 写入 `*kind:target`，读/写/List 透明穿透；Del 末段作用于链接本体 |

**字符家族（`char/<编码>`）**：`char` 前缀是「字符」行为类别，`/` 后是具体编码。定宽编码（`utf32` 4B、`ascii` 1B、未来 `utf16` 2B）支持 O(1) 码点索引（`string.char`/`slice`/`s[i]`/`len`）；变宽编码 `utf8`（1–4B/码点）**拒绝索引操作**，仅用于存储/交换。判定用前缀 `IsCharKind(kind) = strings.HasPrefix(kind, "char/")`。字符串字面量默认落 `char/utf32`，写槽类型标注 `:char/utf8`/`:char/ascii` 会反推字面量编码。

**kind 铁律——禁止别名**。kvlang **不支持** kind 别名。`"int"`、`"float"` 等短名在任何代码路径中均非法——必须使用全称 `"int64"`、`"float64"` 等上表所列的精确字符串。kind 字符串是跨语言类型契约的一部分（kvspace-go → kvspace-cpp → kvregion shm → op-gpu 张量 dtype），别名会破坏所有 kind-aware 中间件的匹配逻辑。违反此规则的代码（如 `kvspace.Raw("int", ...)`）必须在 code review 中拒绝。

> 历史：`deepx-design/internal/kvspace/DESIGN.md` 中 kind 写作 `"int"` `"float"` 等短名——那是设计草案，**已作废**。`slotValue` 中的 `kind="int"` 也已修正为 `"int64"`（fix-0721）。

**TLV 编码**：

```
[1B kind_len][N B kind_name][1B isptr][4B arraylength LE][4B raw_len LE][M B raw_value]
```

| 字段 | 大小 | 说明 |
|------|------|------|
| `kind_len` | 1B | kind_name 字节数（0 表示 None） |
| `kind_name` | N B | vtype name，`[a-zA-Z0-9_/]` 字符集（`/` 仅用于 `family/format` 如 `char/utf32`）|
| `isptr` | 1B | 0=普通值，1=软链接（raw 为目标 key 路径） |
| `arraylength` | 4B | 数组元素数，uint32 LE，默认=1（单值） |
| `raw_len` | 4B | raw_value 字节数，uint32 LE |
| `raw_value` | M B | 类型化原始数据；isptr=1 时为目标 key 路径字符串 |

`IsNone()` 编码为 nil（零字节）。`DecodeXValueHead` 内部 copy raw bytes（owned 语义，防止与 Redis 读缓冲区共享）。

### 变量名即地址，有地址即有 XValue——未赋值即 None

kvlang 代码中的每一个变量名，就是该中间变量在 kvspace 中的**相对地址**。声明/首次出现一个变量名，即在当前帧下分配一个 KV slot（如 `/vthread/<vtid>/<frame>/x`）。因此：

- **有变量名 ⇒ 有地址**（相对地址也是地址）
- **有地址 ⇒ 有 XValue**（每个 slot 对应一个 XValue）
- **未赋任何值 ⇒ 该 XValue 为 None**（kind=`""`，`IsNone()` 返回 true）

这意味着 kvlang 中不存在"未定义变量"——变量一经 parser 识别，其 slot 就已存在。区别仅在于该 slot 的 XValue 是 None 还是持有类型化数据。None 参与算术/比较/类型转换直接 TypeError，迫使代码显式初始化。

**访问器分级**：
- **宽容读取器**：`Int64()` 按 kind 实际宽度解码 + 符号扩展（对标 Go `reflect.Value.Int`），`Uint64()` 同理。算术/比较走宽容读取器。
- **精确访问器**：`Int8()` `Float32()` 等严格校验 kind 字符串，不匹配返回零值。

### 定义变量时指定基础类型（fix-021）

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
| None 输入直接 TypeError | strict None — 拒绝 None 参与数值运算 |

**声明精度是存储/传输类型**：`int16(-2) -> n; n -> /x` 后 `kvspace get /x` 显示 `int16:-2`——
精度进入 TLV kind 落盘，kvspace-cpp / kvregion shm / 张量 dtype 的跨语言类型契约由此成立。

### 字符编码转换：kind(x) 同创建函数

字符家族的三个 kind **既是构造器也是转换器**，与数字类型算子同构——`kind(x)` 即转换函数，必须有写参（读参只读）：

```kv
s:char/utf8 = "hello"     # 变宽，禁索引
t <- char/utf32(s)        # 转 utf32 定宽（可索引）
u <- char/ascii(t)        # 转 ascii（拒非 ASCII 码点 U+00E9）
```

- 转换语义 = `NewChar(kind, ValueString())`：任一编码的源值取 UTF-8 表示，按目标 kind 落盘。
- `char/ascii` 严格 7-bit：非 ASCII 码点 → `TypeError`（不做静默替换）。
- 函数名即 kind 名（含 `/`），与数字 `int8(x)` 同一调用形态；`/` 是 family/format 分隔（MIME 式），非路径。

### 数值运算域

窄类型进入算术后**提升至统一运算域**（C 整型提升风格），三条定理：

1. **int ∧ int → 原生 int64 运算与比较**，绝不经 float64 中转（fix-020：float64 尾数仅 53 位，
   `maxint64 - 1`、`2⁵³+1` 曾经必然丢值/误判相等）；溢出 = 补码回绕（同 C/Go）
2. **任一侧 float → float64 提升**；混合比较为 C 式 double 提升（`3 == 3.0` 为 true）
3. **None 参与算术直接 TypeError**（strict None：拒绝 None 隐式转换为 0）

读取器契约（kvspace-go）：`Int64()`/`Uint64()` 是**宽容读取器**（对标 Go `reflect.Value.Int/Uint`：
按 kind 实际宽度解码 + 符号扩展）；`Int8()`/`Float32()` 等是严格 kind 精确访问器。
消费方（算术/display/String）一律走宽容读取器。

语义回归锚点：`tutorial/01-basics/precision.kv`（提升与精度真相，17 断言）、`numtypes.kv`（十算子，12 断言）。

### Bool 铁律

**bool 只能是 `true`/`false`，禁止一切隐式 coerce。** 这比主流语言更严格，是刻意设计。

| 输入 | kvlang | Python | JS | C | Go |
|------|--------|--------|-----|---|-----|
| `true`/`false` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `0`/`1` 整数 | **panic** — 必须写 `!= 0` | ✅ if/while 接受 | ✅ | ✅ | ❌ |
| 非空字符串 | **panic** — 必须写 `!= ""` | ✅ | ✅ | N/A | N/A |
| `"false"` 字符串 | **panic** | ✅ 真 | ✅ 真 | N/A | N/A |
| None | **panic** | ✅ falsy | ✅ falsy | N/A | ❌ |

**为什么比五语言更严格？**

kvlang 的设计目标是"运行时代码即数据"——所有状态落在 KV 空间，外部 agent 和插件可读写。隐式 coerce 意味着 `if (x)` 的行为取决于 x 的运行时 kind，agent 在不知道 x 类型的情况下无法判定控制流走向。强制显式比较（`x != 0`、`x != ""`）使控制流条件在 KV 层面自描述：br 指令的 cond 槽永远是 bool，读即知真假。

**实现**：`AsBool` (coerce.go) 仅接受 `kind=="bool"`，其他 kind 直接 panic。`bool()` 构造器将任意值转为 bool（`bool(0)`=false、`bool(1)`=true、`bool("")`=false），但这是**显式转换**，不用在条件位置。
