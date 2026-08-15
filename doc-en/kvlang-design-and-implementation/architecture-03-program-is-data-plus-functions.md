# Program = Data Structures + Functions + Data

## Program = Data Structures + Functions + Data

Niklaus Wirth's classic formula "Program = Data Structures + Algorithms" has dominated for half a century — algorithms are active transformations, and data structures are the type frameworks that algorithms operate on. In Wirth's era, the programmer first designed data structures and then wrote algorithms for them.

kvlang's proposition: **Program = Data Structures + Functions + Data**. Data structures are no longer user-defined — every data structure in kvlang is builtin: `struct ≡ dict` (key-family prefix), `array` (TLV or key-family integer keys), and `link list` (`/n0.val`, `/n0.next` flat keys, with `"/n1"` storing a path string, i.e. a pointer). When programming an agent there is no need to "design data structures" — you only declare types (`lib name { }`) and write functions (`def`), and the data lands naturally in the four domains of kvspace (`/lib/` `/vthread/` `/sys/` `/dev/`).

Just as, once the DNA AT/GC base-pair mechanism was worked out, the next three billion years of biological evolution never reached for any other complex base-pair design — once the most fundamental data structure is locked in, everything above it converges into a combination of functions (proteins) and data (gene-expression products). kvlang is the same: the key family is its base pair; everything else is functions and data.

**kvlang is an agent-native, training-inference-integrated, self-iterating strong-AI computing architecture.** With kvspace tree paths as a unified address space, the same syntax simultaneously serves four roles: VM instructions, high-level language, compiler IR, and human-readable source code.

## Implementation Consistency Notes

The following claims in this document were cross-checked against the Go implementation under `/home/peng.li24/github.com/array2d/kvlang/`.

1. **`def` is not the function keyword — it is `rwfunc`.** The document says "write functions (`def`)", but no `def` token exists anywhere in `parser/scanner.go`, `parser/parser.go`, `ast/`, or `symbol/`. The parser only recognizes `rwfunc name(...) -> (...) { body }` for function declarations and `rwir` for rwir declarations (see `parser/parser.go` `parseFile`/`parseLibBody`). Note that `README_CN.md` still shows `def` in its examples, but the parser does not accept it. Either the design doc predates the rename, or it describes an intended-but-not-implemented syntax.

2. **`lib name { }` is a package namespace block, not a type declaration.** The document says "declare types (`lib name { }`)", but in the implementation `lib pkgname { ... }` is a package/namespace block (parser `parseLibBody`, supporting nested `lib a { lib b { } }` that composes names as `a.b.c`); functions inside are registered under `/lib/<pkg>.<name>/`. There is no user-declared type system in the current code — types appear only as annotations on function parameters/returns and as dict literals `{ name="kv"; ver=1 }`.

3. **Array representation: "TLV or key-family integer keys" does not fully match.** The implementation (`rwir/builtin/array.go`) stores arrays as single `XValue` values: a homogeneous fixed-width packed array when all elements share a fixed-size kind (`int32`, `float64`, etc.), otherwise a heterogeneous TLV array. There is no array-stored-as-key-family-with-integer-keys representation in the Go code. However, integer-keyed key-family member access is supported through `at`/`set` on dict/path bases via `keytree.Member` + `kvKey` (integer keys are stringified). The "键族整数键" alternative in the doc corresponds only to this at/set-on-dict access, not to how arrays are stored.

4. **`struct ≡ dict` (key-family prefix) is confirmed.** `keytree/member.go` documents `struct ≡ dict ≡ 键族` and implements `Member(base, name) = base + "." + name`, so `obj.a`, `obj.b` are sibling flat keys (member separator `.` is the single definition `MemberSep` in `keytree/const.go`).

5. **Link-list representation is confirmed.** Tutorial examples such as `tutorial/07-leetcode/206_reverse_linked_list.kv` build nodes with `/n0 = { val=1; next="/n1" }` and read `.val`/`.next`; a path string stored in a variable acts as a pointer, and member access on it dereferences to the pointed-to key.

6. **The four kvspace domains are confirmed.** `keytree/const.go` defines the path segments `lib`, `vthread`, `sys`, `dev`; `keytree/entry.go` (`LibRoot`, `LibFunc`, `LibSrc`) covers `/lib/`, `keytree/vthread.go` (`VthreadRoot`) covers `/vthread/`, `keytree/sys.go` (`SysRoot`, `/sys/op/…`, `/rwir/…`) covers `/sys/`, and `keytree/dev.go` (`DevTTY` → `/dev/tty/<name>/<stream>`) covers `/dev/`.

7. **"Same syntax as VM instructions / high-level language / compiler IR / human-readable source" is broadly consistent.** Compiled rwir instructions are stored in KV at `/lib/<pkg>.<name>/[i,j]` and decoded at runtime by `rwir.Decode` (`rwir/rwir.go`); `rwir`-keyword declarations act as IR with signatures but no bodies (`layout.WriteRwir`); source text is archived at `<func>.src` via `keytree.LibSrc` (`fix-034`); and the same readable syntax is the user-facing source. The only correction is that the function declaration keyword is `rwfunc`, not `def` (see item 1).

Overall, the document appears to describe an earlier design: the function-keyword and type-declaration claims (`def`, `lib name { }` as a type) are out of sync with the current implementation, which uses `rwfunc` and treats `lib` purely as a namespace.
