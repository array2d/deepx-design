# Functions


## Functions Have No Return Value: Only Read Params and Write Params

### Core Principle

kvlang functions **have no return value**. This is the fundamental difference from almost all other languages.

Traditional languages:
```python
result = add(a, b)        # the function "returns" a value, assigned to result
```

kvlang:
```kv
add(a, b) -> result   # -> result is a write-param mapping: callee write param C lands at this frame's result position
add(a, b)             # no receiver: write param C is written only into the child frame (pure side effect)
```

kvlang functions have exactly two kinds of parameters:

| Parameter Type | KV Slot | Direction | Description |
|---------|---------|------|------|
| **Read Params** | `[s0, -1], [s0, -2], ...` | caller → callee | the function's inputs, bound to values at call time |
| **Write Params** | `[s0, +1], [s0, +2], ...` | callee → caller | the function's outputs, written back to the parent frame on return |

---

### A Write Slot Is a Position

```kv
rwfunc add(A: int, B: int) -> (C: int) {
    A + B -> C            # write param C is written into the callee frame
}
```

The `-> (C: int)` in the function signature is a **write-param declaration**, not a "return value type".

A write slot (right of `->` / left of `<-`) must be a **position** (pointer, §8):

```kv
# ✅ the three forms of a position
add(a, b) -> s            # bare name s —— this-frame position (frame root + "/s")
add(a, b) -> /global/s    # absolute path —— global position
add(a, b) -> obj.prop     # member expression —— key-family member position (§10)

# ✅ multiple write-param mappings: comma-separated, one-to-one with the signature's write params
divmod(17, 5) -> q, r      # q←quotient, r←remainder
addmul(x, y) -> s, p       # s←x+y, p←x*y

# ❌ a literal is not a position
add(a, b) -> 42
add(a, b) -> "s"
```

`-> s` does not mean "the function's return value is assigned to s" — it is a **cross-frame path mapping of a write param** (§3.3): the callee frame's write param C is written by HandleReturn to the s position of the caller frame. Semantically, there is still no such thing as a "return value."

