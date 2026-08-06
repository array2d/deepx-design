# Function Calls (the builtin perspective)

## Call-form overview

User-defined functions can be invoked in three source-level forms, all of which ultimately resolve to the same KV key `/lib/<pkg>.<name>`:

| Source form | Example | May pkg be multi-level? |
|------------|---------|------------------------|
| Full path | `/lib/math.sum(3, 4) -> s` | ✅ `/lib/a/b.add(3, 4) -> s` |
| Dot / slash-qualified | `math.sum(3, 4) -> s` | ✅ `a/b.add(3, 4) -> s` |
| Bare name | `sum(3, 4) -> s` | ❌ within the same lib |

**Core conclusion**: a full path is only 4 characters longer than a dot-qualified name (`/lib`), and the two are completely equivalent at the HandleCall stage. All the difference lies in the tokenization path taken by the Scanner/Parser, but both converge on the same KV key.

---

## Prelude: Scanner tokenization

The key to understanding "full path vs. dot-qualified" is how the Scanner treats `/` and `.`.

### The dual nature of `/`

In the Scanner, `/` is not a token delimiter (`parser/scanner.go:375-384`, `isTokenDelim` does not include `/`). The tokenization of `/` depends on context:

```go
// scanner.go:331-352
if c == '/' {
    // // 行注释 → 跳到行尾
    if i+1 < len(src) && src[i+1] == '/' { ... }

    // / 后跟字母/数字/下划线 → 路径字面量（读至真正 delimiter）
    if i+1 < len(src) && isAbsPathStart(src[i+1]) {
        start := i
        i++
        for i < len(src) && (!isTokenDelim(src[i]) || src[i] == '.') {
            i++  // '.' 不在路径中作为分隔符——/lib/a/b.add 是单个 token
        }
        tokens = append(tokens, Token{Kind: Literal, Value: src[start:i], Pos: p})
    // / 单独出现 → 除法算子
    } else {
        tokens = append(tokens, Token{Kind: Ident, Value: "/", Pos: p})
    }
}
```

**Key point**: in the path-literal read loop, `.` is excluded from the delimiter test — `!isTokenDelim('.') || '.' == '.'` = `false || true` = `true`, so `/lib/a/b.add` is a **single Literal token**.

### `/` inside identifiers

Because `/` is not in `isTokenDelim`, the identifier read loop (scanner.go:355-358) treats `/` as an ordinary character:

```
a/b.add(10, 20)
→ identifier read: starting from a, neither / nor b is a delimiter → reads up to a/b
→ . is a delimiter → stops
→ Token: IDENT("a/b")
```

**Implicit constraint**: in a multi-level pkg path, the segment separator must be `/` (e.g. `a/b`), not `.` (`a.b` would be parsed as the func portion of a pkg.func form).

---

## Phase 1: Parser (source → AST opcode)

File `parser/inst.go`, function `parsePrimaryExpr`.

### Full path `/lib/a/b.add(args)`

Scanner output: `LITERAL("/lib/a/b.add") LPAREN ...` (the path literal is one token, `.` is not a delimiter)

The Parser takes the **regular function-call branch** (`inst.go:288-307`):

```go
// peek = LITERAL("/lib/a/b.add"), peekAt(1) = LPAREN → 命中
name := p.advance().Value  // "/lib/a/b.add"
// 结果 opcode = "/lib/a/b.add"
```

### Dot / slash-qualified `a/b.add(args)` (multi-level pkg)

Scanner output: `IDENT("a/b") DOT(".") IDENT("add") LPAREN ...`

The Parser takes the **dotted function-call branch** (`inst.go:310-326`):

```go
// 触发条件：(IDENT|LITERAL_PATH) . IDENT LPAREN
opcode := p.advance().Value     // "a/b"
p.advance()                     // skip Dot
opcode += "." + p.advance().Value // "a/b.add"
// 结果 opcode = "a/b.add"
```

### Dot-qualified `math.sum(args)` (single-level pkg, no `/`)

Scanner output: `IDENT("math") DOT(".") IDENT("sum") LPAREN ...`

Same dotted function-call branch as above: `opcode = "math.sum"`.

### Bare name `sum(args)`

