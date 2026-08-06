# Layout Format of Code Instructions

## Design Principles of layoutrwir and the Function-Call extindex Mechanism

A traditional VM has the compiler emit linear bytecode: a `call` pushes a return address and jumps to the function entry. kvlang does not copy bytecode — **function bodies are never duplicated; a call = ExtIndex turns the frame root into an extended index** pointing at the instruction tree under `/lib/`.

layoutrwir's counterpart across the five languages:

| Language | Stage name | What it does | Output |
|------|--------|------|------|
| **C (GCC)** | codegen + assemble + link | AST→GIMPLE→RTL→asm→.o, three separate tools (cc1/as/ld) | linear machine code |
| **Go** | compile (walk + SSA) | AST→SSA→machine code, a single `gc` binary does everything | linear machine code |
| **Rust** | codegen | MIR→LLVM IR→machine code | linear machine code |
| **Python** | compile | AST→bytecode, built-in `compile()` function | linear bytecode `.pyc` |
| **V8** | bytecode gen (Ignition) + optimizing compile (TurboFan) | AST→Ignition bytecode→TurboFan machine code | linear bytecode→machine code |
| **kvlang** | **layoutrwir** | AST→KV structured key-value `[s0,s1]` laid out two-dimensionally into kvspace | **tree-shaped KV keys**, addressable slot by slot |

All five languages produce a linear sequence. kvlang's layoutrwir is not serialization — it is **spatial layout**: each instruction expands into a set of `[s0,s1]` coordinates, read params on the negative axis, write params on the positive axis, opcode at zero. The result can be `kv.Get`/`kv.List`-ed slot by slot, with no disassembler needed.

### ExtIndex: frame root points at the instruction tree

On a function call, the instruction bytecode is not copied into a new frame — instead **ExtIndex** turns the frame root into an extended index pointing at the `/lib/` instruction tree:

```
Compile time (WriteFunc):
  AST → written into /lib/main.add/ as structured KV:
    /lib/main.add/[0,0] = "+"      /lib/main.add/[0,-1] = "A"
    /lib/main.add/[0,-2] = "B"     /lib/main.add/[0,1] = "C"
    /lib/main.add/[1,0] = "return"
    /lib/main.add                 = "rwfunc add(A:int,B:int)->(C:int)"  (signature)

Call time:
  kv.ExtIndex(frameRoot+"/", "/lib/main.add/")  # frame root itself → /lib/ instruction tree
  # all frames share the same instruction tree under /lib/, zero-copy
```

The execution mechanics of HandleCall/HandleReturn are covered in parser篇-06.

### Key differences from a traditional VM

| | Traditional VM | kvlang |
|--|---------|--------|
| Code passing | copy bytecode into a new stack frame | **ExtIndex** (all frames share the same instruction tree under /lib/) |
| Frame model | one frame type (call/return) | **Two frame types**: rwfunc (function call) + scope (goto/br; see the Control Flow chapter) |
| Crash recovery | stack frames live in memory; process death loses everything | PC is a path string; the return point lands in KV via frameRoot — restart and continue |
| Observability | requires a debugger attach | `kvspace tree /vthread/…` shows where the extindex points and where frameRoot is; local variables read directly from the frame root |

**The `=` opcode is a value copy, not a function call**: `a -> b` is encoded as `[s0,0]="="` (value copy), whereas a function call is `call(name, args…) → writes` — its opcode slot is `call`, and the ExtIndex happens inside HandleCall. The two are unambiguous at the KV layer: the opcode slot never holds a variable reference (§2.3).

## Implementation Consistency Notes

1. **Stage/package name: `layoutrwir` vs `layout`.** The doc calls the stage "layoutrwir". The Go package is `package layout` in `/home/peng.li24/github.com/array2d/kvlang/layout/layout.go` (import path `kvlang/layout`; the directory was renamed from `layoutrwir/` — commit `40a32c8` "layoutrwir/ → layout/ 重命名"). The package comment still reads "Package layoutrwir", and sibling docs (e.g. `parser篇-06-layoutrwir.md`) keep the old name. The functions the doc names — `WriteFunc`, `HandleCall`, `HandleReturn`, `ExtIndex` — all exist with exactly those names in `layout/layout.go` (`WriteFunc` L400, `HandleCall` L131, `HandleReturn` L228).

