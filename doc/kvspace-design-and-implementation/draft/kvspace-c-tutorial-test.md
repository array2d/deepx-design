# kvspace-c tutorial/test.py 设计

> 2026-08-05
> 跨语言测试与 bench 框架：kvlang / Python (ctypes) / C (libkshm.so) 三种语言
> 全部运行在同一 kvspace-c shm 后端上

---

## 一、问题分析：现有 benchmark 的三个缺陷

### 1.1 混了启动开销

当前 `test.py --bench` 测量的是整个进程生命周期：

```
kvlang:   启动二进制 + 连接 Redis + 解析 .kv + 执行 + 退出  → ~86-22568ms
Python:   启动解释器 + 导入模块 + 执行 + 退出               → ~25ms
C:        启动进程 + 执行 + 退出                            → ~5ms
```

`hello.kv`（一行 `print("hello")`）花了 87ms，其中 Redis 连接和二进制加载占了大头。这不是算法性能——是工艺开销。benchmark 应该测量"每次操作的延迟"，不是"从零到一的完整启动时间"。

### 1.2 无统计量

单次运行，无 warmup，无数理统计。波动一次就写进 CSV。正确的 bench 应该：

```
warmup: N 次（丢弃）
measure: M 次 → min / median / p99 / stddev
```

### 1.3 后端不统一

kvlang 连 Redis（~100μs/次），C 直连 CPU（~ns），Python 直连解释器。三个程序做的事本质不同——只是输出了同样的字符串。benchmark 应该是"同一个 kvspace-c shm 后端，三种语言分别调用"。

---

## 二、新设计目标

| 目标 | 含义 |
|------|------|
| **统一后端** | kvlang/Python/C 全部调用 `kshm`（kvspace-c 的 C ABI） |
| **统一 workload** | 同一个 .kv 文件中 `# benchmark: SET=1000 GET=1000` 之类 annotation 定义 workload |
| **纯操作延迟** | 不计后端启动/连接，不计进程启动。measure 的是 SET/GET/DEL 操作的 wall-clock |
| **统计量** | warmup × 3 + measure × 10 → min/median/p99/stddev |
| **正确性门禁** | 三种语言的输出必须逐字节一致，不一致则 bench 结果标记 invalid |

---

## 三、Tutorial 目录结构

```
kvspace-c/tutorial/
├── test.py                  # 统一测试 + bench 入口
├── bench.csv                # 输出：benchmark 结果
├── 01-basic/                # 功能测试（对齐 kvspace-go tutorial）
│   ├── 01.kv                # kvlang 脚本，调用 kvspace-c
│   ├── 01.py                # Python ctypes 实现
│   └── 01.c                 # C libkshm 实现
├── 02-link/
│   ├── 02.kv
│   ├── 02.py
│   └── 02.c
├── ...                      # 13 个 .sh 用例的三种语言版
└── bench/                   # 纯性能 workload
    ├── set_get.kv
    ├── set_get.py
    ├── set_get.c            # 1000 SET + 1000 GET 循环
    ├── list_scan.kv
    ├── list_scan.py
    ├── list_scan.c
    ├── link_resolve.kv
    ├── link_resolve.py
    └── link_resolve.c
```

---

## 四、.kv 文件的 annotation 格式

```kvlang
# 期望输出:
#   ok: 1000 set
#   ok: 1000 get
# benchmark: SET=1000 GET=1000
```

`# benchmark:` 行定义 workload 参数，test.py 解析后传给三种语言各自的 harness。

### .py 实现（ctypes 调 kshm）

```python
import ctypes, os, sys
kshm = ctypes.CDLL("libkshm.so")
# 解析 benchmark: SET=1000 GET=1000 参数
N = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
h = kshm.kshm_create(b"/kshm_bench", 256 * 1024 * 1024)
# warmup + measure 由 test.py 控制迭代次数，.py 只实现单次 workload
for i in range(N):
    kshm.kshm_set(h, f"/key{i}".encode(), ...)
# ...
```

### .c 实现（链接 libkshm.so，同 workload）

```c
#include <kshm.h>
int main(int argc, char **argv) {
    int N = argc > 1 ? atoi(argv[1]) : 1000;
    kshm_t *h = kshm_create("/kshm_bench", 256 * 1024 * 1024);
    for (int i = 0; i < N; i++) {
        kshm_set(h, key, klen, val, vlen);
    }
    // ...
}
```

