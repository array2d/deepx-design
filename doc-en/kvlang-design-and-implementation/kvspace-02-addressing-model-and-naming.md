# The Address Space and Naming（寻址模型与命名）

## The Addressing Model: KV Path vs. Memory Address

### Traditional VMs (Python/Lua/JVM)

```
program counter PC = 0x7fff5fbff830 (64-bit memory address)
instruction    = memory[PC] → 1-byte opcode → operands
jump           = PC = new address (directly modify the register)
call           = push return address → PC = function entry address
stack frame    = contiguous memory [rbp-8] = local variable
```

A memory address is a **one-dimensional linear integer**; jumps and calls are, at bottom, integer arithmetic.

### kvlang

```
program counter PC = "[0,0]/entry/[0,0]" (KV path string)
instruction    = kv.Get("/vthread/tid/[0,0]/entry/[0,0]")
jump           = PC = "[0,0]/merge/[0,0]" (string concatenation)
call           = PC = "[0,0]/then/[0,0]" (path nesting)
stack frame    = /vthread/tid/[0,0]/ subtree (KV key hierarchy)
```

A KV path is a **tree-shaped hierarchical string**; jumps and calls are, at bottom, path concatenation + subtree navigation.

| Dimension | x86/ARM | Python | Lua | kvlang |
|------|---------|--------|-----|--------|
| PC type | `uint64` | `*PyCodeObject + offset` | `Instruction*` | `string` (KV path) |
| Instruction fetch | `mov rax, [rip]` | `_PyEval_EvalFrameDefault` loop | `luaV_execute` loop | `kv.Get("/vthread/tid/" + pc)` |
| Jump | `jmp 0x400100` | `next_instr += oparg` | `pc++` | `pc = new_path` |
| Call | `call 0x400200` | `call_function` pushes stack | `luaD_precall` | `pc = pc + "/[0,0]"` |
| Stack frame | `push rbp; sub rsp, N` | `PyFrameObject` (heap-allocated) | `CallInfo + L->stack` | `/vthread/tid/<pc>/` KV subtree |
| Scope | stack offset | `f_localsplus` array | register index | KV key subpath (bare names `x`, `y`) |

## A Variable Name Is a Pointer

kvlang has no `&` address-of operator — **the name of an object variable in the code is itself that variable's pointer** (a kvspace path). What an instruction slot stores is never the value but the pointer text (`[0,-1] = "A"`, `[0,1] = "C"`); evaluation always goes through one level of pointer indirection.

Pointers come in two forms:

| Form | Spelling | Semantics | When resolved |
|------|------|------|---------|
| **Relative pointer** | bare identifier `x` | offset relative to the current stack frame | concatenated with the stack path at runtime |
| **Absolute pointer** | `/counter` | kvspace global absolute path | zero concatenation; direct Get/Set |

**The name of a local variable is a relative pointer.** Runtime resolution formula:

```
absolute path pointer = FrameRoot(PC) + "/" + relative pointer

example: PC = /vthread/7/[3,0]/[1,0]
    FrameRoot(PC) = /vthread/7/[3,0]      ← drop the trailing /[coord]
    x → /vthread/7/[3,0]/x
```

The stack path (frame root) needs no separate register — the PC itself is a path inside the frame (the frame root is itself an extindex pointing at the `/lib/` instruction tree), and `FrameRoot(PC)` is obtained by truncation. This is isomorphic to C's `rbp + offset`:

| | C/x86 | kvlang |
|--|-------|--------|
| Frame base | `rbp` register | `FrameRoot(PC)` — truncated from PC |
| Local variable address | `rbp - 8` (base + offset) | `frame root + "/x"` (stack path + relative pointer) |
| Global variable address | fixed `.data` address | absolute pointer starting with `/`, zero concatenation |
| Pointer variable | stores an integer address | stores a path string: `"/n0" -> ptr`, dereference with `ptr.val` |

Only relative pointers appear inside the function templates under `/lib/`, so they are naturally reentrant: every call creates a different frame path, and the same relative pointers concatenate into mutually non-interfering absolute pointers — recursion and TCO (Tail Call Optimization: `goto`/`br` create no new frame) need no extra mechanism.

This explains why the global variable `/counter` is zero-cost — absolute pointers skip the frame-prefix concatenation. It also explains why arrays can be passed as arguments — `flattenNestedCalls` expands `[1,2,3]` into temporary variables, then passes the temporaries (which hold the XValue) as ordinary arguments.

**Parameters may not share a name (fix-032)**: a variable name is a pointer — two same-named parameters in one frame would point to the same kvspace location. Names must not be duplicated within the read-param list, within the write-param list, or across the two lists. A signature like `rwfunc f(A:int) -> (A:int)` is itself illegal — A cannot be both a read param and a write param. The parser's `checkParamDup` blocks this on the source-code path; the VM's `checkDupParams` backstops illegal signatures that an agent constructs by writing KV directly. error_case anchor: `tutorial/error_cases/read_only/dup_param.kv`.

## Implementation Consistency Notes

Cross-checked against the Go implementation under `/home/peng.li24/github.com/array2d/kvlang/` (`keytree/`, `layout/layout.go`, `rwir/`). The document is broadly accurate; the following points should be noted:

