# kvspace-c: ART + boxmalloc 混合方案

> 2026-08-03
> ART 做 key 树索引，boxmalloc 做 value 变长存储。两个方案的各自长板合并。

---

## 一、为什么是这个组合

两个方案的各自的弱点正好被对方覆盖：

| | 方案 A (ART 纯) | 方案 B (memkv 复用) | **混合方案** |
|---|---|---|---|
| key 查找 | ✅ 前缀压缩，~6× 延迟 | ❌ 逐 byte 依赖 load | **✅ ART** |
| 节点内存 | ✅ 自适应 64B–2112B | ❌ 固定 1032B | **✅ ART** |
| value 分配 | ❌ 自研 bump（不回收） | ✅ boxmalloc（free+reuse） | **✅ boxmalloc** |
| 分配器代码 | ❌ 自研 slab+bump ~180行 | ✅ 零新代码 | **中**（blockmalloc ×4 for nodes） |

**一句话**：ART 解决了方案 B 的 "逐 byte 串行 load" 性能瓶颈，boxmalloc 解决了方案 A 的 "bump 不回收，需要 compact" 的维护负担。

---

## 二、架构

```
shm block (file-backed mmap)
┌──────────────────────────────────────────────────────┐
│ header                                               │
├──────────────────────────────────────────────────────┤
│ ART node slabs (blockmalloc × 4)                     │
│   node4_slab:   block_size=64,   header=2B (3.1%)   │
│   node16_slab:  block_size=80,   header=2B (2.5%)   │
│   node48_slab:  block_size=416,  header=2B (0.5%)   │
│   node256_slab: block_size=2112, header=2B (0.1%)   │
├──────────────────────────────────────────────────────┤
│ box meta zone (boxmalloc 内部分配器)                   │
│   boxmalloc 内部用 blockmalloc 管理 box_head_t 节点    │
│   → 第 5 个 blockmalloc 实例                          │
├──────────────────────────────────────────────────────┤
│ box data zone                                       │
│   XValue TLV bytes, Index children lists,            │
│   Link target paths, ExtIndex extpath+children       │
└──────────────────────────────────────────────────────┘
```

依赖链：
```
ART 树节点
  └→ blockmalloc × 4 (node4/16/48/256 slab)

boxmalloc (value 变长分配)
  └→ blockmalloc × 1 (box_head_t 节点)
```

总共 5 个 blockmalloc 实例。blockmalloc 自身 ~310 行，5 个实例只是 5 份 `blocks_meta_t` 结构体，不是 5 份代码。

---

## 三、ART 节点的 value 引用

leaf = node4{hasValue=true} 需要存 value 的引用。由于一切在 shm 内，用偏移量：

```c
// ART node header
typedef struct {
    uint8_t  prefix[PREFIX_MAX];   // 压缩前缀
    uint16_t prefix_len;
    bool     has_value:1;
    uint64_t box_offset:63;        // boxmalloc 分配的 value 偏移
    uint16_t count;                // 当前子节点数
} node_header_t;

// 4 种节点
typedef struct { node_header_t h; uint8_t keys[4];  uint32_t children[4];  } node4_t;
typedef struct { node_header_t h; uint8_t keys[16]; uint32_t children[16]; } node16_t;
typedef struct { node_header_t h; uint8_t index[256]; uint32_t children[48]; } node48_t;
typedef struct { node_header_t h; uint32_t children[256]; } node256_t;
```

`children[i]` 存的是 blockmalloc 分配的 **block_id**（int32_t），不是指针。通过 `blockdata_offset(meta, block_id)` 换算到 shm 内的字节偏移。

`box_offset` 存的是 boxmalloc 分配的 **obj_offset**（uint64_t），直接是 data zone 内的字节偏移。

**与方案 A 自研 slab 的关键差异**：方案 A 把 freelist 嵌入空闲节点的 `children[0]`，省掉了 block header。混合方案用 blockmalloc 原生管理，每个节点前面有 2B 的 block header——代价是 3.1% 的 node4 开销，收益是零 slab 代码。

---

## 四、boxmalloc 相比 bump 的收益

KVSpace 的 value 空间有大量更新/删除。以 kvlang 解释器的典型 workload：

```
vthread 生命周期:
  Set("/vt/0/pc",    "init/[0,0]")     // alloc 12B
  Set("/vt/0/pc",    "main/[1,0]")     // free 12B, alloc 13B
  Set("/vt/0/pc",    "return/[5,2]")   // free 13B, alloc 15B
  Notify("/vt/0/status", "running")    // alloc 7B, Watch pop → free 7B
  Del("/vt/0/pc")                      // free 15B
```

