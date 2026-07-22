# kvspace-server 实现分析：树形 C++ 方案、地址可计算性与 Watch/Notify 协调面

> 草稿 · 2026-07-18 · 遵守根 DESIGN.md 与 doc/kvspace/shm.md（kvregion 底座）
> 命题（用户提出）：**大部分读写接口可以直接由函数地址计算实现；Watch/Notify 需要额外设计。**
> 本文验证并形式化该命题：先解剖 redis 后端实现，再对标学术界，最后给出 C++ 树形方案。
> 状态：分析文档，零实现。

---

## 一、redis 后端逐方法解剖（internal/kvspace/redis/redis.go，253 行）

| 方法 | Redis 映射 | RTT | 备注 |
|------|-----------|-----|------|
| Get | GET | 1 | 前置 `ResolveCore` 逐段链接穿透（客户端缓存后 0 RTT） |
| GetMany | MGET | 1 | |
| Set | SET + **每层 SADD** | 1+depth | 写放大：`/vthread/7/[3,0]/x` = 1 SET + 4 SADD（已有 issue P1-6） |
| SetMany | pipeline MSET+SADD | 1 | 批量合并 ✓ |
| Del | DEL + **每层 SREM** | 1+depth | 🔴 **实测 bug**：沿全路径 SREM 把祖先索引清掉，无视兄弟存活（fix-013） |
| DelTree | 递归 SMEMBERS+DEL | O(子树) | 非原子 |
| List | SMEMBERS `<prefix>/.` | 1 | 目录=显式维护的每层集合 |
| Link/Unlink | SET `"->target"` 哨兵 | 1 | 进程内 links 缓存，多实例过期（P1-5）；删除语义=末段本体、祖先穿透（fix-014） |
| Notify | LPUSH | 1 | 队列，无 watcher 时值滞留 |
| Watch | BLPOP | 阻塞 | 服务端唤醒，一次 notify 恰被一个 watcher 消费 |

三个结构性事实：

1. **数据与目录是双结构**（string 键值 + 每层 set 索引），一致性靠客户端双写维护——fix-013 正是双写漏了删除方向的兄弟判断。**双结构是 bug 之源。**
2. **链接解析在客户端**（`ResolveCore` + 进程内缓存），服务端只见解析后的绝对路径。
3. **Watch/Notify 语义是 MPMC 有值队列**（LPUSH/BLPOP），不是 pub/sub 广播、不是条件变量：值被恰好一个消费者取走，无消费者时滞留。任何替代后端必须复刻这一点（tutorial/vthread 终态通知都依赖它）。

## 二、方法二分类学：地址可计算 vs 时间协调

命题成立，且边界precise：

| 类 | 方法（11/13） | 本质 |
|----|--------------|------|
| **地址可计算** | Get/GetMany/Set/SetMany/Del/DelTree/List/Link/Unlink/ClearAll/DisConn | `路径 → f(路径) → 内存地址 → load/store`。f 是纯函数（hash 一跳或 radix 下降），无会合、无等待，天然适合 one-sided 访问 |
| **时间协调** | Watch/Notify | sleeper 与 waker 的**会合（rendezvous）**。地址计算只能定位队列头；"阻塞直到有值"必须引入等待原语（futex/事件/轮询）。空间可计算，**时间不可计算** |

精确到子步骤：`Watch = 地址计算（定位队列）+ 等待原语（时间）`；`Notify = 地址计算 + 入队 + 唤醒`。所以"额外设计"的准确范围是**等待/唤醒机制与队列内存管理**，而非整个方法。

## 三、学术对标

### 3.1 地址可计算面：hash 与 tree 两条路线

