# Reference 01: How to Design a Programming Language

Programming language design: core principles, design constraints, and a systematic checklist of pitfalls to avoid.

Written from the top-level design perspective of programming languages and virtual machines, this document distills a set of standardized design principles and trade-off logic shared across industry and academia. It applies to brand-new languages, custom VMs, and domain-specific execution models, and can be used directly as project design basis, as a paper's Motivation, or as material for a Design Philosophy chapter.

---

## I. Top-Level Design Core Principle: A Language Is a Self-Consistent System of Trade-offs

A programming language's core value is not in piling up syntax features, but in a design philosophy and trade-off decision system that runs uniformly across the entire stack. All underlying modules must obey unified constraints; no design exists in isolation.

The core dimensions a language design must align on:

- Type system model (strong/weak typing, implicit conversion rules, null semantics)
- Evaluation and execution strategy (eager/lazy, execution order, instruction semantics)
- Memory and state model (variable lifetime, scoping rules, how state is stored)
- Control flow model (jump, call, return, exception, resume semantics)
- Error and crash semantics (exception handling, fault recovery, state consistency)
- Debugging and observability model (state visible, traceable, snapshot-able)
- Versioning and compatibility strategy (iteration rules, trade-off of historical burden)

A good language design must be fully defensible: every feature's benefit, cost, alternatives, and extreme edge cases must be answerable. There is no room for feature-piling without justification. Language design is a long-term iterative engineering effort, not a one-shot assembly of inspirations.

---

## II. Two-Level Grading Standard for Language Innovation (common to academia and industry)

Programming language innovation has two levels, and the level directly determines the project's academic value and engineering breakthrough:

### 1. Surface-level innovation (incremental improvement, low value)

Only optimizes syntax sugar, keywords, writing conventions, and API encapsulation, without changing the underlying execution and semantic model. The vast majority of self-written scripting languages and AI-generated languages stay at this level.

### 2. Deep innovation (paradigm breakthrough, high value)

Reworks the underlying execution paradigm, state storage model, instruction encoding mechanism, control flow abstraction, and type semantic system. Typical examples: a novel PC addressing model, a layered storage/compute/control architecture, two-dimensional instruction encoding, a source-as-IR design with no intermediate layer, a fully plaintext persistent VM, and so on.

The academic community and top conferences (PLDI/POPL) only recognize deep semantic and execution-model innovation; surface-level syntax optimization does not constitute a core contribution.

---

## III. Hard Standards for a Deliverable Language Project

A qualified programming language/VM system must satisfy three conditions — verifiable, reproducible, and deliverable. Pure theoretical design carries no academic or engineering credibility:

- A runnable, compilable complete prototype, not just a textual conception
- A systematic test suite: basic syntax, algorithm cases, edge cases, and negative/exception cases
- Clear error diagnosis, error-reporting mechanisms, and exception handling logic
- Design semantics that are formalizable, explainable, and horizontally comparable with mainstream work

---

## IV. The Core Pitfall in Language Design: Reject "Omnipotent Hybrid Design"

Every mature language design follows the core logic that you must give something up to gain something. There is no "perfect language" that covers all paradigms and fits every scenario. Forcibly merging opposing designs produces an incoherent system, semantic conflicts, and explosive maintenance cost.

Typical high-risk hybrid traps:

- Mixing dynamic and static typing rules with no unified type constraint
- Simultaneously allowing implicit type conversion and strong type checking
- Mixing multiple memory management models (GC + manual memory + ownership with no governing rules)
- Piling up functional, object-oriented, and procedural syntax with no unified execution semantics

A good language design always proactively gives up some capabilities in exchange for global consistency and long-term stability.

---

## V. An Easily Overlooked Core Design Dimension: Observability and Fault Semantics

Entry-level language design only asks "how does normal code execute." Top-level language design must define the exception and crash system in advance — this is the core dividing line between industrial-grade systems and toys:

