# Member Access and Data Structures（成员访问与数据结构）


## `.` 运算符——kvspace 路径的标准成员访问

`ptr.val` → `at(ptr, "val")` → `kv.Get(ptr/val)`。Pratt 循环中 `.` 作为后缀运算符，对标 C `ptr->val`、Go `ptr.val`。写侧 `42 -> ptr.val` 展开为 `set(ptr, "val", 42) -> ptr`。Scanner 将 `.` 作为 token 分隔符和独立 Dot token，`at`/`set` builtin 支持字符串字段名做 kvspace 路径拼接。

### 静态字段：`h.field`

```
h.field  →  at(h, "field")    # field 是字面量字符串
```

解析时 Pratt 消费 `.` 后读到普通标识符 → 作为 `StrLit` 传给 `at`。

### 动态解引用：`h.*key`

```
h.*key  →  at(h, key)         # key 是变量，取其值作为路径段名
```

解析时 Pratt 消费 `.` 后读到 `*` + 标识符 → 作为裸 `Leaf` 传给 `at`，不做字符串化。这是 kvlang 内置 hash map 的语法基础：

```kvlang
"/tmp" -> h           # h = 路径前缀
2 -> key              # key = 2
h.*key                # at("/tmp", 2) → 读 /tmp/2
```

与传统语言的对比：

| 语言 | 静态字段 | 动态字段 |
|------|---------|---------|
| kvlang | `h.field` | `h.*key` |
| Python | `h["field"]` | `h[key]` |
| Go | `h.field` | `h[key]` (map) |
| JS | `h.field` | `h[key]` |

**与 None 配合**：`at` 查不到 key 返回 None。此时用 `has` 判存在更直接。O(1) hash map，解锁数百道 LeetCode 题。

详见 `doc/kvlang/design/kvspace-hash-map.md`。

### dict：`. ` 作为独立成员目录（kind="dict"）

kvlang 的 dict 在 kvspace 中以 `.` 为成员分隔符，尾 `.` 的 key 是 dict 成员目录，与父 `/` index 隔离：

```
/dir/      → index: ["obj.", "other"]
/dir/obj.  → dict: ["x", "y"]          ← 独立 dict 成员目录（kind="dict"）
/dir/obj.x → int64:42                  ← member key = parent + DictSep + name
/dir/obj.y → string:hello
/dir/other → ...
```

`/` 管层级（lib、vthread 栈），`.` 管结构（dict member）。dict 成员动态增删，无编译期固定字段约束，对齐 Python dict / JS object 语义。

**dict 字面量**：`a = {x: 1, y: 2}` 创建 dict 目录 `/frame/a.` (kind=dict) + 成员 `/frame/a.x` (int64:1)、`/frame/a.y` (int64:2)。

**静态字段 vs 动态字段**：

| 语法 | kvspace 层 |
|------|-----------|
| `obj.prop` | `at(obj, "prop")` → `Get(obj., ["prop"])` |
| `obj.*key` | `at(obj, key)` → key 变量值作为字段名 |
| `a[i]` | `at(a, i)` → 整数下标作为字段名 |

**成员分隔符**：`keytree.Member(base, name)` = `base + "." + name`。`. ` 是统一的成员访问符，亦用于 lib 函数限定名（`/lib/pkg.name`）。

### 成员解析规则：按值优先，按名回退

表达式 `base.名`（读写两侧同规则）中 base 的解析：

1. **按值解引用**：base 持有非空字符串值（路径指针）→ 成员键 = `值(base).名`。如 `"/n0" -> p` 后 `p.next` → `/n0.next`。
2. **按名回退**：base 无值（或非字符串）→ 成员键 = `解析(base).名`，其中 `解析()` 为帧感知：裸名 → `帧根/base`，`/` 开头 → 直通。如局部键族 `chars.0` → `帧根/chars.0`；字面量 `/n0.val` → `/n0.val`。

该规则使"局部 struct"与"指针解引用"共用一套语法：键族的 base 永不赋值（保持按名），指针变量存路径字符串（触发按值）。

**遗留不一致**（待收敛）：`dget`/`dset` 仍纯按名寻址（`帧根/变量名.key`），未走按值优先规则。
