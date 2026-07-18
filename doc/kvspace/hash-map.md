> 本文档，遵守父级文档 `deep-dive.md`，是其子模块的丰富。

# kvspace Path as Hash Map

> **关键发现**：`set("/tmp", key, val)` + `at("/tmp", key)` = 内置 hash map。
> kvspace 的树形路径天然就是 key-value 存储，零额外数据结构即可实现 O(1) 查找。

---

## 1. 核心算子

| 算子 | 签名 | 语义 |
|------|------|------|
| `at(path, key)` | (path: str, key: any) → value | 读 `path/key` 的值；key 不存在返回 nil |
| `set(path, key, val)` | (path: str, key: any, val: any) → () | 写 `path/key = val` |
| `kv.has(path, idx)` | (path: str, idx: int) → bool | 检查 `path/idx` 是否存在 |

### 1.1 nil 的布尔语义

- `at` 查不到 key 时返回 `nil`
- `nil == 0` → `true`（数值比较，nil 视为 0）
- `nil > 0` → `false`

**技巧**：存储 `idx + 1`（永远 ≥1），读取时判断 `> 0` 即可区分"找到"和"未找到"。

```kvlang
# 存：i + 1 → v; set(h, x, v) → _
# 查：h.*x → j; j > 0 → found
```

---

## 2. 路径成员访问语法

### 2.1 静态字段：`h.field`

```kvlang
h.field → v       # 等价于 at(h, "field")
# h = "/tmp" → 读 /tmp/field
```

`.` 运算符在 Pratt 解析器中作为后缀运算符。解析时：
```
h.field  →  ast.Call("at", ast.Leaf("h"), ast.StrLit("field"))
```

### 2.2 动态解引用：`h.*key`

```kvlang
h.*key → v        # 等价于 at(h, key)
# h = "/tmp", key = 42 → 读 /tmp/42
```

`*` 前缀表示"取此变量的值作为路径段名"，而非字面量字符串。解析时：
```
h.*key  →  ast.Call("at", ast.Leaf("h"), ast.Leaf("key"))
```

**与 `h.field` 的区别**：
- `h.field` → 字段名是字面量 `"field"`（传给 `at` 时加引号）
- `h.*key` → 字段名是变量 `key` 的值（传递裸标识符）

### 2.3 写侧语法糖

```kvlang
42 -> h.field       # set(h, "field", 42) -> h
42 -> h.*key        # 待支持：set(h, key, 42) -> h
```

写侧 `expr -> base.field` 自动展开为 `set(base, "field", expr)` 调用。

---

## 3. 时间复杂度

| 模式 | 查找 | 插入 | 例子 |
|------|------|------|------|
| brute force 双重循环 | O(n²) | — | #217 无 hash |
| kvspace hash map | O(n) | O(1) | 所有 hash-pattern 题 |

将时间复杂度从 brute force 的 O(n²) 降到 O(n)，解锁了几百道需要 hash map 的 LeetCode 题。

---

## 4. 完整示例：Two Sum (O(n))

```kvlang
# 001: Two Sum — O(n) hash map (h.*key 动态解引用)

def two_sum(nums, target:int) -> () {
  len(nums) -> n; 0 -> i
  "/tmp" -> h                              # h = 路径前缀
  while (i < n) {
    nums[i] -> x; target - x -> need
    h.*need -> j                           # at("/tmp", need) — 动态路径读
    j -> found
    if (found) { j - 1 -> k; print("[", k, ",", i, "]"); n -> i }
    else { i + 1 -> v; set(h, x, v) -> _; i + 1 -> i }
  }
}
```

**执行过程**（`nums = [2, 7, 11, 15]`, `target = 9`）：

| i | x | need | h.*need → j | 操作 |
|---|----|------|------------|------|
| 0 | 2 | 7 | nil (未找到) | set(/tmp, 2, 1) |
| 1 | 7 | 2 | 1 (找到!) | j-1=0, 输出 [0, 1] |

---

## 5. 完整示例：Contains Duplicate (O(n))

```kvlang
# 217: Contains Duplicate — O(n) hash set
def has_dup(a) -> () {
  len(a) -> n; 0 -> found; 0 -> i
  while (i < n) {
    a[i] -> x
    at("/seen", x) -> v                    # 等价于 "/seen".*x
    v -> exists
    if (exists) { 1 -> found; n -> i }
    else { set("/seen", x, 1) -> _; i + 1 -> i }
  }
  found -> f
  if (f) { print("true") } else { print("false") }
}
```

---

## 6. 与内置数据结构对比

| | kvspace hash map | 传统 hash map | kvlang dict |
|--|-----------------|--------------|-------------|
| 存储位置 | Redis/KV 持久化 | 进程内存 | 帧内 KV 子路径 |
| 跨 vthread 可见 | ✅ | ❌ | ❌ |
| 崩溃恢复 | ✅ | ❌ | ❌ |
| 语法 | `h.*key` | `m[key]` | `dget(d, k)` |
| 命名空间 | 全局路径 `/tmp/k` | 变量作用域 | 帧局部 |

**推荐**：
- **算法/刷题**：用 `h.*key`（kvspace 全局路径），简单直接
- **帧内临时数据**：用 `dget`/`dset`（dict builtin），不污染全局命名空间

---

## 7. 设计优势

1. **零额外数据结构** — hash map 不需要单独实现，kvspace 树形路径天然就是
2. **持久化** — 存在 Redis 中，崩溃可恢复
3. **可观测** — `kvlang kvspace dump /tmp` 即可查看所有键值
4. **分布式** — 多个 vthread 可共享同一路径前缀（注意并发安全）
5. **语法统一** — `.` 运算符同时服务静态字段和动态解引用，一条 Pratt 规则搞定
