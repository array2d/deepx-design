# kvspace-c: 复用 memkv 分配器方案

> 2026-08-03
> 直接复用 memkv 的 blockmalloc + boxmalloc 作为分配器层，上层重写 trie 节点类型系统以适配 KVSpace 目录语义。

---

## 一、复用范围

### 1.1 零改动复用

| 组件 | 来源 | 在 kvspace-c 中的角色 |
|------|------|----------------------|
| `blockmalloc` (blockmalloc.c + spinlock.c, ~310行) | miaobyte/blockmalloc | 管理 trie 节点的 alloc/free。4种节点类型每种一个 blocks_meta_t 实例 |
| `boxmalloc` (boxmalloc.c, ~450行) | miaobyte/boxmalloc | 管理变长 value 数据（XValue TLV bytes + Index children list） |
| 自旋锁 | blockmalloc spinlock.c | 全局锁，x86 pause / ARM yield，零外部依赖 |
| 三区池分割模式 | memkv | meta + node zone + box zone + data zone |

### 1.2 不采纳的

| 组件 | 理由 |
|------|------|
| memkv 的 256-ary 定长 Trie (`key_node_t`) | 无前缀压缩，稀疏 key 浪费严重（每节点 ~1KB） |
| memkv 的单节点类型 (`has_key` + `box_offset`) | KVSpace 需要 INDEX/VALUE/LINK/EXTINDEX 四种节点类型 |
| memkv 的 DFS 全遍历 `List` | KVSpace 的 `List` 必须读 Index children list（O(1)），不能扫描子树 |
| miaobyte 48字符编码层 | kvspace-c 的 key 是任意 byte 序列，不需要字符集编码 |

---

## 二、节点类型系统

memkv 只有一种节点：`{has_key, box_offset, children[char_type]}`。KVSpace 需要区分四种语义。

### 2.1 节点定义

```c
// 节点类型判别：不存独立 type 字段，由 value kind 推断
// has_value == false  → 纯目录节点（路径中间节点）
// has_value == true:
//   kind == "index"      → INDEX 节点（目录，children 列表在 box zone）
//   kind == "linkindex"  → LINK 节点（软链接，value 是 target 路径）
//   kind == "extindex"   → EXTINDEX 节点（扩展索引，value 含 extpath + 自身成员）
//   kind == 其他          → VALUE 节点（普通文件，value 是 XValue TLV bytes）

typedef struct {
    bool     has_value:1;       // 此节点是否持有 value
    uint64_t box_offset:63;     // boxmalloc 分配的 value 偏移（has_value=true 时有效）
    int32_t  children[256];     // 子节点 block id，-1 表示空。定长 256，不做自适应
} trie_node_t;
```

### 2.2 四种节点类型的行为

```
插入 "/a/b" (文件):
  root ─'/'→ node('/a/') ─'b'→ node('/a/b', has_value=true, kind="string")
                                  box_offset → "hello" (XValue TLV bytes)

插入 "/a/c" (文件):
  root ─'/'→ node('/a/') ─'b'→ node('/a/b', has_value=true)
                       └'c'→ node('/a/c', has_value=true)
  node('/a/').has_value = true
  node('/a/').box_offset → "b\nc" (Index children list)

Mkindex("/d/"):
  root ─'/'→ node('/d/', has_value=true, kind="index")
              box_offset → "" (空 children list)
```

### 2.3 与 memkv 节点的关键差异

| | memkv `key_node_t` | kvspace-c `trie_node_t` |
|---|---|---|
| children 数组 | `int32_t[char_type]` 动态大小 | `int32_t[256]` 固定 256 |
| value 语义 | 任何 key 的 value 都是数据 | value 的 kind 决定节点类型 |
| has_key 含义 | "此节点存了一个 value" | has_value=false → 纯路径节点（不是目录！） |
| 目录表示 | 不存在此概念 | has_value=true + kind="index" |
| 叶子粒度 | 每个 key byte 创建一个节点 | 同样，byte-by-byte 下降 |

**"非目录不产生 leaf"的含义**：`node('/a/')` 即使 children 里有 `'b', 'c'`，只要 `has_value=false`，它就不是一个 KVSpace 目录——`List("/a/")` 查不到它。必须先 `Mkindex("/a/")` 或通过 `Set` 的副作用让它 `has_value=true, kind="index"`，`List` 才能返回子项。

---

## 三、内存布局

```
shm block (file-backed mmap, 方案 C)
┌────────────────────────────────────────────────────┐
│ header (magic + region_size + resize_mutex + ...)  │
├────────────────────────────────────────────────────┤
│ node slabs (blockmalloc × 1, 统一管理)              │
│   blocks_meta_t: total_size = node_zone_size       │
│                  block_size = sizeof(trie_node_t)  │
│                  = 8 + 256*4 = 1032 bytes          │
│                                                    │
│   trie_node_t 固定 1032B，所有节点同一大小            │
│   blockmalloc LIFO freelist 管理                    │
├────────────────────────────────────────────────────┤
│ box meta (boxmalloc)                               │
│   box_meta_t + box_head_t 节点                      │
│   管理变长分配（XValue bytes + Index children list） │
├────────────────────────────────────────────────────┤
│ box data zone                                      │
│   原始 value 数据，无元数据                          │
│   XValue TLV bytes, Index children strings,        │
│   Link target strings, ExtIndex extpath+children    │
└────────────────────────────────────────────────────┘
```

