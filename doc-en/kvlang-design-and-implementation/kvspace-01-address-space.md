# Address Space

> This chapter covers how kvlang **uses** kvspace — the process design and constraints — not kvspace's own design and implementation.
> For kvspace's interface definition, XValue type system, storage model, etc., see [kvspace Design and Implementation](../kvspace-design-and-implementation/).

## The Four Domains

kvspace's tree-shaped paths are divided into four system domains, borrowing the Unix filesystem idea; **all other `/` paths are completely free and user-defined**:

```
/lib/{pkg}.{name}         compiled function (signature + instruction tree) + .src source copy
/vthread/{vid}/           virtual-thread stack frames (runtime)
/sys/                     system infrastructure (VM / op-plat)
/dev/                     external I/O devices (/dev/tty terminal, /dev/screen display)
```

### `/lib/`

Borrowed from Unix `/lib/` — the standard path for shared libraries — this is the single source of truth for functions (compiled products). A `lib name { }` namespace block declares a package. Multiple files are spliced into a single source via `kvlang layoutrwir <files...>`, then parse → lower → write to `/lib/`. There is no `import` keyword — the lib tree is the global namespace, and cross-lib calls go through the full path `/lib/{lib}.{func}()`. The `.src` source copy sits alongside the instruction tree. Already-loaded files are deduplicated automatically.

For the naming of `/lib/`, `/func/` was previously considered — after all, mainstream languages define functions with the `func` keyword — but since `/func` (`/lib`) stores the output of layoutrwir, which resembles the `.so`/`.a` artifacts of C++/Go, and since kvlang supports hot reload, the Unix `/lib/` directory convention was borrowed and finalized.

### `/vthread/`

Runtime stack frames, borrowing the Unix `/proc/<pid>/` idea — one subtree per vthread, with system keys such as `.pc`/`.status` exposing execution state; the frame root itself is an extindex pointing at the `/lib/` instruction tree.

### `/sys/`

An infrastructure registry (VM heartbeat, op-operator list), borrowing the Unix `/sys/` pseudo-filesystem idea.

### `/dev/`

Borrowing from Unix `/dev/` — the I/O boundary. `/dev/tty/` (terminal input/output), `/dev/screen` (screen rendering). External devices are mounted as kvspace subtrees; reading/writing a device = reading/writing kvspace keys.

### Outside the Four Domains

`/` paths (e.g. `/counter`, `/n0.val`, `/tmp/seen`) are entirely defined by user code — kvspace presets no schema, providing only Write/Read/Watch primitives.

## The Storage Iron Law and Extension Storage

**kvspace storage iron law**: Keys must be string paths (tree-shaped levels separated by `/`); Values must be byte arrays of XValue-serialized data. It is **forbidden** to directly write raw primitive-type values, raw strings, or JSON — every value must go through XValue encode/decode. Writes that violate this law are behavior undefined when the reader side reads illegal bytes.

kvspace stores two kinds of data: **primitive data types** (int, float, bool, string) and **tensor metadata** (shape, dtype, a handle into extension storage). The full tensor data lives in extension storage:

| Extension location | Typical data |
|---------|---------|
| Cluster-node shared memory | Large tensors, activation values (heap-plat manages the lifecycle) |
| GPU memory | Compute tensors (op-plat holds handles device-side) |
| File system / object storage | Model weights, checkpoints, datasets |

Instruction classification is described in [parser篇-01](parser篇-01-指令架构.md).

## Implementation Consistency Notes

Cross-checked against the Go source under `/home/peng.li24/github.com/array2d/kvlang/` (`keytree/`, `layout/`, `rwir/`, `kvcpu/`, `vthread/`, `device/`, `cmd/kvlang/`, `parser/`) and the sibling translation `parser篇-06-layoutrwir.md`.

### Discrepancies

1. **The CLI command is now `layout`, not `layoutrwir`.** The doc says multi-file loading goes through `kvlang layoutrwir <files...>`. `cmd/kvlang/main.go` registers only `layout` and `layoutandrun`; `cmd/kvlang/help.go` still prints a stale `layoutrwirandrun` usage line. The described behavior otherwise matches: `cmdLayout` (`cmd/kvlang/layout.go`) concatenates all collected `.kv` files into a single source → parse → lower → write to `/lib/`. (Git history confirms the rename; see `parser篇-06-layoutrwir.md` notes.)

