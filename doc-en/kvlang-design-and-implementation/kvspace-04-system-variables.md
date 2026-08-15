# System Variables

## `X/.var` shadow keys

> **⚠️ This section is the sole source of truth for all `/.` system keys.**
> Any code change (adding, modifying, or deleting a `/.` key) **must update this section first**, and only then the code.
> `grep -rn '"\.' internal/ cmd/ --include="*.go"` audits all current `/.` keys.

The VM runtime generates **built-in variables (system variables)** for the objects it manages, stored in the form `{object-key}/.var`: one level down under `/`, with a key name starting with `.`. kvlang identifiers cannot start with `.`, so every `.`-prefixed key is engine-reserved and cannot be read or written directly by user code — analogous to Unix hidden files: not shown in the default view, visible to the engine.

**Distinction from user member keys**: `X.attr` (a `.` flat key, e.g. `obj.prop`, `lib.func`) is a normal struct member reachable from user kv code; `X/.attr` (`/` + `.` prefix) is engine-exclusive. `keytree.Member(base, name)` produces `base + "." + name` (user side), while `FuncLib`/`VThreadPC` etc. produce `path + "/.name"` (system side) — zero intersection.

### Full inventory (grouped by host object)

**vthread objects** (host = `/vthread/<vtid>`, lifecycle and scheduling; code `keytree/vthread.go`):

| Key | Mechanism | Code location | Semantics |
|----|------|---------|------|
| `.pc` | String | `VThreadPC` / `vthread.Set` | current execution PC (absolute path) |
| `.status` | String → Notify | `VThreadStatus` / `vthread.SetDone/SetError` | running states `init`/`running`/`wait`; terminal state Del + Notify(retVal) |
| `.<status>/msg` | String | `VThreadStatusMsg` | terminal extra info (`.error/msg`, `.timeout/msg`, path dynamically generated) |
| `.debugger` | String | `VThreadDebugger` | debug control: `""` normal, `"step"` single-step; the `debugger()` builtin (fix-031) reads this key |
| `.debugger.pause` | Notify queue | `VThreadDebuggerPause` / `debugNotifyPause` | CPU → agent pause event (JSON `{"pc","func","frame","op"}`) |
| `.debugger.resume` | Notify queue | `VThreadDebuggerResume` / `debugWaitResume` | agent → CPU resume command: `step`/`continue`/`abort` |

**frame objects** (host = frameRoot, calling protocol; the vthread root is the top-level frame; code `keytree/frame.go` + `layout/layout.go`):

| Key | Mechanism | Code location | Semantics |
|----|------|---------|------|
| *(frame-root extindex)* | extindex | `Stack` / `HandleCall:ExtIndex` / `HandleReturn:UnLink` | rwfunc frame-root extindex → `/lib/<pkg>.<name>` read-only instruction region; scope frames create no extindex |
| `.lib` | String | `HandleCall:Set` / `Bootstrap:Set` / `funcFrameRoot:Get` | lib path of the rwfunc frame (e.g. `/lib/sum_to`); `funcFrameRoot` identifies rwfunc frame boundaries |
| `.callpc` | String | `CallPC` / `HandleCall:Set` / `HandleScope:Set` | per-frame execution progress (updated per op); scope frames update it on every re-entry |
| `.returnpc` | String | `ReturnPC` / `HandleCall:Set` / `HandleScope:Set` | return address (frozen at frame creation); scope frames set it only once, re-entry does not overwrite |
| `.ro` | String | `FrameRO` / `HandleCall:Set` / `Bootstrap:Set` | read-only parameter list (comma-separated), used by the kvcpu write-slot check (fix-027; not written for zero-argument functions) |
| `.rparam/<name>` | String | `RParam` / `HandleCall:Set` / `Bootstrap:Set` | read-param redirection: stores the absolute path of the caller's value; the CPU reads directly from this path, zero-copy |
| `.wparam/<name>` | String | `WParam` / `HandleCall:Set` | write-param redirection: stores the absolute path of the caller's write target; the CPU writes directly to this path, HandleReturn no longer moves data |

