# kvspace-c 存储引擎方案

> 2026-08-03
> 分析 kvspace-go/art、armon/libart、memkv 三个实现，确定 kvspace-c 最优方案

---

## 零、分析对象

| | 语言 | 行数 | 索引结构 | 定位 |
|---|---|---|---|---|
| **kvspace-go/art** | Go | tree.go 587 + backend.go 1450 = 2037 | ART | kvspace-go 进程内后端 |
| **armon/libart** | C99 | art.c 975 + art.h 215 = 1190 | ART | 独立 ART 库，BSD 协议 |
| **memkv** (miaobyte) | C11 | memkv.c 424 + miaobyte.c 125 | 256-ary 定长 Trie | 进程内 KV Store |

---

## 一、kvspace-go/art 详细分析

### 1.1 节点结构

```go
type nodeHeader struct {
    prefix   []byte   // 压缩前缀，无长度限制
    value    []byte   // TLV 编码 XValue body（hasValue=true 时有效）
    hasValue bool     // 节点自身是否持有 value
    count    int      // 当前子节点数
}

type node4 struct {
    h        nodeHeader
    keys     [4]byte
    children [4]node      // Go interface，实际是 *node4/*node16/等
}
```

**关键设计决策：没有独立 leaf 类型。** leaf 就是一个 `node4{hasValue: true, count:0}`。这与 armon/libart 的 `art_leaf` 不同——省掉了 IS_LEAF 指针 tag 和单独的 leaf 分配器。

### 1.2 插入路径 insertNode

```
insertNode(n, key, depth)
├─ n==nil → newLeaf(suffixPrefix, value)      // 创建含值 node4
├─ prefix 部分匹配 → 分裂节点
│   ├─ 创建新 node4 作为 parent
│   ├─ 旧节点的剩余 prefix 下沉
│   └─ 新 leaf 作为 sibling
├─ depth 到达 → h.hasValue=true              // 原地赋值
├─ child 不存在 → addChild + newLeaf
└─ child 存在 → insertNode(child, depth+1) → replaceChild
```

newLeaf 实现：
```go
func newLeaf(prefix, value []byte) node {
    return &node4{h: nodeHeader{
        prefix: cloneBytes(prefix),
        value:  cloneBytes(value),
        hasValue: true,
    }}
}
```

### 1.3 节点膨胀/收缩

```
addChild 内联膨胀:
node4 满(count==4)  → grow4()  → node16 (线性扫描→无 SSE)
node16 满(count==16) → grow16() → node48 (keys[i]→index[256])
node48 满(count==48) → grow48() → node256

removeChild 内联收缩:
node16 count≤4  → shrink16() → node4
node48 count≤16 → shrink48() → node16
node256 count≤48 → shrink256() → node48

compactNode: 单子节点 + 无值 → 吞并唯一 child 的 prefix，消除中间节点
```

### 1.4 迭代与 key 重建

**key 不在树中存储**。`walkNode` 在遍历过程中用 path builder 拼接 key：

```go
func walkNode(n node, path []byte, fn func([]byte, []byte) bool) bool {
    h := n.header()
    if h.hasValue && !fn(cloneBytes(path), cloneBytes(h.value)) {
        return false
    }
    n.eachChild(func(edge byte, child node) bool {
        childPath := appendCopy(path, edge)
        childPath = appendCopy(childPath, child.header().prefix...)
        return walkNode(child, childPath, fn)
    })
}
```

path 从 root 开始逐层 append：`path + edge_byte + child.prefix`。

### 1.5 并发模型

`store.mu sync.RWMutex` — 全局读写锁。读操作持 RLock，写操作持 Lock。

### 1.6 内存分配

完全依赖 Go GC — 每次 `newLeaf`/`grow*`/`cloneBytes` 都在堆上分配。树节点通过 Go interface（带类型指针的 fat pointer）关联。**零手动内存管理。**

### 1.7 KVSpace 语义层 (backend.go)

1450 行的 backend.go 实现了 KVSpace 的 13 个方法（Get/Set/Del/List/...），关键抽象：

