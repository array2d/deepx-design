# deepx-design

Design documentation, architecture specs, and todo tracking for the **deepx ecosystem**. kvlang is deepx's VM — the interpreter, execution scheduler, and agent-native compute language.

## Structure

```
.claude/                        Claude project rules and standards
  claude.md                     Core design principles (p0–p6)
  writing-standards.md          Article/doc writing standards
  test-checklist.md             Test verification checklist
  design-review.md              Design doc review standard

DESIGN.md                       Root design spec (address space, execution model)

internal/
  ast/DESIGN.md                 HIR/LIR type system
  parser/DESIGN.md              Frontend pipeline
  lower/DESIGN.md               Control flow lowering
  layoutrwir/DESIGN.md          AST → KV layout
  kvcpu/DESIGN.md               CPU execution model
  kvspace/DESIGN.md             KV storage interface
  keytree/DESIGN.md             Key tree schema
  */todo.md                     Per-package improvement todos

doc/
  LANGUAGE_SPEC.md              Formal language specification
  kvlang/spec/                  Formal grammar and dev guide
  kvlang/draft/                 Draft design documents
  kvlang/design/                Design scenarios and analysis
  reference/                    Competitive analysis

issue/                          Tracked issues and verification notes
post/                           Blog posts and external articles
```

## Usage

This repo is consumed as a git submodule at `deepx-design/` inside the main [kvlang](https://github.com/array2d/kvlang) repo:

```bash
git submodule update --init deepx-design
```

## Design tree

Design specs form a tree rooted at `DESIGN.md`.
Every package implementation must conform to all ancestor specs on its path.
See `.claude/claude.md` for the full rule set.
