# runtime篇-06: C 运行时与后端抽象

> 状态：与实现同步（2026-08-19）。代码：`kvlang/runtime/`（纯 C）、`kvlang/runtime-rwirext_example/rust/term/`（Rust term）、`kvlang/runtime-rwirext_example/go/json/`（Go json）、`kvlang/runtime-rwirext_example/py/numpy/`（Python numpy，零拷贝）、`array2d/kvspace-c/`（kvspace-c SHM）、`array2d/kvspace-durable/`（fs/redis）。旧 Go runtime 已迁 `kvlang/oldhero/`。符号命名遵循 [[abi-naming-standard]]（无下划线 camelCase，前缀 `kvspace`/`kvlang`）。

## 一、小 runtime 大扩展

kvlang 是**小 runtime 大扩展**架构。中央 runtime 只实现最核心的 execute 大循环（fetch-decode-execute、call/return/br/goto、native rwir 派发、`=` 拷贝、用户函数调用），其余能力（tensor 计算、LLM、agent、print/println/cerr）全部交给**扩展 runtime**（见 [[runtime篇-05-rwirext扩展运行时]]）。

扩展 runtime 从中央 runtime 拿到 PC，**连续执行己方 rwir 到非己方指令**，再把最终 PC 交还。op-gpu 扩展收到 handoff 后连续跑完整段 tensor 计算，不必每算一个 op 就回报中央——否则对训练/推理这种分秒必争的场景，逐条 handoff 的延迟不可接受。

## 二、C 运行时（纯 C + extern "C" ABI）

`kvlang/runtime/` 是 Go runtime 的 C 顶替版，作为嵌入式通用执行器，嵌入到 rwirext 项目（op-gpu、agent、livebyte）。

- **对外两组 C ABI**：`kvlang_runtime.h`（`kvlangRuntimeConnect/Disconnect/Execute/...`，核心执行器）+ `kvlang_rwirext.h`（扩展 runtime 语义，**只暴露 kvspace 不提供的部分**）。KV 存取（connect/get/set/del/list/mkindex/tlv）**不在此**——扩展宿主自己连 kvspace ABI（`kvspaceConnect/Get/Set/...`），把 kvspace 句柄（`void *`）传进带句柄的 rwirext 函数。rwirext 现有 9 个符号：`Register`（写 `/lib/<op>` 签名）、`PrintLine`（print/println/cerr 的 resolve+display，非己方指令返 NULL）、`NextPc`（纯函数，下一条 PC）、`Params`（opcode + 读/写参名，\n 分隔）、`ResolveRead`/`ResolveReadPath`/`ResolveWrite`（读/写参解析为值或 KV 路径，供零拷贝读整块 ndarray）、`TypeValid`/`TypeMatch`（签名类型表达式校验与值判定）。（`kvlang_rwirext*` 为扩展宿主符号，暂沿用下划线形态，另行统一，见 [[abi-naming-standard]] §八。）
- **模块**（内部实现符号也统一 `kvlang`/`kvspace` 前缀、全导出）：`xvalue.c`（`kvlangXvalue*`：XValue TLV 编解码 + value_string）、`kv.c`（`kvlangKv*`：kvspace ABI 封装）、`keytree.c`（`kvlangKeytree*`：路径构造）、`rwir.c`（`kvlangRwir*`：PC 解析 + Decode）、`vthread.c`（`kvlangVthread*`）、`builtin.c`（`kvlangBuiltin*`：~50 算子 + 静态注册表）、`kvcpu.c`（`kvlangKvcpu*`：Execute 循环 + Bootstrap/HandleScope）、`runtime.c`（`kvlangRuntime*` ABI 层）、`rwirext.c`（扩展 ABI）。
- **注册表**：Go 的 `map[string]nativeRwir` → C 静态数组 + 线性查找。
- **print/println/cerr 是外部 rwir**（不在 builtin），在 `/lib/<opcode>`（kind=rwir）注册，dispatch 经 `is_ext_rwir` 命中。执行分两种模式：**嵌入式**（`KVMODE_RETURN`）——`runtime-rwirext` 的 Rust `term` 与 runtime 同进程，execute 循环经 `out_pc` 把控制交回扩展、扩展直接写宿主进程 stdout/stderr，零轮询；**handoff**——`handoff_external_rwir` 写 `/lib/<op>/.todo<vid>`、watch `/lib/<op>/.done<vid>`==id（30s 超时、50ms 轮询），供独立进程扩展（Go json、Py numpy）。