2. **System keys use the U+2025 separator `‥`, not `.`.** The doc (and the `vthread` package comment in `vthread/vthread.go`) present system keys as `.pc` / `.status`. `keytree/const.go` defines `RuntimeMemberSep = "‥"` (U+2025 TWO DOT LEADER) as the runtime reserved-field prefix, and `keytree/vthread.go` actually builds the keys as `/vthread/<vtid>/‥pc`, `/vthread/<vtid>/‥status`, `/vthread/<vtid>/‥ctime`, while `keytree/frame.go` builds frame keys as `frameRoot/‥callpc`, `frameRoot/‥returnpc`, `frameRoot/‥lib`. The doc's `.` is the conceptual "hidden file" convention; the implementation diverges to U+2025.

3. **`/dev/screen` is not implemented.** The four-domain block and the `/dev/` section list `/dev/screen` (screen rendering). No `screen` constant, key tree, or device implementation exists in the Go source — only `keytree.DevTTY` → `/dev/tty/<name>/<stream>` is implemented (`keytree/dev.go`). `/dev/screen` appears to be a planned/aspirational device. (Also, `device/term_ws.go`'s header comment mentions `/sys/term/${name}/...` while the code actually reads `/dev/tty/<name>/<stream>/type|detail` — a stale code comment.)

4. **No VM heartbeat under `/sys/`.** The `/sys/` section says the registry holds the "VM heartbeat" and the op-operator list. The Go source implements the operator registry (`/sys/op/<backend>/<n>`, `/sys/op/<backend>/<n>/cmd`, `/sys/op/<backend>/func/<opname>` via `keytree/sys.go` and `rwir/dispatch/router.go`, plus `/sys/rwir/<opcode>` via `rwir/builtin/ops.go`), but no heartbeat key. The only `/sys/vm/` key is `/sys/vm/<vmID>/err` for system-level error notification (`kvcpu/cpu.go`). The "VM 心跳" claim is not reflected in the implementation.

5. **`.src` is a flat sibling member key, not a node inside the instruction-tree directory.** The doc says the `.src` copy sits "in the same directory as the instruction tree". Concretely, `keytree.LibSrc` yields `/lib/<pkg>.<name>.src` (joined by the member separator `.`), a sibling of the `/lib/<pkg>.<name>` signature key; the instruction tree is `/lib/<pkg>.<name>/[i,j]`. "Same directory" is a loose description.

6. **"Already-loaded files auto-deduplicate" holds only on the load-and-run path.** `cmd/kvlang/layoutandrun.go` → `loadFunctions` → `_loadFile` skips files already seen via a `loaded` map keyed by absolute path. The `layout` command (`cmdLayout`) instead concatenates every collected file into one source with no dedup.

7. **"Write/Read/Watch primitives" is informal naming.** The doc says kvspace provides only Write/Read/Watch primitives. The actual KVSpace contract is Get/Set/Del/List/Watch/Notify/Link/Unlink + XValue (per the authoritative `kvlang/internal/kvspace/kvspace.go` / kvspace-go). Shorthand, not a contradiction.

### Verified-accurate claims

- The four domains `/lib/`, `/vthread/`, `/sys/`, `/dev/` all exist as constants in `keytree/const.go` (`PathSegLib`, `PathSegVthread`, `SegSys`, `SegDev`).
- `/lib/{pkg}.{name}` holds the compiled function signature + instruction tree: `layout.WriteFunc` writes the signature (`kvspace.NewRwfunc`) at `keytree.LibFunc(pkg, name)` and `layout.WriteBody` writes instructions at `/lib/<pkg>.<name>/[i,j]`; `layout.WriteFunc` also writes the source copy to `keytree.LibSrc` (`/lib/<pkg>.<name>.src`) from `fn.FullText()`.
- `lib name { }` namespace blocks declare packages: `parser.parseFile` / `parser.parseLibBody` (which also support nesting).
- No `import` keyword; the lib tree is the global namespace; cross-lib calls use the full path `/lib/{lib}.{func}()`: `layout.writeStmt` leaves `/lib/`-prefixed opcodes untouched, `kvcpu/execute.go` rewrites such opcodes into `call` at runtime, and `layout.HandleCall` parses the `/lib/` prefix.
- `/vthread/<vid>/` runtime stack frames, with the frame root being an extindex overlay onto the `/lib/` instruction tree: `layout.HandleCall` and `layout.Bootstrap` call `kv.ExtIndex(keytree.Stack(frameRoot), funcKey+"/")`.
- `/sys/` op-operator registry: `keytree/sys.go` (`SysOpRoot`, `SysOpCmd`, `SysOpFunc`, `SysRwirRoot`).
- `/dev/tty` terminal streams: `keytree.DevTTY(name, stream)` → `/dev/tty/<name>/<stream>`; `device/term_ws.go` resolves terminal config from `/vthread/<vtid>/term`.
