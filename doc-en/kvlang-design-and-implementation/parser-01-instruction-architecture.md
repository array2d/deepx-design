# Instruction Architecture

## Instruction Classification

Instructions are layered; the execution layer only sees the first two layers:

1. **rwir** (directly executable by kvcpu, or delegated to a registered op backend): `writes <- opcode(reads...)` or `opcode(reads...) -> writes`. Whether a parameter is a read or a write is decided by the arrow direction; there is no implicit stack and no anonymous registers. `writes = expr` is an equivalent spelling of `<-` (write slots on the left); unlike `=` in other languages, the read/write roles are still strictly constrained by the instruction shape — `=` is not an expression and cannot be nested inside a condition.
2. **`rwfunc funca(ra, rb) -> (wa, wb) { … }` = a user-defined composite rwir**, also called a custom function. A single rwir `A + B -> C` is an atomic rwir (one opcode + read params + write params); `def` packs several rwirs into one named unit that exposes the same arrow interface externally — `(ra, rb)` is the read-param declaration and `-> (wa, wb)` is the write-param declaration. Calling `add(3,4) -> s` binds actual arguments into the read slots and maps the write slots back to the caller's frame. **A call must match all of the callee's write params** — discard the ones you don't want with `._` (aligned with Go `_`/Rust `_`, not Python's conventional variable name; `frameSlotKey` returns an empty path directly, so nothing is persisted). The design carries straight through from rwir: the arrow direction decides the data flow, and the contract is identical whether the rwir is single or composite.

### rwfunc — the deeper meaning of composite rwir. `def` does not make the traditional compiler's "function body → bytecode → calling convention" three-step jump — it just loads several rwirs into a named unit whose external interface is still the `(reads) -> (writes)` arrow interface. This means kvlang's call stack is not "push + jump + return" but **cross-frame mapping of write params**: HandleCall binds actual arguments into the child frame's read slots, and HandleReturn carries the child frame's write-slot values back to the parent frame — throughout the whole process there is no such concept as a "return value", only data flowing between slots. rwfunc can therefore be seen as **rwir with a frame boundary**: an atomic rwir completes its reads and writes inside one frame, an rwfunc spans parent and child frames to do the same, and the lib namespace is one more level of aggregation above rwfunc. From `A+B->C` to `rwfunc add` to `lib math`, **all three layers share one paradigm** — the arrow direction decides the data flow, slots explicitly declare their read/write role, and the source is a data-flow graph.
3. **Control-flow primitives** (a subset of rwir): `call`/`return`/`br`/`goto` — these change the PC. See [parser&runtime-01 — 控制流](parser&runtime-01控制流篇.md) for control flow.
4. **High-level syntax** (disappears after lower): `if`/`else`, `while`, `for`, `label:` — lowered into basic blocks + `br` before being written to `/lib/`; kvcpu is unaware of them.

### `->` / `<-` / `=` and write slots

The three instruction spellings share exactly the same write-slot constraint: the write slots on the right/left side must be **positions**.

| Form | Write-slot location | Example |
|------|---------|-----|
| `expr -> writes` | right | `A + B -> C` |
| `writes <- expr` | left | `C <- A + B` |
| `writes = expr` | left (≡ `<-`) | `C = A + B`、`p.val = 8` |

`=` differs from assignment in other languages: it is merely an alias for `<-`, with the read/write roles constrained by the instruction shape — `=` is not an expression and cannot appear inside a condition (`if (x = 5)` is a class of error that does not exist at the syntax layer). In source it is homomorphic with the KV-layer copy opcode `=` (§2.3).

| Position | Meaning | Legal write slots |
|------|------|---------|
| Ordinary instruction `A + B -> C` | expression result written to a position | bare name, `/abs`, `base.名` |
| Function call `add(a,b) -> s` (incl. implicit return mapping) | destination positions for the callee's write params | bare name, `/abs`, `base.名` |
| ~~`f() -> 42`~~ / ~~`f() -> "s"`~~ (error) | a literal is not a position | — not legal — |

**Iron rule**: the right side of `->` / the left side of `<-` and `=` must be a position (pointer); a literal (number, quoted string) in write-slot position is a syntax error — the Parser rejects it and reports a diagnostic.

### Call write arity and the `._` discard slot. kvlang sides with the Go/Rust camp: a call must match all of the callee's write params — either receive them all, or `._`. `f() -> s` is a compile error for a function with multiple write params.