Scanner output: `IDENT("sum") LPAREN ...`

The Parser takes the regular function-call branch: `opcode = "sum"`.

---

## Phase 2: writeStmt (AST → KV instruction slots)

File `layoutrwir/layoutrwir.go:44-58`.

```go
opcode, reads := s.Flat()
if pkg != "" && !builtin.IsNativeOp(opcode) && !op.IsControlOp(opcode) &&
    !strings.Contains(opcode, keytree.FuncPathSep) &&  // "."
    !strings.HasPrefix(opcode, keytree.LibRoot+keytree.PathSegSep) &&  // "/lib/"
    opcode != "=" {
    opcode = pkg + keytree.FuncPathSep + opcode
}
```

Completion condition: the opcode must satisfy **all** of the following — no `.`, no `/lib/` prefix, and not a builtin / control-flow op / `=`.

| Source form | opcode entering writeStmt | contains `.`? | starts with `/lib/`? | completed? | KV `[s0,0]` written |
|-------------|--------------------------|---------------|----------------------|------------|---------------------|
| `/lib/a/b.add(3,4) -> s` | `/lib/a/b.add` | ✅ | ✅ | ❌ | `/lib/a/b.add` |
| `a/b.add(3,4) -> s` | `a/b.add` | ✅ | ❌ | ❌ | `a/b.add` |
| `math.sum(3,4) -> s` | `math.sum` | ✅ | ❌ | ❌ | `math.sum` |
| `sum(3,4) -> s` (in lib math) | `sum` | ❌ | ❌ | ✅ → `math.sum` | `math.sum` |
| `sum(3,4) -> s` (top level, no lib) | `sum` | ❌ | ❌ | ❌ (pkg=="") | `sum` |

---

## Phase 3: Execute dispatch (opcode → CALL)

File `kvcpu/execute.go:136-163`.

Dispatch priority:
1. `IsControlOp` — call/return/br/goto
2. `IsNativeOp` — builtin operators (`registry` map)
3. `vtype.Lookup` — the `tensor.*` and other namespaces
4. `isCopyOp` — `opcode="="` with a write slot
5. **default → user-defined function**

```go
// default 分支：opcode 不在前四个优先级 → 改写为 call 指令
inst.Reads = append([]string{inst.Opcode}, inst.Reads...)
inst.Opcode = op.OpCall
execErr = handleControl(ctx, c.kv, vtid, pc, inst)
```

The opcode is moved to `inst.Reads[0]` as the name of the called function.

---

## Phase 4: HandleCall (function name → KV key)

File `layoutrwir/layoutrwir.go:87-105`.

```go
funcName := inst.Reads[0]

libPrefix := "/lib/"
if strings.HasPrefix(funcName, libPrefix) {
    // "/lib/a/b.add" → rest = "a/b.add"
    rest := funcName[len(libPrefix):]
    if dot := strings.LastIndex(rest, "."); dot > 0 {
        pkg = rest[:dot]       // "a/b"
        funcName = rest[dot+1:] // "add"
    } else {
        funcName = rest
    }
} else if dot := strings.LastIndex(funcName, "."); dot > 0 {
    // "a/b.add" → pkg = "a/b", funcName = "add"
    pkg = funcName[:dot]
    funcName = funcName[dot+1:]
}
// else: "sum" → pkg = "", funcName = "sum"

funcKey := keytree.LibFunc(pkg, funcName)
// "/lib/" + pkg + "." + funcName → "/lib/a/b.add"
```

`keytree.LibFunc` (`keytree/entry.go:5-8`):
```go
func LibFunc(pkg, name string) string {
    if pkg == "" { return "/lib/" + name }
    return "/lib/" + pkg + "." + name
}
```

**All forms converge on the same KV key**, and the `/` of a multi-level pkg is preserved as-is in the pkg segment:

| Source | funcName in HandleCall | pkg | final KV key |
|--------|------------------------|-----|--------------|
| `/lib/a/b.add(3,4)` | `a/b.add` (strip `/lib/`) | `a/b` | `/lib/a/b.add` |
| `a/b.add(3,4)` | `a/b.add` | `a/b` | `/lib/a/b.add` |
| `/lib/math.sum(3,4)` | `math.sum` (strip `/lib/`) | `math` | `/lib/math.sum` |
| `math.sum(3,4)` | `math.sum` | `math` | `/lib/math.sum` |
| `sum(3,4)` (in lib math) | `math.sum` (completed by writeStmt) | `math` | `/lib/math.sum` |