- **Index**: 节点的 children 列表 = child key names。`List` = 遍历 tree node 的 children。
  - Index value 存在单独的 key 路径（如 `/a/` 的 value 是 `/a` 路径上的 index TLV）
  - 代码中 radixTree 存的是 TLV encoded bytes，通过 XValue 接口解码
- **LinkIndex**: 特殊的 value kind，Get 时 resolve 跳转目标（最多 64 层）
  - 不存 link 自身，存 TLV 编码的 linkindex value 在叶子节点
- **ExtIndex**: external index path，写/创建穿透到自身，读合并 extpath 的子成员
  - `exts map[string]string` 存储 extindex → exttarget 映射，不在树中

---

## 二、armon/libart 详细分析

### 2.1 结构对比 kvspace-go/art

| | armon/libart | kvspace-go/art |
|---|---|---|
| leaf 表示 | 独立 `art_leaf` 类型，key 内联（变长） | node4 + hasValue=true，无独立类型 |
| prefix 上限 | MAX_PREFIX_LEN=10（固定数组） | 无上限（Go slice） |
| node16 find_child | SSE `_mm_cmpeq_epi8` + `__builtin_ctz` | 线性扫描 |
| 节点分配 | calloc (4 种大小) | Go new |
| leaf 分配 | calloc(sizeof(leaf)+key_len) | Go new(node4) |
| 删除 | free(leaf) | Go GC |
| IS_LEAF 判别 | 指针低 bit tag | n.count==0 && n.hasValue 推断 |
| value 指针 | `void *value`（用户任意指针） | TLV `[]byte` 编码 |
| 语言 | C99 | Go |

### 2.2 插入路径差异

libart 在 prefix split 时更复杂——因为 MAX_PREFIX_LEN 截断，超长 prefix 需要用 `minimum()` 找到叶子来比较后续字符。kvspace-go/art 没有这个上限，无需该逻辑。

### 2.3 删除路径差异

libart 的 `remove_child4` 在只剩 1 个 child 时做 prefix 吞并（与 kvspace-go/art 的 compactNode 等价）。libart 的 node256→node48 收缩阈值是 37（不是 48），避免 48/49 边界颠簸。

### 2.4 指针 tag 技巧

```c
#define IS_LEAF(x)  (((uintptr_t)x & 1))
#define SET_LEAF(x) ((void*)((uintptr_t)x | 1))
#define LEAF_RAW(x) ((art_leaf*)((uintptr_t)x & ~1))
```

malloc 返回的指针最低 bit 永远是 0（对齐到至少 2 字节），所以偷 1 bit 做 tag。

---

## 三、memkv 详细分析

### 3.1 索引结构：非 ART

```c
typedef struct {
    bool has_key:1;
    uint64_t box_offset:63;      // value 在 box zone 的偏移
    int32_t child_key_blocks[2]; // 实际长度=char_type，定长
} key_node_t;
```

**256-ary 定长 Trie**。`char_type` 预设字符集大小（miaobyte 使用 48 个 ASCII 字符）。

每个节点分配 `8 + 4*char_type` 字节。所有可能的子字节索引槽在节点创建时就全量分配，不管实际有几个子节点。

### 3.2 内存分层

```
pool_data
├── memkv_meta_t (header)
├── key zone → blockmalloc (定长块 = keynode_size)
├── valueptr zone → boxmalloc (16叉伙伴系统 meta)
└── value zone → 纯数据区 (boxmalloc data)
```

### 3.3 对比 ART

| | memkv 256-ary Trie | ART |
|---|---|---|
| 每节点开销 | 200B（char_type=48）~1032B（char_type=256） | 64B (node4) ~ 2112B (node256) |
| 稀疏 key | 大量 1 子节点的 node 浪费 200B | node4 只占 64B |
| 密集 key | 填充率高时效率尚可 | node256 O(1) 查找 |
| 前缀压缩 | 无 | 有（prefix 字段） |
| 自适应 | 不 | 4 种 size class |

---

## 四、leaf 设计的三种方案

