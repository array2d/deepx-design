# kvspace

KV 树形存储的 Go 客户端 SDK。对应仓库 `github.com/array2d/kvspace-go`。

## KVSpace 接口

```go
type KVSpace interface {
    Get(keys []string) []XValue         // 批量读，缺失返回 null XValue
    Set(pairs []KVPair) error           // 批量写，维护目录索引；key 禁止尾斜杠
    List(prefix string) []string         // 列出直接子项
    Del(keys ...string) error           // 精确删除
    DelTree(prefix string) error        // 递归删除；prefix 本身是链接则只删链接

    Notify(key string, val XValue) error
    Watch(key string, timeout time.Duration) XValue  // 超时返回 null XValue

    Mount(target, linkpath string) error // 路径映射 linkpath → target
    Overlay(target, r, w string) error   // overlay：读 w/ → r/ 回退
    UnMount(linkpath string) error       // 删除映射

    Clear() error   // redis: FLUSHDB
    DisConn() error
}
```

**Get/Set** 统一批处理，单 key 操作传 `[]string{"k"}`。**Watch** 超时返回 kind=null 的 XValue。

## XValue 类型系统

```
TLV wire format: [1B kind_len][N B kind_name][4B raw_len LE][M B raw_value]
```

| kind | Go 构造 | Go 读取 | raw 格式 |
|------|---------|---------|----------|
| `int8`–`int64` | `Int8(v)`–`Int64(v)` | `v.Int64()` 宽容读 | LE |
| `uint8`–`uint64` | `Uint8(v)`–`Uint64(v)` | `v.Uint64()` 宽容读 | LE |
| `float32`/`float64` | `Float32(v)`/`Float64(v)` | `v.Float64()` | LE |
| `bool` | `Bool(v)` | `v.Bool()` | 1B |
| `string` | `Str(s)` / `String(s)` | `v.Str()` | UTF-8 |
| `bytes` | `Bytes(b)` | `v.Bytes()` | 原始 |
| `array1d` | `Array(elems)` | `v.Len()`, `v.Index(i)` | [4B count][elem TLV]… |
| `dict` | `Dict()` | — | 空，键族标记 |
| `time` | `Time(ns)` | `v.TimeNs()` | 8B LE UnixNano |
| `mount` | — | — | target 路径字节 |
| `overlay` | — | — | `w_path\nr_path` 字节 |
| 任意 | `Raw(kind, raw)` | `v.RawBytes()` | 原始 |

`Int64()`/`Uint64()`/`Float64()` 宽容读取：kind 不对齐返回 0，不 panic。

`mount` 和 `overlay` 是系统 kind，由 Mount/Overlay 内部 TLV 存储，不暴露用户构造。

## 路径模型

所有 key 以 `/` 开头。**尾斜杠 `/` 表示目录**，存储该目录的子项集合（Redis SET）。

```
/a          value（不以 / 结尾）
/a/         目录索引（/a 的子项集合：{b, c}）
/a/b        value
```

**约束**：`Set` 拒绝尾斜杠 key，value 键和目录键永不相交。

根目录 `/` 的索引键就是 `/` 本身。`dirKey(parent)` 处理此特例：

```go
func dirKey(parent string) string {
    if parent == kvspace.PathSep { return kvspace.PathSep }
    return parent + kvspace.DirIndexSuf
}
```

**JoinPath** 避免根路径拼接产生 `//`：

```go
func JoinPath(parent, child string) string {
    if parent == PathSep { return PathSep + child }
    return parent + PathSep + child
}
```

## 常量

```go
const (
    PathSep     = "/"  // 路径分隔符
    DirIndexSuf = "/"  // 目录索引后缀
)

// XValue kind
const (
    KindNull    = "null"
    KindInt64   = "int64"    // + int8/16/32, uint8/16/32/64, float32/64
    KindString  = "string"
    KindBytes   = "bytes"
    KindArray1d = "array1d"
    KindDict    = "dict"
    KindMount   = "mount"    // raw = target 路径
    KindOverlay = "overlay"  // raw = "w_path\nr_path"
)
```

## Mount 系统

Mount 和 Overlay 以 XValue TLV 存储在对应 key 上。`checkLinkEntry` 惰性 `DecodeXValue` 解析并全量缓存。

### Mount

`Mount("/real", "/alias")` 写入 `TLV{kind=mount, raw="/real"}` → `/alias`。`ResolveCore` 路径解析时透明替换 `/alias/x` → `/real/x`，40 跳防环。

### Overlay

`Overlay("/merged", "/r", "/w")` 写入 `TLV{kind=overlay, raw="/w\n/r"}` → `/merged`。

**读路径**：`resolveOL(path)` 沿路径从深到浅逐级查 `checkLinkEntry`，找最深层 overlay 祖先 → 返回 `(wPrefix, rPrefix)` → `Get` 先 GET w 路径，miss 则 fallback GET r 路径。

**写路径**：`Set` 发现 overlay → 将 resolved key 替换为 w 路径。r 层只读。

**List**：`List` 发现 overlay → 合并 `SMEMBERS dirKey(wPrefix)` + `SMEMBERS dirKey(rPrefix)`，w 的条目去重优先。

**UnMount**：删除 w 层全部数据及索引，再删除 overlay 标记本身。r 层不受影响。

### linkEntry 缓存

```go
type linkEntry struct {
    checked   bool
    target    string    // mount: 目标路径
    isOverlay bool
    w, r      string    // overlay: writable / readonly 层
}
```

`checkLinkEntry(path)`：首次查 Redis → `DecodeXValue` → 按 kind 解析。之后走内存缓存。

## Redis 实现

### 连接注册

```go
kv := kvspace.Conn("redis://host:port")  // 默认 poolSize=16
```

DSN scheme 注册：`init()` 中 `kvspace.Register("redis", ConnPool)`。

### 索引维护

`Set` 对路径每级父目录 `SADD dirKey(parent) child` 维护索引。`Del` 的 `delIndex` 级联清理空目录：目录无子项且自身无 value → 从祖父索引 SREM，沿祖先链向上重复。

## Walk

```go
func Walk(kv KVSpace, prefix string, fn func(path string, v XValue))
```

深度优先递归遍历。节点无值时 fn 不被调用。

## CLI 工具

```bash
kvspace get /a /b /c           # 批量读取
kvspace set /k string:hello    # 单 key 写入
kvspace del /a /b              # 精确删除
kvspace deltree /prefix        # 递归删除
kvspace list /                 # 列出子项
kvspace tree /                 # 可视化树（含 [s0,s1] 二维表格打印）
kvspace dump /                 # 递归遍历
kvspace watch --timeout 5s /k  # 阻塞等待通知
kvspace notify /k string:msg   # 推送通知
kvspace mount /real /alias     # 创建路径映射
kvspace unmount /alias         # 删除映射
kvspace clear                  # FLUSHDB
```

`--kvspace dsn` 指定后端地址，默认 `redis://127.0.0.1:6379`。环境变量 `KVLANG_KVSPACE` 覆盖。
