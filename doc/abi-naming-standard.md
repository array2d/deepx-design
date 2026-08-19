# ABI 命名规范（跨组件契约）

> 状态：v2 已落地（2026-08-19），两后端回归通过（见 §九）。范围：所有经 C ABI 互相链接调用的导出符号 + 各组件内部函数（全部统一前缀）。rwirext 扩展宿主 `kvlang_rwirext*` 仍为下划线形态，待 §八 单独统一。
> 关联：kvspace 侧与 [[kvspace设计与实现]] 双向同步（p9）；kvlang 侧见 [[runtime篇-06-C运行时与后端抽象]]。

kvlang 生态各组件（layout/runtime/扩展、kvspace-durable/kvspace-c）主要通过 **C ABI** 互相链接调用。本规范定义唯一的符号命名方式（p2 单一正确路径、p5 不向后兼容）。

## 一、两个顶层前缀

导出符号只允许两个顶层前缀，前缀恒小写，前缀即职责归属（**不是** 产出该符号的二进制，而是**语义域**）：

| 前缀 | 职责域 | 实现方 |
|------|------|--------|
| `kvspace` | 地址空间 + KV 存取 + 索引 + watch + **值构造/TLV 编解码（原 xvalue 并入）** | kvspace-durable(Rust)、kvspace-c(C) |
| `kvlang` | VM：编译（layout）+ 执行（runtime）+ 扩展宿主（rwirext） | kvlang/layout、kvlang/runtime |

## 二、符号形态（唯一）

**函数**：`<prefix><CamelApi>` —— 前缀小写，**前缀与 API 之间无下划线**，整体就是一个 camelCase 标识符，首字母（前缀）小写。子模块名作为 API 的驼峰首词。

```
kvspaceGetBatch          kvlangLayoutFile
kvspaceDelTree           kvlangLayoutSrc
kvspaceMkindexExt        kvlangLayoutVet
kvspaceXvalueNewChar     kvlangRuntimeExecuteVthread
kvspaceNewCharByte       kvlangBuiltinAdd
kvspaceDecodeHead        kvlangKeytreeFramePc
```

layout 的 C ABI 三入口（`kvlang/layout/src/capi.rs`，cdylib 导出）：`kvlangLayoutFile`
从文件、`kvlangLayoutSrc` 从内存源码串（LLM 生成即插入，不落盘）、`kvlangLayoutVet`
只校验不写 kvspace（自造代码闸门）。`vet` 归 `Layout` 子模块前缀（`kvlangLayout<What>`），
不另立 `kvlangVet*` 前缀。三者在 C 边界 `catch_unwind`，非法输入返回 -1 不打崩宿主。

**所有 api 都加前缀，无一例外**（p2）：不再有裸 `bi_`/`kt_`/`xv_`/`kvsc_`/`xvalue_` 等自造前缀。所有函数（含内部实现）统一归入 `kvspace`/`kvlang` 二前缀，**全部保留导出**（不做 visibility 隐藏）。

**不透明类型/结构体**：`<prefix><CamelNoun>_t`（POSIX `_t` 后缀惯例保留，`_t` 前的下划线是类型标记，非「前缀-api 分隔」）。
`kvspaceHead_t`、`kvlangRuntime_t`、`kvlangRwirInst_t`。纯句柄类型无 api 词时保留裸 `_t`：`kvspace_t`（类比 `FILE`/`size_t`）。

**kind 常量宏**：`KVSPACE_KIND_*`（C 宏全大写惯例）。runtime 原 `K_*` 与 kvspace-c 原 `XK_*` 统一到此一套。

## 三、无缩写（#4）

私有/自造缩写一律展开全拼；仅保留三类**主流约定俗成术语**：`Pc`（程序计数器）、`Tlv`（Type-Length-Value 编码格式名）、`Kv`（key-value，且是前缀本身）。

