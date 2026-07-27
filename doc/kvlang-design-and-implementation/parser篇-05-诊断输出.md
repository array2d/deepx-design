# Diagnostic Output（诊断输出规范）


## 诊断输出规范（logx）

**所有 stderr 诊断输出必须通过 `internal/logx` 包，禁止直接调用 `fmt.Fprint*`。** 输出格式对齐五大语言编译器（GCC/Go/V8）：`{level}: {context}: {msg}`，无时间戳、无 key=value。

**logx API 范式：**

| 函数 | 前缀 | 用途 |
|------|------|------|
| `Debug/Info` | 无 | 操作消息、调试追踪（仅 `LOG_LEVEL=debug`/`info` 可见） |
| `Warn` | `warn: ` 自动 | 可恢复警告 |
| `Error` | `error: ` 自动 | 错误信息 |
| `Fatal` | 同 Error | 错误 + `os.Exit(1)` |
| `Diag(d)` | Diagnostic 自带 | parser 诊断单行输出 |
| `DiagWithSource(d)` | Diagnostic 自带 | parser 诊断 + 源码行 + `^` caret |

**明确豁免 `fmt.Fprint*` 的情形：**
- `flag.FlagSet.Usage` 内的 usage 文本（无前缀的说明文字）
- help 命令的输出（完整帮助文档）
- `fmt.Printf` 到 stdout 的成功状态（如 `%s: OK`）
- `ast.Dump` / `ast.Format` 的格式化输出（到 `io.Writer`，非诊断）

**原则：如果你在写诊断，走 logx；如果你在写内容（help / usage / 格式化 / stdout 结果），走 fmt。**