**data objects** (host = variable key, planned):

| Key | Mechanism | Semantics |
|----|------|------|
| `.shape` | — | shape of a kvspace array (after the todo-009 key-family arrays land) |
| `.gc` | — | garbage-collection reference count (future) |

**Syntax-layer reserved names (not persisted, intercepted by `frameSlotKey`)**:

| Key | Semantics |
|----|------|
| `._` | discard slot — `frameSlotKey` returns an empty path for `.xxx`, never written to kvspace |

**Inline pause instruction**: `debugger()` (fix-031, aligned with the V8/TypeScript `debugger;` statement) — an inline source-level pause point; a no-op outside debug mode, and in debug mode it pauses the vthread to wait for the agent's step/continue/abort. The pause/resume protocol flows through the three keys `.debugger`, `.debugger.pause`, `.debugger.resume`.

Note the distinction: `/sys/` (vm heartbeat, op registration) is an independent system **domain** (top-level tree), a different mechanism from the object-attached `/.var` system **variables**. `/vthread/<vtid>/term` (terminal binding name) is a plain struct key (not `/.`-prefixed), readable and writable by user code.

### Three key shapes: identify at a glance

After member-key `.` separation (todo-009) lands, the shape of any key uniquely determines its nature:

| Shape | Example | Nature | Ownership |
|------|----|------|--------|
| `X/名` (`/` + ordinary name) | `/vthread/7/[3,0]`, `/_while_2/[0,0]` | structure: frames, instruction slots, scope frames | VM |
| `X.名` (`.` flat key) | `/c0.next`, `frame/obj.prop` | user data member (key family) | user |
| `X/.名` (`/` + dot name) | `/vthread/7/.pc`, `arr/.shape` | system variable (shadow metadata) | VM |

### Design conclusion: system variables keep `/` separation (`X/.var`)

System variables **should use `/` separation**, orthogonal to the `.` separation of user members, for three reasons:

1. **A collision-free private namespace.** User member syntax produces only `.` flat keys (`a.b` → key `a.b`), and identifiers cannot start with `.`; even a dynamic key `h.*k` whose value of `k` starts with `.` yields `h..xxx` by `.` concatenation — user code can never fabricate the `/.` sequence. Conversely, if system variables also used `.` concatenation (`arr..shape`), they would collide with dynamic-key injection and force maintaining a reserved-word table.
   ⚠️ The status quo (`/`-joined members) cannot guarantee this: `at(h, ".pc")` already hits the `h/.pc` system key — yet another argument for todo-009: `.`-joining naturally blocks system-key injection.
2. **Lifecycle binding.** `X/.var` lives inside X's `/` subtree; `DelTree(X)` clears all system variables along with it (frame destruction already relies on this); the user key family `X.*` is managed by prefix deletion. The two deletion planes each belong to their owner: the structure tree to the VM, the data plane to the user.
3. **Unified with frame system keys into a single axiom.** Frames are themselves objects — `.pc`, `.rootfunc`, `.ro` already have the `{object}/.var` shape. Axiom: **for any kvspace object X — VM metadata lives in `X/.name` (engine-reserved), the frame-root extindex points to the `/lib/` code region, user data in `X.name` (members), children in `X/name` (structure)**. Array `.shape` and a future GC counter slot in directly, needing no new mechanism.

## Implementation Consistency Notes

Cross-checked against the kvlang Go sources (keytree/{const,frame,vthread,sys,entry,member}.go, layout/layout.go, kvcpu/{execute,debug}.go, vthread/vthread.go, rwir/rwir.go, rwir/builtin/{debugger,helper,resolve,array}.go, rwir/dispatch/router.go, device/term_ws.go).