| 缩写 | 全拼（camel 首词） | 出处 |
|------|------|------|
| `XK_*` / `K_*` | `KVSPACE_KIND_*` | 值 kind 常量 |
| `bi_` | `kvlangBuiltin` | builtin.c 内建算子 |
| `kt_` | `kvlangKeytree` | keytree.c KV 路径构造 |
| `vt_` | `kvlangVthread` | vthread.c 虚拟线程/帧 |
| `xv_` | `kvlangXvalue` | runtime xvalue.c 值处理（kvlang 域，避免与 kvspace-c 撞名） |
| `fmt_` | `kvlangFormat` | 数值格式化 |
| `sb_` | `kvlangStrbuf` | strbuf.c 字符串缓冲（模块名保留） |
| `kv_` | `kvlangKv` | kv.c kvspace 客户端封装 |
| `kvcpu_` | `kvlangKvcpu` | kvcpu.c 执行核（cpu 为标准词） |
| `log_` | `kvlangLog` | logx.c 日志 |
| `rwir_` | `kvlangRwir` | rwir.c 指令解码（rwir 为既定概念名，保留） |
| `kvsc_` | `kvspaceShm` | kvspace-c 原生 shm 实现 |
| `xvalue_` | `kvspaceXvalue` | kvspace-c 值编码 |

## 四、ABI 调用约定（跨组件统一）

- **返回 `int`**：`0`=成功，`<0`=失败（`-1` 通用错误）。
- **错误信息**：尾参 `(char *err, uint32_t err_cap)`，callee 写入定长缓冲、不 malloc。
- **输出字节**：callee 分配，调用方用**同模块**的 `kvspaceFree`/`kvspaceBytesFree` 释放；禁止跨 allocator。
- **句柄**：`connect` 返回，`disconnect`/`free` 释放。

## 五、handoff / KV 路径 与 概念名

- 路径布局固定：签名 `/lib/<opcode>`（kind=rwir），handoff `/lib/<opcode>/.todo<vid>`·`.done<vid>`，拓扑 `/ext/`。
- **`rwir` / `rwirext` 是既定概念名，保留不动**（含目录 `runtime-rwirext_example/`）。

## 六、动词表（一词一义，对齐五大语言 p7）

| 操作 | 唯一动词 | 废弃别名 |
|------|---------|---------|
| 连接 / 断开 | `connect` / `disconnect` | conn·disconn·open·close |
| 释放句柄 / 释放字节 | `free` / `bytesFree` | — |
| 读单 / 批量 | `get` / `getBatch` | getOne |
| 删 key / 删子树 | `del` / `delTree` | deltree |
| 建索引 / 建外部索引 / 删外部索引 | `mkindex` / `mkindexExt` / `rmindexExt` | ext_index·del_ext_index |

## 七、完整符号映射（公有 ABI，current v1 → target v2）

v1（上一版，带下划线）→ v2（无下划线 camelCase）。

