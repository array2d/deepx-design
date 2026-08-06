# Type System（类型系统）


## Type System

kvlang is a **strictly typed language**. Every variable, parameter, and return value must have a definite type at compile time — typeless variables are not allowed, and a type may not implicitly change at runtime.

### Numeric types: no `float`, no `int`, only exact-width types

**kvlang has no `float` and no `int`.** There is no type named `float` or `int`; only numeric types carrying a concrete bit width exist:

| Category | Types |
|----------|-------|
| Signed integers | `int8` `int16` `int32` `int64` |
| Unsigned integers | `uint8` `uint16` `uint32` `uint64` |
| IEEE 754 floats | `float32` `float64` |

**Design rationale**: bit width is the core of the type contract. When `int64` propagates across kvspace-go (Go) → kvspace-cpp (C++) → kvregion shm (C struct) → op-gpu tensor dtype, every layer depends on precise width information. Ambiguous names such as `float` and `int` map to different widths across languages/platforms (C `int` is 32 bits on both ILP32 and LP64; Python `int` is arbitrary precision), which would break the cross-language type contract.

**Implementation constraint**: the XValue kind string accepts only the 10 exact names listed in the table above. Short names such as `"int"` and `"float"` are illegal kinds on every code path; code review must reject them.

### Type ownership rules

Type ownership is split into two levels:

| Owner | Where it lives | Example |
|-------|----------------|---------|
| **Function signature** | `/lib/<pkg>.<name>` — `string:rwfunc func(args) -> (rets)` | `rwfunc add(A:int, B:int) -> (C:int)` |
| **Instruction slot reference** | `/lib/<pkg>.<name>/[s0,s1]` — currently a `rwir:` text reference | `[0,-1]="A"` `[0,1]="C"` |
| **Runtime value** | `/vthread/<vtid>/<frame>/<var>` — an XValue carrying its kind | `A → int64:10`, `s → float64:3.14` |

**Iron rules**:
- Every parameter and return value in a `def` signature **must declare its type** (`name:type`)
- A rwfunc whose signature lacks a type **is rejected at load time** (parser error)
- The instruction slot `[s0,s1]` is a slot descriptor; type information lives in the signature, not in the slot
- At runtime, values are self-describing via the `kind` tag — no signature lookup needed
- Alignment with the five languages: C must declare, Rust must declare, Go must declare, TS optional but recommended, Python no declaration — kvlang takes the C/Rust/Go camp

### XValue structure and the kind family

**XValue structure** (`github.com/array2d/kvspace-go`):

```go
type XValue struct {
    kind        string // vtype name, e.g. "int64" "float64" "string" "bool" "bytes" "array" "dict" "rwir"
    arraylength int32  // number of array elements; single value = 1, >1 means array
    raw         []byte // typed raw bytes, XValue owned (copied at construction)
}
```

**Complete kind list** (the kind strings actually persisted by kvspace-go are authoritative):

| kind string | constructor | description |
|-------------|-------------|-------------|
| `"int8"` `"int16"` `"int32"` `"int64"` | `Int8(v)` `Int16(v)` `Int32(v)` `Int64(v)` | signed integers, little-endian encoding |
| `"uint8"` `"uint16"` `"uint32"` `"uint64"` | `Uint8(v)` `Uint16(v)` `Uint32(v)` `Uint64(v)` | unsigned integers |
| `"float32"` `"float64"` | `Float32(v)` `Float64(v)` | IEEE 754 floats |
| `"bool"` | `Bool(v)` | 1 byte: 0=false, 1=true |
| `"string"` | `Str(v)` | raw UTF-8 bytes |
| `"bytes"` | `Bytes(v)` | raw binary bytes (copied at construction) |
| `"dict"` | `Dict()` | zero-payload type marker — members stored as a flat key family `base.name` |
| `"array"` | `Array(elems)` | fixed-length homogeneous array, raw stored contiguously |
| `"rwir"` | `Rwir(v)` | instruction-slot text reference (kvlang internal) |
| `""` | `None()` | None value (kind is the empty string); `IsNone()` returns true |

**Iron rule for kinds — no aliases.** kvlang does **not** support kind aliases. Short names such as `"int"` and `"float"` are illegal on every code path — you must use the full names `"int64"`, `"float64"`, etc., i.e. the exact strings listed above. The kind string is part of the cross-language type contract (kvspace-go → kvspace-cpp → kvregion shm → op-gpu tensor dtype); aliases would break the matching logic of every kind-aware middleware. Code that violates this rule (e.g. `kvspace.Raw("int", ...)`) must be rejected in code review.

