# runtime篇-07: 签名类型表达式（多态 + 高维数组）

> 状态：设计定稿。代码：签名字符串存 `/lib/<pkg>.<name>`（kind=rwfunc/rwir），参数类型即本文件的「类型表达式」。

## 一、动机

函数签名里参数/返回值的类型，之前是手工字符串、无形式文法，且与 kind 体系脱节（`charbyte` 是旧名、`path` 不是 kind）。要让一个 rwir/rwfunc 支持**多态**（一个参数声明时接受多种类型，含高维数组），需要一个：

- 人和 AI 都容易读、容易生成的类型定义表达式；
- 不比正则长；
- 不撞 kvlang 的 rwir/rwfunc 语法分隔符（`(` `)` `,` `:` `->` 空格）。

**kind 铁律**：`charbyte` 是旧名，一律作废，字符类型用 `char/utf32` / `char/utf8` / `char/ascii`。

## 二、文法

```
type   := union ( "..." )?                 # 末参可带 "..." 变参：0..N 个同型实参
union  := atom ( "|" atom )*               # 并集：A|B 表 A 或 B
atom   := dims ( any | kind )
dims   := ε                                # 无 [] = 标量（0 维）
        | "[]"                             # 空 [] = 1 维、长度任意（等价 [?]）
        | "[" dim ("," dim)* "]"           # 逗号分割的维
dim    := int | "?"                        # 精确大小 或 动态维（? = 任意）
any    := "any"                            # 通配，匹配任意 kind
kind   := 精确 kind 串（bool、int8..uint64、float32/64、char·、dict、index、extindex、rwir、rwfunc、scope、time、duration）
```

**变参 `...`**：仅**末**读参可带尾缀 `...`（如 `A:any...`、`A:int64|float64...`），表「0..N 个同型实参」——匹配器把该参数之后的全部实参逐个按去掉 `...` 的 union 判定。用于 `print`/`println`/`cerr`/`min`/`max` 等 arity 开放的 rwir。非末参带 `...` 是装载期错误。

**铁律：无家族简写**。`int`/`uint`/`float`/`num` 这类「按位宽聚族」的简写**不提供**——位宽是开放集合（未来有 int4、fp8、fp16…），封闭枚举会漏、开放前缀又会收进 runtime 尚不支持的 kind。`char` 编码简写同样不提供——字符类型须写明确的编码格式（`char/utf8`、`char/utf32`、`char/ascii`）。多态靠显式 `|` 枚举：`int8|int16|int32|int64`。仅保留一个与「位宽/编码枚举」无关的简写：

| 简写 | 展开 | 说明 |
|------|------|------|
| `any` | 全部 kind | 通配，不是位宽/编码聚族 |

## 三、实例

```
A:int64                      # 标量 int64
A:int64|float64              # 标量并集（多态核心）
A:int8|int16|int32|int64     # 任意整数（显式枚举，无数值家族）
A:char/utf8                  # 单个 utf8 字符（标量）；字符串用 []char/utf8

A:[]float32                  # 1-D float32，长度任意（= [?]float32）
A:[2]float32                 # 1-D，恰好 2 元素
A:[2,3]float32               # 2-D，恰好 2×3 矩阵
A:[2,3,4]float64             # 3-D tensor
A:[?,768]float32             # 2-D，第一维动态、第二维 768（weight 场景）
A:[]float32|[]float64        # 多精度 1-D

A:[2,3]float32|float32       # shape 并标量：2×3 矩阵 或 标量（丢给函数自行判断）
A:[?,?]float64               # 2-D 浮点、两维都动态
A:bool|char/utf8             # bool 或 单个 utf8 字符
A:index|dict                 # 目录
```

维数（rank）= 逗号分隔的项数；`?` 表示该维动态；`[]`（空）是 `[?]` 的短写。**shape 与标量的并集合法**（如 `[2,3]float32|float32`），匹配器按原子分别判，语义由函数自己决定。

## 四、匹配语义

一个值（kind=`k`，ndim=`n`，dims=`d`）匹配类型表达式 `E` 的判定：

```
match(E, k, n, d):
  对 E 按 "|" 拆成若干 atom，任一 atom 命中即 true
  matchAtom(atom, k, n, d):
    atom == "any" → true（顶类型，任意 kind + 任意 shape）
    有 "[" 前缀 → 解析出 shape 与剩余 type：
        matchShape(shape, n, d) 且 base(type) 匹配 k
    无 "["      → 单值（shape=[1]）：n == 0 且 kind 匹配 k
  matchShape(shape, n, d):
    shape == ""        → n == 1     # "[]" 等价 "[?]"：恰一维、任意长
    len(拆逗号) != n    → false
    逐维：p=="?" 跳过；否则 d[i] == int(p)
```

## 五、语法冲突规避（`[` `]` 感知切分）