1. **The illustrative PC examples `[0,0]/entry/[0,0]`, `[0,0]/merge/[0,0]`, `[0,0]/then/[0,0]` are label-like, not literal reserved paths.** In the implementation there is no fixed `entry` segment. A function call creates the child frame directly at the call instruction's own PC (`callPC = 子帧根` in `layout.HandleCall`), so a call at `/vthread/7/[3,0]` yields frame root `/vthread/7/[3,0]` and entry PC `/vthread/7/[3,0]/[0,0]`. Label frames (from `goto`/`br` and named basic blocks) are nested as `<currentFrame>/<label>/` (`layout.HandleLabel`), giving exactly the `[0,0]/then/[0,0]` shape for a label named `then`. The conceptual claim — PC is a nested KV path string and jump/call are path concatenation + subtree navigation — holds.

2. **Instruction fetch is a batched `kv.Get`, not a single `kv.Get(pc)`.** `rwir.Decode` reads each instruction with one batch `Get` of up to `1 + 2*maxParams` (257) keys: the opcode at `[addr0,0]`, reads at `[addr0,-j]`, writes at `[addr0,+j]`, constructed relative to the frame's linkBase; for scope frames the keys carry a dot-joined scope-chain prefix (`scopePrefixAndBase`). The doc's `kv.Get("/vthread/tid/" + pc)` captures the concept.

3. **"Slots never store values, only pointer text; evaluation always goes through one pointer indirection" is an overstatement for literals.** `layout.slotValue` writes typed literal values (string/int/bool/float XValue kinds) directly into slots; only variable references are stored as pointer text (`KindRwir`). `builtin.resolveReadValue` returns literal params directly without indirection; only rwir-reference params are dereferenced (by their name — absolute names directly, relative names through the frame). "Always one pointer indirection" holds for variable references, not for literals.

4. **Relative-pointer resolution is slightly richer than `FrameRoot(PC) + "/" + name`.** `builtin.resolveReadValue` resolves a relative name by: (a) locating the nearest rwfunc frame (`funcFrameRoot`, found via the frame's `.lib` marker, not merely `FrameRoot(pc)`); (b) checking the read-param redirection `frameRoot‥rparam/<name>` first; (c) falling back to the local slot `Stack(rwRoot)+name`. Local variables of label/scope frames escape to the enclosing function frame. Also note `keytree.FrameRoot` panics when the PC has no `/[` segment. The doc's example `x → /vthread/7/[3,0]/x` matches the code for a plain function frame with no param redirection.

5. **Frame metadata keys use the U+2025 separator `‥` (`keytree.RuntimeMemberSep`)**, e.g. `frameRoot‥returnpc`, `frameRoot‥callpc`, `frameRoot‥rparam/<name>`, `frameRoot‥wparam/<name>`, `frameRoot‥lib`. The doc's "frame is a KV subtree" claim is accurate; this separator detail is not mentioned in the doc.

6. **Duplicate-param checking (fix-032) is implemented in two layers, with slight asymmetries.** The parser `checkParamDup` (`parser/parser.go`) blocks a name appearing in both read-params and write-params. The VM `checkDupParams` (`layout/layout.go`) additionally flags duplicates within the read-param list (message `duplicate read-param`); a duplicate inside the write-param list is detected but reported with the misleading message `param "..." appears in both read-params and write-params`. The error-case anchor `tutorial/error_cases/read_only/dup_param.kv` exists and its expected message matches.

7. **Absolute paths are recognized as references, not literals.** `layout.isLiteral` returns true for `/...`, but `slotValue` still emits a `KindRwir` reference for such strings, and `builtin.resolveReadValue`/`writeSlotKey` treat a leading `/` as an absolute path and Get/Set it directly with zero concatenation — consistent with the doc's "global variable zero cost" claim.

8. **`flattenNestedCalls` detail**: it lives in `lower/lower.go` (with `flattenExpr`), generating anonymous temp slots named `_N` (e.g. `_3`, `_4`) for nested non-leaf arguments, appending temp-writing instructions before the call. Matches the doc.

9. **Features present in the code but not covered by this doc** (newer or out of scope for addressing/naming):
   - Named basic-block ("scope") frames: `layout.HandleScope`/`HandleScopeReturn` and flat instruction keys `/lib/<func>/<scope>[coord]` (`writeStmtScope`), with scope-chain prefixes in `Decode`.
   - Per-instruction slot cap `maxParams = 128` (`rwir/rwir.go`); `Decode` errors when exceeded rather than truncating.
   - Frame-type detection helpers `extKind`/`ExtKind` (rwfunc frame = frame carrying a `.lib` marker).
   - Additional keytree path families not referenced here: `/sys/op/<backend>/<n>` (`keytree.SysOp`), `/sys/rwir/<op>` (`SysRwir`), `/dev/tty/<name>/<stream>` (`DevTTY`), and `.src` source copies under `/lib/` (`LibSrc`, fix-034).
   - vtid allocation: `/vthread/‥seq` atomic counter (`AllocVtid`) or a nanosecond timestamp (`CreateVThread`); the doc's `7`/`tid` examples are valid decimal vtid spellings.