leaf 是"树中存储 value 的终结点"。三种实现有根本差异：

### 方案A: armon/libart — 独立变长 leaf

```c
typedef struct {
    void *value;        // 用户指针
    uint32_t key_len;
    unsigned char key[];  // 内联 key
} art_leaf;  // allocate: sizeof(art_leaf) + key_len
```

- **优点**: 迭代时 key 直接可用，不需要 path reconstruct
- **缺点**: 变长分配，slab 的 freelist 失效；需要 IS_LEAF 指针 tag
- **shm 适应**: key 移到 bump zone，leaf 变定长（存 offset+len），失去"内联"优势

### 方案B: kvspace-go/art — leaf=带值的 node4

```go
func newLeaf(prefix, value []byte) node {
    return &node4{h: nodeHeader{
        prefix:   cloneBytes(prefix),
        value:    cloneBytes(value),
        hasValue: true,
    }}
}
```

- **优点**: 无独立 leaf 类型，无 IS_LEAF tag，统一 slab（leaf 复用 node4 slab）
- **缺点**: key 不在 leaf 中存储——迭代时必须 path reconstruct（逐层拼接）
- **shm 适应**: 完全自然。leaf 就是 node4（64B slab）。prefix/value 都存 offset+len。

### 方案C: memkv — 无独立 leaf

memkv 没有 leaf 概念。`has_key` + `box_offset` 直接在 trie 节点的 bitfield 中。value 在 box zone。

---

## 五、slab 分配器方案

kvspace-c 需要 4 种 ART 节点类型 + leaf 的固定大小分配。两种候选：

### 方案A: blockmalloc 模式（推荐）

来自 `miaobyte/blockmalloc` — 每个 slab 实例 = metadata (blocks_meta_t) + block 数组：

```c
typedef struct {
    uint64_t node_size;     // 每个 slot 的字节数
    uint64_t zone_offset;   // 该 slab 的 data zone 在 shm 块内的偏移
    uint64_t zone_size;     // data zone 总大小
    uint64_t malloc_count;  // 已分配 slot 数
    uint64_t used_count;    // 使用中 slot 数
    int64_t  free_head;     // freelist 头，-1=空
    int64_t  lock;          // spinlock
} slab_t;
```

- 每个 slot 开头不存 header（blockmalloc 有 2/4/8 字节 block head，对 64B node4 浪费 3-12%）
- freelist 嵌入空闲 slot 的 `children[0]`：因为 node 空闲时没有子节点，复用该字段
- **O(1) 分配**：pop freelist，或 bump 新 slot

### 方案B: bbu bit-vector 模式

3-list (partial/empty/full) + 64-bit bitmask。更复杂的分配策略，但需要 mmap 动态增长、最大 64 item per page。对于我们的固定 4 种 slab 类型+预分配 shm 场景，过度设计。

---

## 六、shm 可扩展存储方案分析

上一轮设计假设固定 size shm block。本轮分析 4 种扩展方案。

### 方案 A: 固定大小，用户选（MVP）

创建时指定 size，不可扩容。满了就报错。需要 compact 回收 bump zone。

- **优点**: 最简单，~50 行代码
- **缺点**: 用户必须预估容量；估小了跑一段时间就 OOM
- **适用**: 单机开发/测试，数据量可预测

### 方案 B: 多 chunk 分段（segmented）

固定大小 chunk（如 64MB），装满一个再建一个。指针 = `(chunk_id, offset)`。

```
shm: /kshm_0  /kshm_1  /kshm_2 ...
     64MB     64MB     64MB

节点中 child 引用: uint64_t = (chunk_id << 32) | offset
每个进程 mmap 所有活跃 chunk，chunk_table[chunk_id].base
OFF(chunk_id, offset) = chunk_table[chunk_id].base + offset
```

- **优点**: 理论上无限扩展；chunk 大小可控
- **缺点**: 每个指针解引用多一次数组查表；OFF() 宏从 1 行变 3 行；slab freelist 不能跨 chunk

### 方案 C: file-backed mmap，预留虚拟地址空间

