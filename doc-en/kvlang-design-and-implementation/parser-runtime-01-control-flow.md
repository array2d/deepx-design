# Control Flow: The Scope Frame Model

> Updated 2026-07-30: todo-036 scope frame redesign. The old label/TCO/HandleLabel have been deleted.

# Part 1: Parser & Layout

## Lower Pass

Lower lowers `if`/`while`/`for` into ScopeStmt + `br`/`goto`. goto/br targets use short names (e.g. `_while_2`).

```
# source                                   # after lower
while (found == 0) {                      goto(_while_2)
    s = lo + hi                           _while_2: {
    int(s/2) -> mid                           found == 0 -> _1
    tries = tries + 1                         br(_1, _do_3, _exit_4)
    mid == target -> hit                  }
    if (hit) { ... }                      _do_3: {
    else {                                    lo + hi -> s
        too_low = mid < target                tries + 1 -> tries
        if (too_low) {                        mid == target -> hit
            mid + 1 -> lo                     goto(_if_5)
        }                                 }
}                                         _if_5: {
                                              br(hit, _then_6, _else_7)
                                          }
                                          _then_6: { ... }
                                          _else_7: {
                                              mid < target -> too_low
                                              goto(_if_9)
                                          }
                                          _if_9: {
                                              br(too_low, _then_10, _else_11)
                                          }
                                          _then_10: { mid + 1 -> lo; goto(_merge_12) }
```

- All ScopeStmts are flat sibling nodes at function top level
- `splitInstsAndBlocks` separates instructions from blocks; `injectGotoBlocks` fills in the gotos that become missing after blocks are hoisted
- break/continue → `goto exitLabel` / `goto condLabel`

## WriteFunc

```go
func WriteFunc(kv kvspace.KVSpace, pkg string, fn *ast.Func) {
    kv.DelTree(keytree.LibFunc(pkg, fn.Sig.Name))
    kv.Set([]kvspace.KVPair{
        {keytree.LibFunc(pkg, fn.Sig.Name), kvspace.Rwfunc(...)},
    })
    WriteBody(kv, pkg, fn.Sig.Name, fn.Body, typeMap)
    // RegisterBlocks no longer needed — scope frames build no extindex; instructions are
    // looked up through the parent frame's extindex plus a scope-prefixed key
}
```

## Instruction Layout

**Function instructions**: `/lib/<func>/[i,j]` (standard format, resolved by extindex)

**Scope instructions**: `/lib/<func>/<scopeName>[i,j]` (flat key, the scope name is concatenated directly in front of the coord)

```
/lib/sum_to/[0,0]              ← function-body instruction
/lib/sum_to/[1,0]              ← goto _while_2
/lib/sum_to/_while_2[0,0]      ← scope condition block
/lib/sum_to/_while_2[1,0]      ← br(bool, _do_3, _exit_4)
/lib/sum_to/_do_3[0,0]         ← scope loop body
```

---

# Part 2: Runtime

## Frame Types

| | rwfunc frame | scope frame |
|--|----------|---------|
| Created by | `call(func, args...)` | `goto(label)` / `br(cond, t, f)` |
| extindex | Yes → /lib/<func>/ | No extindex |
| `.rparam` / `.wparam` | Yes | No |
| `.lib` marker | Yes | No |
| `.returnpc` / `.callpc` | Yes | Yes |
| Implicit return | DelTree the whole frame | DelTree itself; variables inaccessible to the parent scope |
| Frame location | callPC | Flat sibling subdirectory under the rwfunc frame |

```
/vthread/1/[0,0]/                   ← rwfunc frame (created by call)
├── .lib = /lib/sum_to              ← rwfunc marker (used for funcFrameRoot detection)
├── .rparam/n → ...                 ← read-param zero-copy
├── .wparam/total → ...             ← write-param zero-copy
├── .returnpc / .callpc
├── total = 0, i = 1                ← local variables (all variables live in the rwfunc frame)
├── _while_2/                       ← scope frame (created by goto/br, flat sibling)
│   ├── .callpc                     ← updated on every re-entry
│   └── .returnpc                   ← set on first entry, never overwritten
├── _do_3/                          ← scope frame (flat sibling)
│   ├── .callpc
│   └── .returnpc
└── _exit_4/                        ← scope frame (flat sibling)
```