| Language | Multi-output call | Discarding partial outputs | Arity enforced? |
|------|----------|-------------|------------|
| Go | `q, r := divmod(17, 5)` | `_, r := divmod(17, 5)` | **Yes** — `x := f()` is a compile error for multiple return values |
| Rust | `let (q, r) = divmod(17, 5)` | `let (_, r) = divmod(17, 5)` | **Yes** — pattern must match |
| Python | `q, r = divmod(17, 5)` | `_, r = divmod(17, 5)` | No — `x = f()` gets the whole tuple |
| C | `divmod(17, 5, &q, &r)` | `divmod(17, 5, NULL, &r)` | No — pass NULL, the compiler doesn't care |
| V8/TS | `const [q, r] = divmod(17, 5)` | `const [, r] = divmod(17, 5)` | No — `const x = f()` silently drops the rest |

`._` is a formal discard slot built into the language (aligned with Go `_`/Rust `_`): the parser recognizes it, and `frameSlotKey` returns an empty path directly for `.`-prefixed slots — nothing is persisted, no KV storage is occupied. Unlike `_` in Python/JS, which is only a convention (a variable name that still occupies memory), kvlang's `._` is engine semantics — the slot is never allocated.

## The Two-Dimensional Coordinate Model of Instructions: `[s0, s1]`

### The meaning of the two axes

Each instruction occupies a **two-dimensional coordinate** `[s0, s1]` in the KV tree:

```
s0 axis (horizontal) — execution-order axis: which instruction
s1 axis (vertical)   — parameter axis: the role of that slot

        s1 < 0           s1 = 0         s1 > 0
      (read param)     (opcode)       (write param)
           ←─────────────── 0 ───────────────→
s0 = 0  │  [0,-2] [0,-1]  [0,0]  [0,1] [0,2]
s0 = 1  │  [1,-2] [1,-1]  [1,0]  [1,1]
s0 = 2  │  [2,-1]         [2,0]  [2,1]
  ...
```

