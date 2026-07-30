---
name: kvspace
description: KVSpace 文件系统风格 KV 存储的使用指南。在操作 kvspace 命令、读写 KVSpace 接口、或设计依赖 kvspace 的功能时使用。
---

# KVSpace

分布式元数据存储，Redis 实现。路径=文件系统，索引=目录。

## 接口（12 方法）

```go
Get(prefix string, keys []string) []XValue          // 批量读，prefix 尾 / 必须
Set(pairs []KVPair) error                           // 写+维护目录索引
List(prefix string, expandExt bool) []string         // 列出直接子项；expandExt 合并 extindex
Del(keys ...string) error                           // 精确删除
DelTree(prefix string) error                        // 递归删除；prefix 是 link 则只删 link
Mkindex(path string) error                          // mkdir -p，path 须以 / 结尾
Link(target, linkpath string) error                 // 软链接，类型必须一致
ExtIndex(path, extpath string) error                // 写时复制叠加层
UnLink(path string) error                           // 移除 link/extindex
Notify(key string, val XValue) error                // LPUSH 一次性通知
Watch(key string, timeout time.Duration) XValue     // BLPOP 阻塞等待
Clear() error                                       // FLUSHDB（波及同 Redis 实例）
DisConn() error
```

## XValue

带 kind 标签的不可变值，TLV 编码存入 Redis。

```
[1B kind_len][N B kind_name][4B arraylength LE][4B raw_len LE][M B raw]
```

**基础类型**：`int8`~`int64`, `uint8`~`uint64`, `float32`, `float64`, `bool`, `string`, `bytes`, `time`(unix ns)

**特殊 kind**：
| kind | raw 含义 | 用途 |
|------|---------|------|
| `index` | `child1\nchild2\n...` | 目录索引 |
| `linkindex` | `target_path` | 软链接，读穿透，删自身 |
| `extindex` | `…extpath\nchild1\n...` | 扩展索引，本地优先，写留本地 |
| `rwir` | sig bytes | 原子读写指令槽；arraylength=(reads<<16)\|writes |
| `rwfunc` | 4B(2B reads+2B writes)+sig | 函数定义；arraylength=指令数 |
| `dict` | 无 | 键族标记 |
| `scope` | — | 作用域 |

**构造/访问方法**：`Int64(v)`, `String(v)`, `Bool(v)`, `Float64(v)`, `Array([]XValue)`, `Raw(kind, raw)`, `Rwir(r,w,sig)`, `Rwfunc(n,r,w,sig)`
**读取**：`v.Int64()`, `v.Str()`, `v.Bool()`, `v.Float64()`, `v.Plain()`(纯值，无 kind), `v.Kind()`, `v.IsNone()`

## 路径模型

| 概念 | 例子 | 存储 |
|------|------|------|
| 值 key（文件） | `/a/b` | Redis key `/a/b` → XValue TLV |
| 索引 key（目录） | `/a/` | Redis key `/a/` → `index` XValue（子名列表） |
| 根 | `/` | 顶级条目名 |
| 目录标记 | 尾 `/` | `PathSep="/"`, `DirIndexSuf="/"` |

文件与目录可同名共存：`/a`（值）和 `/a/`（索引）是两个独立 key。

## Link（软链接）

- `Link(target, linkpath)` — target 和 linkpath 类型一致（同为文件或同为目录）
- 读穿透到 target，删只删自身
- 路径解析：`resolvePath` 逐段读 key value，遇 `linkindex` 重定向，迭代至无 link

## ExtIndex（写时复制叠加层）

- `ExtIndex(path, extpath)` — 只容许单层，不容许级联（extpath 必须是普通 index）
- **List** = `path 自身成员` ∪ `extpath 成员`（本地优先遮蔽）
- **读** = 本地先查，miss 回退 extpath
- **写** = 总是写入本地
- **禁止**写/删 extpath 内的只读 key → panic + 警告开发者
- 设计约束：ExtIndex 只容许单层，不递归找祖先；kvlang 调用方自行确保 scope 路径回退

## kvspace CLI

```bash
kvspace list   [--showext --kind] <prefix>    # 扁平列表，--kind 默认 true
kvspace array2d [--showext --kind] <prefix>   # [s0,s1] 折叠，--kind 默认 false
kvspace tree   [--showext --kind] <prefix>    # 缩进树形，2D 折叠
```

**list**：`key\tkind\tvalue`（--kind=false → `key\tvalue`）
**array2d**：`[s0,s1min~s1max]\tv1\tv2\t...`，常规条目同 list 格式
**tree**：`├── └── │   ` 缩进，目录递归，2D 折叠同 array2d 风格

## 实现约束

- 代码在 `kvspace-go/`（独立 Go 库），kvlang 通过 go.mod replace 引用
- 严禁 hardcode 字符串，常量集中在 `const.go`
- `README.md` 恒为英文，`README_CN.md` 同步翻译
- 设计文档 `deepx-design/doc/kvspace-design-and-implementation/kvspace设计与实现.md` 与代码双向同步
- kvspace-cpp API 与 Go `KVSpace` 接口契约一致（12 方法对齐）