- After a program crashes, can execution state be preserved, recovered, and restarted
- Whether runtime state is observable, traversable, and snapshot-able
- Whether non-invasive debugging is supported — no process attach, no low-level debugger required
- The underlying semantics of state persistence, instruction replay, and breakpoint-and-resume

This dimension is a hard requirement for modern distributed, training/inference, and long-running systems, and it is a key capability missing from traditional scripting languages.

---

## VI. Four Academic Review Standards for Language Design (common to top conferences)

To gain academic recognition, a new language or new VM must fully satisfy four baseline requirements:

1. **Clear Motivation**: precisely identify the pain points and inherent architectural bottlenecks that existing mainstream languages/VMs cannot solve
2. **Clear Semantics**: type, evaluation, control flow, and state-transition rules must be precisely definable, with no ambiguity
3. **Reproducible Engineering Implementation**: a runnable prototype, a complete test suite, and performance and functionality evaluation
4. **Sufficient Horizontal Comparison**: clearly differentiate from existing PL, VM, and IR systems, and argue for the innovation and advantage

---

## VII. Underlying Constraints of Language Architecture Design (avoiding AI-style design flaws)

No automated tool (LLM) can complete top-level language architecture design. The core reason is that top-level language design requires long-range consistency, engineering-constraint awareness, and systematic trade-offs — inherent weaknesses of statistical generative models, and precisely the core value of human design:

- **Reject compromise**: language design requires making hard trade-offs actively, rather than accommodating every option and avoiding conflict, so the system does not collapse
- **Insist on paradigm originality**: models excel at integrating existing features but cannot natively generate new execution paradigms and underlying architectures
- **Global consistency first**: all syntax, semantics, and underlying mechanisms must obey the same top-level design specification, with no local contradictions
- **Complete boundary reasoning**: must exhaustively enumerate extreme cases, nested semantics, exception interactions, and concurrency conflicts to fill design holes
- **Fit engineering implementation constraints**: design must account for storage overhead, serialization, scheduling, hardware adaptation, and distributed consistency — not armchair theorizing
- **Long-term iterative evolution**: hold a fixed top-level design goal so every iteration continues the same philosophy, without drift
- **Quantifiable trade-off argumentation**: be able to clearly state every design's long-term cost, applicable scenarios, and the pros and cons of alternatives

---

## VIII. Designer Role Boundary Specification (common in the academic community)

In modern language development, the boundary between tools and the designer must be strictly maintained:

Automated tools may be used for mechanical assistance: boilerplate code generation, documentation writing, test-case assistance, syntax drafting, code refactoring, and similar.

Top-level architecture, the execution model, semantic specification, core trade-offs, and system-wide consistency verification must be completed, verified, iterated, and maintained independently by the designer.

---

## IX. Design Summary Adapted to Innovative VM Languages (for KVLang-style paradigm innovation projects)

Unlike traditional syntax-improvement languages, the core design advantages of paradigm-innovative VM languages fully match the top PL design standards:

- No surface-level syntax iteration; instead reworks the underlying paradigm of program state, PC addressing, and instruction encoding
- Implements strict three-layer decoupling of storage, compute, and control flow; the architecture is self-consistent and original
- Unifies the four representations of source, IR, instruction, and persistent storage, eliminating intermediate-layer redundancy
- Ships native fully-plaintext semantics that are observable, persistent, and crash-recoverable
- Comes with a complete runnable prototype plus a large-scale test suite, satisfying academic delivery review standards

The core value of this project type is redefining the underlying paradigm of program execution and state storage; it belongs to the category of top-level PL academic innovation.

---

## Implementation Consistency Notes

This document is a design-philosophy/reference piece rather than an implementation walk-through, so it cites no specific function signatures, struct fields, or KV paths. The concrete KVLang architecture claims it makes appear in Section II.2 and Section IX. Each was cross-checked against the current Go sources under `kvlang/` (parser/, lower/, layout/, rwir/, kvcpu/, keytree/, vthread/):

