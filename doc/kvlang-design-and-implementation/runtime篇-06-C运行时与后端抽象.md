# runtime篇-06: C 运行时与后端抽象

> 状态：与实现同步（2026-08-17）。代码：`kvlang/runtime/`（纯 C）、`kvlang/runtime-rwirext_example/rust/term/`（Rust term）、`kvlang/runtime-rwirext_example/go/json/`（Go json）、`kvlang/runtime-rwirext_example/py/numpy/`（Python numpy，零拷贝）、`array2d/kvspace/`（kvspace-c SHM）、`array2d/kvspace-durable/`（fs/redis）。旧 Go runtime 已迁 `kvlang/oldhero/`。

## 一、小 runtime 大扩展

kvlang 是**小 runtime 大扩展**架构。中央 runtime 只实现最核心的 execute 大循环（fetch-decode-execute、call/return/br/goto、native rwir 派发、`=` 拷贝、用户函数调用），其余能力（tensor 计算、LLM、agent、print/println/cerr）全部交给**扩展 runtime**（见 [[runtime篇-05-rwirext扩展运行时]]）。

扩展 runtime 从中央 runtime 拿到 PC，**连续执行己方 rwir 到非己方指令**，再把最终 PC 交还。op-gpu 扩展收到 handoff 后连续跑完整段 tensor 计算，不必每算一个 op 就回报中央——否则对训练/推理这种分秒必争的场景，逐条 handoff 的延迟不可接受。

## 二、C 运行时（纯 C + extern "C" ABI）

`kvlang/runtime/` 是 Go runtime 的 C 顶替版，作为嵌入式通用执行器，嵌入到 rwirext 项目（op-gpu、agent、livebyte）。

- **对外两组 C ABI**：`kvlang_runtime.h`（`kvlang_rt_connect/disconnect/execute/...`，核心执行器）+ `kvlang_rwext.h`（`rwext_connect/register/list/get/set/del/print_line/params/next_pc`，扩展 runtime 的 kv 读写 + rwir 解码 + resolve/display 封装）。`rwext_params` 返回 opcode + 读参名 + 写参名（\n 分隔），供 numpy/tensor 扩展按路径零拷贝读 raw 数据。
- **模块**：`xvalue.c`（XValue TLV 编解码 + value_string）、`kv.c`（kvspace ABI 封装）、`keytree.c`（路径构造）、`rwir.c`（PC 解析 + Decode）、`vthread.c`、`builtin.c`（~50 算子 + 静态注册表）、`kvcpu.c`（Execute 循环 + Bootstrap/HandleScope）、`runtime.c`（ABI 层）、`rwext.c`（扩展 ABI）。
- **注册表**：Go 的 `map[string]nativeRwir` → C 静态数组 + 线性查找。
- **print/println/cerr 是外部 rwir**（不在 builtin，`bi_is_native` 不含），在 `/lib/<opcode>`（kind=rwir）注册，dispatch 经 `is_ext_rwir` 命中。执行分两种模式：**嵌入式**（`KVMODE_RETURN`）——`runtime-rwirext` 的 Rust `term` 与 runtime 同进程，execute 循环经 `out_pc` 把控制交回扩展、扩展直接写宿主进程 stdout/stderr，零轮询；**handoff**——`handoff_external_rwir` 写 `/lib/<op>/.todo<vid>`、watch `/lib/<op>/.done<vid>`==id（30s 超时、50ms 轮询），供独立进程扩展（Go json、Py numpy）。

## 三、kvspace 统一 C ABI（去特化）

layout 与 runtime **都不对 kvspace 做特化处理**：不 dlopen、不双后端、不解析 scheme。二者只走一套 `kvspace_*` 兼容 C ABI（24 符号）：

```
kvspace_conn / free / bytes_free
kvspace_set / get_one / get_batch / list / del / del_tree
kvspace_mkindex / ext_index / del_ext_index / clear / disconn / watch
kvspace_tlv_encode / tlv_encode_ptr / decode_head / new_ptr / new_char / new_char_byte / new_bool / new_int64 / new_float64
```

- **后端由链接决定**：`KVLANG_KVSPACE_LIB` 环境变量在**构建期**选择链接哪个 kvspace 库（`kvspace-c` 或 `kvspace_durable`），两者导出同一 ABI。runtime 的 `kv_t` 只保留 `void *h`，代码零后端分支。
- **layout 零改动**：`layout/build.rs` 与 `runtime/Makefile` 都读 `KVLANG_KVSPACE_LIB`，同一套 layout 源码对接任一后端。
- **kvspace-c 补齐 ABI**：`kvspace/src/durable_abi.c` 导出完整 24 符号（内部复用 `kvsc_*` + `xvalue_*`），其中 `get_batch`（前缀批量读，`[4B len][TLV]` 拼接）与 `watch`（轮询到值==target 或超时）为补齐的手工实现。原生符号一律加 `kvsc_` 前缀避免与 ABI 重名。

## 四、三个后端

| 后端 | 库 | 存储 | 场景 |
|------|-----|------|------|
| shm | kvspace-c（C） | ART 树 + slotsboxmalloc + file-backed mmap | 高性能（训练/推理引擎） |
| redis | kvspace-durable（Rust） | Redis | 基线验证 |
| fs | kvspace-durable（Rust） | 文件系统 | 本地持久化 |

`conn(dsn)` 按 scheme 选后端（`shm://`、`redis://`、`fs://`），DSN 是唯一的后端区分。

## 五、numpy 扩展（零拷贝 buffer 在 kvspace）

`runtime-rwirext_example/py/numpy/` 是第一个 **tensor 类** rwirext（CPU 侧，numpy 计算，为 op-gpu 铺垫）。tensor 本体较大，**走 kvspace-c SHM**（redis/fs 有 I/O 拷贝，不适用）。

- **零拷贝核心**（`numpy_ext.py`）：tensor 存 kvspace 为 XValue 定长数组（`float64`/`int64` 等）。`tensor_view(key)` 经 `kvsc_get` 拿 TLV 指针，解析 kindexp 头（`[1B kl][kind][1B ref][1B arr_flag][1B ndim][ndim×4B dims][4B raw_len][raw]`）定位 raw 段，用 `numpy.ctypeslib.as_array` 在该地址上建 ndarray——ndarray 的 buffer 就是 SHM 的 raw data，读写 ndarray 即读写 kvspace，无拷贝。
- **rwir handoff**（`numpy_rwext.py`）：复用 `rwext_*` ABI（`rwext_connect/register/list/get/set/del/params/next_pc`）。注册 `numpy.add`/`numpy.mul`（nr=2, nw=1），serve 循环里 `rwext_params` 解码出读参/写参**路径**，`tensor_view` 零拷贝读、numpy 算、`tensor_alloc` 写回、交还 PC。
- **测试**：`test_numpy.py`（直接零拷贝 view + 原地改写回 kvspace）+ `test.kv`（`numpy.add(/t/a, /t/b) -> /t/c` 经 shm 全流程）。

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
- **rwext ABI**：`rwext_print_line` 原用「起始 op」的 `rawnl` 覆盖整段 RunSeq（print 起头的 run 里 println 也不换行）→ 改为逐条指令返回自身 `rawnl/cerr`，term 扩展逐条按自身属性输出。
