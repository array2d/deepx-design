# Compiler Pipeline

## Compiler / Interpreter Architecture Comparison

### Python

```
source → tokenizer → parser → AST
  → symtable (symbol-table analysis, scopes)
  → compile (AST → basic blocks → bytecode)
  → marshal (bytecode → .pyc)
  → ceval (interpreter main loop: fetch opcode → dispatch → execute)
```

Key features:
- Basic blocks are constructed by the compiler (`flowgraph.c`) and carry jump offsets
- Bytecode operands carry PC offsets (integers)
- The interpreter increments the PC across a contiguous bytecode array

### Lua

```
source → lexer → parser → AST
  → codegen (AST → register instructions)
  → luaV_execute (register VM: fetch instruction → dispatch → execute)
```

Key features:
- Register-based VM (not stack-based); instructions carry register indices
- Control flow via `JMP` / `TEST` / `FORLOOP` etc. instructions + offsets
- No separate basic-block construction stage

### kvlang

```
source → lexer → parser → AST (if/while/for → IfStmt/WhileStmt/ForStmt)
  → lower  (structured control flow → BlockStmt + br/goto)
         (br/goto further simplified → call(block_label))
  → layoutrwir (AST → KV structured key-value)
         (WriteBody: recursively writes the /lib/<pkg>.<name>/[i,j] KV instruction tree)
```

Once the compiled output has been written to `/lib/`, it is executed by kvcpu (see parser篇-06).

Key features:
- **The PC is a KV path string, not an integer**
- **Instructions live in the KV tree and are fetched via `kv.Get`, not from an in-memory array**
- **A label block is a parameterless function; control flow is uniformly call/return**

### Compiler Frontend Pipeline

The kvlang compiler frontend follows the standard pipeline: **`Source → Scanner.Scan() → []Token → Parser → *ast.File`**.
Core design decision: **block structure is tracked naturally by consuming the LBrace/RBrace tokens — `strings.Count/Index` is never used to make syntax judgments.**
A newline is a statement separator (a `Newline` token), not a block-structure marker (`{ }` handle those). The `parser` struct is driven by recursive descent over `tokens[] + pos` with `peek/advance/expect`;
the one-way file dependency chain is `file.go → stmt.go → inst.go → scanner.go`. Error collection does not stop at the first error — `parser.errors []Diagnostic` accumulates the full set of diagnostics.

### AST Type Tagging — the Quote Field

`Expr.Quote` distinguishes string literals from variable names, replacing the old `"`-prefix hack. The parser carries the scanner's token Quote information onto the AST; `Flat()` adds the `"` prefix at the KV transport layer, and `stringPrec` uses `escapeString` to restore the source form. Numeric literals (e.g. `-5`) are no longer mistakenly wrapped in quotes.

---

## Implementation Consistency Notes

Cross-checked against the Go source under `kvlang/` (parser/, lower/, layout/, rwir/, kvcpu/, ast/, keytree/). The following items are confirmed against the code; anything that deviates is flagged explicitly.

**Confirmed claims:**

1. **Frontend chain and API match.** `ParseFile(path)` / `ParseCode(io.Reader)` in `parser/parser.go` both return `(*ast.File, []Diagnostic, error)`. `ParseCode` runs `Scan(string(raw))` → `&parser{tokens, srcLines, srcName}` → `parseFile()`. There is no hard error on the first syntax error: `p.errors []Diagnostic` accumulates diagnostics and `expect` performs error recovery by consuming the unexpected token and returning a synthetic token so parsing continues.

2. **Block structure via LBrace/RBrace tokens; no `strings.Count/Index` syntax judgment.** The parser consumes `LBrace`/`RBrace` tokens in `parseBody` / `parseLibBody` / `parseIf` / `parseFor` / `parseWhile` / `parseBlockLabel`. `strings` is imported in `parser/parser.go` only for `strings.TrimSpace` and `strings.ContainsAny` (read-only-param checks) — never for bracket matching.

3. **Newline as statement separator.** `Newline` is emitted for `\n` and also for `;` (both folded into one token); `Comment` tokens preserve `#` line comments. `parseInst` stops at `Newline / RBrace / EOF`.

4. **Parser driver struct.** `type parser struct { tokens []Token; pos int; errors []Diagnostic; srcLines []string; srcName string }` with `peek / peekAt / advance / eat / expect / skipNewlines / skipNewlinesAndComments / collectLeadingComments` — matches the doc's `tokens[]+pos+peek/advance/expect` description.

