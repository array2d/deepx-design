# A KV-Tree Compute Architecture with Strict Separation of Storage, Compute, and Control Flow


## Strict Separation of Storage, Compute, and Control Flow

The top-level axiom of the kvlang architecture: **storage, compute, and control flow are strictly separated, each with its own responsibility.**

| Layer | Responsibility | Carrier | Description |
|----|------|------|------|
| **Storage** | Holds all code, data, and execution state | kvspace tree paths | Meta-store: the core kvspace capability, managing slots/indexes/system variables/instruction trees/frame stacks |
| **Compute** | Executes operators and tensor computation | op-plat (GPU/CPU operators) | Holds no state; only consumes data from the storage layer and writes results back to the storage layer |
| **Control flow** | Scheduling, jumps, call/return | kvcpu (PC path navigation) | Produces no data; only moves the PC to a different position within the storage tree |

Comparison with traditional architectures:

| | Traditional VM | kvlang |
|--|---------|--------|
| Storage | Stack + heap + global segments, mixed in memory | **kvspace tree**, a unified path address space |
| Compute | Instruction-inlined (ALU op + addressing) | **rwir `<-`/`->`**: explicit read/write params; compute decoupled from addressing |
| Control flow | PC as an integer offset, sharing the same space as data | **PC = KV path string**; control flow is tree navigation |

Payoffs of the separation: crash recovery needs only the PC path (already persisted in KV); operators can be replaced independently without affecting storage/control flow; debugging only requires `kvspace tree` to statically observe the storage tree — no need to attach to the process.

### Storage subdivides: Meta-store and Extension Store

The storage layer is itself further split into two levels:

| Storage level | Contents | Managed by | Visibility |
|--------|---------|--------|--------|
| **Meta-store** | slots, indexes, system variables, instruction trees, frame stacks, lib signatures | **kvspace** (Redis/in-memory KV) | **Globally public**: any kvspace connection can read and write |
| **Extension store** | tensor data (weights, activations, gradients) | heap-plat / op-plat | **Connection-private**: accessible only to the node directly connected to that storage |

Relationship between the two:

```
kvspace meta-store (slot → XValue)
  ├── primitive types: int/float/bool/string/bytes → XValue.raw self-contained
  └── tensor type: XValue stores only metadata (shape + dtype + extension-store handle)
                    ↓ handle points to
                extension store (tensor data itself, not inside kvspace)
```

**Meta-store is globally public**: kvspace is a shared KV store; any node can connect directly and read/write any slot. This is the foundation of distributed scheduling — all workers see the same code (`/lib/`) and the same execution state (`/vthread/`), so kvlang needs no separate scheduler metadata center.

**Extension store is connection-private**: GPU memory and node-local shm are accessible only to the directly connected process. The tensor data on a card on node A and the tensor data on a card on node B **cannot directly interact in computation** — the kvlang scheduler is responsible for colocating the tensors participating in the same operator onto the same card, and data movement (AllReduce, all-to-all, etc.) is explicitly managed through op-plat.

**Iron rule**: kvspace never holds raw tensor data — only its metadata and the extension-store handle. The meta-store is the global consensus layer, the extension store is the local acceleration layer, each doing its own job.

## Implementation Consistency Notes

Cross-checked against the kvlang Go sources (kvcpu/execute.go, rwir/rwir.go, rwir/pc.go, rwir/control.go, keytree/const.go, keytree/sys.go, keytree/vthread.go, keytree/frame.go, layout/layout.go, vthread/vthread.go, plus kvspace-go).