> History: in `deepx-design/internal/kvspace/DESIGN.md` the kinds were written with short names like `"int"` `"float"` — that was a design draft and is **obsolete**. The `kind="int"` in `slotValue` has also been corrected to `"int64"` (fix-0721).

**TLV encoding**:

```
[1B kind_len][N B kind_name][4B arraylength LE][4B raw_len LE][M B raw_value]
```

| Field | Size | Description |
|-------|------|-------------|
| `kind_len` | 1B | number of bytes in kind_name (1~127; 0 means None) |
| `kind_name` | N B | vtype name, character set `[a-zA-Z0-9_]` |
| `arraylength` | 4B | number of array elements, uint32 LE, default = 1 (single value) |
| `raw_len` | 4B | number of bytes in raw_value, uint32 LE |
| `raw_value` | M B | typed raw data |

`IsNone()` encodes to nil (zero bytes). `DecodeXValue` copies the raw bytes internally (owned semantics, to avoid sharing a buffer with the Redis read buffer).

### Variable name is an address; having an address means having an XValue — unassigned means None

Every variable name in kvlang code is the **relative address** of that intermediate variable in kvspace. Declaring / first mentioning a variable name allocates a KV slot in the current frame (e.g. `/vthread/<vtid>/<frame>/x`). Therefore:

- **Having a variable name ⇒ having an address** (a relative address is still an address)
- **Having an address ⇒ having an XValue** (every slot corresponds to one XValue)
- **Having been assigned nothing ⇒ that XValue is None** (kind=`""`, `IsNone()` returns true)

This means there is no such thing as an "undefined variable" in kvlang — once the parser recognizes a variable, its slot already exists. The only difference is whether that slot's XValue is None or holds typed data. Having None participate in arithmetic / comparison / type conversion raises a TypeError directly, forcing code to initialize explicitly.

**Accessor tiers**:
- **Permissive readers**: `Int64()` decodes at the actual width of the kind and sign-extends (like Go `reflect.Value.Int`); `Uint64()` likewise. Arithmetic/comparison go through the permissive readers.
- **Exact accessors**: `Int8()` `Float32()` etc. strictly validate the kind string; they return the zero value on mismatch.

### Specifying a base type at variable definition (fix-021)

The ten numeric type operators are **both constructors and converters**, using the ordinary call form (zero parser changes):

```kv
f = float32(3)        # kind=float32, persisted        # = is equivalent to <-
i <- int8(0.1)        # 0 (float→int truncates toward zero)
int8(300) -> w        # 44 (narrowing = two's-complement wrap)
uint64(18446744073709551615)   # uint64 upper bound round-trips exactly
```

`int8/16/32/64 · uint8/16/32/64 · float32/float64`.

| Semantics | Alignment camp |
|-----------|----------------|
| float→int truncates toward zero | all five languages agree |
| narrowing = two's-complement wrap (`uint8(-1)`=255, `int32(2³¹)`=-2³¹) | Go conversion / Rust `as` / C |
| integer literal (2⁶³, 2⁶⁴-1] without decimals → uint64 | — |
| None input → direct TypeError | strict None — rejects None in numeric operations |

**Declared precision is the storage/transport type**: after `int16(-2) -> n; n -> /x`, `kvspace get /x` shows `int16:-2` — the precision enters the TLV kind and is persisted, which is how the cross-language type contract (kvspace-cpp / kvregion shm / tensor dtype) is established.

### Numeric operation domain

When a narrow type enters arithmetic it is **promoted into a unified operation domain** (C integer-promotion style), with three theorems:

1. **int ∧ int → native int64 arithmetic and comparison**, never routed through float64 (fix-020: float64 has only 53 mantissa bits, so `maxint64 - 1` and `2⁵³+1` used to silently lose precision / compare equal); overflow = two's-complement wrap (same as C/Go)
2. **Either side float → float64 promotion**; mixed comparison is C-style double promotion (`3 == 3.0` is true)
3. **None in arithmetic → direct TypeError** (strict None: refusing to implicitly convert None to 0)

