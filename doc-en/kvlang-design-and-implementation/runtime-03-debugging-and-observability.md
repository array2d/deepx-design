# Debugging and Observability

 Updated 2026-07-30: `.funclib` → `.lib` (aligned with the scope-frame redesign).
> To be corrected section by section once the code-side refactor completes and the `kvspace tree` output stabilizes.

import ch10-system-variables

## Debugger in practice: statically observing the stack and source with kvspace

This section uses `tutorial/03-debugger/chain_array.kv` as an example and shows how to pause a program with `kvlang --debug`, then use `kvspace tree/list` to statically observe the vthread stack frames (`/vthread`) and the compiled source (`/lib`).

### Starting the debugger

```bash
cd kvlang
kvlang {vid} --debug tutorial/03-debugger/chain_array.kv
# The VM pauses at debugger(), and the process blocks waiting for agent commands
```

source:

```
rwfunc f3() -> (a:int64, s:int64) {
    debugger()          # ← pauses here
    at(a, 0) -> v0
    at(a, 1) -> v1
    v0 + v1 -> s        # 30 + 40 = 70
}

rwfunc f2() -> (a:int64, s:int64) {
    a:int64 = [30, 40]  # f2 creates an array into its write param
    f3() -> (a, s)      # passed to f3 as write params, no copy, same path directly
}

rwfunc f1() -> (s:int64) {
    f2() -> (_, s)      # receives s, discards the array
}

rwfunc init() -> () {
    f1() -> r
    print(r)            # 70
}
```

### Observing stack frames: `kvspace tree /vthread`

While paused, `kvspace tree /vthread` outputs (only the `{vid}` vthread and the relevant functions are kept):

```
/vthread/{vid}
├── .lib → rwfunc init() -> ()
│   ├── [0]  rwir:f1  rwir:r
│   ├── [1]  rwir:r   rwir:print
│   └── [2]  rwir:return
├── .rootfunc  string:init
├── .pc        string:/vthread/{vid}/[0,0]/[0,0]/[1,0]/[0,0]
├── .status    string:{vid}ning
├── .debugger  string:break
└── [0,0]                                    ← f1 call frame
    ├── .lib → rwfunc f1() -> (s:int64)
    │   ├── [0]  rwir:f2  rwir:_  int64:0
    │   └── [1]  int64:0  rwir:return
    ├── .rootfunc  string:f1
    ├── .rparam/s  → /vthread/{vid}/r          ← read param s → init's r
    ├── .wparam/s  → /vthread/{vid}/r          ← write param s → init's r
    ├── [0,0]                                ← f2 call frame
    │   ├── .lib → rwfunc f2() -> (a:int64, s:int64)
    │   │   ├── [0]  int:30  int:40  rwir:array  int64:0
    │   │   ├── [1]  rwir:f3  int64:0  int64:0
    │   │   └── [2]  int64:0  int64:0  rwir:return
    │   ├── .rootfunc  string:f2
    │   ├── .rparam/a  → /vthread/{vid}/[0,0]/_
    │   ├── .rparam/s  → /vthread/{vid}/r
    │   ├── .wparam/a  → /vthread/{vid}/[0,0]/_
    │   ├── .wparam/s  → /vthread/{vid}/r
    │   └── [1,0]                            ← f3 call frame (current pause position)
    │       ├── .lib → rwfunc f3() -> (a:int64, s:int64)
    │       │   ├── [0]  rwir:debugger       ← PC is here
    │       │   ├── [1]  int64:0  int:0  rwir:at  rwir:v0
    │       │   ├── [2]  int64:0  int:1  rwir:at  rwir:v1
    │       │   ├── [3]  rwir:v0  rwir:v1  rwir:+  int64:0
    │       │   └── [4]  int64:0  int64:0  rwir:return
    │       ├── .rootfunc  string:f3
    │       ├── .rparam/a  → /vthread/{vid}/[0,0]/_
    │       ├── .rparam/s  → /vthread/{vid}/r
    │       ├── .wparam/a  → /vthread/{vid}/[0,0]/_
    │       └── .wparam/s  → /vthread/{vid}/r
    └── _  int64[2]:30                       ← f1's discard slot, holds the array produced by f2
```

### Key findings

**The PC string IS the call stack.** PC = `/vthread/{vid}/[0,0]/[0,0]/[1,0]/[0,0]`, decoded segment by segment:

