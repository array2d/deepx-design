# kvspace shm:// 后端与 kvregion 统一底座设计

> 草稿 · 2026-07-18 · 遵守根 DESIGN.md（kvspace 抽象存储、R3 路径规范）
> 前置：DSN 机制（issue/fix-012，`--kvspace scheme://addr`）、kvspace-rdma-distributed.md（多组件共享数据平面）、rdma-server.md（同目录，kvspace-rdma 根设计权威版）
> 状态：**纯设计，零实现**。当前唯一后端为 `redis://`。

---

## 零、一句话定位

**shm:// 是单机零拷贝的 kvspace 后端；它与 rdma:// 不是两个平行实现，而是同一块内存布局（kvregion）的两条访问路径**——本地进程 mmap 直访，远端节点 one-sided RDMA 直访。

```
--kvspace redis://127.0.0.1:6379    参考后端（语义锚点，网络 RTT ~100μs）
--kvspace shm://kvlang              本机 mmap（规划，~100ns 级）
--kvspace rdma://10.0.0.1:9999      远端 verbs（规划，~2-5μs 级）
```

## 一、动机：VM 指令周期的数据面瓶颈

kvlang 执行模型下，每条指令至少一次 `Get`（取指）+ N 次读写槽访问。redis 后端每次访问一个 TCP RTT：

| 数据面 | 单次访问延迟 | 指令周期量级 |
|--------|------------|-------------|
| redis://（现状） | ~50-100 μs | 每条指令 3-6 次访问 → ~0.5 ms/条 |
| shm://（目标） | ~0.1-1 μs（mmap load/store + 锁） | ~1-10 μs/条 |
| rdma://（目标） | GET ~2-5 μs（one-sided READ） | 远端组件访问路径 |

单机部署（VM + heap-plat + op-plat + CLI 同机）是最常见形态，数据面却在走 loopback TCP——shm 消除这层税。分布式场景中，热点数据所在节点的本地组件同样应走 shm 而非绕行网络。

## 二、设计目标

| 目标 | 含义 |
|------|------|
| **语义等价** | 13 方法语义以 `kvlang-go/kvspace.go` 接口注释 + redis 后端行为为锚，tutorial 全量作为跨后端一致性测试 |
| **布局即契约** | kvregion 是一份**语言中立的二进制布局规范**（C ABI），不是 Go 实现细节 |
| **一份内存两条路径** | 本地 mmap 与远端 RDMA 访问同一 region；layout 设计前置考虑 one-sided 友好性 |
| **多进程多语言** | Go VM、C++ 执行器（kvspace-cpp）、Python op-plat、CLI 并发 attach 同一 region |
| **崩溃恢复对齐** | 进程崩溃后 region 存活（/dev/shm 生命周期），PC/帧可恢复；机器重启丢失（持久化非目标，见 §九） |

## 三、关键洞察：kvregion = shm 与 rdma 的公共底座

RDMA 网卡读写的是一块注册过的内存区（MR）；本地进程 mmap 的也是一块内存区。**让它们是同一块**：

```
                    ┌─────────────────────────────────┐
                    │        kvregion（布局规范）        │
                    │  header │ 桶区 │ 值日志 │ 队列区   │
                    └───┬──────────────────────┬──────┘
              mmap 直访 │                      │ ibverbs MR 注册
        ┌───────────────┴───────┐   ┌──────────┴────────────┐
        │ 本机进程（shm://）      │   │ kvspace-rdma server    │
        │ Go VM / C++ 执行器 /   │   │ 远端节点 rdma:// 访问：  │
        │ Python op-plat / CLI  │   │ GET = one-sided READ   │
        └───────────────────────┘   │ SET = HERD 风格 RPC    │
                                    └───────────────────────┘
```

分层与仓库分工：

| 层 | 内容 | 归属 |
|----|------|------|
| KVSpace 接口 | 13 方法契约（fix-019：含 ClearAll） | `github.com/array2d/kvlang-go`（权威） |
| **kvregion 规范** | 二进制布局 + 并发协议（本文档 §四） | deepx-design（规范文档）；实现首发于 kvlang `internal/kvspace/shm` |
| shm:// 后端 | mmap attach + 本地并发访问 | kvlang（Go）、kvspace-cpp（C++ 后续对齐） |
| rdma:// 传输 | 同一 region 的 verbs 服务化 | **kvspace-rdma 仓库**（其根设计已定：RC QP、Pilaf/HERD 混合） |

推论：kvspace-rdma server 的正确形态是 **kvregion host**——它 mmap 一个 shm region、注册为 MR、对外提供 verbs 访问；本机组件直接 `shm://` attach 同一 region，天然绕过 loopback RDMA。rdma 仓库不应发明第二套存储引擎。

## 四、kvregion 内存布局 v0

固定大小 region（创建时指定，如 1 GiB），六段：

```
┌ header ─────┬ 桶区 ────────┬ 目录索引区 ──┬ 值日志区 ────┬ 队列区 ─────┬ 分配器元数据 ┐
│ magic/ver/  │ cuckoo 桶     │ 每父路径的   │ entry:      │ watch 队列： │ size-class  │
│ epoch/size/ │ 2-way,       │ 子项名集合   │ [ver|klen|  │ futex 字 +  │ 空闲链       │
│ 段偏移表     │ 每桶8槽       │ (List 用)   │ vlen|crc|   │ MPSC ring   │             │
│             │              │             │ key|TLV 值] │ (Notify 用)  │             │
└─────────────┴──────────────┴─────────────┴─────────────┴─────────────┴─────────────┘
```

关键决策（每条都为 one-sided RDMA 预留）：