Reader contract (kvspace-go): `Int64()`/`Uint64()` are **permissive readers** (like Go `reflect.Value.Int/Uint`: decode at the kind's actual width + sign extension); `Int8()`/`Float32()` etc. are strict, exact kind accessors. Consumers (arithmetic/display/String) always go through the permissive readers.

Semantic regression anchors: `tutorial/01-basics/precision.kv` (promotion and precision truth, 17 assertions), `numtypes.kv` (the ten operators, 12 assertions).

### Bool iron rule

**bool can only be `true`/`false`; all implicit coercion is forbidden.** This is stricter than the mainstream languages and is a deliberate design choice.

| Input | kvlang | Python | JS | C | Go |
|-------|--------|--------|-----|---|-----|
| `true`/`false` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `0`/`1` integers | **panic** — must write `!= 0` | ✅ if/while accepts | ✅ | ✅ | ❌ |
| non-empty string | **panic** — must write `!= ""` | ✅ | ✅ | N/A | N/A |
| `"false"` string | **panic** | ✅ truthy | ✅ truthy | N/A | N/A |
| None | **panic** | ✅ falsy | ✅ falsy | N/A | ❌ |

**Why stricter than the five languages?**

kvlang's design goal is "runtime code as data" — all state lives in KV space, readable and writable by external agents and plugins. Implicit coercion would mean the behavior of `if (x)` depends on x's runtime kind, and an agent that doesn't know x's type cannot determine the control-flow direction. Forcing explicit comparisons (`x != 0`, `x != ""`) makes control-flow conditions self-describing at the KV level: the cond slot of a `br` instruction is always a bool, and reading it tells you the truth value directly.

**Implementation**: `AsBool` (coerce.go) accepts only `kind=="bool"` and panics on any other kind. The `bool()` constructor converts arbitrary values to bool (`bool(0)`=false, `bool(1)`=true, `bool("")`=false), but that is an **explicit conversion**, not to be used in condition position.

## Implementation Consistency Notes

Cross-checked against the Go implementation (kvlang `rwir/builtin/*.go`, `kvcpu/execute.go`, `kvcpu/controlflow.go`, `rwir/rwir.go`, `rwir/frame.go`, `vthread/vthread.go`, `keytree/*.go`, `layout/layout.go`, `parser/*.go`) and `kvspace-go` (`xvalue.go`, `xvalue_*.go`, `const.go`).

1. **XValue is an interface, not the struct shown in this doc.** The document shows `type XValue struct { kind, arraylength, raw }`. The current kvspace-go defines `XValue` as an interface with methods `Kind()/Encode()/ByteLen()/ArrayLen()/String()`, implemented by per-kind concrete types (`Int8`, `Int16`, ..., `Char`, `Bool`, `Bytes`, `Dict`, `Rwir`, `Rwfunc`, `Time`, `Duration`, `None`). The closest thing to the documented struct is `XValueHead{Kind string; ArrayLen int32; Raw []byte}` (kvspace-go/xvalue.go), which is the TLV-decoded header, not the value itself.

2. **Constructor names have changed.** The kind table lists `Int8(v)`, `Int16(v)`, ..., `Str(v)`, `Bytes(v)`, `Rwir(v)`, `None()`. The current constructors are `NewInt8(v)`, `NewInt16(v)`, ... `NewChar(v)` (for the `"string"` kind — the string constructor is `NewChar`, not `Str`), `NewBytes(v)`, `NewRwir(numReads, numWrites, sig)` (three arguments, not one), and `None{}` (a zero value, not a constructor call). Array construction has no `Array(elems)` constructor — see item 3.

3. **The kind inventory is broader than the doc, and there is no `"array"` kind.** `const.go` additionally defines `"array1d"`, `"index"`, `"linkindex"`, `"extindex"`, `"rwfunc"`, `"scope"`, and the decode paths also handle `"time"` and `"duration"`. Arrays are not a separate kind: a multi-element array is stored under its element kind (e.g. `int64`) with `arraylength > 1` and raw bytes laid out contiguously. `KindArray1d = "array1d"` is defined but unused in both kvlang and kvspace-go, and no `Array()` constructor exists (array assembly lives in `arrayVal()` in `rwir/builtin/coerce.go`).

4. **`bool()` does not convert arbitrary values.** The doc's last section claims `bool(0)`=false, `bool(1)`=true, `bool("")`=false. In `rwir/builtin/cast.go`, `evalToBool` calls `AsBool`, which panics (`"AsBool: expected bool kind, got ..."`) for any kind other than `"bool"`. So `bool(0)`, `bool(1)`, `bool("")` all panic in the current implementation; only `bool(true)`/`bool(false)` work. The doc text is not updated for this.

5. **Accessor naming/location.** The "permissive readers `Int64()`/`Uint64()`" and "exact accessors `Int8()`/`Float32()`" do not exist as kvspace-go methods (no such method names were found in kvspace-go). The width-widening behavior described ("decode at actual width + sign extension", like `reflect.Value.Int`) is implemented by `asInt64()`/`asFloat()` in `rwir/builtin/coerce.go`, not by kvspace-go `Int64()`/`Uint64()` methods. No strict exact-kind accessor returning zero on mismatch exists.

6. **`DecodeXValue` is now `DecodeXValueHead`.** kvspace-go/xvalue.go exposes `DecodeXValueHead(data)` (returning `XValueHead`) plus `XValueHead.Decode()`; the owned-copy semantics described in the doc are preserved (`DecodeXValueHead` copies the raw bytes).

7. **The signature example `rwfunc add(A:int, B:int) -> (C:int)` uses a type name the parser rejects.** `parser/parser.go` (`checkParamTypes`) and `parser/inst.go` (`parseWriteSlot`) reject `int` and `float` with "ambiguous type — use int64 or float64 instead". This contradicts the doc's own "no int/float" rule; the example should read `A:int64, B:int64 -> C:int64`. Note also that `tutorial/01-basics/numtypes.kv` still carries a header comment claiming "int/float 是 int64/float64 别名" (int/float are aliases) — that is stale; the parser currently rejects both.

8. **The value stored at `/lib/<pkg>.<name>` is kind `rwfunc`, not `string`.** In `layout/layout.go` (`WriteFunc`) the signature is persisted as `kvspace.NewRwfunc(...)`, whose `Kind()` is `"rwfunc"` and whose `String()`/`Sig()` is the signature text. The doc's `string:rwfunc func(args) -> (rets)` shorthand conflates the kind with the plain-text signature; the runtime kind is `rwfunc`.

9. **None in comparison is not always a TypeError.** `rwir/builtin/cmp.go` (`evalCmp`) permits None for `==`/`!=` (it compares the kind strings, so `None == None` is true and `None == 5` is false); only `< > <= >=` raise `TypeError: None in comparison`. The doc's blanket "None participating in comparison → TypeError" is imprecise for equality.

10. **`precision.kv` assertion count is stale.** The doc says `precision.kv` has "17 assertions"; the current `tutorial/01-basics/precision.kv` has 21 `println` statements / expected outputs. `numtypes.kv` has 12, matching the doc.

11. **Result narrowing (fix-034) is not described.** Arithmetic computes in int64/float64 but the result is narrowed back to the wider of the two input kinds (`narrowInt`/`narrowFloat` in `rwir/builtin/coerce.go`), e.g. `int8(100) + int8(100)` yields kind `int8`, not `int64`. The doc's theorems describe the computation domain only and omit this fix-034 behavior.

12. **None's TLV encoding detail.** The doc's table says `kind_len` 0 means None; in practice `None.Encode()` returns nil (zero bytes), and `DecodeXValueHead` on empty data returns a zero-value `XValueHead`. No frame with `kind_len=0` is ever produced.

13. **Referenced fix-issues are not present as files.** The doc cites `fix-020`, `fix-021`, and `fix-0721` (also referenced in code comments and tutorial files). The current `deepx-design/issue/` directory contains no `fix-*` files (only `tothink-*`/`reject-*`/`todo-*`), so those issue files appear to have been archived or removed.

Path format notes (verified consistent with the doc): `/lib/<pkg>.<name>` = `keytree.LibFunc(pkg, name)` (`/lib` root with `.` as the package/name separator); instruction slots live at `/lib/<pkg>.<name>/[i,0]`, `[i,-j]` (reads), `[i,j]` (writes); runtime variables live at `<frameRoot>/<var>` where `frameRoot` is the PC prefix such as `/vthread/<vtid>/[3,0]`; the `br` cond slot is resolved via `builtin.AsBool` (`kvcpu/controlflow.go`), confirming the doc's "cond slot is always bool" claim.
