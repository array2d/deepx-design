# Chapter 9: Member Access and Data Structures（成员访问与数据结构）

import kvlang-design-and-implementation

## 10. `.` 运算符——kvspace 路径的标准成员访问

`ptr.val` → `at(ptr, "val")` → `kv.Get(ptr/val)`。Pratt 循环中 `.` 作为后缀运算符，对标 C `ptr->val`、Go `ptr.val`。写侧 `42 -> ptr.val` 展开为 `set(ptr, "val", 42) -> ptr`。Scanner 将 `.` 作为 token 分隔符和独立 Dot token，`at`/`set` builtin 支持字符串字段名做 kvspace 路径拼接。

### 10.1 静态字段：`h.field`

```
h.field  →  at(h, "field")    # field 是字面量字符串
```

解析时 Pratt 消费 `.` 后读到普通标识符 → 作为 `StrLit` 传给 `at`。

### 10.2 动态解引用：`h.*key`

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

**与 nil 配合**：`at` 查不到 key 返回 nil。存 `idx+1`（≥1），读时判断 `> 0` 区分"找到/未找到"。O(1) hash map，解锁数百道 LeetCode 题。

详见 `doc/kvlang/design/kvspace-hash-map.md`。

### 10.3 struct ≡ dict：kvspace 中的等价性

kvlang 不区分 struct 和 dict。二者在 kvspace 中是**同一种东西：共享前缀的键族**。

| 语言层视角 | kvspace 层实质 |
|-----------|---------------|
| struct：编译期已知的字段名 | `base` + 字面量成员名，`obj.prop` → `at(obj, "prop")` |
| dict：运行期动态的 key | `base` + 变量值成员名，`obj.*key` → `at(obj, key)` |
| 链表节点：`val` + `next` 指针 | 键族 `{val, next}`，`next` 存下一节点的路径字符串（§8 变量名即指针） |
| 数组：下标索引 `a[i]` | `base` + 整数成员名，`a[i]` → `at(a, i)` |

kvspace 没有类型边界：同一键族可以同时按 struct 用（静态字段）、按 dict 用（动态 key）、按数组用（整数 key）。静态/动态的区别只存在于**语法层**（`.field` vs `.*key` vs `[i]`），到 `at`/`set` 之后完全消失。

**dict 字面量与类型标记**：`a = { attr1="s1"; attr2=2; attr3=null }` 是键族的一等创建语法——
desugar 为 `dict("attr1", "s1", ...)`，base 键 `a` 写入 `kind="dict"` 的零负载标记值，
成员写入平坦键族 `a.attr1`、`a.attr2`；值为 `null`（裸名，运行时解析为 nil）的成员**不写入**——
kvspace 中缺席即 null。dict 标记非 string 值，成员解析自动走按名回退（§10.4）；
`at`/`set` 亦显式识别 `kind=="dict"` 的 base 强制路径模式。键值对分隔符为 `;`、换行或逗号，
对内的 `=` 与赋值算子同形（fix-010）。

**成员分隔符已统一为 `.`**（fix-009）：`at`/`set`/`dget`/`dset`/`kvat`/`kvhas` 的成员拼接全部经 `keytree.Member(base, name)`（`base + "." + name`）。链表节点落盘即 `/n0.val`、`/n0.next` 平坦键，零子树。

### 10.4 成员解析规则：按值优先，按名回退

表达式 `base.名`（读写两侧同规则）中 base 的解析：

1. **按值解引用**：base 持有非空字符串值（路径指针）→ 成员键 = `值(base).名`。如 `"/n0" -> p` 后 `p.next` → `/n0.next`。
2. **按名回退**：base 无值（或非字符串）→ 成员键 = `解析(base).名`，其中 `解析()` 为帧感知：裸名 → `帧根/base`，`/` 开头 → 直通。如局部键族 `chars.0` → `帧根/chars.0`；字面量 `/n0.val` → `/n0.val`。

该规则使"局部 struct"与"指针解引用"共用一套语法：键族的 base 永不赋值（保持按名），指针变量存路径字符串（触发按值）。

**遗留不一致**（待收敛）：`dget`/`dset` 仍纯按名寻址（`帧根/变量名.key`），未走按值优先规则。