**Iron rules**:
- `[s0, 0]` is **always the opcode** (an operator or the called function's name)
- `[s0, -1], [s0, -2], ...` are **read slots**, the minus sign meaning "consumes data"
- `[s0,  1], [s0,  2], ...` are **write slots**, the plus sign meaning "produces data"

### Concrete example

```kv
rwfunc add(A: int, B: int) -> (C: int) {
    A + B -> C
}
```

After lower + layoutrwir it is written into KV:

```
/lib/main.add/[0,0]   = "+"      ← s1=0: opcode
/lib/main.add/[0,-1]  = "A"      ← s1=-1: 1st read param (left operand)
/lib/main.add/[0,-2]  = "B"      ← s1=-2: 2nd read param (right operand)
/lib/main.add/[0,1]   = "C"      ← s1=1:  1st write param (result destination)

/lib/main.add/[1,0]   = "return" ← s1=0: opcode (implicit return, appended by appendReturn)
```

Rendered as a two-dimensional table:

```
       s1=-2   s1=-1   s1=0      s1=1
s0=0 │  "B"    "A"    "+"      "C"
s0=1 │                "return"
```

The data-flow direction is clear: **read in from the negative axis, execute at the zero axis, write out to the positive axis**.


### Multi-parameter instructions and fan-out

```kv
print("hello", x, y)
```

```
/lib/main.foo/[3,0]   = "print"   ← opcode
/lib/main.foo/[3,-1]  = "\"hello" ← 1st read param (string literal, " prefix encoding)
/lib/main.foo/[3,-2]  = "x"       ← 2nd read param
/lib/main.foo/[3,-3]  = "y"       ← 3rd read param
(no write params; the side effect is output)

`print` concatenates multiple params directly (no space separator) and does not append a trailing newline. `println` space-separates the params and appends a trailing newline (Go style).
```

Write-param fan-out (the same result written into multiple slots):

```kv
a + b -> sum, backup
```

```
/lib/main.foo/[2,0]   = "+"
/lib/main.foo/[2,-1]  = "a"
/lib/main.foo/[2,-2]  = "b"
/lib/main.foo/[2,1]   = "sum"    ← 1st write param
/lib/main.foo/[2,2]   = "backup" ← 2nd write param (fan-out)
```

Copy instructions (leaf expression → write slot) are encoded as the explicit opcode `=`, with the value reference in the read slot:

```kv
a -> b        # [s0,0]="="  [s0,-1]="a"   [s0,1]="b"
42 -> x       # [s0,0]="="  [s0,-1]="42"  [s0,1]="x"
```

The opcode position is **always an opcode**, never a variable reference — `=` makes a copy unambiguous from a zero-argument function call (`greet() -> x`, opcode="greet") at the KV layer.


### `op.Decode`: scanning along the s1 axis

When kvcpu decodes, it expands outward along both directions of the s1 axis until it hits an empty key:

```go
// reads:  s1 = -1, -2, -3 ... until kv.Get returns empty
// writes: s1 = +1, +2, +3 ... until kv.Get returns empty
```

This means the parameter count is **implicitly encoded**: compactly allocated, with no arity stored in the opcode.

**WriteFunc correctness constraint**: before writing a new function you must `DelTree` the old data.
If the old function has a leftover value at `[0,-1]`, the new function's Decode will treat it as a real read param — this was exactly the root cause of the earlier `a -> b` being misread as `a(1, 2) -> b`.


### Comparison with traditional bytecode

| Dimension | Traditional bytecode | kvlang `[s0, s1]` |
|------|-----------|-------------------|
| Instruction storage | linear array `code[PC]` | KV tree `kv.Get(prefix + "[s0,0]")` |
| Operand position | operands immediately follow the opcode | `s1<0` (read) / `s1>0` (write) separated |
| Parameter count | arity encoded in the opcode | implicit: stop at an empty key |
| Data-flow direction | unidirectional (operands → result) | sign-encoded (negative = read, positive = write) |
| Observability | bytes are not independently addressable | every slot is an independent KV key, individually Get/Watchable |
| Debugging | requires a disassembler | `kv.List("/lib/main.add")` suffices |


### Why negative numbers represent read params

This is not convention but the natural consequence of number-axis symmetry:

```
write (produce)→   0  ← read (consume)
+1 +2 +3 … [opcode] … -1 -2 -3
```

- **Positive axis**: data **flows out** (written to target slots — comparable to the "addition direction", producing a new value)
- **Negative axis**: data **flows in** (read from source slots — comparable to the "subtraction direction", consuming existing values)
- **Zero point**: the execution center (the opcode itself neither consumes nor produces; it only defines the operation's semantics)

The two-dimensional coordinate of a single instruction stays unique and unchanged for the entire lifetime of a vthread — it is both an address and the node description of a data-flow graph.

## Implementation Consistency Notes

Cross-checked against the kvlang Go sources (parser/inst.go, parser/parser.go, ast/ast.go, lower/lower.go, rwir/rwir.go, rwir/control.go, rwir/pc.go, layout/layout.go, kvcpu/execute.go, kvcpu/controlflow.go, rwir/builtin/{io,helper,resolve,ops}.go, keytree/{const,entry,frame}.go), plus the prebuilt `./kvlang` binary (`vet`).

- **`[s0, s1]` two-dimensional model, read/write axis convention** — CONFIRMED. `layout.writeStmt`/`writeStmtScope` (layout/layout.go) write the opcode to `<prefix>/[n,0]`, reads to `[n,-(j+1)]`, writes to `[n,+(j+1)]`; `rwir.Decode` (rwir/rwir.go) reads them back symmetrically. `[s0,0]` is always the opcode — `ast.Instruction.Flat()` (ast/ast.go) forces a leaf expression (`a`, `42`, `"s"`, `/abs`) into the explicit copy opcode `=`, so the opcode slot never holds a bare variable reference.

- **`=` / `<-` equivalence and copy opcode** — CONFIRMED. The parser treats `=` and `<-` identically (`ArrowLeft=true`, `inst.Eq = arrowVal == "="`, parser/inst.go); `=` is a reserved glyph of the `assign` word (symbol/symbol.go) and `isCopyOp` (kvcpu/execute.go) recognizes opcode `=` with write slots as a value copy rather than a call. A zero-arg call `greet() -> x` keeps the function name in the opcode slot, so `=` vs. call is unambiguous at the KV layer.

- **Implicit return is NOT appended anymore** — DISCREPANCY. The example `[1,0] = "return"` ("implicit return, appended by appendReturn") is outdated. `appendReturn` does not exist in the codebase, and `lower.Func` states: "不再插入隐式 return——VM 在遇到空指令槽时自动弹帧" (no longer inserts an implicit return — the VM auto-pops the frame when it hits an empty instruction slot). `kvcpu.Execute` (kvcpu/execute.go) handles `inst.Opcode == ""` by calling `HandleScopeReturn` or `HandleReturn` (and the sibling doc `kvspace篇-03` still shows the same obsolete `[1,0]="return"` line). For the `add` example, only `[0,*]` keys exist in the current implementation.

- **Discard slot: doc says `._`, codebase actually uses `_`** — DISCREPANCY. The doc claims `._` is the built-in discard slot recognized by the parser, never allocating a KV slot. In the current implementation: (a) the parser REJECTS source-level `._` — `./kvlang vet` reports `warn: unexpected token "." in write slot position` and produces no write slot; (b) actual usage in the codebase (e.g. `tutorial/03-debugger/chain_array.kv`: `f2() -> (_, s)`) is a bare `_`; (c) a bare `_` is parsed as a normal write slot and resolves to a real slot via `frameSlotKey` (layout/layout.go) / `writeSlotKey` (rwir/builtin/resolve.go), i.e. `Stack(frameRoot)+"_"` — it allocates a stray slot in the caller's frame rather than "never being allocated". The `frameSlotKey` `.`-prefix → `""` mechanism the doc cites does exist (layout/layout.go:481-486) but is currently unreachable from source as a discard slot.

- **Write-arity enforcement is not implemented** — DISCREPANCY. The doc states a call must match all write params ("要么接收，要么 `._`") and that `f() -> s` is a compile error for multi-write functions, and puts kvlang in the Go/Rust camp of the five-language table. In the current code there is no compile-time arity check anywhere (parser, vet, or layout). `layout.HandleCall` (layout/layout.go:206-219) iterates `funcSig.Returns` and binds a write target only while `i < len(inst.Writes)`; missing targets are silently skipped, so a child's writes to an unbound write param land in the child's own frame as garbage. No error-level diagnostic is produced.

- **HandleReturn semantics wording** — MINOR DISCREPANCY. The doc says "HandleReturn 将子帧写槽的值搬回父帧" (HandleReturn carries the child frame's write-slot values back to the parent frame). The current implementation uses **zero-copy write-param redirection at call time**: HandleCall sets `.wparam/<name>` on the child frame to a path resolved in the caller's frame, and the child's writes go straight to the caller's target via `resolveWriteSlot` (rwir/builtin/helper.go). `layout.HandleReturn` only reads `.returnpc`, then `UnLink`+`DelTree`'s the frame; it copies nothing. Also note HandleReturn currently panics if a `return` carries read args (`return 不得带参数`).

- **`op.Decode` scan description** — MINOR IMPLEMENTATION NOTE. The doc describes scanning `s1 = -1,-2,...` until `kv.Get` returns empty. `rwir.Decode` now fetches a single batched MGET of `1 + 2*maxParams = 257` keys in one call (`maxParams = 128`, rwir/rwir.go, aligned with C's guaranteed ≥127 params) and stops on `None` values; an instruction exceeding 128 slots returns a decode error instead of silently truncating. The "arity is implicit, no arity stored in the opcode" claim holds unchanged.

- **WriteFunc `DelTree` constraint** — CONFIRMED. `layout.WriteFunc` and `layout.WriteRwir` both `kv.DelTree` the target `/lib/<pkg>.<name>` key before writing (layout/layout.go), matching the doc's warning that stale `[0,-1]` leftovers would otherwise be decoded as real read params.

- **KV path formats** — CONFIRMED. `/lib/<pkg>.<name>/[i,j]` via `keytree.LibFunc` (`LibRoot`=`/lib`, `MemberSep`=`"."`), scope/label subpaths `/lib/<pkg>.<name>/<label>/`, source copy `<name>.src` (`keytree.LibSrc`). The function signature is stored at `/lib/<pkg>.<name>` as an XValue of kind `rwfunc`, and slots are kind `rwir` for variable references (layout/layout.go).

- **`print` / `println` semantics** — CONFIRMED. `rwir/builtin/io.go` registers `print` with `nosep:true, rawnl:true` (direct concatenation, no separator, no trailing newline) and `println` with neither (space-separated, trailing newline via `WriteTerm`). Additional IO ops `cerr` and `input` also exist.

- **String-literal `"`-prefix encoding** — CONFIRMED at the layout level. `ast.Instruction.Flat()` emits a quoted string literal into the read slot with a leading `"` (e.g. `"hello`, exactly as the doc shows); `layout.slotValue` then strips the quote and stores an XValue of kind `string`.

- **`writeStmt` arity/fan-out keys** — CONFIRMED. Reads are laid out at `[n,-(j+1)]` for `j=0,1,...`, writes at `[n,+(j+1)]`, matching the doc's `[2,1]="sum"`, `[2,2]="backup"` fan-out example.

- **Repo build state** — NOTE. As of this translation, `kvlang/go.mod` is malformed (the second `require (` block is unterminated before the `replace` directive), so `go build`/`go test` fail in the working tree (verified: `go build ./...` → `go.mod:17: syntax error`). The prebuilt `./kvlang` binary works; `kvlang vet` was used to verify the parser behaviors above. `./kvlang layout` could not be exercised (hung in the sandbox), so runtime KV layout was verified from source.
