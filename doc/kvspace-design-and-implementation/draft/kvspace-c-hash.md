# kvspace-c: 纯 Hash 方案分析

> 2026-08-03
> key → hash → box_offset，value 由 boxmalloc 管理。目录语义由显式 Index 维护，不依赖树结构。

---

## 一、核心思路

ART/Trie 的树结构在 KVSpace 中是**冗余的**。KVSpace 已经显式维护了目录 Index（每个目录节点存了 children list），`List` 和 `DelTree` 都走 Index，不走树遍历。树只做一件事：点查找（给定完整 key，找到 value）。

这件事，hash 表做得更好：O(1) 与 key 长度无关。

```
Get("/vt/0/pc"):
  ART:  root → partial="/vt/" → partial="0/" → partial="pc"   (3 节点下降)
  Hash: hash("/vt/0/pc") → slot → box_offset                    (1 次探测)
```

---

## 二、数据结构

### 2.1 Hash 表

```c
typedef struct {
    uint64_t key_offset;      // boxmalloc 偏移（存完整 key bytes，用于碰撞比较）
    uint64_t value_offset;    // boxmalloc 偏移（XValue TLV bytes）
    uint32_t key_len;
    uint32_t hash;            // 完整 hash，用于快速拒绝
    uint8_t  state;           // 0=EMPTY, 1=OCCUPIED, 2=DELETED (tombstone)
} hash_slot_t;  // 32 bytes per slot
```

开放寻址，线性探测。负载因子 70%。100K keys → ~143K slots × 32B = **4.6MB**。

### 2.2 内存布局

```
shm block
┌────────────────────────────────────────────┐
│ header (magic + table_size + used_count)   │
├────────────────────────────────────────────┤
│ hash_table[table_size]     (hash_slot_t)   │
│ 预分配固定容量，不 resize                   │
├────────────────────────────────────────────┤
│ box meta zone (boxmalloc)                  │
├────────────────────────────────────────────┤
│ box data zone                              │
│ key bytes + XValue bytes + Index lists     │
└────────────────────────────────────────────┘
```

**关键决策：hash 表不 resize。** 创建时指定最大容量，此后大小不变。Resize 在 shm 中是噩梦——需要分配新表区域，协调多进程，rehash 所有条目，回收旧表空间。不做 resize 消除了 hash 表在 shm 中的最大工程风险。

代价：用户必须预估 key 数量上限。对于 kvlang 的场景（vthread 数量、函数数量、中间变量数量都可预测），这不是问题。

### 2.3 为什么 key 存在 boxmalloc

hash 碰撞时需要比较完整 key。如果 key 内联在 slot 中，slot 大小不可控（key 长度可变）。存到 boxmalloc 让 slot 固定 32B。

代价：碰撞解决路径上每次 key 比较都是一次 boxmalloc 读取。但 70% 负载下，平均探测次数 ≈ 1.4，碰撞不常见。

---

## 三、各操作实现

### 3.1 Get

```
Get(prefix="/vt/0", keys=["pc", "status"]):

  for each key:
    full_path = JoinPath(prefix, key)  // "/vt/0/pc"

    // link 穿透（同 ART 方案）
    resolved = resolve_links(full_path)

    // 点查找
    slot = hash_find(resolved)
    if slot.state == OCCUPIED:
      value = box_read(slot.value_offset)
      // 检查 ext fallback
      if value is None and has_ext_fallback(resolved):
        slot = hash_find(ext_target_path + relative)
        value = box_read(slot.value_offset)

    results[i] = decode_xvalue(value)
```

hash_find 本身是 O(1)，但 `resolve_links` 是 O(path_depth) 次 hash 探测（见 3.8）。

### 3.2 Set

```
Set("/a/b", value):
  resolved = resolve_links("/a/b")
  
  // 确保 parent directory 存在
  ensure_dir("/a/")

  // 写 value
  slot = hash_find(resolved)
  if slot.state == OCCUPIED:
    box_free(slot.value_offset)        // 释放旧 value
  slot.value_offset = box_alloc(encode_xvalue(value))
  slot.state = OCCUPIED

  // 更新 parent index
  add_to_index("/a/", "b")
```

### 3.3 List

```
List("/a/", expand_ext=false):
  slot = hash_find("/a/")
  if slot.state != OCCUPIED: return []
  
  value = box_read(slot.value_offset)
  kind = decode_kind(value)
  
  if kind == "index":
    return split(value.raw, '\n')
  if kind == "extindex" && expand_ext:
    own = split(lines[1:], '\n')
    ext = List(resolve(lines[0]), expand_ext=true)
    return dedup(own ∪ ext)
  return []
```

**List 完全不依赖树结构。** 它读的是目录 key 的 Index value——与方案 A/B 完全相同。hash 改不了 List 的实现。

### 3.4 DelTree

```
DelTree("/a/"):
  // 若 /a/ 是 link → 只删 link，不穿透
  if is_link("/a/"):
    hash_remove("/a/")
    return

  // 递归，完全走 index 系统——不扫描 hash 表
  del_recursive("/a/")

del_recursive(prefix):
  children = List(prefix)          // 读 prefix 的 Index
  for each child:
    full = prefix + child
    if is_dir(full):
      del_recursive(full + "/")
    else:
      hash_remove(full)
      remove_from_index(prefix, child)
  hash_remove(prefix)
```

**DelTree 同样不依赖树结构。** 通过 Index children list 递归，与 Redis backend 完全相同。不需要扫描整个 hash 表。

