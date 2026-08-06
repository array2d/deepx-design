# Member Access and Data Structures

## The `.` Operator — Standard Member Access on kvspace Paths

`ptr.val` → `at(ptr, "val")` → `kv.Get(ptr/val)`. In the Pratt loop, `.` acts as a postfix operator, mirroring C `ptr->val` and Go `ptr.val`. On the write side, `42 -> ptr.val` expands to `set(ptr, "val", 42) -> ptr`. The scanner treats `.` both as a token separator and as a standalone `Dot` token; the `at`/`set` builtins accept string field names to concatenate into kvspace paths.

### Static fields: `h.field`

```
h.field  →  at(h, "field")    # field is a literal string
```

When parsing, after Pratt consumes `.`, an ordinary identifier read next is passed to `at` as a `StrLit`.

### Dynamic dereference: `h.*key`

```
h.*key  →  at(h, key)         # key is a variable; its value is used as the path segment name
```

When parsing, after Pratt consumes `.`, it reads `*` + an identifier, which is passed to `at` as a bare `Leaf` — no stringification. This is the syntax foundation of kvlang's built-in hash map:

```kvlang
"/tmp" -> h           # h = path prefix
2 -> key              # key = 2
h.*key                # at("/tmp", 2) → reads /tmp/2
```

Comparison with conventional languages:

| Language | Static field | Dynamic field |
|------|---------|---------|
| kvlang | `h.field` | `h.*key` |
| Python | `h["field"]` | `h[key]` |
| Go | `h.field` | `h[key]` (map) |
| JS | `h.field` | `h[key]` |

**Working with None**: when `at` cannot find a key, it returns None. In that case using `has` to test existence is more direct. An O(1) hash map that unlocks hundreds of LeetCode problems.

See `doc/kvlang/design/kvspace-hash-map.md`.

### struct ≡ dict: equivalence in kvspace

kvlang does not distinguish struct from dict. In kvspace they are **the same thing: a family of keys sharing a common prefix**.

| Language-level view | kvspace-level reality |
|-----------|---------------|
| struct: field names known at compile time | `base` + literal member name, `obj.prop` → `at(obj, "prop")` |
| dict: keys dynamic at run time | `base` + member name taken from a variable's value, `obj.*key` → `at(obj, key)` |
| linked-list node: `val` + `next` pointer | key family `{val, next}`; `next` stores the next node's path string (§8 variable names are pointers) |
| array: index `a[i]` | `base` + integer member name, `a[i]` → `at(a, i)` |

kvspace has no type boundaries: the same key family can be used as a struct (static fields), a dict (dynamic keys), and an array (integer keys) all at once. The static/dynamic distinction exists only at the **syntax level** (`.field` vs `.*key` vs `[i]`); it disappears entirely once lowered to `at`/`set`.

**Dict literal and type marker**: `a = { attr1="s1"; attr2=2; attr3=None }` is first-class syntax for creating a key family —
desugared to `dict("attr1", "s1", ...)`; the base key `a` receives a zero-payload marker value of `kind="dict"`,
and members are written to the flat key family `a.attr1`, `a.attr2`; members whose value is `None` are **not written** —
in kvspace, absence is None. The dict marker is not a string value, so member resolution automatically falls back to name-based addressing (§10.4);
`at`/`set` also explicitly recognize a base with `kind=="dict"` to force path mode. Key-value pairs are separated by `;`, a newline, or a comma,
and the `=` within uses the same token shape as the assignment operator (fix-010).

**Member separator unified to `.`** (fix-009): member concatenation in `at`/`set`/`dget`/`dset`/`kvat`/`kvhas` all goes through `keytree.Member(base, name)` (`base + "." + name`). Linked-list nodes are persisted as flat keys `/n0.val`, `/n0.next` — zero subtrees.

### Member resolution rules: value-first, name-fallback

For the expression `base.name` (identical rules on the read and write sides), the resolution of `base` is:

1. **Value dereference**: `base` holds a non-empty string value (a path pointer) → member key = `value(base).name`. E.g. after `"/n0" -> p`, `p.next` → `/n0.next`.
2. **Name fallback**: `base` has no value (or is non-string) → member key = `resolve(base).name`, where `resolve()` is frame-aware: a bare name → `frameRoot/base`, a `/`-prefixed name → passthrough. E.g. local key family `chars.0` → `frameRoot/chars.0`; literal `/n0.val` → `/n0.val`.

This rule lets "local struct" and "pointer dereference" share a single syntax: a key family's base is never assigned a value (stays name-based), while a pointer variable stores a path string (triggers value-based).

**Legacy inconsistency (pending convergence)**: `dget`/`dset` still address purely by name (`frameRoot/variableName.key`) and do not follow the value-first rule.

## Implementation Consistency Notes

Cross-checked against the kvlang Go sources: `parser/inst.go`, `parser/parser.go`, `parser/scanner.go`, `lower/lower.go`, `rwir/builtin/array.go`, `rwir/builtin/kvop.go`, `rwir/builtin/dict.go`, `rwir/builtin/helper.go`, `rwir/builtin/resolve.go`, `keytree/member.go`, `keytree/const.go`, `keytree/frame.go`, `kvcpu/execute.go`, plus kvspace-go.