- **Runtime member separator is now `‥` (U+2025), not `.`** — MAJOR DISCREPANCY (doc predates the change). Commit `c5b2a1a` (2026-07-31, "refactor: runtime/keytree/layout/parser 修复") replaced `ReservedPrefix = "."`, `ScopeSep = "/."`, `SegLib = ".lib"` with `RuntimeMemberSep = "‥"` (defined in keytree/const.go:8, U+2025 "two dot leader", with a comment forbidding hardcoding). All system keys this document writes as `X/.pc` are therefore physically `X/‥pc` in the current implementation, e.g. `/vthread/<vtid>/‥pc`, `/vthread/<vtid>/‥status`, `/vthread/<vtid>/‥debugger`, `/vthread/<vtid>/‥debugger.pause` and `.resume` (the `debugger.pause`/`debugger.resume` suffix still uses `MemberSep` `.`), `/vthread/<vtid>/‥<statusVal>/msg`, `frameRoot/‥lib`, `frameRoot/‥callpc`, `frameRoot/‥returnpc`, `frameRoot/‥ro`, `frameRoot/‥rparam/<name>`, `frameRoot/‥wparam/<name>`. The doc's `.`-prefixed notation is the pre-2026-07-31 form; the translation keeps the doc's notation verbatim. The audit command `grep -rn '"\.' internal/ cmd/ --include="*.go"` is stale on three counts: the prefix is now `"‥` (not `".`), the keytree package lives at the repo root (not under `internal/` or `cmd/`), and the authoritative place to audit is `keytree/const.go` (plus `keytree/{frame,vthread}.go`). A working audit is `grep -rn 'RuntimeMemberSep' keytree/`.

- **`/vthread/<vtid>/term` is not a plain struct key** — DISCREPANCY. `keytree.VThreadTerm(vtid)` (keytree/vthread.go:21) goes through `vtMember`, which prepends `RuntimeMemberSep`, so the physical key is `/vthread/<vtid>/‥term` — inside the runtime-member namespace. The binding intent matches the doc (`device.ResolveTerm` reads it and resolves `/dev/tty/<name>/<stream>`), but the doc's claim that it is a "plain struct key (not `/.`-prefixed)" is not reflected in the implementation; it is as `/.`-prefixed as any other system variable.

- **`.callpc` "updated per op" is not actually performed** — DISCREPANCY (claim matches a comment, not the code). The per-op-progress wording matches `SegCallPC`'s comment in keytree/const.go:19 ("本帧执行进度（每 op 更新）"), but no code updates `.callpc` per instruction. It is written only at frame entry/creation — `HandleCall`, `HandleLabel`, `Bootstrap` set it to `EntryPC(frameRoot)`, and `HandleScope` re-sets it on each re-entry (layout/layout.go) — and it is never read anywhere in the current runtime.

- **`.ctime` and `/vthread/‥seq` exist in the code but are missing from the doc's vthread table** — DOC OUTDATED. `keytree.VThreadCtime(vtid)` (creation time, written by `vthread.CreateVThread` as XValue kind=time) and `keytree.VthreadSeq` = `/vthread/‥seq` (vtid auto-increment, used by `vthread.AllocVtid`) are implemented and in use, but the doc's vthread inventory lists neither.

- **debugger() issue id: doc says `fix-031`, code comment says `tothink-031`** — MINOR DISCREPANCY. rwir/builtin/debugger.go:17 reads "tothink-031" ("aligned with V8/TypeScript `debugger;`"), while this doc calls it fix-031. No `issue/` file numbered 031 exists in `deepx-design/issue/` (current issue files span 006–010, 018, 032–037).

- **Pause event JSON payload: two variants** — MINOR NOTE. The `kvcpu.debugNotifyPause` event marshals `{"pc","func","frame","op"}` exactly as the doc shows (kvcpu/debug.go:35-40). The inline `debugger()` builtin (rwir/builtin/debugger.go:31-34) Notifies a slightly different payload, `{"pc","vtid","opcode","func","frame"}` — no `op` field, extra `vtid`/`opcode` fields, and `func` is an empty string there.