签名用 `,` 分隔参数，shape 里也用 `,` 分隔维：

```
matmul(A:[2,3]float32, B:[3,4]float32) -> (C:[2,4]float32)
```

解法：签名解析器**跟踪方括号深度**——见 `[` 进 shape 态、见 `]` 退出；shape 态内的 `,` 归维、shape 态外的 `,` 归参数。这是标准括号感知切分，非 regex、非状态机大工程。

类型表达式可出现的字符（字母数字、`/`、`|`、`[`、`]`、`,`、`?`、数字）里，`(` `)` `:` `->` 空格**都不出现**，故与签名外层结构无歧义。

## 六、匹配器（无正则、无外部进程）

```go
func match(expr, kind string, ndim int, dims []int32) bool {
    for _, atom := range strings.Split(expr, "|") {
        if matchAtom(atom, kind, ndim, dims) { return true }
    }
    return false
}
func matchAtom(atom, kind string, ndim int, dims []int32) bool {
    if strings.HasPrefix(atom, "[") {
        end := strings.Index(atom, "]")
        if !matchShape(atom[1:end], ndim, dims) { return false }
        return matchAtom(atom[end+1:], kind, -1, nil)
    }
    if ndim >= 0 && ndim != 0 { return false }   // 标量
    switch atom {
    case "any":   return true
    default:      return atom == kind
    }
}
func matchShape(shape string, ndim int, dims []int32) bool {
    if shape == "" { return ndim == 1 }   // "[]" 等价 "[?]"：恰一维、任意长
    parts := strings.Split(shape, ",")
    if len(parts) != ndim { return false }
    for i, p := range parts {
        if p == "?" { continue }
        if int32(atoi(p)) != dims[i] { return false }
    }
    return true
}
```

`any` 是唯一简写，`kind` 走精确字符串相等。C 侧在 runtime 建等价的 `type_expr.c`（供装载期校验 + rwext 实参类型判定），Rust 侧在 `layout/src/type_expr.rs`。

## 七、多态派发

联合/shape 声明的是「签名接受的类型集合」；运行时按**实际实参 kind + ndim + dims** 派发：

- native 算子（`+`）内建多态，签名只做类型检查。
- rwext 扩展（numpy.add）经 `rwext_resolve_read` 拿实参实际 kind，按精确 kind 分派；高维 shape 由扩展自行处理。
- rwfunc 用户函数：并集声明接受集，函数体 `kind(x)` 分支。

shape 与标量的并集（`[2,3]float32|float32`）**丢给函数自己判断**——签名只保证「实参属于声明集合」，具体语义（是矩阵还是标量）在实现侧。

运行时值仍自描述（XValue 带 kind + ndim + dims），签名只是编译/装载期的类型契约，不参与运行时装箱。

## 八、参数 kindexp 的落盘与运行时内联匹配

**铁律**：rwir/rwfunc 每个参数落盘的 **kindexp 与源码里的类型定义表达式逐字节相同**（同一文法，含 `...`）。不再有「显示串」与「机器串」两种编码。

**统一落盘于 `/lib/<opcode>`**。rwir 定义（扩展算子，无指令体）与 rwfunc（用户函数）**同一存储位置、同一定义体格式**——不再有独立的 `/rwir/{runtime}/<opcode>/` 槽位树。`<opcode>` 可带包前缀（如 `numpy.add`、`math.sqrt`）。定义体（`char/utf8` 值）布局：

```
[nr : u16 LE][nw : u16 LE][读参 kindexp × nr, 再 写参 kindexp × nw，全部以 "\n" 连接]
```

即读参在前、写参在后，各 kindexp 逐字节等于源文法（如 `int64|float64`、`[2,3]float32`、`any...`）。命名参数→帧槽指针仍在 `/lib/<opcode>/<param>`。

**native op 不落盘**：其 kindexp 内联在 runtime 的 C 注册表中，是唯一权威来源，**运行时匹配直接读 C 表，不回查 kvspace、也不镜像写入**。数值多类型算子在 C 表中**融合为单条**（如 `add(a:int64|int32|…, b:…)`），派发时由实参 kind 决定输出 kind，不再按 `<numkind>.<op>` 铺开条目。**扩展 op** 经 `rwext_register(opcode, nr, nw, sig)` 注册，runtime 把 `sig` 按方括号感知切分为各参 kindexp、做 `type_expr_valid` 校验（拒绝 `path` 等非 kind），并按上述定义体格式写入 `/lib/<opcode>`。

**运行时内联匹配**：execute 大循环解码指令、走到 `[n,0]` 派发前，按 opcode 从 `/lib/<opcode>` 取定义体、解出读参 kindexp，对每个已解析实参（kind/ndim/dims）跑 `type_expr_match(kindexp, …)`；变参 `...` 把尾随实参全部对末 kindexp 判定。任一失配 → `TypeError` 写 vthread 错误并停机。