shm_open 一个文件，ftruncate 到初始物理大小，mmap 一个巨大的虚拟范围。

```
fd = shm_open("/kshm", ...)
ftruncate(fd, 128MB)                                    // 初始物理 128MB
base = mmap(NULL, MAX_SIZE, PROT_RW, MAP_SHARED, fd, 0) // 虚拟预留 64GB
// 文件大小 128MB，映射了 64GB 虚拟空间
// 访问偏移 >128MB → SIGBUS（文件未扩展）
// ftruncate(fd, 1GB) → 物理扩展到 1GB，原 SIGBUS 区域变有效
```

- **优点**: 所有指针就是 `base + offset`，OFF() 宏不变；扩容无需 munmap；所有进程自动可见
- 原理: mmap 的 size 参数是虚拟地址空间预留，物理页在首次访问时才分配。MAP_SHARED 保证所有进程共享物理页
- **扩容**: `pthread_mutex_lock → ftruncate(fd, new_size) → header.region_size = new_size → unlock`
- **注意**: 必须确保所有进程 mmap 时用了足够大的 MAX_SIZE

### 方案 D: 纯 bump + periodic compact（不推荐）

完全不 free。填满后 compact（拷贝到新 shm）或 fail。

---

### 推荐：方案 C

**file-backed mmap + 虚拟预留** 是 Linux 上最成熟的"可扩展 shm"模式。LMDB 的 `mapsize` 参数本质上就是这个——mmap 一大段但只提交用到的页。

与其他方案对比：

| | 方案 A 固定 | 方案 B 分段 | **方案 C 虚拟预留** |
|---|---|---|---|
| OFF() 宏 | `base+offset` | `chunks[hi32(off)].base+lo32(off)` | `base+offset` |
| 扩容 | ❌ | 加 chunk | ftruncate |
| 进程间同步 | 无 | 需传播 chunk_table | 读 header.region_size |
| slab freelist | 单体 | 不能跨 chunk | 单体 |
| 实现行数 | 50 | ~300 | ~100 |
| 虚拟内存浪费 | 无 | 无 | 预留 64GB（但不占物理） |

**方案 B 的致命伤**: slab freelist 跨 chunk。node4 slab 的 freelist 嵌入空闲节点的 `children[0]`——但空闲节点可能散布在不同的 chunk，freelist 变成了跨 chunk 链表。每次 slab_alloc 都要查 chunk_table，且不能保证在最佳 chunk 分配。

**方案 C 零侵入**：ART 树 800 行一字不改，OFF() 宏一字不改，slab/bump 各加一行检查 `region_size`。

### 具体实现

```c
#define KSHM_MAX_SIZE (64ULL * 1024 * 1024 * 1024)  // 64GB

// 创建
int fd = shm_open("/kshm", O_RDWR | O_CREAT, 0644);
ftruncate(fd, INITIAL_SIZE);  // 初始物理 256MB
void *base = mmap(NULL, KSHM_MAX_SIZE,
                  PROT_READ | PROT_WRITE,
                  MAP_SHARED, fd, 0);
header->region_size = INITIAL_SIZE;
header->region_max   = KSHM_MAX_SIZE;
header->resize_mutex;  // PTHREAD_PROCESS_SHARED

// 扩容（bump_alloc 内部自动触发）
static uint64_t bump_alloc(kshm_t *h, size_t sz) {
    if (h->header->bump_top + sz > h->header->region_size) {
        size_t new_size = h->header->region_size * 2;
        if (new_size > h->header->region_max) return 0;
        pthread_mutex_lock(&h->header->resize_mutex);
        ftruncate(h->fd, new_size);
        h->header->region_size = new_size;
        pthread_mutex_unlock(&h->header->resize_mutex);
    }
    uint64_t off = h->header->bump_top;
    h->header->bump_top += sz;
    return off;
}
```

自动扩容：bump 区快满时，容量翻倍（2×, 直到 MAX_SIZE）。ftruncate 是 O(1) 操作（文件系统元数据更新，不涉及数据拷贝）。

