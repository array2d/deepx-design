# The Four-Level Hierarchy of Code

All code in kvlang is organized into four levels, from macro to micro. Each level has a corresponding path in the kvspace tree.

## The Four Levels

| Level | Keyword | Responsibility | Owns |
|----|--------|------|------|
| **lib** | `lib name { }` | Package organization; manages every rwfunc under its namespace | a set of rwfuncs |
| **rwfunc** | `rwfunc name(...) -> (...) { }` | Function level; owns its own rwir instruction sequence and variables | rwir sequence + scope tree |
| **scope** | (lower product, not hand-writable) | Domain level; produced by lowering if/while/for; owns its own instruction sequence and variables, and can nest | rwir sequence + sub-scopes |
| **rwir** | `rwir name(...) -> (...)` | Instruction level; executed atomically by the interpreter or delegated to a backend engine | none — atomic and indivisible |

```
/lib/leetcode/                   ← lib: top-level package
└── /lib/leetcode/search/        ← lib: nested sub-package
    ├── [rwfunc] binary(target)  ← rwfunc: function signature
    ├── [0,0]: lo <- 0           ← rwir: atomic instruction
    ├── [1,0]: hi <- 100
    ├── _while_1/                ← scope: while-loop domain
    │   ├── cond/
    │   │   └── [0,0]: found == 0 -> _c
    │   ├── body/
    │   │   ├── [0,0]: lo + hi -> s
    │   │   └── _if_1/           ← scope: nested if domain
    │   │       ├── then/
    │   │       └── else/
    │   └── exit/
    └── _exit_1/
```

## Level Constraints

- **lib** can nest sub-libs (`lib a { lib b { } }`); leaf libs contain rwfuncs. A lib cannot directly contain rwir or scope. It corresponds to the `/lib/<name>/` path; when nested, the path cascades as `/lib/a/b/`.
- **rwfunc** contains only rwir and scope — it cannot contain other rwfuncs. Function calls are not embedded in the body; they go through the `call`/`return` cross-frame mechanism. A rwfunc corresponds to `/lib/<pkg>/<name>/`, and at runtime is instantiated as a `/vthread/<vtid>/[N,0]/` frame.
- **scope** contains only rwir and sub-scopes. Semantically identical to a rwfunc but without its own call stack — a scope's variables and instruction sequence are laid out flat inside the owning rwfunc's frame. Scopes are produced by the lower phase from if/while/for and cannot be hand-written.
- **rwir** is the smallest execution unit and cannot be subdivided. It is executed atomically by the interpreter (builtin `Call()`) or a backend engine (`dispatch.Compute`). A rwir declaration is stored at `/rwir/<opcode>`, and its runtime signature is available for agent introspection.

## Mapping to the KV Tree

Each level has a corresponding path in the kvspace tree; `kvspace tree` is complete introspection:

| Level | KV Kind | `/lib/` path | `/vthread/` path |
|----|---------|-------------|-----------------|
| lib | — | `/lib/<pkg>/` | — |
| rwfunc | `rwfunc` | `/lib/<pkg>/<name>/` | `/vthread/<vtid>/[N,0]/` |
| scope | `scope` | `/lib/<pkg>/<name>/_while_N/` | `/vthread/<vtid>/[N,0]/_while_N/` |
| rwir | `rwir` | `/rwir/<opcode>` | — |

## Implementation Consistency Notes

The claims above were cross-checked against the Go implementation under `/home/peng.li24/github.com/array2d/kvlang/`.

1. **Scopes are not nested — the diagram's `cond/` `body/` `exit/` and nested `_if_1/then|else` structure does not exist.** The doc shows `_while_1/` containing `cond/`, `body/`, `exit/` sub-directories and `_if_1/` nested inside `body/` with `then/`/`else/`. The actual implementation flattens everything to top-level sibling scopes ("读写码结构", read-write-code structure):
   - `lower/lower.go` `lowerWhileWithCont` emits sibling `ScopeStmt`s named `_while_N` (the condition block), `_do_N` (the body block) and `_exit_N` (the exit block) — there is no `body/` label and no `cond/`/`exit/` sub-directories.
   - `lowerIfWithCont` emits `_if_N`, `_then_N`, `_else_N`, `_merge_N`, and both functions explicitly "promote" all nested blocks out of the then/else/body/cont bodies to the function top level.
   - `layout.writeStmtScope` writes nested-scope instructions flat, keyed `funcPrefix/<scopeLabel>[coord]` ("嵌套 scope 也用 funcPrefix，保持平级").
   - At runtime `layout.HandleScope` also creates every scope frame directly under the owning rwfunc frame (`/vthread/<vtid>/[0,0]/<scopeName>/`), never nested under another scope frame.
   The doc's only claim that matches is the trailing `_exit_1/` sibling at function top level (which is the real `_exit_N` block).

