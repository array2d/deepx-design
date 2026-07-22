# Chapter 6: Compiler Pipeline（编译器流水线）

import kvlang-design-and-implementation

## 5. 编译器/解释器架构对比

### Python

```
源代码 → tokenizer → parser → AST
  → symtable (符号表分析, 作用域)
  → compile (AST → 基本块 → 字节码)
  → marshal (字节码 → .pyc)
  → ceval (解释器主循环: 取字节码 → 分发 → 执行)
```

关键特征：
- 基本块由编译器构建（`flowgraph.c`），包含跳转偏移
- 字节码操作数携带 PC 偏移量（整数）
- 解释器在连续字节码数组上递增 PC

### Lua

```
源代码 → lexer → parser → AST
  → codegen (AST → 寄存器指令)
  → luaV_execute (寄存器 VM: 取指令 → 分发 → 执行)
```

关键特征：
- 寄存器式 VM（非栈式），指令携带寄存器索引
- 控制流通过 `JMP`/`TEST`/`FORLOOP` 等指令 + 偏移量
- 无独立的基本块构建阶段

### kvlang

```
源代码 → lexer → parser → AST (if/while/for → IfStmt/WhileStmt/ForStmt)
  → lower  (结构化控制流 → BlockStmt + br/goto)
         (br/goto 又简化 → call(block_label))
  → layoutrwir (AST → KV 结构化 key-value)
         (WriteBody: 递归写入 /lib/<pkg>.<name>/[i,j] KV 指令树)
  → kvcpu (执行循环: Decode → 分发 → 执行)
         (call = HandleCall: 软链接函数指令树到子帧 .funclib)
         (return = HandleReturn: 回传值, 清理子栈, 恢复父 PC)
```

关键特征：
- **PC 是 KV 路径字符串**，不是整数
- **指令在 KV 树中**，通过 `kv.Get` 获取，不是内存数组
- **调用 = 软链接**（HandleCall 通过 kv.Link 将子帧 .funclib 指向 /lib/<pkg>.<name> 只读指令树）
- **返回 = 子树删除**（HandleReturn 清理子栈, 回传值）
- **label block = 无参函数**，控制流统一为 call/return

### 5.1 编译器前端流水线

kvlang 编译器前端走标准流水线：**`Source → Scanner.Scan() → []Token → Parser → *ast.File`**。
核心设计决策：**块结构由消费 LBrace/RBrace Token 自然追踪，杜绝 `strings.Count/Index` 做语法判断**。
换行是语句分隔符（`Newline` token）而非块结构标记（`{ }` 负责）。`parser` 结构体以 `tokens[]+pos+peek/advance/expect` 递归下降驱动，
文件单向依赖链 `file.go → stmt.go → inst.go → scanner.go`。错误收集不首错即止——`parser.errors []Diagnostic` 累积全量诊断。


### 5.2 AST 类型标记——Quote 字段

`Expr.Quote` 区分字符串字面量和变量名，替代旧的 `"` 前缀 hack。parser 将 scanner 的 token Quote 信息保留到 AST，`Flat()` 在 KV 传输层加 `"` 前缀，`stringPrec` 用 `escapeString` 还原源码形式。数字字面量（如 `-5`）不再被误引号包裹。