### 3.5 Mkindex

```
Mkindex("/a/b/"):
  ensure_dir("/a/")           // 递归确保祖先
  slot = hash_find("/a/b/")
  if slot.state != OCCUPIED:
    slot.key_offset = box_alloc("/a/b/")
    slot.value_offset = box_alloc(encode_index([]))  // 空 children list
    slot.state = OCCUPIED
    add_to_index("/a/", "b/")
```

### 3.6 Link / ExtIndex / Notify / Watch

与 ART 方案**完全相同**——这些操作读/写的是 hash 表中的 value，不关心底层是 hash 还是树。

### 3.7 Clear

```
Clear():
  memset(hash_table, 0, table_size * sizeof(hash_slot_t))
  box_clear()
```

比 ART 方案简单——不需要重置 5 个 blockmalloc 实例。

### 3.8 resolve_links — hash 方案的额外开销

这是 hash 方案唯一的性能退化点：

```
resolve_links("/lib/standard/math/add"):
  // 对路径的每个前缀检查是否是 link
  检查 "/"          → hash_find → 否
  检查 "/lib"       → hash_find → 否
  检查 "/lib/"      → hash_find → 可能有 link
  检查 "/lib/standard" → hash_find → 否
  检查 "/lib/standard/" → hash_find → 否
  ...
```

路径 `/a/b/c`（不含 link）需要 5-6 次 hash 探测。如果大部分路径都不含 link，这些探测是纯浪费。

**ART 方案中，link 检测是树下降的副作用**——每个节点下降时顺便检查 `has_value` 和 `kind`，零额外开销。

但注意：在 kvspace-go/redis 中，resolve_links **已经是**逐 prefix 探测（每次探测是一次 Redis GET）。hash 方案延续了同一模式，只是把网络 round-trip 换成了本地 hash 探测。

### 3.9 优化：link cache

方案 A 的 `kvspace-c.md` 中提到的 lazy link cache：

```c
// 每个 hash 表自带 link cache
struct {
    uint64_t path_hash;   // 完整路径的 hash
    bool     is_link;
    uint64_t target_offset;  // 如果是 link，target 路径在 box 中的偏移
} link_cache[256];  // LRU
```

首次查询路径时探测所有前缀。后续同一路径的查询直接查 cache。kvlang 解释器对同一路径（如 `/vt/0/pc`）会反复访问——cache 命中率接近 100%，resolve_links 的开销归零。

---

## 四、与三个方案的对比

| | ART 纯 | memkv 复用 | ART+boxmalloc | **纯 Hash** |
|---|---|---|---|---|
| key 查找 | O(key_len)，树下降 | O(key_len)，逐 byte load | O(key_len)，树下降 | **O(1)，hash 探测** |
| link 检测 | 免费（树上副作用） | 树下降中检查 | 免费 | **O(path_depth) 次 hash** |
| List/DelTree | 走 Index | 走 Index | 走 Index | **同（走 Index）** |
| 前缀压缩 | ✅ | ❌ | ✅ | **N/A（无树）** |
| 新代码量 | ~800 (ART) | ~200 (trie) | ~800 (ART) | **~300 (hash)** |
| 分配器依赖 | 自研 slab+bump | block+box | block×5+box | **block×1+box** |
| resize | 不需要 | 不需要 | 不需要 | **不需要（固定容量）** |
| 内存（100K keys） | ~4MB | ~21MB | ~4MB | **~5MB** |

---

## 五、关键判断

### 5.1 hash 没有简化 List 和 DelTree

List 读的是 Index value，DelTree 走的是 Index children 递归——两者都与底层索引结构无关。hash 不能消除 Index 维护的复杂度，Index 维护的代码在所有方案中完全相同（~500 行）。

### 5.2 hash 真正简化的是：点查找

```c
// ART 方案：要 ~800 行 insert/delete/search/grow/shrink/prefix_split/compact
n = art_search(root, key);

// Hash 方案：~300 行，就是标准的开放寻址 hash 表
slot = hash_find(key);
```

这 500 行差距是 hash 方案的核心吸引力。

### 5.3 hash 的代价是：link 解析

每次 Get 需要 O(path_depth) 次额外 hash 探测。有 link cache 后这个代价在热路径上归零——但 link cache 本身是 ~50 行额外代码和运行时开销。

### 5.4 hash 的风险是：固定容量

"不 resize" 意味着创建时必须知道 key 数量上限。对于 kvlang：
- vthread 数量：可预测（用户配置的并发数）
- 函数数量：可预测（代码库中的函数注册量）
- 中间变量：难以精确预测，但可以给足够大的上限

如果估小了：hash 表填满后 Set 返回错误。如果估大了：浪费 shm 空间。这个 tradeoff 对开发者不友好，但工程上可接受——与 Redis 的 `hash-max-ziplist-entries` 同类问题。

---

## 六、总结

**hash 方案的定位**：用 ~500 行代码简化（省掉 ART 树）换取 link 解析的额外开销。这个 tradeoff 在以下条件成立时是最优解：

1. key 空间可预测（不依赖动态 resize）
2. link 使用不频繁（大部分路径不含软链接）
3. 或者 link 访问模式有高局部性（link cache 命中率高）

对于 kvlang 解释器——vthread pc 更新每秒数千次，每次更新同一路径——条件 2 和 3 都满足。条件 1 取决于部署者是否愿意预估容量。

**如果这三个条件都满足，hash 方案是三个方案中代码最少、最简单的选择。**