---

## Complete tokenization comparison

### Single-level pkg

| Source | Scanner token stream | Parser branch | opcode |
|--------|----------------------|---------------|--------|
| `/lib/math.sum(3,4)` | `LITERAL("/lib/math.sum") LPAREN ...` | regular function call | `/lib/math.sum` |
| `math.sum(3,4)` | `IDENT("math") DOT IDENT("sum") LPAREN ...` | dotted function call | `math.sum` |

### Multi-level pkg

| Source | Scanner token stream | Parser branch | opcode |
|--------|----------------------|---------------|--------|
| `/lib/a/b.add(3,4)` | `LITERAL("/lib/a/b.add") LPAREN ...` | regular function call | `/lib/a/b.add` |
| `a/b.add(3,4)` | `IDENT("a/b") DOT IDENT("add") LPAREN ...` | dotted function call | `a/b.add` |

---

## Lookup failure

If the XValue at `funcKey` is None (signature not registered), HandleCall returns:

```
NameError: func signature not found: <funcName>
```

The thread enters its error terminal state.

---

## Related files

| File | Responsibility |
|------|----------------|
| `parser/scanner.go:331-384` | tokenization rules for `/` and `.` |
| `parser/inst.go:288-326` | parses the three call forms into an AST Expr |
| `layoutrwir/layoutrwir.go:44-58` | completes a bare-name opcode with the pkg prefix |
| `kvcpu/execute.go:156-163` | user-function dispatch → CALL |
| `layoutrwir/layoutrwir.go:87-182` | HandleCall: function-name resolution + frame creation |
| `keytree/entry.go:5-8` | LibFunc KV key construction |
| `keytree/const.go:9` | `FuncPathSep = "."` |

---

## Implementation Consistency Notes

The Go source was cross-checked against this document (kvlang tree at the time of translation). The document's algorithm and the three call forms resolving to `/lib/<pkg>.<name>` are all still accurate, but the following details have drifted from the current implementation:

1. **Package / file renames** (all functionally equivalent, but identifiers and paths changed):
   - `layoutrwir/layoutrwir.go` → **`layout/layout.go`**; the package was renamed `layoutrwir` → `layout` (commit "refactor: layoutrwir/ → layout/ 重命名"). `writeStmt` completion logic now at `layout/layout.go:55-60`; `HandleCall` name resolution at `layout/layout.go:133-149` (full `HandleCall` at `131-225`). The package doc comment on `layout/layout.go:1` still reads "Package layoutrwir" — a leftover.
   - `keytree.FuncPathSep` → **`keytree.MemberSep`** (`keytree/const.go:7`, `MemberSep = "."`). The doc's `keytree/const.go:9` reference is stale (it is now line 7), and `FuncPathSep` no longer exists.
   - `keytree.LibFunc` (`keytree/entry.go:5-8`) now builds paths with the `LibRoot`/`PathSegSep`/`MemberSep` constants instead of hardcoded `"/lib/"` and `"."` — same values, so the doc's literal strings remain correct.
   - `op.OpCall` → **`rwir.OpCall`** (`rwir/control.go:3`); the `op` package was migrated to `rwir`. `rwir.IsControlOp` also now takes the place of the doc's `op.IsControlOp`.