### .kv 实现（kvlang 调 kvspace-c shm 后端）

```kvlang
// # benchmark: SET=1000 GET=1000
// kvlang 通过 KVLANG_KVSPACE=shm://kshm_bench 自动连接
for (i <- 0; i < 1000; i -> i + 1) {
    key = "/key" + str(i)
    kv.set(key, "value" + str(i))
}
```

---

## 五、test.py 架构

### 5.1 两种模式

```
python3 tutorial/test.py                    # 功能测试：验证输出一致
python3 tutorial/test.py --bench            # benchmark：测量延迟
```

### 5.2 功能测试模式

对每个 `.kv` 文件：
1. 解析 `# 期望输出:` 头注释 → expected lines
2. 运行 `.kv`（KVLANG_KVSPACE=shm:// → kvlang）、`.py`（python3）、`.c`（gcc -O3 编译后执行）
3. 三种语言的 stdout 必须逐字节一致
4. 三种语言的 stdout 必须包含所有 expected lines
5. 任一失败 → 标记 failed，记录差异

### 5.3 Benchmark 模式

对每个 `bench/*.kv` 文件：
1. 解析 `# benchmark:` 参数（如 SET=1000 GET=1000）
2. 对三种语言分别执行：
   ```
   for i in range(WARMUP):
       compile + run, discard timing
   for i in range(MEASURE):
       compile + run, collect wall-clock
   → compute min/median/p99/stddev
   ```
3. 正确性门禁：所有 run 的 stdout 必须与 expected 一致
4. 输出 CSV：`file, lang, op, n_iter, min_us, median_us, p99_us, stddev_us`

### 5.4 核心代码

```python
#!/usr/bin/env python3
"""kvspace-c tutorial test — cross-language functional test + benchmark."""

import argparse, csv, statistics, subprocess, sys, tempfile, time
from pathlib import Path

WARMUP, MEASURE = 3, 10
ROOT = Path(__file__).resolve().parent.parent

def parse_header(kv_file: Path) -> dict:
    """从 .kv 头注释提取 expected 输出和 benchmark 参数。"""
    info = {"expects": [], "bench": {}}
    with open(kv_file) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("# benchmark:"):
                for part in line.split(":")[1].strip().split():
                    k, v = part.split("=")
                    info["bench"][k] = int(v)
            elif line.startswith("# 期望输出"):
                in_block = True
            elif line.startswith("#   ") and info.get("_in_expect"):
                info["expects"].append(line[2:].strip())
            elif line.startswith("# 期望输出") is False and line.startswith("#"):
                pass
    return info

def run_c(source: Path, shm_name: str, bench_args: str = "") -> subprocess.CompletedProcess:
    """编译 .c → 执行，返回 CompletedProcess。"""
    with tempfile.NamedTemporaryFile(suffix=".out", delete=False) as tmp:
        exe = tmp.name
    subprocess.run(["gcc", "-O3", "-lkshm", str(source), "-o", exe],
                   capture_output=True, check=True)
    result = subprocess.run([exe, shm_name] + bench_args.split(),
                            capture_output=True, text=True, timeout=30)
    Path(exe).unlink(missing_ok=True)
    return result

def run_python(source: Path, shm_name: str, bench_args: str = "") -> subprocess.CompletedProcess:
    """执行 .py。"""
    return subprocess.run([sys.executable, str(source), shm_name] + bench_args.split(),
                          capture_output=True, text=True, timeout=30)

def run_kvlang(source: Path, shm_name: str) -> subprocess.CompletedProcess:
    """执行 .kv（kvlang 二进制）。"""
    env = {"KVLANG_KVSPACE": f"shm://{shm_name}", **__import__("os").environ}
    return subprocess.run([str(ROOT / "kvlang" / "kvlang"), str(source)],
                          capture_output=True, text=True, timeout=60, env=env)

def timed_bench(runner, source: Path, shm_name: str, bench_args: str, n_warm: int, n_meas: int):
    """warmup + measure，返回 (min, median, p99, stddev) 微秒。"""
    times = []
    for _ in range(n_warm):
        runner(source, shm_name, bench_args)
    for _ in range(n_meas):
        t0 = time.perf_counter()
        runner(source, shm_name, bench_args)
        times.append((time.perf_counter() - t0) * 1_000_000)  # µs
    return (
        min(times),
        statistics.median(times),
        statistics.quantiles(times, n=100)[98],  # p99
        statistics.stdev(times),
    )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--filter", default="")
    args = ap.parse_args()

    bench_dir = ROOT / "tutorial" / "bench" if args.bench else ROOT / "tutorial"
    kv_files = sorted(bench_dir.rglob("*.kv"))

    if args.bench:
        rows = []
        for kvf in kv_files:
            info = parse_header(kvf)
            if not info["bench"]:
                continue
            bench_args = " ".join(f"{k}={v}" for k, v in info["bench"].items())
            for lang, runner in [("kvlang", run_kvlang), ("python", run_python), ("c", run_c)]:
                mn, med, p99, std = timed_bench(runner, kvf, f"bench_{kvf.stem}", bench_args)
                rows.append({"file": str(kvf.relative_to(ROOT)), "lang": lang,
                             "bench": bench_args,
                             "min_us": f"{mn:.1f}", "median_us": f"{med:.1f}",
                             "p99_us": f"{p99:.1f}", "stddev_us": f"{std:.1f}"})
        with open(ROOT / "tutorial" / "bench.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["file", "lang", "bench", "min_us", "median_us", "p99_us", "stddev_us"])
            w.writeheader()
            w.writerows(rows)
    else:
        # 功能测试
        ...

if __name__ == "__main__":
    main()
```