- **The system-key injection vector is already closed** — UPDATE to the design discussion. Design-conclusion point 1 argues that with `/`-joined members `at(h, ".pc")` hits the `h/.pc` system key and defers the fix to todo-009 (`.`-joining). With the current `‥` separator, `at`/member access join with `MemberSep` `.` (`keytree.Member`), so `at(h, ".pc")` yields `h..pc`, which cannot collide with `h/‥pc`. The injection the doc warns about therefore no longer exists today; the `‥` separator already delivers the "system keys are unreachable from user member syntax" property that the doc postponed to todo-009.

- **`._` discard slot via `frameSlotKey`** — CONFIRMED. `layout.frameSlotKey` (layout/layout.go:481-486) returns `""` for any dot-prefixed slot name, so `._` (and any `.xxx`) resolves to an empty path and is never written to kvspace. (Sibling doc parser篇-01 additionally notes the parser rejects source-level `._`; the mechanism cited here exists and is what the doc describes.)

- **Frame/vthread inventory otherwise CONFIRMED**:
  - `.pc`/`.status`/`.<status>/msg` — keytree.VThreadPC/VThreadStatus/VThreadStatusMsg and `vthread.Set`/`SetDone`/`SetError` (vthread/vthread.go) match; `SetError` builds `.<status>/msg` dynamically (`/vthread/<vtid>/‥error/msg`), `SetDone`/`SetError` Del + Notify the `.status` key.
  - `.debugger`/`.debugger.pause`/`.debugger.resume` — keytree.VThreadDebugger/VThreadDebuggerPause/VThreadDebuggerResume, kvcpu/debug.go `debugNotifyPause`/`debugWaitResume`, and kvcpu/execute.go's stepping logic all match the doc's `""`/`"step"`/`continue`/`abort` semantics.
  - frame-root extindex — `HandleCall`/`HandleLabel`/`Bootstrap` `kv.ExtIndex(Stack(frameRoot), funcKey+"/")`; `HandleReturn`/`HandleScopeReturn` `UnLink(Stack(...))` + `DelTree`; scope frames never call ExtIndex. All match.
  - `.lib` — `SegLib = RuntimeMemberSep + "lib"`; written in `HandleCall` (layout/layout.go:178) and `Bootstrap` (:350); read by `funcFrameRoot` (rwir/builtin/helper.go:114) and layout's `rwfuncFrameRoot` (:515) to find rwfunc frame boundaries. Confirmed.
  - `.returnpc` — set at frame creation, scope frames set it only on first entry (`if !exists` guard in HandleScope), matching "re-entry does not overwrite".
  - `.ro` — `FrameRO` written only when `len(params) > 0` (HandleCall :220-222, Bootstrap :371-373), confirming the "not written for zero-argument functions" note; consumed by `cpu.checkReadOnlyWrites` (fix-027) in kvcpu/execute.go:202.
  - `.rparam/<name>`/`.wparam/<name>` — set by HandleCall/Bootstrap; read via `resolveReadValue`/`resolveKVPath` (rwir/builtin/resolve.go:36) and `WriteTarget` (rwir/builtin/helper.go:75). `HandleReturn` only reads `.returnpc` then `UnLink`+`DelTree`s the frame and copies nothing — the zero-copy write-param redirection claim holds. `HandleReturn` also panics if a `return` carries read args.

- **`.rootfunc` referenced in design-conclusion point 3 does not exist** — DISCREPANCY. The axiom's example list ".pc, .rootfunc, .ro" cites a `.rootfunc` key that appears nowhere in the Go sources (grep finds zero hits) and is not in this document's own inventory; it is a stale leftover of an earlier design. The current frame-boundary marker is `.lib`.

- **Data-object keys `.shape`/`.gc`** — NOT IMPLEMENTED (as the doc labels them "planned"). No keytree constant exists for either; they remain prospective. The doc correctly marks them as planned.

- **`/sys/` system domain** — CONFIRMED. keytree/sys.go defines `/sys/op/<backend>/<n>`, `/sys/op/<backend>/cmd`, `/sys/op/<backend>/func/<name>`, `/rwir/<opcode>`, used by rwir/dispatch (router.go, dispatch.go) and `rwir/builtin.WriteRwir`; distinct from the per-object `X/.var` system variables as the doc describes.