2. **`kvcpu/execute.go` dispatch** (doc cites `136-163`; the dispatch switch now lives at **`144-172`**):
   - Priority 3 changed: the doc says `vtype.Lookup` — **no such function exists in the current tree**. The current code is `strings.HasPrefix(inst.Opcode, "tensor.")` → `dispatch.Compute` (`kvcpu/execute.go:155-156`).
   - Priority 4 `isCopyOp` is now `symbol.Lookup(opcode).Word == "assign" && len(writes) > 0` (`kvcpu/execute.go:194-196`). Functionally equivalent to the doc's `opcode="="`: the `symbol` package maps `=`, `<-`, and `->` all to the word `"assign"` (`symbol/symbol.go:38`).
   - Default-branch snippet drift: `[]string{inst.Opcode}` → **`[]rwir.Param{{Name: inst.Opcode}}`** and `op.OpCall` → **`rwir.OpCall`** (`kvcpu/execute.go:169-170`), because `Rwir.Reads` is now `[]rwir.Param` (see `rwir/rwir.go:12-15`).
   - The doc omits code added since it was written: the recursion depth guard `const MaxStackDepth = 256` (`kvcpu/execute.go:21`, enforced at `60-66` with a `RecursionError: stack overflow` message), the inline debugger stepping hook (`111-136`, activated by `/vthread/<vtid>/.debugger`), and `checkReadOnlyWrites` (fix-027 read-only write protection, `139-141`).

3. **`writeStmt` completion condition** (`layout/layout.go:55-60`):
   - `op.IsControlOp` → `rwir.IsControlOp`.
   - `keytree.FuncPathSep` → `keytree.MemberSep`.
   - `opcode != "="` → `symbol.Lookup(opcode).Word != "assign"` — now also suppresses pkg-completion for the `<-` alias (consistent with the project rule that `=` is just an alias of `<-`).
   - The doc shows only the `*ast.Instruction` path; scope-block bodies are written by the sibling `writeStmtScope` (`layout/layout.go:88-125`) with identical completion logic.

4. **Parser (`parser/inst.go`)**:
   - Doc line refs `288-307` / `310-326` are stale; the regular function-call branch is now at `290-311` and the dotted function-call branch at `313-339`.
   - The dotted-call branch now supports **chained dotted segments** `a.b.c(args)` — it loops over `(. IDENT)+` and merges with `keytree.MemberSep` (`inst.go:317-339`); the doc's single-dot snippet is a simplification (multi-segment chains were a later addition).
   - New since the doc: the regular call branch rejects `return(...)` with a diagnostic ("return 不接受参数…", `inst.go:293-297`), enforcing that return takes no arguments (values pass via zero-copy write-params).

5. **Scanner (`parser/scanner.go`)**:
   - Doc line refs `331-352` / `375-384` / `355-358` are stale; actual locations: `/` handling at **`269-286`**, `isTokenDelim` at **`379-388`**, keyword/identifier loop at **`358-372`**.
   - Behavior matches the doc exactly: `/` is not a delimiter, `.` is a delimiter (emitted as a dedicated `Dot` token via the `singleCharToken` table, `scanner.go:113-118`), and the path-literal loop excludes `.` from the delimiter test so `/lib/a/b.add` is one `Literal` token. `//` line comments produce no token (`scanner.go:272-275`).

6. **`HandleCall` does more than the doc describes** (`layout/layout.go:131-225`): the name-resolution algorithm at `133-149` matches the doc verbatim, but the real function additionally reads the registered signature, checks it with `kvspace.IsNone`, and on failure calls `vthread.SetError` with `"NameError: func signature not found: " + funcName` — which matches the doc's "Lookup failure" section exactly (also verified: the error message string in the source is identical). Since the doc was written, `HandleCall` also gained: duplicate-param validation via `checkDupParams` (`layout/layout.go:554-565`), the `kvspace.MkIndexRecursive` + `ExtIndex` instruction-tree overlay, `.returnpc`/`.callpc`/`.lib`/`rparam`/`wparam` frame setup, zero-copy read/write param binding (with `._lit<n>` inline-literal materialization for concrete values), and the `FrameRO` read-only param list. Frame semantics are unchanged.

7. **"Related files" line references** are all shifted by the renames above. Current anchors: `parser/scanner.go:269-286` and `379-388`; `parser/inst.go:290-339`; `layout/layout.go:55-60` (writeStmt) and `131-225` (HandleCall); `kvcpu/execute.go:144-172` (dispatch); `keytree/entry.go:5-8` (LibFunc); `keytree/const.go:7` (`MemberSep = "."`).

No functional discrepancies were found in the core claim of the document: the three source-level call forms, their token streams, the opcode completion rule, and the final convergence on the `/lib/<pkg>.<name>` KV key all match the current implementation.