bump 方案：每次更新都 append 新 value，旧 value 永不回收。1000 次 pc 更新 → 1000 个废弃的 value 在 bump zone 里。要么定期 compact（拷贝所有存活 value 到新 bump zone），要么接受空间浪费。

boxmalloc：`box_free(old_offset)` → `box_alloc(new_size)` → 复用刚释放的 slot。LIFO freelist 让最近释放的 slot 大概率仍在 cache。对 vthread 这种高频更新场景，boxmalloc 的复用消除了 compact 需求。

**单次 alloc/free 开销对比**：

| | bump | boxmalloc |
|---|---|---|
| alloc | O(1) 指针加 | O(tree_depth) ≈ 5 层遍历 + slot 查找 |
| free | 空操作 | O(5) 树遍历 + `update_parent` 向上传播 |
| 需要 compact | 是 | 否 |

boxmalloc 的 alloc/free 路径确实比 bump 多 ~10 个 cache miss（16 叉树下降）。但 KVSpace 不是每纳秒都在 alloc/free value——大部分调用是 `Get`（纯读 ART 树），value 分配只在 `Set`/`Del` 时发生。

---

## 五、关于 "5 个 blockmalloc 很丑"

这不丑。blockmalloc 的设计意图就是**一个实例管理一种固定大小块**。memkv 用它管理 trie 节点（1 个实例），boxmalloc 用它管理 box_head_t（1 个实例）。混合方案加 4 个 ART 节点实例，是同一模式的延续。

```
blocks_meta_t slabs[5] = {
    {.block_size = 64},     // node4
    {.block_size = 80},     // node16
    {.block_size = 416},    // node48
    {.block_size = 2112},   // node256
    {.block_size = sizeof(box_head_t)},  // boxmalloc 内部
};
```

每个 `blocks_meta_t` 是 40 字节，5 个共 200 字节的元数据——在整个 shm block（256MB+）中可忽略。

---

## 六、与前两个方案的行数对比

```
                        方案 A        方案 B        混合方案
                        (ART 纯)     (memkv 复用)  (ART + boxmalloc)
                        ────────     ────────────  ────────────────
ART 树 (~800行)         自研          0              自研
slab 分配器 (~100行)     自研          0              0 (blockmalloc)
bump 分配器 (~80行)      自研          0              0 (boxmalloc)
boxmalloc (~450行)       0             复用            复用
blockmalloc (~310行)     0             复用            复用
KVSpace 语义 (~1100行)   自研          自研            自研
                        ────────     ────────────  ────────────────
新增 C 代码              ~2080         ~1100          ~1900
                        ────────     ────────────  ────────────────
依赖                    无            blockmalloc    blockmalloc
                                      + boxmalloc    + boxmalloc
```

混合方案的代码量接近方案 A，比方案 B 多 ~800 行——这 800 行是 ART 树本身（insert/delete/search/grow/shrink/prefix split/compact）。节省的 180 行是 slab + bump（被 blockmalloc + boxmalloc 替代）。

---

## 七、一次 `Get("/vt/0/pc")` 的完整路径

```
1. resolve_links("/vt/0/pc")
   └→ ART search: root → partial="/" → edge 'v' → partial="t/" → edge '0' → partial="/" → edge 'p' → partial="c"
      3 个 node4 遍历（~12 cycles L1 hit）

2. n.has_value == true
   box_offset = n.h.box_offset

3. box data zone: base + box_offset → XValue TLV bytes
   kind = "string", raw = "main/[1,0]"

4. Decode TLV → return XValue
```

三步：ART 树下降（3 节点，~4 cycles/node）→ box 偏移读取（1 次指针解引用）→ TLV 解码（纯寄存器操作）。总计 ~20 cycles。

方案 B 同路径：8 次 `children[byte]` 依赖 load → ~32 cycles + box 读取。差距 1.6×——对于 `/a/b/c` 这样的短路径。对于深度路径如 `/lib/standard/math/add/input/type`（34 bytes），差距拉大到 ~6×。

---

## 八、总结

| 决策 | 选择 | 理由 |
|------|------|------|
| key 索引 | ART | 前缀压缩消除依赖 load 链，路径越深收益越大 |
| 节点分配 | blockmalloc × 4 | boxmalloc 对定长块过度设计；blockmalloc 3% 开销可接受 |
| value 分配 | boxmalloc | 更新/删除密集场景下 free+reuse 消除 compact 需求 |
| shm 扩容 | file-backed mmap | 两个方案同 |

**不采纳的**：方案 A 的自研 slab（blockmalloc 已经够好）、方案 A 的 bump（无回收需要 compact）、方案 B 的 256-ary trie（逐 byte load 性能差）。