**Read-param read-only axiom (fix-027)**: read params are an input binding from "caller → callee". Putting a read param's bare name into a write slot inside the function body (including write-param mappings of sub-function calls and `for` iteration variables) breaks the direction of the rwir data flow — there is a two-stage interception:
- The parser issues an error-level diagnostic that rejects loading;
- The kvcpu write-slot pre-check (the frame's `.ro` list, written by Bootstrap/HandleCall) terminates with SetError.
- Member writes (`A.x = v`, desugared to a `set` body write-back) do not write the body itself and are exempt.
- Note: all five languages allow parameter reassignment when passing by value — kvlang is stricter, because read-param slots are the direction anchors of the IR's data-flow analysis (§2.6). This is kvlang's self-consistency requirement as a single-layer IR, not a behavioral alignment item (p7 exception, explicitly recorded).

**Corollary: if a parameter is both a read param and a write param, then it is a write param.** A write param can be read and written inside the body; the write-param role overrides the read-param role. The real purpose of the axiom is to **force the signature to be honest** — if you write to some parameter in the function body, it is not a pure input, and the signature must place it on the write-param side:

```kv
# ❌ dishonest signature: acc is written but placed on the read-param side → error
rwfunc sum(arr, acc:int) -> (r:int) { acc + arr[0] -> acc }

# ✅ honest signature: acc is a write param → readable and writable (must be explicitly initialized; first read of None rejects arithmetic)
rwfunc sum(arr) -> (acc:int) { acc + arr[0] -> acc }
```

| Variable Type | Signature Position | Form |
|---------|---------|------|
| **Accumulator / output** (the caller wants the final value) | write param | `acc + x -> acc` (readable and writable in body) |
| **Pure working variable** (the caller does not need it) | does not appear | copy to a local on the first line: use `a` after `A -> a` |
| **Pass-through across layers** (recursive lower-layer input) | read param | this layer only reads: `rsum(arr, i+1, acc + arr[i]) -> r` |

### Same-Name Rule for Definition and Call Parameters

**When defining a function, read params and write params must not share the same name.** A variable name is a pointer — two parameters with the same name in the same frame point to the same kvspace position, so a read param and a write param sharing a name would make one slot both input and output, breaking the direction of the rwir data flow. `rwfunc f(A:int) -> (A:int)` is itself an illegal signature — the parser's `checkParamDup` rejects loading, and the VM's `checkDupParams` is the backstop for illegal signatures constructed by agents writing KV directly.

**When calling a function, the same variable may appear in both a read slot and a write slot.** `f(x) -> x` is fully legal — x is passed as an actual argument into the read slot (consuming the old value) while simultaneously serving as the write-slot target that receives the result (producing a new value). These are two different kvspace paths: the read slot `.rparam/x` points to the caller's x, and the write slot `.wparam/x` also points to the caller's x — when HandleCall builds the routing, the two are resolved independently and do not conflict. The typical use case is an in-place update:

```kv
rwfunc inc(val:int) -> (out:int) { val + 1 -> out }

x = 5
inc(x) -> x    # ✅ legal call: x appears in both the read slot and the write slot; x changes from 5 to 6
```

Same-name is forbidden at definition (`checkParamDup`); same-variable is allowed at the call site (routing is independent).

### Current State of Parser Write-Slot Validation

`collectWriteList` already performs write-slot validity checks:

- Literal write slot (starts with a number / quoted string) → warning "unexpected token in write slot position"
- Write slot immediately followed by `(` → warning "function call on same line as write slot" (a second instruction on the same line)
- `./x` has been abolished: `.`-starting write-slot tokens trigger the same warning; only bare names, `/abs`, and `base.名` pass

### Why There Is No Return Value

The "return value" of traditional languages is essentially **a block of memory on the call stack** — when the function finishes, the value in that memory is copied to the caller.

kvlang has no linear call stack and no "return value" — a function call `f(args) -> s` is a **cross-frame path mapping of write params**: the callee frame's write params are written by HandleReturn directly through `.wparam` to the target path in the caller frame. The entire process is only data flow between slots; there is no such concept as a "return value."

---

## Implementation Consistency Notes

Cross-check of this document against the Go source in `/home/peng.li24/github.com/array2d/kvlang/` (as of commit `e6f251a`, latest in `git log` for `layout/layout.go`).

### Confirmed accurate

- **Slot convention** `[s0, -1], [s0, -2]` (reads, negative axis) / `[s0, +1], [s0, +2]` (writes, positive axis) / `[s0, 0]` (opcode) — matches `layout/layout.go` `writeStmt`/`writeStmtScope` (`[n,-(j+1)]` for reads, `[n,j+1]` for writes, `[n,0]` for opcode).
- **`checkParamDup`** — exists in `parser/parser.go` (comment references `fix-032`); rejects `rwfunc f(A) -> (A)`.
- **`checkDupParams`** (VM backstop) — exists in `layout/layout.go:554`, called from both `HandleCall` (line 158) and `Bootstrap` (line 356).
- **Read-only axiom two-stage interception (fix-027)** — parser `checkReadOnlyParams` (`parser/parser.go:341`, also covers the `for` iteration variable) emits error-level diagnostics; runtime `checkReadOnlyWrites` (`kvcpu/execute.go:202`, invoked at execute.go:139) checks the frame `.ro` list and terminates via `vthread.SetError`.
- **`.ro` list writers** — `FrameRO` is written by both `HandleCall` (`layout/layout.go:221`) and `Bootstrap` (`layout/layout.go:372`), as the doc states.
- **Write-slot warning messages** — `collectWriteList` (`parser/inst.go:389`) emits exactly the two warnings named in the doc: `"unexpected token %q in write slot position — ..."` and `"function call %q on same line as write slot — ..."`. A `.`-starting write-slot token (`./x`) does trigger the warning path.
- **Definition-side same-name forbidden / call-side same-variable allowed** — `checkReadOnlyParams` only inspects function bodies, and `HandleCall` resolves the read-arg path and the write-target path independently (`resolveReadPath` on `arg.Name` and on `wTarget`), so `inc(x) -> x` is legal exactly as described.

### Discrepancies (doc vs. current implementation)

1. **Write params are NOT copied by `HandleReturn`; the redirect is established by `HandleCall` at call time.**
   The doc states twice (the "Write Slot Is a Position" section and the final "Why There Is No Return Value" section) that "被调方帧的写参 C，由 HandleReturn 写到调用方帧的 s 位置" / "被调方帧的写参由 HandleReturn 经 `.wparam` 直写调用方帧的目标路径", and the parameter table says write params are written back to the parent frame "on return" (return 时写回父帧). The actual mechanism is a **zero-copy redirect**:
   - `HandleCall` (`layout/layout.go:206-219`) resolves each write param's caller-side target path and writes BOTH the `.rparam/<name>` and `.wparam/<name>` redirect keys pointing to it — at call time, not at return.
   - During execution, when the callee writes a write-param slot, `resolveWriteSlot` (`rwir/builtin/helper.go:72-79`) first checks the `.wparam` redirect and writes directly into the caller's target path.
   - `HandleReturn` (`layout/layout.go:228-246`) only reads `.returnpc` and tears down the frame; it copies nothing.
   In short: the `.wparam` path is correct, but the actor is wrong — the doc should say "by HandleCall's `.wparam` redirect, written when the callee executes the write", not "by HandleReturn on return". The parent-frame write happens when the callee executes its write instruction, not on return.
   (If the caller provided no write mapping, e.g. `add(a, b)` with no `->`, no redirect is set and `resolveWriteSlot` falls back to writing into the callee frame's stack — matching the "pure side effect" case in the doc.)

2. **Minor wording drift on the write-slot validation claims.** The doc says "写槽后紧跟 `(` → warning「function call on same line as write slot」（同行第二条指令）" — accurate. Note the actual warning is emitted as a `Warn`-level `Diagnostic` and the write list is then abandoned (returns partial list); it is not a hard error. This nuance is not stated in the doc but is not contradicted by it.

3. **Doc cross-references** (`§8`, `§10`, `§3.3`, `§2.6`, `p7`) are preserved as-is; they point to other documents in the design series and were not independently verified here.

No renamed functions or changed KV paths were found relative to the doc: `HandleCall`, `HandleReturn`, `checkParamDup`, `checkDupParams`, `collectWriteList`, `checkReadOnlyParams`, `checkReadOnlyWrites`, the `/lib/` root, and the `.rparam`/`.wparam`/`.ro` frame members all exist under the names the doc uses.