- **"Novel PC addressing model"** — CONFIRMED. The PC is an absolute KV path string (`/vthread/<vtid>/[i,0][/[j,0]]...`), not an integer offset. It is read from the `.pc` system key at the end of every `Execute` loop iteration, and `rwir.NextPC` (`rwir/pc.go`) advances a frame's instruction index (`[n,0] → [n+1,0]`). Frame roots, entry PCs, and parent-frame navigation all derive from path string operations (`keytree/frame.go`).

- **"Layered storage/compute/control architecture"** — CONFIRMED. The codebase separates the three concerns: storage = the kvspace tree (all code/data/execution state under `/lib/`, `/vthread/`, and system keys); compute = native scalar ops in `rwir/builtin` plus `tensor.*` dispatch via `rwir/dispatch`; control = `kvcpu` (the fetch-decode-execute loop) with the static control-op set `{call, return, br, goto}` (`rwir/control.go`). Control ops produce no data and only move the PC; compute ops consume slots and write back slots.

- **"Two-dimensional instruction encoding"** — CONFIRMED. A compiled instruction is laid out at two-dimensional coordinates under `/lib/<pkg>.<name>/`: `[n,0]` holds the opcode, `[n,-j]` holds read slots, `[n,+j]` holds write slots (`layout.WriteBody` / `writeStmt`, keys built via `kvspace.KVPair`). At runtime `rwir.Decode` (`rwir/rwir.go`) reconstructs the `Rwir{Opcode, Reads, Writes}` struct from exactly these coordinates, batch-fetching up to `1 + 2*maxParams = 257` keys in one `kv.Get` (`maxParams = 128`).

- **"Source-as-IR, no intermediate layer"** — MOSTLY CONSISTENT (design claim). There is no separately serialized IR/bytecode blob: the compiled output IS the KV tree at `/lib/<pkg>.<name>/[i,j]`, and the same tree is the executable the VM fetches from. However, "no intermediate layer" overstates the front-end: the pipeline is `source → scanner → parser (AST) → lower (structured control flow → blocks + br/goto) → layout (AST → KV)`. The `lower` and `ast` stages are intermediate in-memory representations. What is eliminated is any opaque, off-tree serialized form — `.src` source copies and instruction slots coexist as plaintext in KV, and the PC addresses the instruction tree directly.

- **"Fully plaintext persistent VM / crash recovery"** — CONFIRMED. All execution state (code slots, frames, PC, `rparam`/`wparam` redirects, status) is stored as plaintext keys/values in kvspace; the PC is persisted on every instruction, enabling crash recovery from the KV tree alone. Error paths write terminal state and messages via `vthread.SetError` (e.g. `RecursionError` at `MaxStackDepth = 256` in `kvcpu/execute.go`), satisfying Section III's "error diagnosis/exception handling" requirement.

- **"Non-invasive debugging / observability"** — CONFIRMED, beyond what the doc states. An in-band debugger lives inside `kvcpu` (Section V's "no process attach" requirement): an agent activates single-step mode by writing `/vthread/<vtid>/.debugger`, and pause/resume commands are exchanged through `.debug.pause` / `.debug.resume` keys (`kvcpu/execute.go`, `keytree.VThreadDebuggerPause/Resume`). Section V's "instruction replay / breakpoint-and-resume" is only partially reflected: step/pause/resume and the per-op `.callpc` progress key exist, but no full instruction-replay log facility is present in the code.

- **"Uniform source/IR/instruction/persistent-storage representation"** — MOSTLY CONSISTENT. The KV tree holds instruction slots (`[i,j]`), frame state, and `.src` source copies (`keytree.LibSrc` → `keytree.SrcExt = ".src"`) side by side, and `rwir`/`kvcpu` execute the tree directly. The four representations are unified in the sense that the same KV path space carries all of them; they are not identical objects.

**Recency note:** The Go source is current (last commits touching parser/rwir date 2026-08-03; `go.mod`/deepx-design submodule updates 2026-08-05) and no claim in this document is contradicted by it. The document describes no outdated APIs; the only refinement is that the "no intermediate layer" phrase should be read as "no intermediate serialized IR," since the parser/lower AST stages do exist in-process.
