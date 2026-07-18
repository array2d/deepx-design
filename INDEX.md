# deepx-design Document Index

> This index covers all documents in the `deepx-design` repository, organized by directory structure. Each entry includes a summary of the document's core content and purpose.
> Generated: 2026-07-17

---

## Directory Tree

```
deepx-design/
├── README.md                          # Repository overview
├── DESIGN.md                          # 🔴 Root design specification
├── INDEX.md                           # 📖 This file
│
├── .claude/                           # Claude project rules & standards
│   ├── claude.md                      # Core design principles (p0-p6)
│   ├── writing-standards.md           # Article/documentation writing standards
│   ├── test-checklist.md              # Pre-commit test checklist
│   └── design-review.md               # Design document quality review standard
│
├── doc/
│   ├── LANGUAGE_SPEC.md               # 🟢 Formal language specification v0.1
│   ├── reference/
│   │   └── zerolang-analysis.md       # Zerolang competitive analysis
│   ├── kvspace/
│   │   ├── server.md                  # kvspace server design
│   │   ├── shm.md                     # Shared memory kvspace design
│   │   ├── hash-map.md                # kvspace path as hash map (h.*key)
│   │   ├── typed-value.md             # TLV-encoded typed Value
│   │   ├── rdma-distributed.md        # RDMA distributed kvspace
│   │   └── debug-kvspace.md           # kvspace debug skill
│   └── kvlang/
│       ├── spec/
│       │   ├── README.md              # kvlang language definition (meta-level)
│       │   ├── grammar.bnf            # BNF formal grammar
│       │   ├── dev-guide.md           # Agent development guide
│       │   └── lang-reference.md      # 5-language comparison matrix (C/V8/Go/Rust/Python)
│       ├── draft/
│       │   ├── architecture-analysis.md     # Full codebase architecture analysis (~5000 LoC)
│       │   ├── compile-opt-dynamic-schedule.md # Compile optimization: dynamic scheduling & tensor parallelism
│       │   ├── compiler-analysis-ssa-vs-arrow.md # →/← vs SSA compiler analysis comparison
│       │   ├── control-flow-dependency.md    # Control flow keyword hierarchy
│       │   ├── control-flow.md               # Control flow design (MLIR comparison)
│       │   ├── dot-key-system-stack.md       # System stack "." key full audit
│       │   ├── kvcpu-agent-debugger.md       # kvcpu Agent debugger design
│       │   ├── parser-frontend-design.md     # Compiler frontend design
│       │   ├── repo.md                       # GitHub project optimization assessment
│       │   ├── spec-control-flow-v1.md       # Control flow architecture analysis v1 (5 alternatives)
│       │   ├── test.md                       # Test tooling notes (udp/unixsocket)
│       │   └── triton-gpu-integration.md     # Triton/CUDA GPU integration
│       └── design/
│           ├── array-indexing.md             # Array & multi-dimensional index design
│           ├── deep-dive.md                  # 🟡 Deep dive: addressing model, instruction space, function semantics
│           ├── global-variables.md           # Global variables (absolute path) design
│           ├── pointer-semantics.md          # Pointer semantics design (string vs address)
│           ├── self-evolving-robot.md        # Self-evolving robot (ultimate scenario)
│           ├── transparent-execution.md      # kvspace transparent execution vs traditional
│           ├── unix-plan9-to-kvlang.md       # From Unix/Plan 9 to kvlang lineage
│           └── use-cases.md                  # 5 major use cases (inference/training/RL/Agent/self-iteration)
│
├── issue/                          # tothink- / reject- / todo- / fix- 四前缀
│
├── post/
│   └── reddit-post.md                 # Reddit launch post draft (English)
│
├── internal/
│   ├── ast/DESIGN.md
│   ├── parser/DESIGN.md + todo.md
│   ├── lower/DESIGN.md + todo.md
│   ├── layoutcode/DESIGN.md + todo.md
│   ├── kvcpu/DESIGN.md + todo.md
│   ├── kvspace/DESIGN.md + todo.md
│   ├── keytree/DESIGN.md + todo.md
│   ├── vthread/todo.md
│   ├── vtype/todo.md
│   ├── op/builtin/todo.md
│   └── op/dispatch/todo.md
│
└── cmd/kvlang/todo.md
```

---

## 1. Top-Level Design Docs

### [README.md](README.md) — Repository Overview
**Purpose**: Entry point, structure overview, and design tree introduction.
- Directory structure diagram
- Submodule usage: `git submodule update --init deepx-design`
- Design tree rule: every package implementation must conform to ancestor node constraints

