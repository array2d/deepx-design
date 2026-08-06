# Everything Is Plaintext

## Everything Is Plaintext

In a traditional VM, the PC is an integer, instructions are bytecode, pointers are memory addresses, types are lost at compile time, and debugging requires a disassembler. kvlang is the opposite: **all critical information is stored as plaintext strings in the kvspace tree**, so `kvspace tree`/`kvspace get` give complete system introspection.

**The core intent: make programs completely transparent to both humans and agents.** Humans read the tree by eye with `kvspace tree`; agents read it programmatically with `kv.Get` — the same data, two kinds of consumers, zero intermediate layers. A traditional VM locks people out with binary encoding and then opens a small window through gdb/LSP/debug adapters. kvlang removes that window entirely — the whole wall is transparent.

### Paths as PC, Pointers, and Addresses

```
Traditional: PC = 0x7fff5fbff830   pointer = 0x7fff9b3c   variable = rbp-8
kvlang:     PC = "/vthread/run/[0,0]/[1,0]/[0,0]"   pointer = "/n0"   variable = frame root + "/x"
```

The PC string *is* the call stack (path depth = stack depth). A bare name is a relative pointer; a `/`-prefixed name is an absolute pointer. There is no `&` address-of operator — the variable name itself is the variable's kvspace path. `"/n0" -> p; p.next` is a path string, readable directly with `kvspace get`.

### Instructions and Calls: String Opcodes, Full-Path Calls

```
Traditional: B8 01 00 00 00 (mov eax, 1)   call = call 0x400200 (indirect via symbol table)
kvlang:     [0,0]="+" [0,-1]="A" [0,1]="C"   call = /lib/math.sum(3, 4) -> s
```

Opcodes are strings such as `"+"`, `"print"`, and `"call"`; `kvspace tree /lib/func` is the disassembly. The lib tree *is* the global namespace — a call is a full path, with no imports, symbol tables, or linker relocations.

### Types and State: Self-Describing Kinds, `/.` Exposes Everything

```
Traditional: runtime value = raw bytes   system state = requires ptrace
kvlang:     XValue carries kind "int64"   /vthread/7/.pc .status .rootfunc readable directly with Get
```

`kvspace get /x` returns `int64:42` — the type is stored together with the value. The VM's internal state is exposed through `X/.var` shadow keys: `.pc`, `.status`, `.rootfunc`, `.rparam`, `.wparam`. Source code is stored at `/lib/.*.src`, in the same directory as the instruction tree.

### The Cost and the Compensation

The plaintext principle is carried through every aspect of kvlang — the PC is a string rather than an integer, an opcode is `"+"` rather than a single byte, a pointer is a path rather than an address — and this inherently sacrifices interpreted execution speed. But kvlang's role is that of a **scheduling layer**: the most time-consuming tensor computation does not run inside kvlang. op-gpu compiles the computation to GPU kernels (TileLang → AOT `.so` → dlopen) and wins back the performance through GPU hardware acceleration. Transparency is delivered at the scheduling layer; compute power is recovered at the extension-engine layer.

## Implementation Consistency Notes

Cross-checked against the Go source in `/home/peng.li24/github.com/array2d/kvlang/` (kvcpu/, rwir/, keytree/, layout/, vthread/) and the sibling repos `kvspace-go` and `deepx-op-gpukernelcompiler`.

### Discrepancies

1. **`.rootfunc` does not exist in the Go source.** The document lists `.rootfunc` among the shadow keys under the vthread (`/vthread/7/.pc .status .rootfunc`) and names it among the VM internal-state keys (`.pc` `.status` `.rootfunc` `.rparam` `.wparam`). No `.rootfunc` key is defined anywhere in the Go implementation (zero hits across all `*.go` files). In the current source, vthread-level shadow keys are `.pc`, `.status`, `.ctime`, `.debugger`, `.term`, plus the dynamic `.error/msg` (see `keytree/vthread.go` and `vthread/vthread.go`). `.rootfunc` also appears in other design docs (e.g. `runtime篇-03-调试与可观测性.md`, `draft/dot-key-system-stack.md`), where it is described as the entry function name written by `Bootstrap` and read by `resolveLabel` for TCO label resolution. The current implementation (`layout/layout.go` `HandleLabel`) resolves TCO by comparing ancestor-frame segment names with the label name and finds rwfunc boundaries via the `‥lib` marker — no `.rootfunc` key is involved. It appears `.rootfunc` was either renamed or removed.