## Variable Scoping

**All variables are stored in the rwfunc frame Stack.** `funcFrameRoot` walks the frame tree upward looking for the `.lib` marker to identify rwfunc frames.

- Write: `resolveWriteSlot` first checks the `.wparam` redirection; otherwise writes to the rwfunc Stack
- Read: `resolveReadValue` first checks the `.rparam` redirection; then the rwfunc Stack
- scope frames store no variables; on scope exit the scope frame DelTrees itself
- a child scope can access the parent's variables (all in the rwfunc frame); after a scope exits, its variables are inaccessible to the parent (the scope frame has been deleted)

## Decode: scope-aware key construction

scope frames build no extindex, so Decode manually concatenates a scope-prefixed key:

```go
func Decode(kv, linkBase, pc string) (*Rwir, error) {
    scopePrefix, lookupBase := scopePrefixAndBase(linkBase)
    // scopePrefix: "" (rwfunc frame) or "_while_2" / "_while_2._if_1" (nested)
    // lookupBase: rwfunc frame Stack (for extindex resolution)

    key := scopePrefix + "[0,0]"
    kv.Get(lookupBase, [key, ...])
    // extindex → /lib/func/_while_2[0,0]
}
```

## HandleScope (goto/br)

```go
func HandleScope(kv, pc, scopeName string) string {
    rwRoot := rwfuncFrameRoot(kv, FrameRoot(pc))
    scopeFrame := rwRoot + "/" + scopeName + "/"

    if !exists(scopeFrame + ".callpc") {
        MkIndexRecursive(scopeFrame)
        // scope frames build no extindex
    }
    // .returnpc is set only on first entry, never overwritten (preserves the original return path)
    // .callpc is updated on every entry
    return EntryPC(scopeFrame)
}
```

**Key point**: scope frames are all flat sibling subdirectories of the rwfunc frame and do not reuse the extindex. `.returnpc` is set only on first entry to prevent a wrong while-loop exit path.

## HandleScopeReturn

Scope-frame implicit return: read `.returnpc` → DelTree itself → return to the parent context.

## System Variables

| Variable | Location | Responsibility |
|------|------|------|
| `.pc` | vthread level | external view |
| `.callpc` | every frame | execution progress of this frame |
| `.returnpc` | every frame | return address |
| `.lib` | rwfunc frame | extindex target path (used for funcFrameRoot detection) |

## HandleCall

1. frameRoot = callPC, ExtIndex → /lib/<func>/
2. write `.lib` = funcKey, `.returnpc`, `.callpc`
3. read params zero-copy: `.rparam/<name>` → the caller's value location
4. write params zero-copy: read the write target directly from inst.Writes (not looked up from the frame path)

## Bootstrap

vthread root `/vthread/1/`, extindex → the entry function. Write `.lib` = funcKey.

## Key Differences from a Traditional VM

| | Traditional VM | kvlang |
|--|---------|--------|
| Code passing | copy the bytecode | **ExtIndex** (shared /lib/ instruction tree) |
| Frame model | one frame type | **Two frame types**: rwfunc + scope |
| rwfunc-frame detection | — | **`.lib` marker** (avoids extindex-cascade interference) |
| Variable scoping | lexical scoping | **centralized in the rwfunc frame**, located by funcFrameRoot |
| scope instruction lookup | — | **Decode scope-prefixed key** + parent frame extindex |
| Return address | hardware stack | **`.returnpc`**, recorded explicitly |
| Crash recovery | in-memory stack, lost on death | PC + frameRoot persisted to KV — resume after restart |

---

## Implementation Consistency Notes

Cross-checked against the kvlang Go sources: `lower/lower.go`, `layout/layout.go`, `rwir/rwir.go`, `rwir/control.go`, `rwir/pc.go`, `kvcpu/execute.go`, `kvcpu/controlflow.go`, `keytree/{const,frame,entry,vthread}.go`, `vthread/vthread.go`, `rwir/builtin/{resolve,helper}.go`.

