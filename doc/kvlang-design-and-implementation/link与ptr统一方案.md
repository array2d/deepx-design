# Link 与 Ptr 统一方案（v2）

## 设计决策

1. **isptr 占 1 byte**，不挤 bit7。TLV header 新增独立字段。
2. **删除 Link/UnLink API**，删除 LinkIndex kind。不再有 backend 透明穿透。
3. **全部显式解引用**。runtime 遇 Ptr 时自行 Get 目标 key。

## TLV 格式（v2）

```
[1B kind_len][N B kind][1B isptr][4B al LE][4B raw_len LE][M B raw]

isptr=0  raw = 实际值 body
isptr=1  raw = 目标 key 路径（字符串）
```

## 消除项

| 消除 | 替代 |
|------|------|
| `KindLinkIndex` | isptr=1, kind="" |
| `LinkIndex` 类型 | `Ptr` 类型 |
| `kv.Link(target, linkpath)` | `kv.Set(linkpath, NewPtr("", target, 1))` |
| `kv.UnLink` (LinkIndex 路径) | `kv.Del(path)` |
| backend `linkTargetLocked` | 删除 |
| backend `resolvePathLocked` 中 link 跟随 | 删除（不再自动穿透） |
| `DecodeLinkIndex` | `DecodeXValueHead` isptr 分支返 Ptr |

## Ptr 类型（不变）

```go
type Ptr struct {
    kind   string // 目标类型，"" 表示未知
    target string // 目标 key 路径
    al     int32  // 目标 ArrayLen
}
```

## 受影响的 kvspace-go 文件

| 文件 | 改动 |
|------|------|
| `const.go` | 删 `KindLinkIndex` |
| `xvalue.go` | `TLVEncode`/`DecodeXValueHead` 加 isptr byte；Decode 删 LinkIndex case |
| `xvalue_index.go` | 删 `LinkIndex`/`NewLinkIndex`/`DecodeLinkIndex` |
| `xvalue_format.go` | 删 `LinkIndex` case |
| `kvspace.go` | `Link`/`UnLink` 从 KVSpace interface 移除 |
| `kvspace_common.go` | 移除 `Link`/`UnLink` |
| `goheap/backend.go` | 删 `Link`/`UnLink`/`linkTargetLocked`；resolve 删 link 跟随 |
| `redis/` | 同上 |

## 受影响的 kvlang 文件

| 文件 | 改动 |
|------|------|
| `layout/layout.go` | `UnLink`(extindex) → 保留，`UnLink` 只处理 ExtIndex |
| `rwir/builtin/helper.go` | `isContainerKind` 删 `"linkindex"` |

## 受影响的 kvspace-c

| 文件 | 改动 |
|------|------|
| `kvspace.c` | TLV 解析加 isptr byte；`resolve_path` 删 linkindex 跟随 |
| `xvalue.h` | struct 加 `is_ptr` 字段 |

## kvspace-c TLV C struct（v2）

```c
typedef struct {
    uint8_t  kind_len;
    char     kind[KIND_MAX];
    uint8_t  is_ptr;       // ← 新增
    int32_t  array_len;
    int32_t  raw_len;
    uint8_t  raw[];
} tlv_frame_t;
```