2. **`.rparam`/`.wparam` are frame-scoped, not vthread-root-scoped.** The document groups them with `.pc`/`.status` as VM internal state. In the source they are per-frame runtime members: `frameRoot/‥rparam/<name>` and `frameRoot/‥wparam/<name>` (`keytree/frame.go` `RParam`/`WParam`; written in `layout.HandleCall`). Vthread-level keys never contain `rparam`/`wparam`.

3. **Runtime shadow keys use the U+2025 separator `‥`, not `/.`.** The document describes the mechanism as `X/.var` shadow keys. The implementation's runtime-member separator is `RuntimeMemberSep = "‥"` (U+2025 TWO DOT LEADER; defined in `keytree/const.go` and `kvspace-go/const.go`), producing keys such as `/vthread/42/‥pc`, `/vthread/42/‥status`, and `frameRoot/‥rparam/x`. `kvspace List` hides keys that begin with `‥`. The document's `X/.var` notation is the design-level shorthand for this same mechanism.

4. **PC examples use symbolic vtids (`/vthread/run/...`, `/vthread/7/...`).** The path format itself is accurate (`/vthread/<vtid>/[i,0][/[j,0]]...`; see the `vthread/vthread.go` package comment and `keytree.VThreadSlot`). In the current implementation vtids are allocated as integers: `time.Now().UnixNano()` in `vthread.CreateVThread`, or the incremented `/vthread/‥seq` counter in `vthread.AllocVtid`. `"run"` / `"7"` are illustrative placeholders.

5. **op-gpu: AOT `.so` → dlopen is the documented design intent, but the current code path is JIT.** The document's "TileLang → AOT `.so` → dlopen" matches the design intent recorded in `deepx-op-gpukernelcompiler/doc/kvlang-to-tilelang-interface.md` and `doc/kernel-compiler-analysis.md`. The current op-gpu implementation (`deepx_op_gpukernelcompiler/kernel_tilelang.py`) instead uses `@tilelang.jit` — TileLang's JIT compilation. The claim that tensor computation runs outside kvlang, compiled to GPU kernels, is accurate; the AOT `.so` + dlopen mechanism is still aspirational.

### Verified-accurate claims

- The PC is an absolute string path and path depth = stack depth (`kvcpu/execute.go` `stackDepth` counts `[i,j]` segments; `MaxStackDepth = 256`).
- Instruction slot layout `[addr0,0]` = opcode, `[addr0,-j]` = reads, `[addr0,j]` = writes (`rwir/rwir.go` `Decode`).
- Opcodes are strings: control ops `call`/`return`/`br`/`goto` (`rwir/control.go`); native ops such as `+`, `print` (`rwir/builtin/ops.go` `IsNativeOp`).
- Full-path calls via `/lib/<pkg>.<name>` (`keytree/entry.go` `LibFunc`); no imports, symbol tables, or linker relocations.
- `kvspace get /x` prints `int64:42` for an int64 value — `fmtKindVal` formats as `kind:value` (`kvspace-go/xvalue_format.go`); `KindInt64 = "int64"` (`kvspace-go/const.go`).
- Source is copied to `/lib/<pkg>.<name>.src` alongside the instruction tree (`keytree/entry.go` `LibSrc`; written in `layout.WriteFunc` from `fn.FullText()`).
- `kvspace tree` and `kvspace get` CLI subcommands exist (`kvspace-go/cmd/kvspace/main.go`).
