# Appendix: Design Decisions Summary（设计决策总结）

import kvlang-design-and-implementation

## 7. 设计决策总结

| 决策 | 理由 |
|------|------|
| PC = KV path string | KV 树寻址天然支持层级，无需整数映射 |
| `[s0,0]` = opcode，`s1<0` = 读参，`s1>0` = 写参 | 符号编码数据流方向；每个槽独立可寻址、可观察 |
| 参数数量隐式（扫描到空 key 停止） | 无需在 opcode 中存 arity，指令布局自描述 |
| label block = 无参 call | 消除 jmp/br/goto 原语，控制流统一 |
| WriteFunc 先 DelTree 再写 | KV 不是内存，覆写不清零旧槽；必须显式删除旧函数树 |
| WriteBody 写结构化 KV | 避免文本往返，直接映射 AST→KV |
| lower 在 write 前执行 | 结构化 → 基本块的转换在 AST 层完成 |
| kvspace 抽象存储 | 存储后端可替换，接口 Get/Set/Del/GetMany/MSet/List/DelTree/Notify/Watch/Link/Unlink/ClearAll/DisConn |
| 函数无返回值，只有写参 | `f() -> s` 是写参跨帧映射到位置 s，不是"返回值赋给 s" |
| `->` 右侧必须是位置 | 裸名=帧内、`/abs`=全局、`base.名`=成员；字面量写槽在 Parser 层报警 |
| `./` 前缀全面废除 | 裸名即位置；消除 `.`、`/` 在 parser/VM 中的多次拼接变换 |
| 拷贝指令显式操作码 `=` | `a -> b` 编码为 `[s0,0]="="`、`[s0,-1]="a"`；opcode 位永不放变量引用 |
| int∧int 原生 int64 运算域 | float64 中转必丢 >2⁵³ 精度（fix-020，对齐 C/Go/Rust）；混合数值 C 式 double 提升 |
| 多文件拼接加载 | 参考 Python 去重 + Go 包名规则（fix-033） |
| 数字类型算子 = 构造器 + 转换器 | `int8()/float32()` 等十算子；声明精度即 TLV kind，跨语言类型契约（fix-021） |