| 系统 | 结构 | 与本设计的关系 |
|------|------|---------------|
| [Pilaf (USENIX ATC **2013**)](https://www.usenix.org/biblio/using-one-sided-rdma-reads-build-fast-cpu-ef%EF%AC%81cient-key-value-store) | cuckoo hash + 自校验（CRC） | one-sided GET 绕过服务端 CPU 的开山之作；注：kvspace-rdma 对标表原误写 ATC'12，已更正 |
| HERD (SIGCOMM'14) | hash + RPC | 论证写路径经服务端 CPU 往往优于 one-sided 写协议 |
| FaRM (NSDI'14) | hopscotch + version | 无锁读的 version 校验先例（kvregion seqlock 同源） |
| [RACE (USENIX ATC 2021)](https://www.huaweicloud.com/lab/storage/news_paper_race_hashing.html) | **全单边可扩展 hash** | 首个全部操作（含插入/删除/扩容）纯 one-sided 的哈希索引；证明 hash 路线的地址可计算性可以做满 |
| [Sherman (SIGMOD 2022)](https://github.com/River861/Sherman) | 分布式 B+Tree | 树在解耦内存上可行的证明；条目级 version 减写放大、NIC 片上锁 |
| [SMART (OSDI 2023)](https://www.usenix.org/conference/osdi23/presentation/luo) | **解耦内存上的自适应基数树（ART）** | 论证 radix tree 读写放大小于 B+Tree（写密集 6.1×）；内部节点无锁 + 叶子细粒度锁 + 路径缓存——树形远端访问的完整解法 |
| ART (Leis, ICDE 2013) / OLC (DaMoN'16) | 单机自适应基数树 + 乐观锁耦合 | C++ 单机树的标准并发方案：读者无锁 version 重试 |
| MICA (NSDI'14) | 每核分片 | 消除跨核协调的分片思想（多 worker 场景） |

**路线判词**：hash 的 f(路径) 是一跳，但 List/DelTree 需要第二套目录结构（redis 后端现状，fix-013 即其代价）；radix tree 的 f(路径) 是逐字节下降，**结构即目录**——List = 读子表，DelTree = 摘子树，零双写。学术界两条路线都已在 one-sided 场景走通（RACE vs SMART），选型取决于工作负载。

### 3.2 时间协调面

- futex（Franke et al., OLS 2002）：Linux 跨进程等待/唤醒原语，`FUTEX_WAIT_BITSET|SHARED` 支持超时与共享内存字——单机 shm 场景的标准答案。
- RDMA 侧无 futex 等价物：WRITE-with-IMM 产生远端完成事件（CQ）可当唤醒；FaSST (OSDI'16) 论证双边 RPC 在小消息场景优于单边组合——协调面走服务端 CPU 有学术背书。
- 通用权衡：spin（μs 级延迟、烧 CPU）vs block（唤醒开销）→ 自适应 spin-then-block（Linux futex/Go runtime 惯例）。

## 四、C++ 树形 kvspace-server 方案

### 4.1 结构选型：ART 为主结构

kvlang 路径是层级字符串，且 **List（直接子项）与 DelTree（子树）是一等公民**（帧创建/销毁每次调用都发生）。radix tree 是唯一让这两个操作零额外结构的选择：

| 维度 | ART 主结构 | hash 主结构（RACE 路线） |
|------|-----------|------------------------|
| Get/Set | O(路径长) 下降，节点 256 分支缓存友好 | O(1) 一跳 |
| List | 读节点子表，**原生** | 需第二套目录索引（双写一致性，fix-013 类 bug） |
| DelTree | 摘子树指针 + epoch 回收，**近原子** | 递归扫描 + 双结构逐条删 |
| Link | 节点类型=link，下降时穿透 | 值哨兵 + 客户端解析（现状） |
| **kvlang 帧局部性** | **帧句柄缓存**：FrameRoot(PC) 前缀稳定，VM 持有帧节点指针后，槽访问 = 从句柄一步下降 ≈ O(1)；取指 `[s0,s1]` 同理 | 每次全键重哈希，无法利用前缀稳定性 |
| one-sided 远端 | 多 RTT，需 SMART 式路径缓存 | 一跳（RACE） |

fix-009 的平坦成员键（`/n0.val`）在 ART 中就是同层兄弟叶子，`X.名` 键族与 `X/名` 结构自然共存于同一棵树。

**结论**：单机 shm 场景 ART 完胜（帧句柄缓存是 kvlang 专属红利）；远端 one-sided 点查是 hash 的主场——与 shm.md §三 分工一致：**本地走树（shm://），远端 GET 可加 hash 缓存索引或走 server RPC（HERD/FaSST 路线）**，不强求远端 one-sided 遍历树。

### 4.2 C++ 实现要点（shm 多进程约束）

| 要点 | 方案 | 出处 |
|------|------|------|
| 指针 | **offset_ptr**（自相对偏移）：各进程 mmap 基址不同，绝对指针不可用 | boost::interprocess 惯例 |
| 分配 | region 内 arena + size-class 空闲链；节点/值分区 | MICA/常规 |
| 并发读 | OLC：节点 version，读者无锁下降、结尾校验重试 | ART-OLC (DaMoN'16)、与 kvregion seqlock 同构 |
| 并发写 | 节点内细粒度锁（叶子）+ 内部节点无锁膨胀 | SMART 混合方案 |
| 回收 | epoch-based reclamation：DelTree 摘链后延迟释放，读者不悬空 | 常规 EBR |
| 崩溃一致性 | 进程崩溃留锁 → owner-pid 标记 + attach 时清尸 | robust futex 思想 |
| 跨语言 | 布局 C ABI 定稿（kvregion M0），Go/Python 经 cgo/ctypes 或薄 client | shm.md §二 |

### 4.3 Watch/Notify 的额外设计（命题的"额外"部分）

语义规格（以 redis 后端为锚）：每 key 一条 MPMC 有值队列；Notify=入队+唤醒一个等待者；Watch=出队或阻塞至超时；无等待者时值滞留。

```
树叶子（kind=queue）──► 队列块：{ futex字, head, tail, ring[N], overflow链 }
                        │
        地址可计算部分 ──┘（定位队列 = 一次树下降）
        时间协调部分：
          单机：FUTEX_WAIT_BITSET(SHARED, 超时) / FUTEX_WAKE(1)
          远端：见下表
```

| 远端方案 | 延迟 | CPU | 判词 |
|---------|------|-----|------|
| a. 客户端轮询远端 ring（one-sided READ 循环） | 最低（~2μs） | 烧客户端 CPU + 占 NIC IOPS | 仅超低延迟热点键 |
| b. WRITE-with-IMM → 等待者 CQ 事件 | 低（~3-5μs） | 事件驱动 | 需 waker 知道等待者连接——注册表 |
| c. **region host CPU 中介**：本地 futex 唤醒 + 向远端等待者回发消息 | 中（+1 RTT） | host 少量 CPU | **v0 推荐**：等待者注册到队列块（本地 pid+futex / 远端连接 id），Notify 的唤醒扇出统一由 host 处理；与 HERD/FaSST"协调走 CPU"结论一致 |

自适应：Watch 先 spin ~N μs 再 futex/事件阻塞（协调面延迟通常不在 VM 关键路径——取指/槽访问才在，而它们全在地址可计算面）。

**队列是 region 中唯一"有状态服务"**：有界 ring（满则 Notify 溢出链或阻塞——与 redis 无界 LPUSH 的已知偏离，一致性测试显式覆盖）、等待者注册表、跨进程唤醒。这就是"额外设计"的全部边界，其余 10 方法零协调。

## 五、结论

1. 命题成立并可精确化：**11/13 方法 = 纯地址计算（f(路径)→地址）；Watch/Notify = 地址计算 + 时间协调，额外设计收敛于队列块与等待/唤醒原语**。
2. C++ server 主结构选 **ART**：结构即目录（消灭 redis 双结构 bug 类，fix-013 为实证）、帧句柄缓存吃满 kvlang 前缀稳定性红利；远端 one-sided 点查按需补 hash 缓存（RACE 路线），不作 v0 目标。
3. redis 后端保留为语义锚点与持久化选项；fix-013（Del 误清祖先索引）应先在 redis 后端修复。→ **已修复并双轮验证（fix-013）**。
4. kvspace-rdma 仓库对标表 Pilaf 年份应更正为 **ATC 2013**。
5. 路线衔接 shm.md：M0 kvregion 布局规范应直接按本文 §4.2 的 ART 节点布局起草。