### [DESIGN.md](DESIGN.md) — Root Design Specification 🔴
**Purpose**: The supreme design constitution for kvlang. Root of all child design docs. 73 lines.

**Core content**:
- **§0**: Positioning — agent-native, train-inference unified, self-iterating AI compute architecture
- **§1**: Address space — kvspace tree paths (`/src/` `/func/` `/vthread/` `/sys/`)
- **§2**: Instruction classification — read-write code / control flow primitives / high-level syntax (lowered away)
- **§3**: Execution model — PC = KV path string, path depth = call stack depth
- **§4**: Module responsibilities — ast/parser/lower/keytree/layoutcode/kvcpu/kvspace/vthread/vtype/builtin
- **§5**: Forbidden items (R1-R6)

**Comparison matrix with LLVM/JVM**: single-layer IR / tree address space / read-write code / path depth = stack depth / multi-worker + path ownership / crash recovery

---

## 2. Claude Project Rules (`.claude/`)

### [claude.md](.claude/claude.md) — Core Design Principles
**Purpose**: Supreme tenets guiding Claude agent behavior.
- **p0**: Elegant, perfect design; minimal, beautiful code; do more with less
- **p1**: Design should be as perfect, elegant, and concise as mathematics
- **p2**: Hate redundant code caused by wrong architecture
- **p3**: Fewer lines per package, via architecture design not formatting tricks
- **p4**: DESIGN tree driven — read all ancestor DESIGN.md files before modifying a package
- **p5**: Never backward compatible — garbage code should be deleted
- **p6**: `.kv` example files must balance `A -> ./B` and `./B <- A` styles (~50/50)

### [writing-standards.md](.claude/writing-standards.md) — Article/Doc Writing Standards
**Purpose**: Specifies how to write external articles about kvlang.
- **Core concepts (C1-C7)**: code & data share tree / path depth = stack depth / PC = string / transparent execution / agent transparent / crash recovery / kvspace abstraction
- **Narrative structure**: first paragraph must have positioning statement; code/path examples within first 3 screens
- **Comparison targets**: JVM/CPython/WASM/gdb/Plan 9 only
- **Audience segments**: PLT researchers / AI practitioners / systems engineers / Chinese community
- **Forbidden items (B1-B9)**: no hype words, no company endorsement, no disparaging, no promising unfinished features

### [test-checklist.md](.claude/test-checklist.md) — Pre-Commit Test Checklist
**Purpose**: Complete test checklist that must pass before every commit to `cmd/kvlang/` or `internal/`.
- Build & static checks: `go build/vet/test`, keytree hardcoded path checks, Redis leakage checks
- CLI commands: help / load / run (file/-c/pipe/serve) / vet / format / kvspace CRUD
- Architecture compliance: zero Redis leaks, zero hardcoded paths, zero hardcoded opcodes
- Quick regression: 30s and 60s levels

### [design-review.md](.claude/design-review.md) — Design Doc Quality Review
**Purpose**: Uses kvcpu/DESIGN.md (14/14 score) as template to audit the other 5 design docs.

| Document | Lines | Score | Primary Gap |
|----------|-------|-------|-------------|
| **kvcpu** | 423 | 14/14 ✅ | — (template) |
| keytree | 328 | 9/14 | Design goals table |
| ast | 332 | 8/14 | Design goals table + numbered prohibitions |
| parser | 230 | 8/14 | Design goals table + industry comparison |
| layoutcode | 198 | 9/14 | Design goals table |
| **root** | 73 | 4/14 | **Severely insufficient — needs rewrite to 300+ lines** |

---

## 3. Language Spec & Formal Docs (`doc/`)

### [LANGUAGE_SPEC.md](doc/LANGUAGE_SPEC.md) — Formal Language Spec v0.1 🟢
**Purpose**: Official language specification (English). 10 chapters.
- §1 Overview — design principles/Hello World
- §2 Lexical Structure — comments/identifiers/literals/keywords/operators
- §3 Type System — int/float/bool/str + type annotations + implicit coercion
- §4 Path Addressing — path syntax/resolution/KV path layout
- §5 Functions — definition/calling/multi-return/entry point
- §6 Control Flow — if/else/while/for-in/break/continue
- §7 Expression Syntax — prefix/infix/C-style
- §8 Execution Model — vthread/Worker Pool/TCO
- §9 Built-in Functions — arithmetic/casting/IO/string
- §10 KVSpace — TLV encoding/soft links
- Appendix A: Complete BNF