| v1 | v2 |
|----|----|
| `kvspace_connect` | `kvspaceConnect` |
| `kvspace_disconnect` | `kvspaceDisconnect` |
| `kvspace_free` | `kvspaceFree` |
| `kvspace_bytesFree` | `kvspaceBytesFree` |
| `kvspace_set` | `kvspaceSet` |
| `kvspace_get` | `kvspaceGet` |
| `kvspace_getBatch` | `kvspaceGetBatch` |
| `kvspace_list` | `kvspaceList` |
| `kvspace_del` | `kvspaceDel` |
| `kvspace_delTree` | `kvspaceDelTree` |
| `kvspace_clear` | `kvspaceClear` |
| `kvspace_watch` | `kvspaceWatch` |
| `kvspace_mkindex` | `kvspaceMkindex` |
| `kvspace_mkindexExt` | `kvspaceMkindexExt` |
| `kvspace_rmindexExt` | `kvspaceRmindexExt` |
| `kvspace_tlvEncode` | `kvspaceTlvEncode` |
| `kvspace_tlvEncodePtr` | `kvspaceTlvEncodePtr` |
| `kvspace_decodeHead` | `kvspaceDecodeHead` |
| `kvspace_newPtr` | `kvspaceNewPtr` |
| `kvspace_newChar` | `kvspaceNewChar` |
| `kvspace_newCharByte` | `kvspaceNewCharByte` |
| `kvspace_newBool` | `kvspaceNewBool` |
| `kvspace_newInt64` | `kvspaceNewInt64` |
| `kvspace_newFloat64` | `kvspaceNewFloat64` |
| `kvspace_head_t` | `kvspaceHead_t` |
| `kvlang_runtimeConnect` | `kvlangRuntimeConnect` |
| `kvlang_runtimeDisconnect` | `kvlangRuntimeDisconnect` |
| `kvlang_runtimeKv` | `kvlangRuntimeKv` |
| `kvlang_runtimeExecutePc` | `kvlangRuntimeExecutePc` |
| `kvlang_runtimeBootstrap` | `kvlangRuntimeBootstrap` |
| `kvlang_runtimeExecuteVthread` | `kvlangRuntimeExecuteVthread` |
| `kvlang_runtimeExecute` | `kvlangRuntimeExecute` |
| `kvlang_runtime_t` | `kvlangRuntime_t` |
| `kvlang_layoutFile` | `kvlangLayoutFile` |

内部符号：按 §三 表批量转 `<prefix><CamelApi>`（模块名作首词，snake 尾转驼峰）。

## 八、延迟项（等 #5 指令）

- **rwirext 扩展宿主 `kvlang_rwirext.h`** 的 `kvlang_rwirext*` 符号**命名形态（下划线）本轮不动**，等用户单独指示如何调整。
- 注：命名虽未动，但已按「扩展宿主自连 kvspace」的架构**裁剪 rwirext 表面**——KV 存取转发函数（`Connect/Disconnect/List/Get/Set/Del/GetTlv/SetTlv/Mkindex`）及 `kvlang_rwirext_t` 不透明句柄已删除；带句柄的 rwirext 函数改收扩展自连的 `void *kvspace`。现存 9 符号见 [[runtime篇-06-C运行时与后端抽象]] §二。term/numpy/json 三个扩展均自 `kvspaceConnect` 拿句柄，值编解码走 `kvspaceDecodeHead`/`kvspaceTlvEncode`/`kvspaceNewChar`。

## 九、验证

全部重命名 + 重建后，以 `kvlang/tutorial/test.py --runtime c` 回归（Rust term + C runtime），两后端均从重命名后源码重建：

| 后端 | producer | 基线 | v2 实测 |
|------|----------|------|---------|
| redis | kvspace-durable(Rust) | PASS:139 / FAIL:0 / SKIP:10 | ✅ 139/0/10 |
| shm | kvspace-c(C) | PASS:139 / FAIL:0 / SKIP:10 | ✅ 139/0/10 |

**落地要点**：token 级重命名（`<prefix>_snake` → `<prefix>Camel`，`_t` 保留）。两处非纯 token 需手工修：① kvspace-c `xvalue.c` 的 `DEF_NEW_ARRAY(name,...)` 宏 token-paste `kvspaceXvalueNew##name`，name 实参须由 `bool`/`int64` 改首字母大写 `Bool`/`Int64` 以对齐声明；② `#include "kvlang_runtime.h"` 中的 `kvlang_runtime` 是文件名非符号，重命名后需还原（文件名保持下划线）。Rust crate 加 `#![allow(non_snake_case)]`（layout/durable）与 `non_camel_case_types`（term 的 `kvlangRuntime_t` 结构）。私有实现结构体（kvspace-c 的 `art_*`/`watch_t`/`xvalue_head_t`/`kvspace_hdr_t`、裸句柄 `kvspace_t`）与 Rust 模块/crate 名（`kvspace_common`/`kvspace_durable`）不跨 ABI，保持原样。