| Path segment | Meaning |
|--------|------|
| `/vthread/{vid}/` | vthread root frame (init) |
| `[0,0]/` | the call initiated by init instruction [0,0] → **f1 frame** |
| `[0,0]/` | the call initiated by f1 instruction [0,0] → **f2 frame** |
| `[1,0]/` | the call initiated by f2 instruction [1,0] → **f3 frame** |
| `[0,0]` | f3 instruction [0,0] = `debugger()`, the current execution position |

This is exactly isomorphic to a traditional VM's "stack depth = number of frames" — the only difference being that kvlang uses path depth rather than an integer offset.

**`.lib` soft link = zero-copy shared instruction tree.** The `.lib` of all four frames Link to `/lib/<name>`. Key point: the f3 frame appears in two places — `/vthread/{vid}/[0,0]/[0,0]/[1,0]` (f2's sub-frame) and inside the expanded f2 `.lib` tree — because `kvspace tree` follows soft links and expands the contents of `/lib/f3`. In reality, the KV store keeps only one copy of the instruction tree, and all frames share it through links.

**`.rparam` / `.wparam` implement zero-copy cross-frame parameter passing.** Note that the f3 frame has no local-variable slots — execution has not yet reached the creation of `v0`/`v1`. But the read/write-param routing is already established:

```
f3 .rparam/a → /vthread/{vid}/[0,0]/_    ← f1's discard slot (the array lives here)
f3 .rparam/s → /vthread/{vid}/r          ← init's r slot
f3 .wparam/a → /vthread/{vid}/[0,0]/_    ← written back to f1's discard slot (not kept)
f3 .wparam/s → /vthread/{vid}/r          ← written back to init's r (final result)
```

This is not "pass by value" — it is **path aliasing**. When f3 executes `at(a, 0)`, kvcpu obtains the absolute path `/vthread/{vid}/[0,0]/_` through `.rparam/a` and directly `Get`s that key — zero intermediate copies. Likewise, when f3 writes `s` it goes through `.wparam/s` to `Set` `/vthread/{vid}/r` directly.

**The array flows through the entire call chain and lands in the aligned write-param slot.** f2 creates `[30, 40]`, and its write param `a` is routed through f1's `.wparam/a` to `/vthread/{vid}/[0,0]/_` (f1's discard slot). At this point `kvspace get /vthread/{vid}/[0,0]/_` returns `int64[2]:30` (the tree shows the first element 30; in fact it contains two elements). f3's `.rparam/a` points to the exact same path — the array is stored only once in kvspace, and all frames share it through path aliases.

**`.rootfunc` keeps the root function name under TCO (Tail Call Optimization: `goto`/`br` reuse the current frame without creating a new one) semantics.** Each frame independently records `.rootfunc` (here f1/f2/f3 each record their own function name), and even when a frame is reused by TCO it is not overwritten — `resolveLabel` relies on it to resolve bare labels.

### Observing source: `kvspace tree /lib`

After pausing, `/lib/` already contains all the compiled artifacts of chain_array.kv:

```
/lib
├── init   string:rwfunc init() -> ()
│   ├── [0]  rwir:f1  rwir:r
│   ├── [1]  rwir:r   rwir:print
│   └── [2]  rwir:return
├── f1     string:rwfunc f1() -> (s:int64)
│   ├── [0]  rwir:f2  rwir:_  int64:0
│   └── [1]  int64:0  rwir:return
├── f2     string:rwfunc f2() -> (a:int64, s:int64)
│   ├── [0]  int:30  int:40  rwir:array  int64:0
│   ├── [1]  rwir:f3  int64:0  int64:0
│   └── [2]  int64:0  int64:0  rwir:return
├── f3     string:rwfunc f3() -> (a:int64, s:int64)
│   ├── [0]  rwir:debugger
│   ├── [1]  int64:0  int:0  rwir:at  rwir:v0
│   ├── [2]  int64:0  int:1  rwir:at  rwir:v1
│   ├── [3]  rwir:v0  rwir:v1  rwir:+  int64:0
│   └── [4]  int64:0  int64:0  rwir:return
├── .init.src  string:rwfunc init() -> () { … }   ← source copy
├── .f1.src    string:rwfunc f1() -> (s:int64) { … }
├── .f2.src    string:rwfunc f2() -> (a:int64, s:int64) { … }
├── .f3.src    string:rwfunc f3() -> (a:int64, s:int64) { … }
└── .srcmap    bytes:{"1":"tutorial/03-debugger/chain_array.kv", …}
```

**The `[s0,s1]` two-dimensional layout of KVC instructions is clearly visible.** Taking f3 as an example, there are three read-param instructions (`at` ×2 + `+`) and one zero-param `debugger` instruction:

```
       s1=-2   s1=-1   s1=0          s1=1
s0=0 │                debugger
s0=1 │                at           v0
s0=2 │                at           v1
s0=3 │ v0      v1      +            (int64:0)  ← temporary write slot, type int64
s0=4 │                return
```

Read params are not created in the current frame — `v0`/`v1` in the instruction slots are rwir text references, resolved at execution time via `frameSlotKey` into in-frame paths. Write params (`v0`/`v1`, and anonymous temporary slots) are only created when execution reaches that instruction.

**`/lib/.src` source copies.** The full lowered source of each function is stored as a string value under `/lib/.*.src` — this is why `kvspace tree` can display the source. `.srcmap` records the line-number → file-path mapping, used for error localization.

### Debugger protocol essentials

| Key | Direction | Mechanism | Value |
|----|------|------|-----|
| `.debugger` | agent→CPU (control) | String | `""` normal / `"step"` single-step / `"break"` pause at function entry |
| `.debugger.pause` | CPU→agent (event) | Notify | JSON `{"pc","func","frame","op"}` |
| `.debugger.resume` | agent→CPU (command) | Notify | `"step"` / `"continue"` / `"abort"` |

After pausing, the agent writes `kvspace notify /vthread/<vtid>/.debugger.resume continue` to resume execution.

### Common observation commands

```bash
# Full view of the stack frames
kvspace tree /vthread/

# Current PC
kvspace get /vthread/{vid}/.pc

# Current frame (truncate frameRoot by PC, then tree)
# PC=/vthread/{vid}/[0,0]/[0,0]/[1,0]/[0,0] → frameRoot=/vthread/{vid}/[0,0]/[0,0]/[1,0]
kvspace tree /vthread/{vid}/[0,0]/[0,0]/[1,0]

# Read/write param routing of a frame
kvspace tree /vthread/{vid}/[0,0]/[0,0]/[1,0]/.rparam
kvspace tree /vthread/{vid}/[0,0]/[0,0]/[1,0]/.wparam

# All loaded functions
kvspace tree /lib

# Instruction tree of one function
kvspace tree /lib/f3

# Source copy
kvspace get /lib/.f3.src
```

## Implementation Consistency Notes

The following notes record discrepancies found while cross-checking this document against the Go source (as of the current working tree; the doc header itself notes that it awaits a code-side refactor). Verified by building the module in a throwaway copy and running `tutorial/03-debugger/chain_array.kv` with the in-process `art://` backend.

**1. The doc's trees are not current `kvspace tree` output.**
- Actual output is **flat**: a frame root is an extindex entry rendered inline in its call instruction's `[0,0]` cell, e.g. `[0,0~1] rwir:f1 extindex:(6) …/lib/f1/ rwir:r`. There is no nested per-frame subtree with `.lib`/`.rootfunc`/`.rparam` blocks as drawn in the doc.
- Runtime member keys are stored with the U+2025 separator `‥` (keytree/const.go `RuntimeMemberSep`), not `.` — the live tree shows `‥lib`, `‥pc`, `‥status`, `‥debugger`, `‥callpc`, `‥returnpc`, `‥rparam/`, `‥wparam/`.
- `kvspace tree` row order for 2D `[s0,s1]` rows is s0-descending in the current renderer, not ascending as shown.
- The compiled body has **no explicit `return` instruction** — rwfunc returns are implicit (empty-opcode row). The doc's `… rwir:return` rows do not appear in the live output.

**2. `.rootfunc` does not exist in the code.** No `rootfunc` key is written anywhere (grep of all Go sources). TCO label resolution in `layout.HandleLabel` walks the ancestor frame chain comparing path segments; the `resolveLabel` function named in the doc does not exist, and `HandleLabel` is currently not invoked (goto/br go through `layout.HandleScope` instead).

**3. `.lib` value.** The `.lib` frame member stores the function-key path string (e.g. `‥lib string /lib/f3`), not the rendered signature `rwfunc f3() -> (...)`. The zero-copy instruction sharing the doc attributes to "`.lib` soft links" is actually the frame-root `ExtIndex` (frameRoot/ → `/lib/<name>/`), which is verified working.

**4. The doc's source excerpt and trees reflect an older tutorial.** The current `tutorial/03-debugger/chain_array.kv` starts f3 with `println(a)` before `debugger()`, uses array-index syntax `a[0] -> v0`/`a[1] -> v1` (compiled to op `at`), and uses `println(r)` in init. Consequently the compiled f3 is `[0] println / [1] debugger / [2] at(a,0) / [3] at(a,1) / [4] +`, and the PC at the pause is `/vthread/{vid}/[0,0]/[0,0]/[1,0]/[1,0]` — the doc's PC `/vthread/{vid}/[0,0]/[0,0]/[1,0]/[0,0]` is the old numbering. The frame chain (`[0,0]→f1, [0,0]→f2, [1,0]→f3`) and its "PC string = call stack" interpretation are confirmed correct.

