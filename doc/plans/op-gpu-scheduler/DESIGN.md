# kvlang ↔ op-gpu 调度机制设计

> kvlang = CPU（调度决策），op-gpu = GPU（执行计算）。kvspace 是二者之间唯一的协调平面。
> 场景：1 台 A800（8 GPU），8 个 op-gpu 进程各管 1 卡。

## 一、架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│  kvlang VM (Go)                                                         │
│                                                                         │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────────────────┐  │
│  │ 融合检测  │   │ 编译调度  │   │ 执行调度  │   │ 设备亲和性          │  │
│  │ FUSE_DB  │   │ → op-gpu │   │ → op-gpu │   │ tensor→GPU 映射    │  │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘   └─────────┬──────────┘  │
│       │              │              │                     │             │
│       │    ┌─────────┴──────────────┴─────────────────────┘             │
│       │    │                                                            │
│       ▼    ▼                                                            │
│  ┌────────────────────────────────────────────────────────────┐        │
│  │                    kvspace (Redis)                          │        │
│  │  /sys/op-gpu/*  /cmd/op-gpu/*  /done/op-gpu/*  /func/compiled/*     │
│  └──────┬──────────────┬──────────────┬───────────────────────┘        │
└─────────┼──────────────┼──────────────┼────────────────────────────────┘
          │              │              │
    ┌─────▼──┐    ┌─────▼──┐     ┌─────▼──┐
    │op-gpu 0│    │op-gpu 1│ ... │op-gpu 7│    8 进程，各绑 1 GPU
    │ GPU 0  │    │ GPU 1  │     │ GPU 7  │
    │ cuda:0 │    │ cuda:1 │     │ cuda:7 │
    └────────┘    └────────┘     └────────┘
```

**核心原则**：
- kvspace 是**唯一的协调平面**。kvlang 与 op-gpu 之间不建立 TCP/gRPC 直连。
- kvlang 负责**所有调度决策**（何时编译、哪个 GPU 执行）；op-gpu 只响应请求。
- tensor 数据永远在 GPU 显存，kvspace 只存元数据（shape/dtype/ptr）。

## 二、kvspace 键布局

```
/sys/op-gpu/<gpu_id>/               # op-gpu 注册信息（启动时写入，心跳更新）
├── status        = string:"ready"   # ready | busy | offline
├── device        = string:"NVIDIA A800"
├── memory        = int64:85197971456
└── pattern       = string:""        # 已注册的融合模式（逗号分隔，如 "linear,linear_relu"）

/cmd/op-gpu/compile:<gpu_id>         # 编译请求队列（LPUSH/Notify）
/cmd/op-gpu/execute:<gpu_id>         # 执行请求队列（LPUSH/Notify）

/done/op-gpu/<request_id>            # 完成信号（一次性 Notify → Watch）

/func/compiled/<pattern>/<shape_hash>/   # 编译产物缓存
├── so            = bytes:<.so binary>
└── meta          = string:JSON {
                      "pattern": "linear_relu",
                      "shapes": {"M":512,"N":512,"K":256},
                      "block":  {"BM":128,"BN":128,"BK":32},
                      "backend": "tilelang",
                      "so_size": 123456
                   }
```

### 2.1 request_id 格式

```
compile:<pattern>:<shape_hash>       # 例: compile:linear_relu:a1b2c3d4
execute:<gpu_id>:<vtid>:<seq>        # 例: execute:0:7:42
```

编译 request_id 由 pattern + shape_hash 唯一确定——天然去重：多个 vthread 同时触发同一编译请求时，只需一次编译。

## 三、编译链路

### 3.1 触发时机

kvlang 的 compile pass（在 lower 之后、layoutrwir 之前）扫描函数体：

```
def inference(x, W, b) -> (out) {
    tensor.matmul(x, W) -> tmp1    # ─┐
    tensor.add(tmp1, b) -> tmp2    #  │ FUSE_DB 匹配: linear_relu
    tensor.relu(tmp2)  -> out      # ─┘
}
```

匹配到 `FUSE_DB["tensor.matmul", "tensor.add", "tensor.relu"]` → 生成融合调用：

```
tensor.fused_linear_relu(x, W, b) -> out   # 替换原 3 条指令
```

### 3.2 编译调度

```
kvlang compile pass                    op-gpu (任一空闲)
─────────────────────                  ─────────────────────
│                                      │
│ 1. 检测融合 → pattern="linear_relu"   │
│ 2. 读 heap-plat meta → shape/dtype   │
│ 3. shape_hash = sha256(shape+dtype)  │
│                                      │
│ 4. 检查缓存:                          │
│    /func/compiled/linear_relu/       │
│    <shape_hash>/so 存在?              │
│    ├── 是 → 跳过编译，直接执行        │
│    └── 否 ↓                          │
│                                      │
│ 5. 选 op-gpu:                        │
│    - 任一 status=ready 的 gpu        │
│    - 优先 status=ready 且支持该模式   │
│                                      │
│ 6. compile_spec = JSON {             │
│      "request_id": "compile:         │
│        linear_relu:a1b2c3d4",       │
│      "pattern": "linear_relu",       │
│      "activation": "relu",           │
│      "shapes": {"M":512,"N":512,     │
│        "K":256},                     │
│      "dtype": "float16",             │
│      "backend": "tilelang"           │
│    }                                 │
│                                      │
│ 7. kv.Notify(                        │
│      "cmd/op-gpu/compile:0",        │
│      XValue.string(spec_json))       │
│                                      │
│                                      ├─ 8. Watch 触发, 解析 spec
│                                      │
│                                      │ 9. make_linear_kernel("relu")
│                                      │    → tilelang.jit → compile
│                                      │
│                                      │ 10. 验证: random data → diff
│                                      │
│                                      │ 11. 写缓存:
│                                      │   kv.Set("func/compiled/
│                                      │     linear_relu/a1b2c3d4/so",
│                                      │     XValue.bytes(so))
│                                      │   kv.Set(".../meta",
│                                      │     XValue.string(meta_json))
│                                      │
│ 12. kv.Watch("done/op-gpu/           ├─ 13. kv.Notify("done/op-gpu/
│     compile:linear_relu:a1b2c3d4")  │     compile:linear_relu:a1b2c3d4",
│     ← 超时 30s → 重试其他 op-gpu     │     XValue.string("ok"))
│                                      │
│ 14. Watch 返回 → 编译完成             │
│     /func/compiled/.../so 已就绪     │
│                                      │
▼ 继续执行                             ▼ 回到 Watch 循环
```

### 3.3 编译去重

多个 vthread 可能同时触发同一 pattern+shape 的编译。request_id 天然去重：
- kvlang 在 Notify 前检查 `/done/op-gpu/compile:<pattern>:<shape_hash>` 是否存在
- 若已存在 done 信号或 .so 已缓存 → 跳过编译
- 若正在编译中（Watch 已等待）→ 复用同一 Watch

### 3.4 编译容错

| 失败场景 | 处理 |
|---------|------|
| op-gpu 编译超时 (30s) | Watch 超时 → 标记该 op-gpu 为 offline → 重试其他 |
| op-gpu 编译错误 | Notify done 带 error 消息 → kvlang 读取后回退到逐 op 执行 |
| 所有 op-gpu 不可用 | 回退到逐 op 执行（不融合），记录告警 |

## 四、执行链路

### 4.1 OpTask 格式

```json
{
  "request_id": "execute:0:7:42",
  "kernel": "/func/compiled/linear_relu/a1b2c3d4",
  "inputs": [
    {"path": "/heap/tensor/data/x", "shape": [512,256], "dtype": "float16",
     "gpu_ptr": 140285768000000},
    {"path": "/heap/tensor/data/W", "shape": [256,512], "dtype": "float16",
     "gpu_ptr": 140285768300000},
    {"path": "/heap/tensor/data/b", "shape": [512], "dtype": "float16",
     "gpu_ptr": 140285768500000}
  ],
  "outputs": [
    {"path": "/heap/tensor/data/out", "shape": [512,512], "dtype": "float16",
     "gpu_ptr": 140285768700000}
  ]
}
```

### 4.2 执行调度

```
kvlang dispatch                        op-gpu (tensor 所在 GPU)
─────────────────────                  ──────────────────────────
│                                      │
│ 1. 读 tensor meta → gpu_id           │
│    /heap/tensor/data/x/meta          │
│    → {gpu_id: 3, ptr: 0x7f...}      │
│                                      │
│ 2. 构建 OpTask JSON                  │
│    - kernel: /func/compiled/.../so   │
│    - inputs[]: path + ptr            │
│    - outputs[]: path + ptr           │
│                                      │
│ 3. kv.Notify(                        │
│      "cmd/op-gpu/execute:3",        │
│      XValue.string(op_task_json))    │
│                                      │
│                                      ├─ 4. Watch 触发, 解析 OpTask
│                                      │
│                                      │ 5. 检查 kernel cache:
│                                      │    dlopen'd .so 句柄已缓存?
│                                      │    否 → 读 kvspace → dlopen
│                                      │
│                                      │ 6. kernel_fn(
│                                      │      x_ptr, W_ptr, b_ptr,
│                                      │      out_ptr, cuda_stream)
│                                      │    cudaStreamSynchronize()
│                                      │
│                                      │ 7. kv.Notify(
│                                      │      "done/op-gpu/
│                                      │       execute:0:7:42",
│                                      │      XValue.string("ok"))
│                                      │
│ 8. kv.Watch("done/op-gpu/            │
│     execute:0:7:42")                 │
│     ← 返回 "ok"                       │
│                                      │
▼ 继续下一条指令                        ▼ 回到 Watch 循环
```

### 4.3 设备亲和性

tensor 在创建时由 heap-plat 在特定 GPU 分配显存，此后所有对该 tensor 的操作必须路由到同一 GPU：

```
tensor.new("f16", "[512,256]") -> /data/x, gpu=3

后续所有访问 /data/x 的 tensor op → 自动路由到 op-gpu 3
```

op-gpu 间不交换 tensor 数据（除非显式 `tensor.move`）。

### 4.4 并行执行

多条**无数据依赖**的 tensor 指令可并行 dispatch 到不同 GPU：

```kv
# 这两个 matmul 无依赖 → 可并行
tensor.matmul(x, Wq) -> Q     # → op-gpu:0
tensor.matmul(x, Wk) -> K     # → op-gpu:1
# 同时 Notify，不等待单个返回
```

kvlang dispatch 层识别独立指令，并发 Notify 到多个 op-gpu，再统一 WaitDone。

## 五、op-gpu 进程模型

### 5.1 启动流程

```
1. op-gpu 启动，解析参数: --gpu-id=0 --kvspace=redis://127.0.0.1:6379
2. cudaSetDevice(0)
3. 注册到 kvspace:
   kv.Set("/sys/op-gpu/0/status", XValue.string("ready"))
   kv.Set("/sys/op-gpu/0/device", XValue.string("NVIDIA A800"))
   kv.Set("/sys/op-gpu/0/memory", XValue.int64(mem_info.total))
4. 注册融合模式:
   kv.Set("/sys/op-gpu/0/pattern", XValue.string(
     "linear,linear_relu,linear_gelu,linear_silu,linear_swish"))
5. 进入 Watch 循环:
   for {
       task = kv.Watch("cmd/op-gpu/compile:0")
       result = handle_compile(task)
       kv.Notify("done/op-gpu/" + task.request_id, result)
   }
```

### 5.2 双队列模型

每个 op-gpu 维护两个 Watch：

```
goroutine 1: Watch("cmd/op-gpu/compile:0")  → handle_compile()
goroutine 2: Watch("cmd/op-gpu/execute:0")  → handle_execute()
```

执行优先级高于编译：execute Watch 先检查，有任务立即处理；compile 是后台任务。

### 5.3 心跳

op-gpu 每 5 秒更新 `/sys/op-gpu/<id>/status`。kvlang 定期扫描 `/sys/op-gpu/*`，超过 15 秒未更新标记为 offline。

## 六、kvlang compile 模块集成

### 6.1 在现有管线中的位置

```
parser.ParseFile()
    │
lower.Func()
    │
┌── compile.Fuse()        ← 新增：融合检测 + 编译调度
│   │
│   │  遍历函数体，识别连续 tensor op
│   │  → FUSE_DB 匹配 → 生成 compile_spec
│   │  → 检查缓存 / 发起编译
│   │  → 替换 AST：3 条 tensor op → 1 条 fused_call
│   │
└── layoutrwir.WriteFunc() ← 不变
    │
kvcpu.Execute()
    │
dispatch.Compute()        ← 扩展：tensor op → op-gpu 执行
```

### 6.2 Fuse() 伪代码

```go
func Fuse(kv kvspace.KVSpace, file *ast.File) error {
    for i := range file.Funcs {
        fn := &file.Funcs[i]
        for j := 0; j < len(fn.Body); j++ {
            // 滑动窗口匹配 FUSION_DB
            for _, pattern := range patterns {
                if match := pattern.Match(fn.Body, j); match != nil {
                    shapes := readTensorShapes(kv, match.Tensors)
                    spec := CompileSpec{Pattern: pattern.Name, Shapes: shapes, ...}
                    
                    // 检查缓存
                    hash := sha256(spec)
                    if _, err := kv.Get("/func/compiled/" + pattern.Name + "/" + hash + "/so"); err == nil {
                        // 缓存命中
                        fn.Body = replaceOps(fn.Body, j, match.Len, spec)
                        break
                    }
                    
                    // 发起编译
                    gpu := selectOpGPU(kv)
                    kv.Notify("cmd/op-gpu/compile:"+gpu, spec.ToJSON())
                    kv.Watch("done/op-gpu/compile:"+pattern.Name+":"+hash)
                    
                    fn.Body = replaceOps(fn.Body, j, match.Len, spec)
                    break
                }
            }
        }
    }
}
```

## 七、与 heap-plat 协作

### 7.1 职责边界

| 组件 | 职责 | 不负责 |
|------|------|--------|
| **heap-plat** | GPU 显存分配/释放/生命周期，写入 `/heap/tensor/<path>/meta` | 计算、调度 |
| **op-gpu** | GPU kernel 编译与执行，从 meta 读 ptr | 显存分配 |
| **kvlang** | 调度决策、编译触发、OpTask 组装 | GPU 显存管理 |

### 7.2 交互时序

```
┌─ kvlang ─────────────────────────────────────────────────────────────┐
│                                                                      │
│ ① tensor.new("f16","[512,256]") -> /data/x                          │
│    → Notify("cmd/heap-plat/new", ...)                                │
│    → heap-plat: cudaMalloc → 写 meta → Notify("done/heap-plat/...") │
│    → kvlang 继续                                                     │
│                                                                      │
│ ② tensor.matmul(x, W) -> out                                        │
│    → kvlang 读 /heap/tensor/data/x/meta → {gpu_id:3, ptr:0x7f...}  │
│    → 构建 OpTask（含 ptr）→ Notify("cmd/op-gpu/execute:3")          │
│    → op-gpu: dlopen .so → kernel(ptr...) → Notify done              │
│                                                                      │
│ ③ tensor.del(x)                                                     │
│    → Notify("cmd/heap-plat/del", ...)                                │
│    → heap-plat: cudaFree → DelTree(/heap/tensor/data/x)             │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

## 八、性能特征

### 8.1 延迟分解（单次 fused kernel 执行）

| 环节 | 耗时 | 说明 |
|------|------|------|
| kvlang: 读 tensor meta | ~50μs | Redis GET (loopback) |
| kvlang: 构建 OpTask JSON | ~5μs | 内存操作 |
| kvlang → kvspace: Notify | ~50μs | Redis LPUSH |
| kvspace → op-gpu: Watch 唤醒 | ~100μs | BLPOP 延迟 |
| op-gpu: 解析 OpTask | ~5μs | JSON 解析 |
| op-gpu: kernel launch | ~12μs | TileLang 512×512 GEMM |
| op-gpu → kvspace: Notify done | ~50μs | Redis LPUSH |
| kvspace → kvlang: Watch 返回 | ~100μs | BLPOP 延迟 |
| **总计** | **~370μs** | per fused kernel call |

### 8.2 编译延迟（首次，缓存后为零）

| 环节 | 耗时 | 说明 |
|------|------|------|
| kvlang: FUSE_DB 匹配 | ~10μs | 哈希表查找 |
| 检查缓存 → 未命中 | ~50μs | Redis GET |
| Notify + Watch 往返 | ~300μs | 编译请求 |
| TileLang 编译 | 100ms-2s | 取决于 kernel 复杂度 |
| 写 .so 到 kvspace | ~5ms | 典型 .so ~200KB |
| **总计（首次）** | **~1s** | 后续缓存命中 ~50μs |

### 8.3 并行加速

8 个 GPU 并行执行无依赖的 tensor ops：

```kv
# QKV projection: 3 个独立 matmul → 3 GPU 并行
tensor.matmul(x, Wq) -> Q    # op-gpu 0, Notify 异步
tensor.matmul(x, Wk) -> K    # op-gpu 1, Notify 异步  
tensor.matmul(x, Wv) -> V    # op-gpu 2, Notify 异步
# 三个 Notify 同时发出，三个 Watch 并发等待 → 延迟 ≈ max(单次)
```

## 九、故障恢复

### 9.1 op-gpu 崩溃

```
1. kvlang Watch("done/op-gpu/execute:0:7:42") 超时 (5s)
2. 检查 /sys/op-gpu/0/status → 心跳超时 15s → 标记 offline
3. 检查 tensor 数据: GPU 0 已不可用，tensor 数据丢失
4. 标记相关 tensor 为 invalid → vthread 报错
5. kvlang 可重启 op-gpu 进程（外部 supervisor）
```

### 9.2 编译失败回退

```
1. op-gpu compile 失败 → Notify done 带 error
2. kvlang Watch 返回 error → 记录日志
3. 保留原 3 条逐 op 指令（不回退 AST 替换），逐 op 执行
4. 下次同 shape 请求重试编译（可能已修复 op-gpu 或 backend bug）
```

## 十、与现有设计的对齐

| 现有概念 | 对应 |
|---------|------|
| `cmd:op-triton-cuda:0` (旧设计) | `cmd/op-gpu/execute:<gpu_id>` |
| `done:vtid` (旧设计) | `done/op-gpu/<request_id>` |
| `op-plat` 注册 (旧设计) | `/sys/op-gpu/<gpu_id>/status` |
| 单 op dispatch (旧设计) | 融合 kernel dispatch |
| `fuse_begin/fuse_end` 指令 (旧设计) | compile pass 自动检测，无需显式标注 |
| `@triton_compile` 注解 (旧设计) | 自动触发，无需注解 |

## 十一、实现优先级

| Phase | 内容 | 依赖 |
|-------|------|------|
| 1 | op-gpu Watch 循环 + compile/execute 处理 | kvspace-py |
| 2 | kvlang compile.Fuse() pass | FUSE_DB |
| 3 | OpTask 构建 + device affinity | heap-plat meta |
| 4 | 编译缓存 (shape_hash → .so) | Phase 1 |
| 5 | 多 GPU 并行 dispatch | Phase 2+3 |
| 6 | 心跳 + 故障恢复 | Phase 1 |
| 7 | 并行执行（独立指令并发 Notify） | Phase 5 |