- **Storage layer / meta-store contents** — CONFIRMED. kvspace holds: slots `[i,j]`; indexes / extindexes; system variables (`/vthread/<vtid>/.pc`, `.status`, `.ctime`, etc., via `keytree/vthread.go`); instruction trees (`/lib/<pkg>.<name>/[i,j]` plus scope/label subpaths, `layout/layout.go`); frames (`frameRoot/` with `.returnpc`, `.callpc`, `.rparam/`, `.wparam/`, `.ro`, `.lib`, `keytree/frame.go`); and lib signatures (XValue kind=rwfunc at `/lib/<pkg>.<name>`, `layout.WriteFunc`). Meta-store carrier is kvspace-go (Redis/in-memory KV) — CONFIRMED.

- **PC = KV path string** — CONFIRMED. PC format is `/vthread/<vtid>/[i,0][/[j,0]]...` (vthread.go doc comment); the PC is read from `.pc` at the end of every Execute loop iteration and control ops are the static set `{call, return, br, goto}` (`rwir/control.go`, dispatch in `kvcpu/execute.go`).

- **rwir explicit read/write params** — CONFIRMED. `rwir.Rwir` = `{Opcode, Reads []Param, Writes []Param}`; reads are stored at `[addr0, -i]`, writes at `[addr0, +i]` (`rwir/rwir.go`, `layout.WriteBody`). `<-`/`->` are the source-level syntax for these slots.

- **`kvspace tree` command** — CONFIRMED. The `tree` subcommand exists in `kvspace-go/cmd/kvspace/main.go` (case "tree").

- **Crash recovery from PC only** — MOSTLY CONSISTENT (architectural claim). The PC is persisted to KV every instruction, but terminal-state handling (`vthread.SetDone`/`SetError`) is also part of the stored execution state; recovery relies on the whole `/vthread/<vtid>/` subtree, not the PC string alone.

- **Compute layer = op-plat** — PARTIAL. Native scalar ops (`+`, `-`, `*`, `/`, `print`, `sqrt`, etc.) are executed **in-process by the VM** via `rwir/builtin.Native` (registry in `rwir/builtin/ops.go`), not by an external op-plat. Only `tensor.*`-prefixed ops dispatch out to op-plat via `rwir/dispatch.Compute` → `/sys/op/<backend>/<n>/cmd` (`keytree.SysOpCmd`). The doc's table attributes all computation to op-plat; the in-VM native builtins are a nuance not captured there. The `/rwir/` signature tree (`keytree.Rwir`, written by `builtin.WriteRwir`) also exists.

- **XValue primitive kinds** — CONFIRMED, with more detail. `kvspace-go/const.go` defines bool, int8/16/32/64, uint8/16/32/64, float32/64, string, bytes (plus array1d/dict/index/linkindex/extindex/rwir/rwfunc/scope). The doc's "int/float/bool/string/bytes" is an abbreviation of a richer fixed-width numeric family.

- **"XValue tensor type stores only shape + dtype + extension-store handle"** — NOT REFLECTED IN CURRENT CODE. `kvspace-go/const.go` has **no `KindTensor`**; tensor descriptors are instead carried in the dispatch layer as `dispatch.ParamRef{Key, Dtype, Shape, Address}` inside `dispatch.OpTask` (`rwir/dispatch`). The doc describes the target architecture; the current kvlang implementation encodes tensor metadata in the op task protocol rather than as a dedicated XValue kind. Relatedly, there is no heap-plat implementation in the kvlang repo — the extension-store / colocation / AllReduce claims belong to the deepx ecosystem and are represented here only by the `/sys/op/<backend>/<n>/cmd` dispatch contract and the `Address` field of `ParamRef`.

- **In-band runtime debugger (not mentioned in doc)** — The kvcpu supports an inline debugger: an agent writes `/vthread/<vtid>/.debugger` at any time to activate single-step mode, with pause/resume keys `.debug.pause` / `.debug.resume` (`kvcpu/execute.go`, `keytree.VThreadDebugger*`). The doc only mentions static `kvspace tree` observation; this newer in-band facility is absent from this document.

- **Meta-store globally public, no ACL** — CONFIRMED. kvspace is shared KV with no access-control layer, matching the doc's "any kvspace connection can read/write any slot".