**5. The array zero-copy flow is not reproducible in current code — the tutorial errors out.** Running the current tutorial fails with `IndexError: at: base a is None`. Cause: `arrayOp` writes the array via `writeSlotKey` to the callee's **local** frame slot (`/vthread/1/[0,0]/[0,0]/a`), NOT through the `.wparam` redirect to `/vthread/1/[0,0]/_` that the doc describes. `resolveReadValue` reads `a` via `.rparam/a` → `/vthread/1/[0,0]/_`, which stays empty. Only `=`-copy and arithmetic/other ops that go through `resolveWriteSlot`/`writeResult` (which do consult `.wparam`) deliver values to the caller's slot — a scalar write-param chain (`42 -> s`, `inner() -> (s)`, `outer() -> r`) was verified to land `42` in the caller's `r`. So the "same-path, zero-copy" claim holds for `=` copies and scalar results but fails for the array-creation op (`array`). The `.rparam`/`.wparam` redirect **paths** themselves match the doc exactly (f3 `.rparam/a` → `/vthread/{vid}/[0,0]/_`, `.rparam/s` → `/vthread/{vid}/r`, etc., verified live).

**6. Source-copy keys have no leading dot.** Live keys are `/lib/init.src`, `/lib/f1.src`, `/lib/f2.src`, `/lib/f3.src` (kind `string`, written by `layout.WriteFunc` via `keytree.LibSrc`). The doc's `.init.src`/`.f1.src`/... and its `kvspace get /lib/.f3.src` example are off by the leading dot.

**7. `.srcmap` does not exist.** No `srcmap` key is written anywhere in the current code. (An old `todo-032` issue referenced `/lib/.srcmap` in a now-deleted `cmd/kvlang/layoutrwir.go`; that artifact is gone.)

**8. `.debugger = "break"` semantics.** The doc's table says `"break"` = "pause at function entry". In the implementation (`kvcpu/execute.go`), the Execute loop reacts only to `mode == "step"` (activates stepping at entry points and pauses every instruction); `"break"` does nothing in the loop. Any non-`None` `.debugger` value enables the `debugger()` builtin (`rwir/builtin/debugger.go`) to pause — the CLI (`cmd/kvlang/run.go`) sets `"break"`, which is what makes the tutorial's `debugger()` stop. So `"break"` ≈ "enable `debugger()` breakpoints", not a function-entry pause.

**9. `.debugger.pause` JSON differs by pause source.** The stepping path in `kvcpu/debug.go` notifies `{"pc","func","frame","op"}` (matches the doc's table). The `debugger()` builtin path (the one the tutorial actually hits) notifies `{"pc","vtid","opcode","func","frame"}` as a Bytes value, with `"func":""` — fields differ from the doc.

**10. `.debugger.resume` handling.** Commands `"step"`/`"continue"`/`"abort"` are all handled (both paths); `continue` deletes the `.debugger` key. A comment in `execute.go` says `.debug.resume`, but the real key is `‥debugger.resume` (the doc's `.debugger.resume` is right except for the `‥` separator).

**11. CLI invocation.** The doc's `kvlang {vid} --debug tutorial/...` is shorthand; the actual CLI is `kvlang run [--debug] <file>` (the vtid is auto-allocated via `vthread.AllocVtid`). The `debugger()` builtin is a no-op only when the `.debugger` key is absent (`IsNone`), not merely when its value is `""` — an empty-but-present key would still pause it.

**12. Type-tagged slot rendering.** The doc's `[s0,s1]` diagram and trees annotate some read/write slots as `int64:0`/`int:0`/`(int64:0)`. Actual slots store rwir references (`rwir:a`, `rwir:s`, `rwir:v0`) for identifiers and `int64:0`/`int64:1` (KindInt64) for integer literals — there is no `int:` kind; a literal `0` is stored as `int64:0`. The doc's `rwir:print` (f1's `[1]`) is `rwir:println` in the current tutorial.

**13. Confirmed-accurate parts.** The PC-as-call-stack decoding, the frame chain, the `.rparam`/`.wparam` redirect path values, the `init|running|wait` status lifecycle with terminal `Del+Notify`, the `debugger()`/`step`/`continue`/`abort` resume protocol, the read-params-are-references/write-params-created-on-execution model, and the extindex-based shared instruction tree are all confirmed against the implementation.