---

## 七、三个实现的代码质量与移植性

| | armon/libart | kvspace-go/art | memkv |
|---|---|---|---|
| 算法正确性 | 10 年验证，业界标准 | 基于 libart 重写 | 不可验证 |
| 代码可读性 | 高，注释清晰 | 高，Go interface 清晰 | 中，flexible array hack |
| 移植到 C+shm | 需改 ~300 行（指针→偏移量） | 需从零翻译（Go→C） | 不适用（非 ART） |
| 外部依赖 | 0 | Go runtime + GC | blockmalloc + boxmalloc |
| SSE 优化 | ✅ node16 lookup | ❌ 线性扫描 | N/A |
| MAX_PREFIX_LEN | 10（有截断处理逻辑） | 无限制 | N/A |

---

## 八、最优方案

### 3 组件组合

```
kshm.c (~2500 行, 零依赖)
│
├── 存储后端: file-backed mmap + 虚拟预留
│   ├── shm_open 创建文件，mmap 预留 64GB 虚拟空间
│   ├── ftruncate 按需扩展物理页（翻倍策略，O(1)）
│   └── OFF(ptr, type) = (type*)(base + offset)，不变
│
├── ART 算法: 综合 armon/libart + kvspace-go/art
│   ├── 插入/删除/搜索核心: 来自 libart（含 SSE node16 优化）
│   ├── leaf 模型: 来自 kvspace-go/art（leaf=node4+hasValue，无独立类型）
│   ├── 节点膨胀/收缩: 来自 libart（含 node256→node48 阈值 37 避免颠簸）
│   ├── prefix 无上限: 来自 kvspace-go/art（取消 MAX_PREFIX_LEN 限制）
│   └── 偏移量寻址: C99 uint64_t + OFF() 宏（零改动）
│
├── slab 分配器: 来自 blockmalloc 思想（每类型 freelist, O(1)）
│   ├── node4 slab: 64B, freelist 嵌入 children[0]
│   ├── node16 slab: 80B
│   ├── node48 slab: 416B
│   └── node256 slab: 2112B
│
├── bump 分配器: 自研 (~80 行)
│   ├── key + value TLV + prefix 的 append-only 分配
│   └── 自动触发 ftruncate 扩容（容量翻倍至 MAX_SIZE）
│
└── 并发: pthread_mutex_t(PROCESS_SHARED) 全局锁
```

### 为何不直接用 libart 的 leaf 模型

libart 的独立 `art_leaf` + 变长 key 内联 + 指针 tag 是三个紧密耦合的设计决策。改为 offset-based 后：
- 变长内联失效（slab 需要定长）→ key 必须外置 bump zone → leaf 变定长
- 指针低 bit tag 失效（offset 最小值不确定）→ 需要独立 type 字段
- 独立 leaf 类型需要第 5 个 slab → kvspace-go/art 的 leaf=node4 消除此需求

**结论：在 shm 场景下，kvspace-go/art 的 leaf 模型天生更适合** — leaf 不是特殊类型，只是 `node4{hasValue:true}`，无需 IS_LEAF tag，无需第 5 个 slab。

### 具体采纳

| 特性 | 来源 | 理由 |
|------|------|------|
| ART 核心算法（递归 insert/delete） | armon/libart | 10 年验证，C99 直接可译 |
| leaf 模型（leaf=node4+hasValue） | kvspace-go/art | 无需 IS_LEAF tag，减少 1 个 allocator |
| prefix 无上限 | kvspace-go/art | 消除 MAX_PREFIX_LEN 截断分支 |
| node16 SSE lookup | armon/libart | 已在 x86 上验证，O(1) vs O(16) |
| node256→48 收缩阈值=37 | armon/libart | 避免边界颠簸 |
| 紧凑节点（compactNode） | kvspace-go/art | 比 libart 的 remove_child4 内联 prefix 吞并更清晰 |
| slab 分配 | blockmalloc 思想 | freelist 嵌入空闲 slot，O(1) |
| bump 分配器 | 自研 | 30 行，无外部依赖 |
| 全局 RWMutex | kvspace-go/art | PTHREAD_PROCESS_SHARED 代替 sync.RWMutex |