## 三、kvspace 统一 C ABI（去特化）

layout 与 runtime **都不对 kvspace 做特化处理**：不 dlopen、不双后端、不解析 scheme。二者只走一套 `kvspace*` 兼容 C ABI（24 符号，无下划线 camelCase）：

```
kvspaceConnect / Free / BytesFree
kvspaceSet / Get / GetBatch / List / Del / DelTree
kvspaceMkindex / MkindexExt / RmindexExt / Clear / Disconnect / Watch
kvspaceTlvEncode / TlvEncodePtr / DecodeHead / NewPtr / NewChar / NewCharByte / NewBool / NewInt64 / NewFloat64
```

- **后端由链接决定**：`KVLANG_KVSPACE_LIB` 环境变量在**构建期**选择链接哪个 kvspace 库（`kvspace-c` 或 `kvspace_durable`），两者导出同一 ABI。runtime 的 `kvlangKv_t` 只保留 `void *h`，代码零后端分支。
- **layout 零改动**：`layout/build.rs` 与 `runtime/Makefile` 都读 `KVLANG_KVSPACE_LIB`，同一套 layout 源码对接任一后端。
- **kvspace-c 补齐 ABI**：`kvspace-c/src/durable_abi.c` 导出完整 24 符号（内部复用 `kvspaceShm*` + `kvspaceXvalue*`），其中 `GetBatch`（前缀批量读，`[4B len][TLV]` 拼接）与 `Watch`（轮询到值==target 或超时）为补齐的手工实现。原生 SHM 符号加 `kvspaceShm` 前缀、XValue 编解码加 `kvspaceXvalue` 前缀，与对外 `kvspace*` ABI 区分。

## 四、三个后端

| 后端 | 库 | 存储 | 场景 |
|------|-----|------|------|
| shm | kvspace-c（C） | ART 树 + slotsboxmalloc + file-backed mmap | 高性能（训练/推理引擎） |
| redis | kvspace-durable（Rust） | Redis | 基线验证 |
| fs | kvspace-durable（Rust） | 文件系统 | 本地持久化 |

`conn(dsn)` 按 scheme 选后端（`shm://`、`redis://`、`fs://`），DSN 是唯一的后端区分。

## 五、numpy 扩展（零拷贝 buffer 在 kvspace）

`runtime-rwirext_example/py/numpy/` 是第一个 **tensor 类** rwirext（CPU 侧，numpy 计算，为 op-gpu 铺垫）。tensor 本体较大，**走 kvspace-c SHM**（redis/fs 有 I/O 拷贝，不适用）。