- **Lowering to ScopeStmt + br/goto — CONFIRMED.** `lower/lower.go` lowers `if` (four blocks: `_if/_then/_else/_merge`), `while` (three blocks: `_while/_do/_exit`), and `for` (four blocks: `_for_init/_for_cond/_for_body/_for_exit`) into flat sibling `ScopeStmt`s plus `goto`/`br`, with `splitInstsAndBlocks`, `injectGoto`, and `injectGotoBlocks` exactly as the doc describes. break → `goto exitLabel`, continue → `goto condLabel` (via `loopCtx`). The doc's example block is illustrative but structurally accurate. `for` additionally lowers through `kvhas`/`kvat` (from `kv.has`/`kv.at`) — not shown in the doc but consistent with its "if/while/for" claim.

- **Instruction layout — CONFIRMED.** `layout/layout.go` `writeStmt` writes function-body instructions at `prefix/[i,j]`; `writeStmtScope` writes scope instructions at `prefix/scopeName[coord]` (flat key, no `/` before `[`). Nested scopes are also flattened onto the same `funcPrefix`, so all scopes are flat siblings — matching the doc's `/lib/sum_to/_while_2[0,0]` example.

- **`Decode`/`scopePrefixAndBase` — CONFIRMED, with signature nuance.** The actual signature is `Decode(ctx, kv, linkBase, pc) (*Rwir, error)` and it builds `scopePrefix + "[addr0,0]"`-style keys (the doc's `key := scopePrefix + "[0,0]"` drops the addr0 for brevity). `scopePrefixAndBase` returns `("", lookupBase)` for rwfunc frames and a dot-joined scope chain (e.g. `_do_3`, `_while_2._if_1`) for scope frames, and resolution goes through the parent frame's extindex to `/lib/<func>/<scope>[0,0]`. Since `HandleScope` always creates scope frames flat under the rwfunc root, single-segment prefixes are the norm; multi-segment prefixes only occur for nested explicit user blocks. Lookup is a single `kv.Get(lookupBase, names)` with 257 keys (1 opcode + 2×maxParams=128).

- **`HandleScope` — CONFIRMED.** Actual signature is `HandleScope(ctx, kv, pc, scopeName) string`; `rwRoot` is computed via `rwfuncFrameRoot` on `FrameRoot(pc)`; on first entry (no `.callpc`) it `MkIndexRecursive`s and writes `.returnpc` = `rwir.NextPC(pc)`; `.callpc` is rewritten every entry; returns `EntryPC(scopeFrame)`. `rwir.NextPC` = `[addr0+1,0]` (the instruction after the goto/br), which is the documented "preserve the original return path" behavior.

- **`HandleScopeReturn` / implicit return — CONFIRMED.** `kvcpu/execute.go` handles empty opcode: at a scope frame (`ExtKind != rwfunc`) it calls `layout.HandleScopeReturn` (read `.returnpc` → UnLink stack + DelTree self → jump to parentPC); at an rwfunc frame it calls `layout.HandleReturn` (UnLink + DelTree the whole frame); at the vthread root it `SetDone("ok")`. The doc's description matches.

- **`resolveWriteSlot` / `resolveReadValue` / funcFrameRoot — CONFIRMED.** `rwir/builtin/helper.go` `resolveWriteSlot` checks `.wparam` redirection (`keytree.WParam`) before writing to `Stack(funcFrameRoot) + name`; `rwir/builtin/resolve.go` `resolveReadValue` checks `.rparam` before the frame Stack. Both locate the rwfunc frame by the `.lib` marker (`funcFrameRoot` in builtin, `rwfuncFrameRoot` in layout). All variables therefore land in the rwfunc frame Stack regardless of which scope issued the write.

- **`HandleCall` / `Bootstrap` — CONFIRMED.** `layout.HandleCall` sets frameRoot = pc (callPC), `ExtIndex` → `funcKey+"/"`, writes `.returnpc`/`.callpc`/`.lib` plus `.rparam` and `.wparam` index dirs, binds read params via `RParam` → caller value location and write params from `inst.Writes[i].Name` (zero-copy). `Bootstrap` mirrors this on `/vthread/<vtid>/` (ExtIndex + `.callpc` + `.lib`).

- **DISCREPANCY — HandleLabel/RegisterBlocks not actually deleted.** The doc header states "旧 label/TCO/HandleLabel 已删除" (the old label/TCO/HandleLabel have been deleted), but `layout/layout.go` still contains `HandleLabel` (with TCO logic and `extKind`), `RegisterBlocks`, and `extKind` — none of which have any callers (dead code). `RegisterBlocks` is referenced only in a comment at `lower/lower.go:160`. The layout package doc comment and `extKind`/`HandleLabel` still describe the legacy "label frame" model. Functionally the new scope model is live (execution goes through `HandleScope`/`HandleScopeReturn`), but the deletion claimed by the doc has not been performed.

- **DISCREPANCY (notational) — system-variable keys use `‥` (U+2025), not a literal dot.** The doc renders frame fields as `.lib`, `.rparam/n`, `.wparam/total`, `.returnpc`, `.callpc`, and `.pc`. Concretely (`keytree/const.go`, `keytree/frame.go`, `keytree/vthread.go`) these are `frameRoot + "‥" + "lib"`, `frameRoot + "‥rparam/" + name`, `frameRoot + "‥wparam/" + name`, `frameRoot + "‥returnpc"`, `frameRoot + "‥callpc"`, and `/vthread/<vtid> + "‥pc"`. The `‥` runtime member separator is the single definition site; the dot notation in the doc is a visualization convention. (The vthread package comment loosely describes reserved keys as "starting with .", but the actual separator is `‥`.)

- **DISCREPANCY — scope-local variables are not actually cleaned up on scope exit.** The doc's Variable Scoping section says "after a scope exits, its variables are inaccessible to the parent (the scope frame has been deleted)". Because `resolveWriteSlot`/`ExecuteCopy` route all writes to the rwfunc frame Stack (found via funcFrameRoot), a variable written inside a scope persists after the scope frame is deleted and is in fact visible to later sibling scopes. The claim is aspirational (lexical), not enforced by storage layout.

- **DISCREPANCY (detail) — `WriteFunc` does more than the snippet shows.** `layout.WriteFunc` additionally (a) runs `lower.InferTypes(fn)` for the type map, (b) `MkIndexRecursive` on `/lib/<func>/`, (c) writes the source copy to `/lib/<func>.src` via `keytree.LibSrc` (fix-034), and (d) stores the signature as `kvspace.NewRwfunc(countDirectInsts(fn.Body), numReads, numWrites, sigString)` rather than the generic `kvspace.Rwfunc(...)` in the snippet. The doc's comment about RegisterBlocks being unnecessary is consistent with the dead `RegisterBlocks`.

- **Newer features absent from the doc.** (1) In-band debugger: an agent writes `/vthread/<vtid>/.debugger` to activate single-step mode with `.debugger.pause` / `.debugger.resume` commands (abort/continue), checked at function entry and on each step (`kvcpu/execute.go`, `kvcpu/debug.go`, `keytree.VThreadDebugger*`). (2) Stack-depth protection: `MaxStackDepth = 256`, raising `RecursionError: stack overflow` (`kvcpu/execute.go`, `stackDepth` counts `[i,j]` segments). (3) Read-param write protection (fix-027): bare-name write slots matching the frame `.ro` list abort with `ReadOnlyError` (`checkReadOnlyWrites`); the `.ro` list is written by Bootstrap/HandleCall. (4) The dispatch order in Execute is: control op → native builtin → `tensor.*` → copy op (`=`/assign) → user function rewritten to `call`. (5) `resolveReadPath`/`frameSlotKey` in layout and `vthread.CreateVThread` (nanosecond-timestamp vtid) vs `AllocVtid` (seq counter) — the doc's `/vthread/1/` is illustrative only.