**CONFIRMED items:**

- `.` as a postfix operator in the Pratt loop — `parser/inst.go` (`parsePratt`) lowers `expr.field` → `at(expr, "field")` (as `StrLit`) and `expr.*key` → `at(expr, key)` (as a bare `Leaf`). Matches the doc exactly.
- Write-side expansion — `parser/inst.go` (`desugarMemberWrite`, fix-015) lowers `expr -> base.field` / `base.*key <- expr` to `set(base, "field", expr) -> base` / `set(base, key, expr) -> base`. Matches `42 -> ptr.val` → `set(ptr, "val", 42) -> ptr`.
- Scanner — `.` is a standalone `Dot` token kind (`parser/scanner.go`: `Dot // .`, and `'.': Dot` in the token map); it also separates identifiers inside dotted names. Matches the doc.
- `at` returns None on a missing key — `atOp` (array.go) writes the `kvspace.GetOne` result directly with no error path for a miss; `hasOp` returns `!IsNone(v)`. Matches the doc.
- `kvat`/`kvhas` exist — `rwir/builtin/kvop.go`. Note: `kvat` raises `KeyError` on a missing key (unlike `at`, which returns None); `kvhas` returns a bool. The doc mentions them only in the fix-009 list and does not describe `kvat`'s erroring behavior.
- Dict literal desugaring — `parser/inst.go` turns `{ k1=v1; k2=v2 }` into a `dict("k1", v1, "k2", v2, ...)` call; separators are `;`, newline, or comma; the `=` uses the `Arrow` token with value `"="` (the same shape as the assignment operator, matching fix-010).
- Dict runtime — `rwir/builtin/dict.go` writes `kvspace.Dict{}` to the base key: kind `"dict"`, `ByteLen` 0, zero payload (`TLVEncode("dict", nil, 1)`); members are written as flat key-family keys via `keytree.Member`; None-valued members are skipped. Matches the doc.
- Member separator — `keytree.Member(base, name)` = `base + MemberSep + name`, with `MemberSep = "."` (`keytree/member.go`, `keytree/const.go`). Matches the doc.
- Linked-list on-disk format — `tutorial/07-leetcode/021_merge_two_lists.kv` shows `/a0 = { val=1; next="/a1" }`, i.e. flat `/a0.val`, `/a0.next` keys with `next` holding a path string (zero subtrees). Matches the doc.
- Value-first / name-fallback resolution — implemented by `resolveBasePath` (`rwir/builtin/helper.go`): if the resolved value is None or a container kind (`dict`/`index`/`linkindex`/`extindex`/`rwfunc`/`rwir`) → `resolveKVPath(funcFrame, read.Name)` (frame-aware name resolution: bare name → `funcFrameRoot/name`, `/`-prefixed → passthrough); otherwise → `v.String()` (value dereference). For the cases the doc describes (unassigned/None bases and dict bases → name fallback; string path pointers → value dereference) the doc and code agree.

**DISCREPANCIES / STALE CONTENT:**

- **`dget`/`dset` no longer exist in the codebase.** A repo-wide search over Go, KV, Rust, and Markdown finds zero occurrences outside this document. Both (a) the fix-009 list naming `at`/`set`/`dget`/`dset`/`kvat`/`kvhas` and (b) the "Legacy inconsistency" paragraph describing dget/dset pure-name addressing refer to builtins that have been removed. The documented inconsistency is effectively resolved by deletion.
- **Rule 2 "base 无值（或非字符串）→ 按名回退" is only partially reflected in the code.** `resolveBasePath` falls back to name resolution only for None and container kinds. A variable holding a non-string, non-container value (e.g. an int) does not fall back to name — it is dereferenced via `v.String()`. (Additionally, in `atOp` an int-typed base combined with a string index is rejected with `IndexError: at: index must be integer for typed array`.) The doc's blanket "or non-string" clause is broader than the implementation.
- **`kv.Get(ptr/val)` is conceptual shorthand.** The actual kvspace-go API is `GetOne(kv, key)` (helper in `kvspace-go/kvspace_common.go`) or `kv.Get(prefix, keys []string)`; member keys are built as `base + "." + name`, not joined with `/`.
- **The referenced design doc `doc/kvlang/design/kvspace-hash-map.md` does not exist** in the repo (only `deepx-design/doc/kvspace-design-and-implementation/draft/kvspace-c-hash.md` was found). The reference is stale.
- **fix-009 and fix-010 issue records are not present** under `deepx-design/issue/` (the directory currently contains only `tothink-`/`reject-`/`todo-` prefixed files). The fix-* references in the doc are historical; by contrast, fix-013, fix-015, fix-025, fix-027, and fix-030 are still cited in the Go source comments.
- **Section cross-references (§8 "variable names are pointers", §10.4 name-fallback)** point to sibling documents in the same series and are preserved as-is.
- **Out of the doc's scope but present in the code:** `at`/`set` treat string bases not starting with `/` as character sequences for `s[i]` read/write (fix-025), and typed arrays (int32/float64/…) reject string indices. These do not contradict the doc but are not covered by it.