2. **Package-to-function path separator is `.`, not `/`.** The doc writes `/lib/<pkg>/<name>/` and `/lib/leetcode/search/`. The implementation joins package and function name with the member separator `.` (`keytree.MemberSep`): `keytree.LibFunc(pkg, name)` returns `/lib/<pkg>.<name>/` (e.g. `/lib/leetcode.search/`). Nested sub-libs are joined by `/` in the parser (`parseLibBody`: `pkg = prefix + "/" + name`), so a function `binary` inside `lib leetcode { lib search {} }` is stored at `/lib/leetcode/search.binary/`. (Note the parser comment at `parser/parser.go:215` claims sub-lib names compose with `.` as `a.b.c`, but the code actually joins them with `/` — an internal comment/code mismatch.)

3. **Scope instructions in `/lib/` are flat keys, not a directory.** `layout.writeStmtScope` stores scope instructions as flat keys of the form `/lib/<pkg>.<name>/<scopeLabel>[coord]` (e.g. `/lib/leetcode.search/_while_1[0,0]`); there is no `/lib/.../name/_while_N/` directory and no trailing-slash scope subtree in `/lib/`. The runtime frame path `/vthread/<vtid>/[N,0]/_while_N/` in the table is confirmed (`layout.HandleScope` → `rwRoot/<scopeName>/`).

4. **KV Kind for scope is aspirational.** `KindScope = "scope"` is defined in `kvspace-go/const.go` but is not used anywhere in the kvlang codebase. The only code that would register a block signature, `layout.RegisterBlocks`, writes kind `rwfunc` (`kvspace.NewRwfunc(0,0,0,"")`), and `RegisterBlocks` is itself never called from `cmd/kvlang`. Runtime scope frames are created without an extindex, so their kind is not observable as `scope` in the KV tree.

5. **`/rwir/<opcode>` holds only builtin native op signatures, and user `rwir` declarations are never persisted.** `rwir/builtin/ops.go` `WriteRwir` writes every registered native op signature to `/rwir/<opcode>` (confirmed; invoked in `cmd/kvlang/run.go` `executeEntry`). However, user-declared `rwir name(...) -> (...)` declarations are parsed (`parser.parseRwirDecl` → `ast.File.RwirDecls`) but `layout.WriteRwir`, which would store them at `/lib/<pkg>.<name>` with kind `rwir`, is never called — so user rwir declarations do not appear in `/rwir/` or anywhere else in the KV tree.

6. **lib can contain rwir declarations.** The doc says lib "cannot directly contain rwir or scope". `parser.parseLibBody` actually accepts `rwir` declarations (and plain statements) inside a lib block, appending them to `f.RwirDecls` / `f.InitBody`. The constraint holds only for rwir *instruction* slots, not rwir *declarations*.

7. **No function literally named `builtin.Call()`.** The interpreter executes native ops via `builtin.Native` (`rwir/builtin/call.go`), which resolves the op from the registry and invokes the `Op` interface method `Call(f)`. Tensor-namespace ops (`tensor.*`) are the ones delegated to the backend engine via `dispatch.Compute` (confirmed by `kvcpu.execute.go`, case `strings.HasPrefix(opcode, "tensor.")`). The current `kvcpu.Execute` dispatch also handles control flow (`handleControl` for call/return/br/goto), value copy (`builtin.ExecuteCopy`), and user-function calls (rewritten to `call`), all in the interpreter.

8. **Confirmed claims.** The four keywords and the `lib`/`rwfunc`/`scope`/`rwir` level semantics; rwfunc holds only rwir+scope (no nested rwfunc) and function calls go through the cross-frame `call`/`return` mechanism (`layout.HandleCall`/`HandleReturn`); rwfunc runtime frames at `/vthread/<vtid>/[N,0]/` (root frame from `layout.Bootstrap` = `/vthread/<vtid>/[0,0]/`, called frames rooted at the call PC); scope variables/instructions laid out flat within the owning rwfunc frame (variable slots under the rwfunc frame via `frameSlotKey`, instructions resolved through the rwfunc frame's extindex with the scope-name prefix); rwir as the atomic, indivisible execution unit; nested libs supported (`fix-039`).

Overall, the document describes the intended four-level design correctly at the conceptual level, but its KV-path rendering (`/` separators) and its nested scope tree (`cond/`/`body/`/`exit/`, `then/`/`else/` sub-directories) are out of sync with the current implementation, which stores all scopes flat and uses `.` for the package-to-function boundary. The `scope` KV kind and persisted user `rwir` declarations described in the doc are not (yet) exercised by the running code.