5. **Quote field semantics.** `ast.Expr.Quote byte` (0 / `'"'` / `` '`' ``) carries the literal type; the scanner records `Token.Quote` and the parser rebuilds leaves via `StrLit` (double quotes), `RawStr` (backticks). `Instruction.Flat()` still prefixes string values with `"` for the KV transport layer, exactly as the doc states. `stringPrec` restores the source form via `escapeString` (`ast/escape.go`). Numeric literals are never quoted: in `parser/inst.go` a unary minus followed by a numeric `Literal` token is folded directly into `IntLit("-5")` / `FloatLit`, and plain numeric literals take the `IntLit` / `FloatLit` path with `Quote == 0`.

6. **KV execution model.** The PC is an absolute KV path string (`/vthread/<vtid>/[i,0][/[j,0]]...`), instructions are decoded from the KV tree by `rwir.Decode` using a single batched `kv.Get` (up to `1 + 2*maxParams = 257` keys), and `kvcpu.(*cpu).Execute` walks the PC each loop iteration. Control primitives are the static set in `rwir/control.go`: `OpCall = "call"`, `OpReturn = "return"`, `OpBr = "br"`, `OpGoto = "goto"`.

**Discrepancies and outdated statements:**

1. **The layout stage is named `layoutrwir` in the doc; the Go package is now `layout`.** The package declared in `layout/layout.go` is `package layout` (import path `kvlang/layout`); its package comment still reads "Package layoutrwir". Commit `40a32c8 refactor: layoutrwir/ → layout/ 重命名` renamed the directory. `WriteBody` itself matches the doc — it recursively writes compiled instructions under `/lib/<pkg>.<name>/[i,j]` via `keytree.LibFunc(pkg, name)` (keys `[n,0]`, `[n,-(j+1)]`, `[n,(j+1)]`), and `keytree` builds `/lib/<pkg>.<name>` with `.` as the member separator.

2. **`BlockStmt` → `ast.ScopeStmt`.** The AST node for a labeled basic block is `ScopeStmt` (fields `Label` + `Body`), not `BlockStmt`. `parser.parseBlockLabel` returns `*ast.ScopeStmt`, and lower/layout operate on `*ast.ScopeStmt`. The `RegisterBlocks` doc comment in `layout/layout.go` still says "BlockStmt label", but the type is `ScopeStmt`.

3. **"br/goto further simplified → call(block_label)" does not match the implementation.** `lower` emits two distinct control opcodes — `br(cond, trueLabel, falseLabel)` (`rwir.OpBr`) and `goto(label)` (`rwir.OpGoto`) — which are never rewritten into `call`. At runtime `kvcpu/controlflow.go` `handleControl` dispatches them via `brBlock` / `gotoBlock` → `layout.HandleScope`, which creates/reuses a scope frame (a sibling of the enclosing rwfunc frame, built without an extindex overlay) and jumps to its `[0,0]` entry. The parenthetical reflects an older design.

4. **"Control flow is uniformly call/return" is partially outdated.** The current control-primitive set has four opcodes (call/return/br/goto, per `rwir.IsControlOp`). Function invocation uses call/return; block transfers use br/goto against scope frames. The "label block = parameterless function" part is consistent: `layout.RegisterBlocks` registers every block label at `/lib/<pkg>.<name>/<label>` with `NewRwfunc(0, 0, 0, "")` (0 reads / 0 writes). However, `layout.HandleLabel` (which creates nested label frames with `.returnpc` / `.callpc` and TCO over the ancestor chain) is defined in `layout/layout.go` but is currently **unreferenced by the Go kvcpu execution path** — br/goto go through `HandleScope` instead, and empty-opcode scope frames return implicitly via `HandleScopeReturn`.

5. **The dependency chain's first file is `parser.go`, not `file.go`.** `kvlang/parser/` contains `parser.go`, `stmt.go`, `inst.go`, `scanner.go` — there is no `file.go`. The file header comments state the one-way chain as `parser.go → stmt.go → inst.go → scanner.go`. Same chain; the first filename in the doc is wrong.

6. **The "parser篇-06" cross-reference is valid.** The referenced document exists as `parser篇-06-layoutrwir.md` in the same design-doc directory (the sub-document keeps the old `layoutrwir` name, consistent with note 1 above).

The Go source has evolved past several of this doc's phrasings (notably the `layoutrwir` package rename and the `br/goto`-as-`call` idea), but the frontend pipeline (`Source → Scanner.Scan() → []Token → Parser → *ast.File`), the Quote-field design, and the KV-path PC model it describes are all current and accurate.
