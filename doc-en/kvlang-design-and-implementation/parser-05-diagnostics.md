# Diagnostic Output

## Diagnostic Output Specification (logx)

**All stderr diagnostic output MUST go through the `logx` package; direct calls to `fmt.Fprint*` are forbidden.** The output format aligns with the five major-language compilers (GCC/Go/V8): `{level}: {context}: {msg}`, with no timestamps and no key=value pairs.

**logx API conventions:**

| Function | Prefix | Purpose |
|----------|--------|---------|
| `Debug/Info` | none | Operational messages, debug tracing (visible only under `LOG_LEVEL=debug`/`info`) |
| `Warn` | `warn: ` automatic | Recoverable warnings |
| `Error` | `error: ` automatic | Error messages |
| `Fatal` | same as Error | Error + `os.Exit(1)` |
| `Diag(d)` | carried by the Diagnostic | Single-line output of a parser diagnostic |
| `DiagWithSource(d)` | carried by the Diagnostic | Parser diagnostic + source line + `^` caret |

**Explicitly exempted from the `fmt.Fprint*` ban:**
- Usage text inside `flag.FlagSet.Usage` (unprefixed explanatory text)
- Output of the help command (full help document)
- `fmt.Printf` success status to stdout (e.g. `%s: OK`)
- Formatted output of `ast.Dump` / `ast.Format` (written to an `io.Writer`, not diagnostics)

**Principle: if you are writing a diagnostic, go through logx; if you are writing content (help / usage / formatted output / stdout results), go through fmt.**

---

## Implementation Consistency Notes

Cross-checked against the Go source at `kvlang/` (parser, cmd/kvlang, kvcpu, rwir, ast).

1. **Package path changed: `internal/logx` → `logx`.**
   The spec says "`internal/logx` 包". In the current source the package lives at the repo root: `kvlang/logx/logx.go`, `package logx`, imported as `"kvlang/logx"` across `cmd/kvlang/*.go`, `kvcpu/`, and `rwir/`. There is no `kvlang/internal/` directory anymore. Git commit `b2241ca` ("refactor: 架构拆分 — parser/ast 保留 Go，runtime 迁至 C++/Rust") moved `internal/logx` to the top-level `logx` and removed the old `internal/` Go runtime. The doc should say `logx` (import path `kvlang/logx`).

2. **The "five compilers" enumeration is inconsistent.**
   The spec writes "五大语言编译器（GCC/Go/V8）" — three named but "five" claimed. The source package comment in `logx/logx.go` (lines 8–9) enumerates five: GCC/Go/Rust/Python/V8. The doc's parenthetical list is out of date relative to the code.

3. **The format string matches.**
   `logx.Warn`/`logx.Error` prepend `warn: `/`error: ` to the caller's format string; `Diagnostic.String()` in `parser/scanner.go` renders `kind: src:line:col: msg` (e.g. `error: <inline>:3:5: expected X, got Y`), which is the `{level}: {context}: {msg}` shape the spec describes. `Fatal` indeed calls `Error` then `os.Exit(1)`.

4. **The exemption list matches the code.**
   - `flag.FlagSet.Usage`: `cmd/kvlang/{vet,run,ps,format,layout}.go` print their `usage: kvlang ...` text via bare `fmt.Fprintln(os.Stderr, ...)`.
   - help command: `cmd/kvlang/help.go` uses `fmt.Fprint(os.Stderr, helpText)`.
   - `%s: OK` stdout: `cmd/kvlang/vet.go` line 68 `fmt.Printf("%s: OK\n", name)`.
   - `ast.Dump` / `ast.Format`: `ast/dump.go` `Dump(w io.Writer, f *File)` and `ast/format.go` `Format(w io.Writer, f *File)`, both write to an `io.Writer`.

5. **Level gating and `LOG_LEVEL` match.**
   `logx.go` gates `Debug` to `LOG_LEVEL=debug` and `Info` to `debug`/`info`; default is `warn`. `Diag`/`DiagWithSource` honor the `Diagnostic`'s own Info/Warn flags (error diagnostics print down to `LOG_LEVEL=error`), consistent with the spec's "visible only under `LOG_LEVEL=debug`/`info`" claim. `DiagWithSource` is currently used only by `cmd/kvlang/vet.go` (which also sets `d.SrcName` before printing).

6. **Other diagnostic call sites are consistent.**
   `kvcpu/controlflow.go`, `kvcpu/execute.go`, and `rwir/dispatch/router.go` / `rwir/builtin/io.go` emit their trace messages exclusively through `logx.Debug`, with no stray `fmt.Fprint*` to stderr outside the exempted cases.