### 预计行数

```
kshm.h          ~200 行   (API + 结构体定义)
kshm.c          ~1500 行  (ART + slab + bump + compact + KVSpace 语义)
  ├── ART 树    ~700 行   (insert/delete/search/iter/min/max/prefix/grow/shrink)
  ├── slab      ~100 行   (4 实例 × 25 行)
  ├── bump      ~50 行    (alloc + compact)
  ├── KVSpace   ~500 行   (Get/Set/Del/List/Link/ExtIndex 等 13 方法)
  └── 并发/锁   ~50 行    (pthread mutex attr + init + lock/unlock)
Makefile          ~20 行
test/test.c       ~300 行  (单进程功能测试)
```

### 不采纳的

| 特性 | 来源 | 理由 |
|------|------|------|
| 变长 art_leaf | libart | shm 必须定长 slab |
| IS_LEAF 指针 tag | libart | offset 无闲置 bit |
| MAX_PREFIX_LEN=10 | libart | 无限制更简，消除 fallback 到 minimum() |
| boxmalloc 16叉伙伴 | memkv | 过度复杂，bump 够用 |
| blockmalloc 块头 (used/has_next_free) | memkv | node4 才 64B，2-8B header 浪费 3-12%，freelist 嵌入 children[0] 零开销 |
| Go GC | kvspace-go/art | C 无 GC |

---

## 九、KVSpace 完整 API 层设计

kshm 不是泛用 K/V 存储——它实现 `KVSpace` 接口（13 个方法 + Index/ExtIndex/Link 语义）。

### 9.1 路径模型

```
/a          文件（值 key）      → 树的 leaf: node4{hasValue=true}
/a/         目录（index key）   → 树的 leaf: node4{hasValue=true, value=TLV("index", "\n".join(children))}
/           根目录              → 同一棵 ART 树上的不同路径
```

**目录 key 以 `/` 结尾，文件 key 不以 `/` 结尾**。两者在 ART 树中独立共存——`/a` 和 `/a/` 是两个不同的 key。

### 9.2 ART 树中存什么

树的 **key** = 完整绝对路径（如 `/vt/0/pc`）。树的 **value**（node4 的 value 字段）= TLV 编码的 XValue body bytes。

| KVSpace 概念 | ART 树表现 |
|-------------|-----------|
| 文件值 | leaf 存在，value=TLV bytes |
| None（缺失） | leaf 不存在 |
| Index（目录） | leaf 存在，value=TLV("index", child_names) |
| LinkIndex | leaf 存在，value=TLV("linkindex", target_path) |
| ExtIndex | leaf 存在，value=TLV("extindex", "=extpath\\n" + own_children) |

### 9.3 目录索引维护（类比 kvspace-go/art backend.go）

`Set("/a/b", val)` 的完整路径：

```
1. 解析路径: parent="/a/", name="b", resolved="/a/b"
2. 确保 parent directory 存在 (ensureDirCachedLocked)
   → 如果 parent 不存在 → Mkindex("/a/")
   → Mkindex: 递归创建所有祖先目录 + 在树中 Set("/a/", TLV("index",""))
3. 树中插入: tree.Insert("/a/b", encodeTLV(val))
4. 更新 parent 索引: 读 parent 的 index value → 追加 "b" → Set("/a/", new_index_TLV)
```

### 9.4 Get (prefix + keys[])

```c
// Get("/vt/0", ["pc", "status"]) → [XValue_pc, XValue_status]
int kshm_get(kshm_t *h, const char *prefix,
             const char **keys, uint32_t nkeys,
             kshm_xvalue_t *results);
```

伪代码:
```
for each key in keys:
    full_path = JoinPath(prefix, key)
    resolved = resolve_links(full_path)    // 最多 64 层穿透
    if (resolved has ext_fallback):
        先查 ext.target/Join(target, key)
        后查 resolved/key 自身
    leaf = art_search(resolved)
    if leaf: result[i] = decodeTLV(leaf.value)
    else:   result[i] = NoneKind
```