### 为什么 node 不按类型分 slab

memkv 的 blockmalloc 管理的是单一固定大小块。kvspace-c 的 trie_node_t 固定 1032B（256 个 int32_t children），所有节点同一尺寸。不像 ART 需要 node4(64B)/node16(80B)/node48(416B)/node256(2112B) 四种 slab。

**代价**：稀疏 key 下，一个只有 1 个子节点的 trie_node 浪费 255×4=1020B。**缓解**：不做——对于 KVSpace 的使用场景（路径 key，fanout 通常很小但不可预测），1KB 的浪费在 64GB 虚拟地址空间下可接受。如果后续成为瓶颈，再考虑自适应节点大小。

**收益**：一种 slab，一个 blockmalloc 实例，零代码复杂度。memkv 的 blockmalloc 直接可用的关键前提。

---

## 四、Trie 算法骨架（直接复用 memkv 的遍历逻辑）

### 4.1 insert — 路径下降 + 按需分配 + 类型分发

```
insert_node(root, key="/a/b", value):
  n = root, depth = 0
  for each byte in key:
    if n.children[byte] == -1:
      n.children[byte] = blocks_alloc(node_slab)   // ← 复用 blockmalloc
      init_node(new_child)
    n = deref(n.children[byte])
    depth++

  // 到达目标节点，类型分发：
  if key 以 '/' 结尾:
    n.has_value = true
    n.box_offset = box_alloc(box, encode_index_children(list))
    // kind = "index"
  else if value.kind == "linkindex":
    n.has_value = true
    n.box_offset = box_alloc(box, encode_link_target(target))
  else if value.kind == "extindex":
    n.has_value = true
    n.box_offset = box_alloc(box, encode_extindex(extpath, own_children))
  else:
    n.has_value = true
    n.box_offset = box_alloc(box, encode_xvalue(value))
```

### 4.2 get — 路径下降 + link 穿透

```
get(key="/a/b"):
  n = root, depth = 0
  resolved_key = key

  // 外层 link 穿透（最多 64 层）
  for hop in 0..64:
    n = root
    for each byte in resolved_key:
      if n.children[byte] == -1:
        return None
      n = deref(n.children[byte])

      // 每步检查是否遇到 link
      if n.has_value && kind_of(n.box_offset) == "linkindex":
        target = read_link_target(n.box_offset)
        resolved_key = target + remaining_bytes
        break  // 重新从 root 开始
    else:
      // 完整路径走完，无 link 触发
      break

  if !n.has_value: return None
  return decode_xvalue(read_box(n.box_offset))
```

### 4.3 list — 读 Index children（不走 DFS）

```
list(prefix="/a/"):
  n = walk(root, "/a/")
  if n == null || !n.has_value: return []
  kind = kind_of(n.box_offset)
  if kind == "index":
    return split(read_box(n.box_offset), '\n')
  if kind == "extindex":
    own = split(lines[1:], '\n')     // 自身成员
    ext = list(resolve(lines[0]))     // ext target 的 children
    return dedup(own ∪ ext)           // 自身覆盖同名
  return []
```

这是与 memkv 最根本的分歧。memkv 的 `memkv_keys` 做 DFS 遍历子树，列出所有有 `has_key` 的节点。kvspace-c 的 `List` 直接读目录节点的 Index children list——O(1) vs O(子树大小)。

### 4.4 del — 删除 + 父目录 Index 清理

```
del("/a/b"):
  n = walk(root, "/a/")
  if n has index children:
    remove "b" from children list
    update n.box_offset = box_alloc(box, new_children_list)
    box_free(box, old_box_offset)

  n = walk(root, "/a/b")
  if n.has_value:
    box_free(box, n.box_offset)
  n.has_value = false

  // TODO: 如果 n 没有任何 children 且 !has_value，是否回收节点？
  // 初始版本不回收（简化），后续 compact 时回收
```

---

## 五、KVSpace 12 方法实现映射

沿用 kvspace-go/art backend.go 的语义，替换底层存储为 blockmalloc + boxmalloc + trie_node_t。