### [spec/README.md](doc/kvlang/spec/README.md) — kvlang Language Definition (Meta-Level)
**Purpose**: kvlang language design (kvir = kvlang's instruction view).
- **Unified syntax philosophy**: same syntax serves as VM instructions / high-level language / compiler IR / human-readable source
- **Execution model**: distributed base (kvspace/heap-plat/op-plat/multi-worker VM) + single-threaded language (simple as SQL)
- **Type system**: f16-64, bf16/8, i8-64, tensor<shape,elem_type>, dynamic dims `?1`
- **Single-quote `'` vs double-quote `"`**: single-quote = KV path, double-quote = string literal
- Full fused_linear_norm and fusion attention examples

### [spec/grammar.bnf](doc/kvlang/spec/grammar.bnf) — BNF Formal Grammar
**Purpose**: Formal grammar definition (lexical + syntax rules).

### [spec/dev-guide.md](doc/kvlang/spec/dev-guide.md) — Agent Development Guide
**Purpose**: Quick reference for Claude agent development.
- Quick verification: vet/run/-c/pipe
- Language overview: instruction forms/functions/paths/control flow/terminal output
- Built-in libraries: VM native eval / control flow / tensor lifecycle
- Writing patterns & common errors
- Claude debugging tips

### [spec/lang-reference.md](doc/kvlang/spec/lang-reference.md) — 5-Language Comparison Matrix
**Purpose**: kvlang design decisions benchmarked against top industrial languages (C/V8/Go/Rust/Python). 9 dimensions.

- Lexical analysis / parsing / type systems / memory / concurrency / error handling / toolchain / execution model
- **kvlang differentiators**: PC = path string / crash recovery / observability / read-write code / Agent API = KV API

---

## 4. Design Scenarios & Deep Dives (`doc/kvlang/design/`)

### [use-cases.md](doc/kvlang/design/use-cases.md) — 5 Major Use Cases
**Purpose**: What kvlang is/isn't + 5 core use cases (all with code examples + comparisons).

| Scenario | Core Value | Alternative |
|----------|-----------|-------------|
| AI Inference | Recoverable, monitorable | vLLM, TGI |
| Distributed Training | Control & compute decoupled | DeepSpeed, Megatron |
| Reinforcement Learning | Hot-swappable components | RLlib, IMPALA |
| Agent Workflow | Persistence + human intervention | LangChain, AutoGen |
| AI Self-Iteration | Code IS data | No direct equivalent |

Not suitable for: HFT / systems programming / standalone CLI / frontend Web

### [deep-dive.md](doc/kvlang/design/deep-dive.md) — Deep Dive 🟡
**Purpose**: The most in-depth document on kvlang core design. 11 chapters.
- §1 Addressing model: KV path vs memory address (x86/Python/Lua/kvlang comparison)
- §2 Instruction 2D space `[s0,s1]`: s1<0=read, s1=0=opcode, s1>0=write
- §3 Functions have NO return values: only read-params & write-params. Bare identifier `-> s` is always wrong
- §4 Control flow: label = path, goto = call(label), zero lookup
- §5 Compiler architecture comparison (Python/Lua/kvlang)
- §6 layoutcode design principles
- §7 Design decision summary table
- §8 Variable name IS pointer
- §9 XValue kind system
- §10 `.` operator — kvspace path member access
- §11 AST Quote field

### [unix-plan9-to-kvlang.md](doc/kvlang/design/unix-plan9-to-kvlang.md) — Historical Lineage
**Purpose**: Tracing kvlang's design roots through Unix/Plan 9 lineage. 6 pioneer paths.
- SECD machine (Landin, 1964) — first functional VM
- Plan 9 9P + per-process namespace
- Linda coordination language (tuple space)
- Smalltalk image-based persistence
- Erlang/BEAM — lightweight processes + persistent state
- Redis as execution infrastructure

### [self-evolving-robot.md](doc/kvlang/design/self-evolving-robot.md) — Self-Evolving Robot
**Purpose**: kvlang's ultimate scenario — robot self-analysis/self-design/self-training/self-iteration, no external help.

### [transparent-execution.md](doc/kvlang/design/transparent-execution.md) — Transparent Execution vs Traditional
**Purpose**: 4 execution models (compiled binary/Python/Shell/kvspace) compared from Agent perspective.

### [pointer-semantics.md](doc/kvlang/design/pointer-semantics.md) — Pointer Semantics Design
**Purpose**: String vs pointer type distinction problem. 5-language comparison (C/V8/Go/Rust/Python).

### [global-variables.md](doc/kvlang/design/global-variables.md) — Global Variables Design
**Purpose**: `/` absolute paths are naturally global variables, zero syntax change. 5-language comparison.

### [array-indexing.md](doc/kvlang/design/array-indexing.md) — Array & High-Dimensional Index Design
**Purpose**: Array literal `[1,2,3]`, index `a[i,j]`, iteration syntax. Benchmarked against C/Go/Rust/Python/JS.

---

## 5. Design Drafts (`doc/kvlang/draft/`)

| Document | Core Content |
|----------|-------------|
| [architecture-analysis.md](doc/kvlang/draft/architecture-analysis.md) | Full codebase layer analysis, call chain tracing, dead code check (2026-07-10) |
| [dot-key-system-stack.md](doc/kvlang/draft/dot-key-system-stack.md) | vthread-layer `.pc`/`.status`/`.debug` system keys full audit (2026-07-15) |
| [compiler-analysis-ssa-vs-arrow.md](doc/kvlang/draft/compiler-analysis-ssa-vs-arrow.md) | `->`/`<-` vs SSA: 6 compiler analysis capability comparison; `resolve` fills φ gap |
| [control-flow-dependency.md](doc/kvlang/draft/control-flow-dependency.md) | Control flow keyword hierarchy; identifies the minimal set (call/return/br/goto) |
| [control-flow.md](doc/kvlang/draft/control-flow.md) | 4-layer comparison (Assembly IR/kvir/MLIR/C), basic block model |
| [spec-control-flow-v1.md](doc/kvlang/draft/spec-control-flow-v1.md) | 5 sub-alternatives full spectrum comparison (C1-C5) |
| [parser-frontend-design.md](doc/kvlang/draft/parser-frontend-design.md) | Standard frontend pipeline vs current line-splitting approach |
| [kvcpu-agent-debugger.md](doc/kvlang/draft/kvcpu-agent-debugger.md) | Agent debugger: `.debug`/`.debug.pause`/`.debug.resume` protocol |
| [triton-gpu-integration.md](doc/kvlang/draft/triton-gpu-integration.md) | Triton/CUDA async message dispatch architecture |
| [compile-opt-dynamic-schedule.md](doc/kvlang/draft/compile-opt-dynamic-schedule.md) | kvlang compile optimization vs Triton kernel layer: orthogonal division of labor |
| [repo.md](doc/kvlang/draft/repo.md) | GitHub project 7-dimension optimization assessment |
| [test.md](doc/kvlang/draft/test.md) | Test tooling notes |

---

## 6. kvspace Docs (`doc/kvspace/`)

| Document | Core Content |
|----------|-------------|
| [server.md](doc/kvspace/server.md) | kvspace server design |
| [shm.md](doc/kvspace/shm.md) | Shared memory kvspace design |
| [hash-map.md](doc/kvspace/hash-map.md) | kvspace path as hash map (h.\*key syntax) |
| [typed-value.md](doc/kvspace/typed-value.md) | TLV-encoded typed Value, unified with vtype.VType namespace |
| [rdma-distributed.md](doc/kvspace/rdma-distributed.md) | kvspace's nature: multi-component shared data plane + Raft + RDMA |
| [debug-kvspace.md](doc/kvspace/debug-kvspace.md) | kvspace debug skill |

---

## 7. Competitive Analysis (`doc/reference/`)

### [zerolang-analysis.md](doc/reference/zerolang-analysis.md) — Zerolang Deep Dive
**Purpose**: Analysis of zerolang (vercel-labs, 5201 stars, 1 person/2 months) and lessons for kvlang.
- Graph-First vs KV-path (kvlang IS naturally a graph)
- Agent-First design gaps: structured query / Patch protocol / diagnostic JSON
- 5000 stars truth: Vercel brand > technical merit
- 4 issue deep dives (#68 type strcmp / #348 node ID stability / #290 syntax instability / #181 code quality)
- Breakthrough strategy: narrative reframing ("code IS data") / ride zerolang hype / brand building

---

## 8. Issue Tracking (`issue/`)

4 种前缀，详见 `.claude/claude.md`：

| 前缀 | 含义 |
|------|------|
| `tothink-` | 需要深度分析 |
| `reject-` | 刻意不做 |
| `todo-` | 待解决 |
| `fix-` | 已经解决 |

---

## 9. Public & Launch (`post/`)

### [reddit-post.md](post/reddit-post.md) — Reddit Launch Post
English post: "KVLang: A programming language where execution state lives entirely in KV storage"

---

## 10. Internal Module DESIGN Specs & Todos (`internal/`)

### Design Tree
```
DESIGN.md (root, 73 lines, 4/14 score) ← needs rewrite to 300+ lines
├── ast/DESIGN.md              (332 lines, 8/14)
├── parser/DESIGN.md           (230 lines, 8/14)
├── lower/DESIGN.md
├── layoutcode/DESIGN.md       (198 lines, 9/14)
├── kvcpu/DESIGN.md            (423 lines, 14/14 ✅ template)
├── kvspace/DESIGN.md
└── keytree/DESIGN.md          (328 lines, 9/14)
```

### Todo Summary by Package

| Package | Main TODOs |
|---------|-----------|
| **cmd/kvlang** | Dual vtid generation strategy / non-atomic incrVtid → Redis INCR / JSON serialization violates scalar principle / mainWatcher 1s polling → Watch / hardcoded vtid="run" |
| **parser** | ⚠️ S11 write slot KV path validation / ⚠️ S9 TopLevelCalls→init() merge / S4 linear lookahead / S5 error recovery / S1 Token Span / S2 byte offset pos / S7 EBNF |
| **lower** | P10 if→br full coverage, delete OpIf compat / P11 for/break/continue (✅ done) |
| **layoutcode** | P0-8 block label fakes func signature / P1-1 unused ctx / P1-11 kv.Set errors ignored |
| **kvcpu** | P0-2 duplicate isCopyOp logic / P0-3 negative number literal unsupported / P1-1 unused ctx |
| **kvspace** | P1-5 links cache stale in multi-instance / P1-6 per-layer SADD |
| **vthread** | P1-1 unused ctx / P1-2 vtid generation strategy unified |
| **op/builtin** | P0-2 unexported functions / P0-6 triple map redundancy / P1-4 duplicate with dispatch / P1-12 str.set misleading name |
| **op/dispatch** | P1-4 duplicate with builtin / P1-10 OpTask type mixed into generic dispatch |
| **keytree** | (all resolved) |
| **vtype** | (all resolved) |

---

## 11. Recommended Reading Paths

### 🌱 Newcomer (1-2 hours)
1. [README.md](README.md) — understand repo structure
2. [DESIGN.md](DESIGN.md) — grasp core architecture (73 lines, quick)
3. [LANGUAGE_SPEC.md](doc/LANGUAGE_SPEC.md) — learn language syntax
4. [dev-guide.md](doc/kvlang/spec/dev-guide.md) — start writing code

### 🔬 Deep Understanding (3-4 hours)
5. [deep-dive.md](doc/kvlang/design/deep-dive.md) — **MUST READ!** Instruction space/function semantics/compiler architecture
6. [use-cases.md](doc/kvlang/design/use-cases.md) — all 5 scenarios
7. [lang-reference.md](doc/kvlang/spec/lang-reference.md) — industry benchmarking

### 🧠 Design Research (4-6 hours)
8. [unix-plan9-to-kvlang.md](doc/kvlang/design/unix-plan9-to-kvlang.md) — historical lineage
9. [compiler-analysis-ssa-vs-arrow.md](doc/kvlang/draft/compiler-analysis-ssa-vs-arrow.md) — SSA vs arrows
10. [zerolang-analysis.md](doc/reference/zerolang-analysis.md) — competitive analysis
11. [design-review.md](.claude/design-review.md) — doc quality review
12. Draft series — control flow/frontend/debugger/GPU/compile-opt/distributed/TLV

### 🔧 Engineering Maintenance
- [test-checklist.md](.claude/test-checklist.md) — must-read before commit
- [issue/](issue/) — `tothink-` / `reject-` / `todo-` / `fix-`
- [architecture-analysis.md](doc/kvlang/draft/architecture-analysis.md) — full codebase walkthrough
- [dot-key-system-stack.md](doc/kvlang/draft/dot-key-system-stack.md) — system key spec

### 📣 External Publishing
- [writing-standards.md](.claude/writing-standards.md) — must-read before writing articles
- [reddit-post.md](post/reddit-post.md) — launch post template

---

## Statistics

| Category | Count |
|----------|-------|
| Top-level design | 2 |
| Claude rules | 4 |
| Language spec/formal | 5 |
| Design scenarios & deep dives | 8 |
| Design drafts | 13 |
| Competitive analysis | 1 |
| Issue tracking | 18 |
| Launch/PR | 1 |
| Internal module DESIGN specs | 6 |
| Internal module todos | 11 |
| **Total** | **~69** |