### 5.5 benchmark workload 设计

每个 bench 文件做明确的微观操作：

| bench 文件 | workload | 测量目标 |
|-----------|----------|---------|
| `set.kv` | SET N 个不同 key | 纯写入吞吐 |
| `get.kv` | 预填充 N key → GET N 次 | 纯读取延迟 |
| `set_get.kv` | 交错的 SET+GET N 次 | 混合读写 |
| `list.kv` | 创建 N 子项的目录 → List 1000 次 | 目录列举 |
| `link.kv` | 创建 link → 穿透 GET 1000 次 | 链接解析 |
| `del.kv` | SET N → DEL N | 删除吞吐 |

每个 bench 文件都有 `.kv`、`.py`、`.c` 三个版本，执行相同的 workload。

---

## 六、bench CSV 输出格式

```csv
file,lang,bench,N,min_us,median_us,p99_us,stddev_us
bench/set_get.kv,kvlang,SET=1000 GET=1000,2000,452.0,478.0,612.3,34.1
bench/set_get.kv,python,SET=1000 GET=1000,2000,680.0,720.0,890.0,45.2
bench/set_get.kv,c,SET=1000 GET=1000,2000,120.0,135.0,180.0,12.3
bench/list.kv,kvlang,LIST=1000,1000,89.0,95.0,120.0,8.1
bench/list.kv,python,LIST=1000,1000,150.0,160.0,200.0,15.0
bench/list.kv,c,LIST=1000,1000,45.0,50.0,65.0,5.0
```

---

## 七、与 kvspace-go tutorial 的关系

| | kvspace-go tutorial/test.py | kvspace-c tutorial/test.py |
|---|---|---|
| 测试语言 | shell (bash) | kvlang + Python + C |
| 期望格式 | `# expected:` | `# 期望输出:` + `# benchmark:` |
| 运行方式 | `bash <script>.sh` 调 `kvspace` CLI | 三语言各跑同名 workload |
| bench | 无 | warmup × 3 + measure × 10, min/med/p99/stddev |
| 后端 | `KVLANG_KVSPACE=redis://` | `KVLANG_KVSPACE=shm://` + ctypes + libkshm |
| 跨后端验证 | N/A | 同 workload 三语言输出逐字节对比 |

### 跨仓库一致性测试链

```
kvspace-go tutorial (redis后端)
    ↓ 验证 KVSpace 语义正确
kvspace-c tutorial (shm后端)
    ↓ 同一套用例 .kv/.py/.c 三语言验证
    ↓ 跨后端对比 (redis vs shm 输出一致)
kvlang tutorial (已有 .kv 文件，--bench 模式)
    ↓ KVLANG_KVSPACE=shm:// 跑已有 127 个用例
```