| KVSpace 方法 | 实现 |
|-------------|------|
| `Get(prefix, keys)` | resolve_links(prefix+key) → walk trie → 读 box |
| `Set(pairs)` | 确保 parent dir → insert trie node → 写 box → 更新 parent index |
| `List(prefix, expandExt)` | walk → 读 kind=index/extindex 的 box → split/merge children |
| `Del(keys...)` | resolve → box_free → 清理 parent index |
| `DelTree(prefix)` | 若 prefix 是 link → 只删 link；否则 collect subtree keys → 逐条 Del |
| `Notify(key, val)` | 写 notify queue（独立数据结构，不在 trie 中） |
| `Watch(key, timeout)` | 读 notify queue，pthread_cond_timedwait |
| `Mkindex(path)` | 递归创建祖先目录 node + 每级 Set kind=index |
| `Link(target, linkpath)` | insert linkpath node + kind=linkindex + parent index 维护 |
| `ExtIndex(path, extpath)` | insert path node + kind=extindex + 级联检测 + collision 检测 |
| `UnLink(path)` | 若 kind=extindex → 降级为普通 index；若 kind=linkindex → 删除 node |
| `Clear()` | 重置所有 blockmalloc + boxmalloc + notify queues |
| `DisConn()` | munmap |

---

## 六、ExtIndex 和 Link 的存储

### 6.1 Link

```
Link("/target", "/link"):
  walk("/link") 的末端 node:
    has_value = true
    box_offset → "linkindex\x00/target"  (kind + target path)
  
  parent("/link/") 的 index children 追加 "link"
```

`resolve_links` 在每次 Get/Set/Del 前遍历路径，遇到 kind=linkindex 的节点即跳转。

### 6.2 ExtIndex

```
ExtIndex("/a/", "/lib/"):
  walk("/a/") 的末端 node:
    has_value = true
    box_offset → "extindex\x00=/lib/\nown_child1\nown_child2"
  
  exts map 不需要单独存储——直接从 trie node 的 box 读取 lines[0] 即可获取 extpath。
```

### 6.3 级联检测

```
ExtIndex 创建时:
  1. extpath 必须是普通 index（kind=index），不能是 extindex
  2. extpath 不能在 path 的子树中（否则循环）
  3. 遍历所有现有 extindex，检查 path 是否在某个 extindex 的 extpath 下
```

---

## 七、Notify/Watch 队列

队列不在 trie 树中存储。独立数据结构：

```c
typedef struct {
    char     key[256];           // 队列对应的 key 路径
    uint8_t  value[4096];        // 一次性通知的 value（XValue TLV bytes）
    uint16_t value_len;
    bool     has_value;
    pthread_mutex_t mutex;
    pthread_cond_t  cond;
} notify_queue_t;

// 预分配固定数量（如 256 个），key hash 取模定位
// key 冲突时简单的线性探测下一个空 slot
```

- `Notify(key, val)`: hash(key) → 找到/创建 queue → 写 value → signal
- `Watch(key, timeout)`: hash(key) → 找到 queue → timedwait → 读并清空 value → 返回

---

## 八、与 memkv 的 diff 总结

| | memkv | kvspace-c |
|---|---|---|
| **节点类型** | 1 种（has_key + box_offset） | 4 种语义（由 value kind 推断） |
| **children 数组** | 动态大小 `int32_t[char_type]` | 固定 `int32_t[256]`（1032B/node） |
| **分配器** | blockmalloc(管理节点) + boxmalloc(管理value) | 同，零改动 |
| **目录概念** | 无 | INDEX 节点显式维护 children list |
| **List 实现** | DFS 遍历子树叶子 | 读 Index box，O(1) |
| **前缀压缩** | 无 | 无——路径中的 `/` 在 256-ary trie 中本身就是一层 |
| **编码层** | miaobyte 48字符编码 | 无——key 是任意 byte 序列 |
| **Link/ExtIndex** | 无 | kind=linkindex/extindex 节点 |
| **Notify/Watch** | 无 | pthread_cond_t 队列 |
| **持久化** | 调用者提供 buffer | file-backed mmap |

### 核心复用项

```
memkv 直接拿:
  blockmalloc.c + spinlock.c   → 节点分配
  boxmalloc.c                  → value 分配
  三区池分割                    → 内存布局

memkv 改骨架:
  memkv_init                   → kshm_init（去掉 char_type，固定 256 children）
  trie 遍历/插入/删除           → 加上节点类型分发
  box_alloc/box_free 调用       → 同

全新:
  节点类型系统（INDEX/VALUE/LINK/EXTINDEX）
  Index children list 维护
  resolve_links 链接穿透
  ExtIndex merge/collision 检测
  Notify/Watch 队列
  Mkindex 递归目录创建
  List/DelTree 语义
```

### 为什么不用 ART

memkv 的 blockmalloc 管理的是**固定大小块**。ART 需要 4 种节点大小（64B/80B/416B/2112B），这意味着要么：
- 4 个 blockmalloc 实例（每个管理一种 slab）——可行但 blockmalloc 的 LIFO freelist 嵌入 block header 中，浪费 2-8B/block
- 或者自己写 slab 分配器

选择固定 1032B 的 256-ary trie 节点：**一种 slab，一个 blockmalloc 实例，memkv 的分配器零改动直接可用**。同时保留了 memkv 已验证的 trie 遍历骨架。
