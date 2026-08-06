# Layoutrwir: Loading and Execution

import kvspace篇-03-代码指令的布局格式

## The lib Tree and Loading (fix-033/034/039)

**lib tree**: `kvlang layoutrwir` concatenates multiple `.kv` files into a single source → parse → lower → write to `/lib/`. Each `lib name { }` block forms one lib node, and each lib has exactly one `init` function (the init body merged with the top-level code).

**CLI commands**:
- `kvlang run` (no arguments) → execute `/lib/.init` (the anonymous lib's init)
- `kvlang run {childlib}.{func}` → execute `/lib/{childlib}.{func}` (`/lib/` prefix may be omitted; `func` defaults to `init`)
- `kvlang layoutrwirandrun <files…>` → load first, then run
- `kvlang layoutrwir <file|dir>` → concatenate multiple files into a single source → parse → lower → write to `/lib/`

**Cross-lib calls**: full path `/lib/{childlib}.{func}()` — there is no `import` keyword; the lib tree already lives in kvspace, so a call is a path.

## `rwfunc init() -> () { }` Initialization (fix-036)

Bare top-level code is automatically wrapped into an implicit `rwfunc init() -> () { }`.

```kv
lib math { rwfunc sum(A:int, B:int) -> (C:int) { A + B -> C } }
rwfunc init() -> () { /lib/math.sum(3, 4) -> s; print(s) }
```

- `lib name { }` borrows from C++ `namespace` / Rust `mod`
- Each lib is registered at `/lib/<name>.{func}` (`/lib/math.sum`, `/lib/math.init`)
- An rwfunc not wrapped in a lib belongs to the anonymous lib (path `/lib/.{func}`)
- Source storage: `WriteFunc` writes to `/lib/<pkg>.<name>.src` (fix-034)

## XValue Kind

XValue kinds under `/lib/`:

| Kind | Example | Written by |
|------|---------|------------|
| `rwir` | `/lib/func/[0,0] = "+"` | `writeStmt` (instruction read/write slots) |
| `rwfunc` | `/lib/func` | `WriteFunc` (function signature) |

Frame-type determination: `funcFrameRoot` walks up the frame tree looking for the `.lib` marker → `rwfunc` frame; no `.lib` → scope frame. See **[控制流篇](parser&runtime-01控制流篇.md)**.

## Frame Model and Call Stack

See **[parser&runtime-01 — Control Flow](parser&runtime-01控制流篇.md)**.

## The `=` Opcode

`a -> b` is encoded as `[s0,0]="="` (value copy). The function-call opcode bit is `call`. There is no ambiguity at the KV layer — the opcode slot never holds a variable reference.

## Implementation Consistency Notes

Cross-checked against the Go source in `/home/peng.li24/github.com/array2d/kvlang/` (`cmd/kvlang/`, `layout/`, `lower/`, `parser/`, `rwir/`, `kvcpu/`, `keytree/`, `ast/`) and `kvspace-go`.

### Discrepancies

1. **CLI command names have been renamed: `layoutrwir` → `layout`, `layoutrwirandrun` → `layoutandrun`.** The document lists `kvlang layoutrwir <file|dir>` and `kvlang layoutrwirandrun <files…>`. The current CLI (`cmd/kvlang/main.go`) registers subcommands `layout` and `layoutandrun` only. Git history confirms the rename (commits `40a32c8` "layoutrwir/ → layout/ 重命名" and `c81e613` "layoutrwir 重命名产物"). The `layout` package still declares `package layoutrwir`, and `cmd/kvlang/help.go` still prints a stale usage line ("kvlang layoutrwirandrun <file|dir>… 先 layout 再 run").

2. **Anonymous-lib paths are `/lib/<name>`, not `/lib/.<name>`.** The document states that unwrapped rwfuncs belong to the anonymous lib at `/lib/.{func}` and that `kvlang run` (no args) executes `/lib/.init`. In the Go implementation the anonymous key has no dot prefix: `keytree.LibFunc("", name)` returns `/lib/<name>` (`keytree/entry.go`). The implicit init is therefore `/lib/init`, and `cmd/kvlang/run.go` (`runLib("", "init")`) → `layout.Bootstrap` executes exactly `/lib/init`. An explicitly written `rwfunc init() -> () {…}` also lands at `/lib/init`. (Sibling docs `runtime篇-03` and `total篇-02` show the same stale `/lib/.*.src` pattern for source copies; the actual path is `/lib/<name>.src` for the anonymous lib, via `keytree.LibSrc`.)

3. **The frame-type helper is `rwfuncFrameRoot`, not `funcFrameRoot`.** The described behavior is accurate: a frame with the `‥lib` marker is an rwfunc frame, without it a scope frame. In the code this is `layout.ExtKind` (checks `keytree.Stack(frameRoot)+‥lib`) and `layout.rwfuncFrameRoot` (walks the ancestor chain for the nearest rwfunc frame). Note the runtime separator is `‥` (U+2025 `RuntimeMemberSep`), not `.`.

### Notes on code that has moved past the document

4. **`layout.HandleLabel` and `layout.RegisterBlocks` are currently dead code in the Go source.** Both are defined in `layout/layout.go` but have zero call sites. `kvcpu/controlflow.go` implements `br`/`goto` via `layout.HandleScope`, which creates scope frames (siblings of the rwfunc frame, no extindex overlay). `RegisterBlocks` writes block signatures with `kvspace.NewRwfunc(0,0,0,"")` (kind `rwfunc`) despite its comment claiming kind `label` — `kvspace-go` defines no `KindLabel` (kinds are `rwir`, `rwfunc`, `scope`, … in `kvspace-go/const.go`). The document does not describe label frames in detail (it defers to the control-flow doc), so this is a code-state note rather than a doc contradiction.

5. **`parser.parseLibBody` builds nested-lib pkg names with `/`, contradicting its own comment.** For `lib a { lib b { … } }` the pkg is `a/b` (`prefix + "/" + name`), yielding `/lib/a/b.<func>`, whereas the comment at `parser/parser.go:215` claims dot-joining ("子 lib 名以 "." 拼接：a.b.c"). The document does not discuss nested libs, so this is an internal-comment inconsistency only.

### Verified-accurate claims

- Each `lib name { }` block registers its funcs at `/lib/<pkg>.<name>`; `parser.parseLibBody` sets `fn.Pkg`, `layout.WriteFunc` uses `keytree.LibFunc` (`/lib/math.sum`, `/lib/math.init`).
- Bare top-level code and `TopLevelCalls` are merged into an implicit `ast.Func{Sig: {Name:"init"}}` by `cmd/kvlang/layout.go` and `cmd/kvlang/layoutandrun.go` (the init body + top-level code merge, fix-036).
- `WriteFunc` stores a source copy at `/lib/<pkg>.<name>.src` from `fn.FullText()` (`keytree.LibSrc`, fix-034).
- XValue kinds under `/lib/`: instruction read/write slots are kind `rwir` (`layout.slotValue` → `kvspace.NewRwir`); function signatures are kind `rwfunc` (`layout.WriteFunc` → `kvspace.NewRwfunc`). Slot layout is `[n,0]` opcode, `[n,-j]` reads, `[n,j]` writes (`rwir.Decode`).
- `a -> b` encodes as opcode `=` with the value reference in a read slot (`ast.Instruction.Flat` returns `("=", [a])` for leaf expressions); the call opcode is `call` (`rwir/control.go` `OpCall`). The KV opcode slot never holds a variable reference.
- Cross-lib calls use the full path `/lib/{childlib}.{func}()` with no `import` keyword; `layout.writeStmt` leaves opcodes prefixed with `/lib/` untouched, and `kvcpu/execute.go` rewrites such default opcodes into `call` at runtime (with `layout.HandleCall` parsing the `/lib/` prefix).
- `kvlang run {childlib}.{func}` executes `/lib/{childlib}.{func}` and the function defaults to `init` (`cmd/kvlang/run.go` `cmdRun`/`runLib`).
- Frame-type rule (`.lib`/`‥lib` marker → rwfunc frame; absent → scope frame) matches `layout.ExtKind` and the `kvcpu/execute.go` empty-opcode handling (`HandleScopeReturn` vs `HandleReturn`).