### 9.5 Set ([]KVPair)

```c
int kshm_set(kshm_t *h, const kshm_kv_t *pairs, uint32_t count);
```

伪代码:
```
for each pair:
    validate: 绝对路径、文件不含 Index/LinkIndex/ExtIndex kind
    resolve_links(pair.path) 排除 ext target 路径
    确保 parent directory 存在
    如果 dir: replaceDirectory (更新 children 列表)
    如果 file: art_insert(resolved_path, encodeTLV(pair.value))
    更新 parent 的 index（增删 child name）
```

### 9.6 List

```c
int kshm_list(kshm_t *h, const char *prefix, int expand_ext,
              char **names, int buf_len);
```

伪代码:
```
resolved = resolve_links(prefix)
local = 读 resolved 的 index value → split("\n") → child names
if (!expand_ext): return local

ext_fallback = 查 exts map[resolved]
if (ext_fallback):
    extended = 读 ext_fallback 的 index value → 合并，去重（local 覆盖）
    return local ∪ extended
return local
```

### 9.7 Del / DelTree

```c
int kshm_del(kshm_t *h, const char **keys, uint32_t count);
int kshm_del_tree(kshm_t *h, const char *prefix);
```

`Del`: 解析最终路径（祖先链接穿透，最终组件不穿透）→ 删除 leaf → 从 parent 索引移除 child name。

`DelTree`: 收集子树全部 key（art_iter_prefix）→ 逐条 Del → 清理 ext map 中受影响的条目。

### 9.8 Link

```c
int kshm_link(kshm_t *h, const char *target, const char *linkpath);
```

```
1. resolve_links(parent(linkpath)) 确保祖先是真实路径
2. art_insert(linkpath, TLV("linkindex", target))
3. parent 索引追加 linkpath 的 base name
```

`resolve_links(path)` 是每次 Get/Set/Del 的前置步骤：
```
for (i=0; i<64; i++):
    parts = split(path, "/")
    for each part:
        v = art_search(当前累积路径)
        if (v.kind == "linkindex"):
            替换 target 段，重试整个 path
    return resolved path  // 或超过 64 层 → error
```

### 9.9 ExtIndex

```c
int kshm_ext_index(kshm_t *h, const char *path, const char *extpath);
```

```
1. path 和 extpath 都须以 / 结尾
2. extpath 须是普通 Index（不允许级联）
3. art_insert(path, TLV("extindex", "=" + extpath + "\n"))
4. exts_map[path] = extpath
```

ExtIndex 的读语义（List/Get 时自动合并）:
```
// Get: 先查本地 → 缺失则查 ext target
// List: 本地 ∪ ext target（本地覆盖同名）
// Set/Del: 只操作本地，不动 ext target
// ext target 路径上的 Set → panic（禁止）
```

### 9.10 Watch / Notify

```
┌────────────────────────────────────────┐
│  store.queues: map[string]*notifyQueue │
│  notifyQueue = mutex + cond + value    │
│  一次性通知，唤醒后自动清空            │
└────────────────────────────────────────┘
```

```c
int kshm_notify(kshm_t *h, const char *key, const void *val, uint32_t vlen);
int kshm_watch(kshm_t *h, const char *key, int timeout_ms,
               uint8_t *kind, void *val, uint32_t *vlen);
```

- Notify: key → 找到或创建 queue → push value → pthread_cond_signal
- Watch: key → 找到或创建 queue → pthread_cond_timedwait → pop value

### 9.11 Mkindex

```c
int kshm_mkindex(kshm_t *h, const char *path);
// 类似 mkdir -p
```

逐级创建缺失的祖先目录。每级 Set 一个 Index value（初始 children=空）。

### 9.12 内存开销估算（接口层）

KVSpace 语义层额外需要的数据结构:

```
store {
    tree: radixTree                     // ART 树本体
    exts: map[path → ext_target_path]   // ExtIndex 映射表
    queues: map[path → notifyQueue]     // Watch/Notify 队列
    mu: pthread_mutex_t                 // 全局读写锁
}

// exts map 实现: 直接在 ART 树中查
// 读取 /a/b/ 的 value → decodeTLV → 如果是 extindex → 解析 extpath
// 不需要额外 map 结构！

// queues map 实现: 单独的一块 shm 区域, key=path hash, value=pthread_cond_t+value
```

### 9.13 完整 kshm.h API

```c
// 生命周期
kshm_t* kshm_open(const char *path, size_t size);
kshm_t* kshm_create(const char *path, size_t size);
kshm_t* kshm_attach(const char *path);
void    kshm_close(kshm_t *h);
void    kshm_destroy(const char *path);

// 单点读写
int kshm_get(kshm_t *h, const char *prefix, const char **keys, uint32_t nkeys,
             kshm_xvalue_t *results);
int kshm_set(kshm_t *h, const kshm_kv_t *pairs, uint32_t count);

// 目录操作
int kshm_list(kshm_t *h, const char *prefix, int expand_ext,
              char **names, int buf_len);
int kshm_del(kshm_t *h, const char **keys, uint32_t count);
int kshm_del_tree(kshm_t *h, const char *prefix);

// 通知
int kshm_notify(kshm_t *h, const char *key, const void *val, uint32_t vlen);
int kshm_watch(kshm_t *h, const char *key, int timeout_ms,
               uint8_t *kind, uint8_t *kind_len, void *val, uint32_t *vlen);

// 目录创建
int kshm_mkindex(kshm_t *h, const char *path);

// mount
int kshm_link(kshm_t *h, const char *target, const char *linkpath);
int kshm_ext_index(kshm_t *h, const char *path, const char *extpath);
int kshm_unlink(kshm_t *h, const char *path);

// 管理
int kshm_clear(kshm_t *h);   // 清空全部数据
int kshm_compact(kshm_t *h); // bump zone 碎片回收
int kshm_stats(kshm_t *h, kshm_stats_t *out);
```

### 9.14 与 kvspace-go/art backend.go 的对应

| kvspace-go/art 方法 | kshm.c 对应 |
|---|---|
| `Get(prefix, keys)` | `kshm_get` — 同语义 |
| `Set([]KVPair)` | `kshm_set` — 同语义，含 index 维护 |
| `List(prefix, expandExt)` | `kshm_list` |
| `Del(keys...)` | `kshm_del` |
| `DelTree(prefix)` | `kshm_del_tree` — collectTreeKeys + 逐条 del |
| `Notify(key, val)` | `kshm_notify` |
| `Watch(key, timeout)` | `kshm_watch` |
| `Mkindex(path)` | `kshm_mkindex` |
| `Link(target, linkpath)` | `kshm_link` |
| `ExtIndex(path, extpath)` | `kshm_ext_index` |
| `UnLink(path)` | `kshm_unlink` — 删除 extindex 条目 |
| `Clear()` | `kshm_clear` — 重置 slab + bump |
| `DisConn()` | `kshm_close` — munmap |

### 9.15 行数修正

```
kshm.h          ~250 行   (API + XValue 类型 + 结构体定义)
kshm.c          ~2500 行  (完整 KVSpace 实现)
  ├── ART 树    ~800 行   (insert/delete/search/iter/min/max/prefix/grow/shrink)
  ├── slab      ~100 行   (4 实例 × 25 行)
  ├── bump      ~80 行    (alloc + compact + stats)
  ├── XValue    ~200 行   (TLV encode/decode + 各类型构造函数)
  ├── KVSpace   ~1100 行  (11个方法: Get/Set/List/Del/DelTree/Link/ExtIndex/Unlink/
  │                        Mkindex/Notify/Watch + 内部路径解析/索引维护/链接穿透)
  └── 并发/锁   ~60 行    (pthread mutex attr + init + lock/unlock + cond)
Makefile          ~20 行
test/test.c       ~400 行  (对齐 kvspace-go tutorial 的 13 个 .sh 用例)
```