- **零拷贝核心**（单文件 `numpy.py` 的 `Engine.view`）：tensor 存 kvspace 为 XValue 定长数组（`float64`/`int64` 等）。`view(key)` 经 `kvspaceShmGet` 拿 TLV 指针，`kvspaceDecodeHead` 解出权威 `kvspaceHead_t`（`kind[32]/is_ptr/array_len/body_len/body_offset/ndim/dims[8]`）定位 body 段，用 `numpy.ctypeslib.as_array` 在该地址上建 ndarray——ndarray 的 buffer 就是 SHM 的 raw data，读写 ndarray 即读写 kvspace，无拷贝；写走 `kvspaceTlvEncode` 直接 N 维落盘。
- **扩展宿主自连 kvspace**：`Engine` 自己 `kvspaceConnect(dsn)` 拿句柄，KV 存取（`kv_get/kv_set/kv_list/kv_del`、`view/alloc`）全走 kvspace ABI（`kvspaceShmGet/ShmSet/List/Del/NewChar/DecodeHead/TlvEncode`），**不经 runtime**。rwir handoff 只用 `kvlang_rwirext*` 的 runtime 语义（`Register/Params/ResolveRead/ResolveReadPath/ResolveWrite/NextPc`，句柄传自连的 kvspace）：注册五大类算子（nr 依算子、nw=1）+ `numpy.print`，serve 循环里 `Params` 解码读/写参路径，`view` 零拷贝读、numpy 算、`alloc` 写回、交还 PC。Go json 扩展同构（`Serve` 自 `kvspaceConnect`，值编解码走 `kvspaceDecodeHead`/`kvspaceTlvEncode`/`kvspaceNewChar`）。
- **测试**：`tutorial/14-numpy/`（六例：creation/elementwise/linalg/reduce/manipulation/pipeline，shm 全流程）。

## 六、验证（Rust term 扩展 + C runtime）

`tutorial/test.py --runtime c`（Rust layout → kvspace → C runtime），140 例（136 可测 + 4 外部 wip 跳过）：

| 后端 | 结果 |
|------|------|
| shm | **136 PASS / 0 FAIL** |
| redis | 134 PASS（`time.kv` 的 `delta ms` RTT 时序 + `prime_sieve` 超时，非语义） |
| fs | 133 PASS（`time.kv` 文件 I/O 时序 + `prime_sieve` 超时，非语义；`inline.kv` lib 入口检测，见下） |

Go runtime 为 136/136 baseline（`goheap://` 进程内，无 RTT/文件 I/O）。

> fs 的 `inline.kv` 失败源于 fs 后端把 `.`（成员分隔）编码为 `./`（目录边界），dict 成员呈**嵌套**结构（`/lib/math./init`），而 redis 后端是**扁平**结构（`/lib/math.init`）。layout 的 `find_entry` DFS 按扁平结构找 `.init`，在 fs 上落在嵌套成员 "init" 上而丢掉 "math." 前缀。属 fs 后端 `.` 编码与 redis 扁平语义的差异，非执行语义 bug。

## 六、本轮修的 bug

- **kvspace-c ART**（`kvspace/src/kvspace.c`）：① 叶子 prefix 超 `ART_PREFIX_MAX`(10B) 被静默截断 → 长 key（scope 指令 `[coord]`）读回 None，加 `art_leaf_chain` 分级建链；② 插入「已有 key 的前缀」（如 `mid` 在 `mid_float` 后插入）`d > klen` 越界 → `shared == mpl && klen-d < prefix_len` 时拆 prefix。二者是 shm 103 个 tutorial 失败的主因。
- **kvspace-durable fs**（`src/fs/kvspace.rs`）：① 叶值写入时父是文件未转目录（`/lib/println` 既是签名值又是 `.todo` 目录）→ `set` 改用 `ensure_dir`；② `.` 编码把 `.todo<vid>` 的段首 `.` 也替换（`./todo` 被内核归一化丢点）→ `fs_path` 只替换非段首 `.`；③ dict 值存 `seen` 目录但成员访问用 `seen.` 目录（不一致）→ dict 值存 `seen.` + `get` 回落 `seen.`。
- **C runtime builtin**：`bi_cast_char` 对 `char/utf32` 未做 UTF-8→UTF-32 转换（`xv_new_char_kind` 只拷字节）→ 转 `char/utf32` 时用 `xv_new_char_utf32`。
- **rwirext ABI**：`kvlang_rwirextPrintLine` 原用「起始 op」的 `rawnl` 覆盖整段 RunSeq（print 起头的 run 里 println 也不换行）→ 改为逐条指令返回自身 `rawnl/cerr`，term 扩展逐条按自身属性输出。
