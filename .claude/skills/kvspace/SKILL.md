---
name: kvspace
description: KVSpace（kvspace-durable，Rust）文件系统风格 KV 存储与 kvspace CLI 探查命令的使用指南。读写 KVSpace 接口、操作 kvspace 命令、设计依赖 kvspace 的功能时使用。
---

# KVSpace

分布式元数据存储，路径=文件系统，索引=目录。后端 redis / fs / s3。

现版为 Rust 可嵌入库 **`kvspace-durable`**（严格翻译旧 kvspace-go），kvlang layout/runtime、byteseek 均链它的 C ABI。

## 接口（Rust trait，11 方法）

```rust
trait KVSpace {
    fn get(&mut self, prefix, keys, resolve) -> Vec<XValue>;       // 批量读，prefix 尾 / 必须
    fn set(&mut self, pairs: &[KVPair]) -> Result<(), String>;    // 写 + 维护目录/成员索引
    fn list(&mut self, prefix, expand_ext, resolve) -> Vec<String>;// 列出直接子项
    fn del(&mut self, keys) -> Result<(), String>;                // 精确删除（POSIX rm 式）
    fn del_tree(&mut self, prefix) -> Result<(), String>;         // 递归删除；link 只删本体
    fn watch(&mut self, key, target_value, tick) -> XValue;       // 阻塞等到 Get(key)==target（轮询回退）
    fn mkindex(&mut self, path) -> Result<(), String>;            // mkdir -p，path 尾 / 必须
    fn ext_index(&mut self, path, ext_path) -> Result<(), String>;// 写时复制叠加层（单层）
    fn del_ext_index(&mut self, path) -> Result<(), String>;
    fn clear(&mut self) -> Result<(), String>;                    // FLUSHDB（波及同 Redis 实例）
    fn dis_conn(&mut self) -> Result<(), String>;
}
```

## XValue

带 kind 标签的值，TLV 编码存储：

```
XValue = Head + body
Head   = [1B kind_len][kind][1B ref][1B ndim][ndim×4B dims][4B raw_len]
ref: 0=内联  1=软链接(*)  2=扩展句柄(@)
```

**枚举**：`None`、`Ptr`、`Bool`、`Int8/16/32/64`、`Uint8/16/32/64`、`Float32/64`、
`CharByte(char/utf8)`、`CharAscii(char/ascii)`、`Char32(char/utf32，默认字符串)`、
`Dict`、`Index`、`ExtIndex`、`Opaque`（未知 kind，如 kvlang 的 rwir/rwfunc/scope 原样存取）。

## 路径模型

| 概念 | 例子 | 存储 |
|------|------|------|
| 值 key（文件） | `/a/b` | XValue TLV |
| 索引 key（目录） | `/a/` | `index` XValue（子名列表） |
| dict 成员 | `/a/d.name` | 扁平键，`.` 是成员分隔符（`DICT_SEP`） |
| 目录标记 | 尾 `/` | `DIR_INDEX_SUF="/"` |

文件与目录同名共存：`/a`（值）与 `/a/`（索引）是两个独立 key。
`set` 内部自动 `mk_index_recursive` 建父目录索引，无需手动 mkindex。

## Dict / Ptr / ExtIndex

- **Dict**：`/x/d` 存 `Dict(成员名列表)`，成员为扁平键 `/x/d.name`。
- **Ptr 软链接**：值 `*kind:target`；读穿透到 target，del/del_tree 只删链接本体。
- **ExtIndex 写时复制**：`ext_index(path, ext_path)`；list=本地∪扩展（本地优先遮蔽），读本地先查 miss 回退，写总落本地；禁止写/删 extpath 只读 key；单层不级联。

## kvspace CLI（探查命令）

编译：`cargo build --release --bin kvspace` → `target/release/kvspace`。
DSN：`--kvspace dsn` 或环境变量 `KVSPACE`（默认 `redis://127.0.0.1:6379`）。

```bash
kvspace get <key>...                       # key\tkind:value（自动 TLV 解码）
kvspace set <key> <value>                  # value: int:1 / float:1.5 / bool:true / string:x / nil / *kind:target
kvspace del <key>...
kvspace deltree <prefix>
kvspace mkindex <path/>
kvspace extindex <path/> <extpath/>
kvspace delextindex <path/>
kvspace list [--showext --kind] <prefix>   # 扁平：key\tkind\tvalue（--kind=false → key\tvalue）
kvspace tree [--showext --kind] <prefix>   # 缩进树（--kind 默认 false）
kvspace clear
```

探查 kvspace 一律用 `kvspace` 命令，**禁止** `redis-cli`（原始 TLV 二进制不可读，且绕过层次）。

## 约束

- 常量集中在 `const.rs`，禁止硬编码 `/` `.` 等裸字符串。
- 设计文档 `deepx-design/doc/kvspace-design-and-implementation/` 与代码双向同步。
- C ABI 在 `ffi.rs`（camelCase，符合 deepx-design/doc/abi-naming-standard.md）。