2. **Slot convention confirmed.** `WriteBody` (`layout/layout.go` L37) writes exactly the format the doc describes: opcode at `[i,0]`, reads at `[i,-j]`, writes at `[i,+j]` (j = 1, 2, …) — read params on the negative axis, write params on the positive axis, opcode at zero. Keys derive from `keytree.LibFunc(pkg, name)` = `/lib/<pkg>.<name>`, with `.` (`keytree.MemberSep`) as the separator — matching the doc's `/lib/main.add`.

3. **Signature value format.** The doc renders `/lib/main.add = "rwfunc add(A:int,B:int)->(C:int)"`. In `WriteFunc` (layout.go L400-409) the value is `kvspace.NewRwfunc(countDirectInsts(...), fn.Sig.NumReads(), fn.Sig.NumWrites(), fn.Sig.String())` — an XValue of kind `rwfunc` whose payload is the signature string `add(A:int,B:int)->(C:int)`. The leading `rwfunc` in the doc is the XValue kind tag, not part of the stored string. The doc's example also omits the source copy stored at `/lib/<pkg>.<name>.src` (`keytree.LibSrc`, layout.go L405).

4. **ExtIndex call matches.** In `HandleCall` (layout.go L167) the call is `kv.ExtIndex(keytree.Stack(frameRoot), funcKey+"/")` where `funcKey = keytree.LibFunc(pkg, name)`, i.e. `/lib/main.add/` for the example. Zero-copy sharing of a single `/lib/` tree across all frames is exactly what happens. Write-param zero-copy goes through `rparam`/`wparam` redirection (`keytree.RParam`/`keytree.WParam`) — a detail the doc defers to parser篇-06 rather than describing.

5. **`=` opcode = value copy: confirmed.** `ast.Instruction.Flat()` (`ast/ast.go` L282) returns opcode `"="` with the value reference in a read slot for leaf expressions, which is how `a -> b` (copy) is distinguished from a zero-arg call. `layout.go` L56-60/L98-102 guarantees the `=` opcode is stored unqualified via `symbol.Lookup("=").Word == "assign"`. Native ops such as `+` (registered by `registerWord("add", ...)` in `rwir/builtin/arith.go`, which also registers the glyph `+`) are likewise stored unqualified — consistent with the doc's `/lib/main.add/[0,0] = "+"`. Note that user-defined call opcodes are package-qualified at write time (`pkg + "." + opcode`) unless they are native, control, absolute-`/lib/`-prefixed, or `assign` — a detail the doc does not mention.

6. **Two-frame model: accurate.** "Two frame types: rwfunc + scope" matches the current design. `HandleCall`/`Bootstrap` create rwfunc frames; `HandleScope` (layout.go L525) creates scope frames as flat sibling subdirectories under the rwfunc frame without their own extindex; `HandleScopeReturn` (L546) performs the scope's implicit return. Dead code to be aware of: `HandleLabel` (L251) and `RegisterBlocks` (L321) still exist in `layout.go` but have no callers — the label/TCO mechanism was removed per todo-036 (recorded in `parser&runtime-01控制流篇.md`: "The old label/TCO/HandleLabel have been deleted").

7. **`call` opcode + control ops.** `rwir/control.go` defines `OpCall = "call"`, `OpReturn = "return"`, `OpBr = "br"`, `OpGoto = "goto"`. The doc's statement that a call's opcode slot is `call` and the ExtIndex happens inside HandleCall is accurate. `HandleReturn` (layout.go L228) reads `.returnpc`, then UnLinks/DelTrees the frame, and panics if the return instruction carries any read slots.

8. **Cross-references resolve.** "parser篇-06" and "控制流篇" both exist in the EN doc set (`parser篇-06-layoutrwir.md`, `parser&runtime-01控制流篇.md`).

9. **Supplement: per-instruction slot cap.** `rwir.Decode` (`rwir/rwir.go`) caps reads/writes at `maxParams = 128` per instruction (slots `[i,-128]`..`[i,128]`); beyond that it returns an error rather than silently truncating. The doc does not mention this bound.

10. **No stale claims found.** The layout algorithm (negative read axis / positive write axis / zero opcode), the `/lib/<pkg>.<name>` key shape, the `[i,j]` coordinate model, and the `kvspace tree /vthread/…` observability claim all match the current `layout.go`, `keytree/`, and `rwir/` sources as of 2026-08-06.