| 决策 | 理由 | 行业出处 |
|------|------|---------|
| cuckoo hash，桶槽定长 | 远端 one-sided READ 可按 (hash → 桶偏移) 一次读中，无需追链 | Pilaf (ATC'12) |
| entry 头含 seqlock version + CRC | 本地读者与远端 READ 都能无锁校验一致性：version 偶数且前后一致且 CRC 通过才采信 | FaRM (NSDI'14) / Pilaf |
| 值为 TLV 原样字节 | 与现有 XValue 编码 `[kind_len|kind|raw_len|raw]` 完全一致，跨语言零转换 | 现有 kvspace 编码 |
| 目录索引显式维护 | `List` 是直接子项语义（非前缀扫描），与 redis 后端 per-layer 索引对齐 | 现有 redis 实现 |
| 写路径单机全走本地锁/CAS，远端写走 server RPC | 避免远端 one-sided WRITE 的跨节点锁协议（DrTM 级复杂度），v0 不做 | HERD (SIGCOMM'14) |
| futex(FUTEX_SHARED) 做 Watch 阻塞 | 跨进程等待/唤醒，无轮询；Go 侧接受阻塞 OS 线程（Watch 本就阻塞语义） | Linux 惯例 |

**并发协议 v0**：写者对桶槽 CAS 占位 → 值日志 append → seqlock 发布；读者 version-check 重试。同 key 并发写以 kvlang 使用模式为界（单 vthread 顺序执行、跨组件经 Notify 队列协调），不提供多 key 事务——与 redis 后端语义持平。

**成员键族与本布局**：fix-009 后成员是平坦键（`/n0.val`），对 hash 布局天然友好（每成员独立 entry，无嵌套结构）；`DelTree` 的 `/` 子树删除与键族 `.` 前缀删除都落在目录索引/桶扫描上，语义与 redis 后端一致。

## 五、13 方法语义映射

| 方法 | shm:// 实现 | 备注 |
|------|------------|------|
| Get / GetMany | hash 定位 → seqlock 读 TLV | 远端即 one-sided READ 同一路径 |
| Set / SetMany | CAS 桶槽 + 日志 append + 发布 | 远端走 server RPC |
| Del | 桶槽墓碑 + 目录索引移除 | |
| List | 读目录索引子项集合 | 直接子项语义 |
| DelTree | 目录索引递归 + 墓碑 | 原子性同 redis 后端（非事务） |
| Notify | 队列区 ring push + futex wake | 队列按需创建，有界；满则写者阻塞（与 redis 无界 LPUSH 的**已知偏离**，文档化） |
| Watch | futex timed-wait + ring pop | timeout 语义一致 |
| Link / Unlink | entry kind=link，客户端侧解析穿透 | 与 redis 实现同策略 |
| ClearAll | region 全键清 + 队列复位 | redis 后端 = FLUSHDB（整库语义，fix-019） |
| DisConn | munmap | region 本身不销毁 |

## 六、场景

### 6.1 单机（纯 shm）

```
kvlang serve --kvspace shm://kvlang
kvlang -c '...' --kvspace shm://kvlang        # CLI attach 同一 region
deepx-op-cpu --kvspace shm://kvlang           # C++ 执行器 attach（需 kvspace-cpp 对齐 kvregion）
```

全组件同机零网络。崩溃恢复：进程挂掉后 region 仍在 `/dev/shm`，重启进程按 `.pc` 继续——kvlang 崩溃恢复叙事在单机 shm 下依然成立（机器重启除外）。

### 6.2 分布式（shm + rdma 同一 region）

```
节点 A（region host）:  kvspace-rdma serve --region shm://kvlang --listen rdma://0.0.0.0:9999
节点 A 本地组件:        --kvspace shm://kvlang          ← 同一块内存，零网络
节点 B/C 远端组件:      --kvspace rdma://nodeA:9999     ← one-sided GET / RPC SET
```

v0 分布式 = 单 region host（hub 型）。多 region 分片（按路径前缀，如 `/vthread/{vid}` 归属节点）与副本/Raft 见 kvspace-rdma-distributed.md，属后续阶段，本文档不展开。

## 七、DSN 与生命周期

| 形式 | 解析 |
|------|------|
| `shm://kvlang` | POSIX shm 名 → `/dev/shm/kvlang` |
| `shm:///mnt/huge/kvlang` | 绝对路径（hugetlbfs 等） |
| `shm://kvlang?size=1g` | 首次 attach 时不存在则按 size 创建；已存在校验 magic/version |

销毁 = `kvlang kvspace destroy`（规划子命令）或 `shm_unlink`。`kvspace clear` 语义不变（清数据不销毁 region）。

## 八、边界与非目标

- **无持久化**：机器重启即丢。需要落盘用 `redis://`（RDB/AOF）或后续快照工具（region → 文件，本身是 O(size) memcpy）
- **无多 key 事务**：与 redis 后端持平
- **region 容量固定**：满则写失败并报错，不自动扩容（v0）；值日志碎片回收（compaction）与未来 `X/.gc` 引用计数（deep-dive §12）同期设计
- **Notify 有界队列**：与 redis 无界行为的偏离点，需在一致性测试中显式覆盖

## 九、路线图

| 里程碑 | 内容 | 验收 |
|--------|------|------|
| M0 | kvregion 布局规范定稿（本文档 §四细化为字节级规范，独立成文） | 设计评审 |
| M1 | Go `internal/kvspace/shm` 后端，`Register("shm", …)` | `--kvspace shm://` 跑通 tutorial 全量（与 redis 结果逐字节一致） |
| M2 | kvspace-cpp 对齐 kvregion（C++ attach 读写） | 跨语言并发一致性测试 |
| M3 | kvspace-rdma server = kvregion host + verbs | 远端 GET P99 < 5μs（rdma 仓库指标） |
| M4 | 多 region 分片 | 另立设计 |
