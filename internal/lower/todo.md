# lower todo

> 对齐目标：`internal/lower/最高标准设计.md`

---

## ✅ P10：确保 if → br 全覆盖，使 kvcpu 可删除 OpIf 兼容 case

**现状**：`lowerIf` 已将 `*ast.IfStmt` 展开为 `BlockStmt + br`，但
`kvcpu/controlflow.go` 中仍保留 `case op.OpIf` 兼容分支（转发给 `brToCall`）。

**标准**：lower 保证写入 `/func/` 前不存在 `if` opcode；
`kvcpu/controlflow.go` 的 `OpIf` case 即可安全删除。

**验收**：增加 lower 输出断言（或测试），确认无 `Instruction.Opcode == "if"`；
同步删除 `kvcpu/controlflow.go` 中的 `case op.OpIf`。

---

## ✅ P11：for / break / continue lowering

**完成**：`lowerForWithCont` 实现 for→四块结构（init/cond/body/exit），
新增 `kv.has`/`kv.at` builtin 提供 kvspace 路径遍历原语。

**实现**：
- `for (v in ./path) { body }` → `_for_init` + `_for_cond` + `_for_body` + `_for_exit`
- 遍历语义：kvspace 编号子项（`./path/0`, `./path/1`, ...）
- `break` → `call _for_exit_N`（复用 loopCtx，与 while 一致）
- `continue` → `call _for_cond_N`（同上）
- 前置 `kv.has`/`kv.at` builtin 已就绪（`internal/op/builtin/kvop.go`）
