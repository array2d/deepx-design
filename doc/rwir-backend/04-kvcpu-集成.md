# 04 — kvcpu 集成

> 委托机制在 kvcpu.Execute() 调度链中的位置与实现。

## 调度链变更

当前 `kvcpu.Execute()` 的调度优先级（`execute.go:144-172`）：

```
1. IsControlOp    → handleControl    (call/return/br/goto)
2. IsNativeOp     → builtin.Native   (+/-/print/...)
3. tensor.*       → dispatch.Compute (外部后端)
4. isCopyOp       → ExecuteCopy      (=)
5. default        → rewrite as call  (用户函数)
```

变更后：

```
1. IsControlOp    → handleControl    (不变)
2. IsNativeOp     → builtin.Native   (不变)
3. IsDelegatedOp  → Delegate         ← 新增：查 /sys/rwir-backend/
4. tensor.*       → dispatch.Compute (保留，兼容旧路径)
5. isCopyOp       → ExecuteCopy      (不变)
6. default        → rewrite as call  (不变)
```

### IsDelegatedOp 判定

```go
func IsDelegatedOp(ctx context.Context, kv kvspace.KVSpace, opcode string) bool {
    // 快速路径：查内存缓存（避免每条指令都 List /sys/rwir-backend）
    if _, ok := delegationCache.Load(opcode); ok {
        return true
    }
    // 慢路径：扫描后端注册表
    backends := kv.List(keytree.SysRwirBackendRoot + "/", false)
    for _, b := range backends {
        if !kvspace.IsNone(kvspace.GetOne(kv, keytree.SysRwirBackendOp(b, opcode))) {
            delegationCache.Store(opcode, true)
            return true
        }
    }
    return false
}
```

`delegationCache` 是进程内 `sync.Map`，存储已知的可委托 opcode。后端注册变更时缓存失效（Watch `/sys/rwir-backend/` 变更）。

### Delegate() 在循环中的位置

```go
// kvcpu/execute.go — Execute() 主循环中的新增分支

case IsDelegatedOp(inst.Opcode):
    execErr = Delegate(ctx, c.kv, vtid, pc, inst)
    // Delegate 内部：
    //   - Select backend
    //   - 构建 RwirTask
    //   - Notify cmd queue
    //   - Set vthread "wait"
    //   - Watch done_key
    //   - 成功 → Set vthread "running" + nextPC
    //   - 失败 → SetError
    //   返回 nil 或 error
```

关键：`Delegate()` 返回时 PC 已更新（成功）或 vthread 已标记错误（失败）。主循环末尾读取 `.pc` 的逻辑不变。

## 并发委托

同一 vthread 可以连续发起多个委托，无需等待前一个完成——前提是 rwir 之间无数据依赖（由 `tothink-037` 3D 坐标系统保证）。

但是，在 3D 坐标落地前，**当前实现仍然逐条委托**（一次 Delegate 完成后才进入下一条指令的循环迭代）。这不是委托协议的限制，而是 kvcpu 主循环单步执行的固有特性。

当 3D 坐标系统落地后，同一 s0 组内的多条 rwir 可以并发委托：

```go
// 伪代码：3D 并发委托（未来）
func ExecuteBatch(kv, vtid string, batch []*rwir.Rwir) error {
    var wg sync.WaitGroup
    for _, inst := range batch {
        wg.Add(1)
        go func(inst *rwir.Rwir) {
            defer wg.Done()
            Delegate(ctx, kv, vtid, inst.PC, inst)
        }(inst)
    }
    wg.Wait()
    // s0 步 barrier：全部完成后推进到下一 s0
}
```

## 脱离子进程：后端如何在崩溃后恢复

委托协议要求后端实现幂等性（同 `request_id` 不重复执行）。kvspace 侧：

1. 命令队列中的消息是持久的（Redis LPUSH，无消费者时保留）
2. 完成信号 `/done/rwir/<request_id>` 是一次性 Notify——后端写入后 kvcpu 消费即消失
3. 后端崩溃重启后重新 Watch 命令队列，可能收到旧消息

后端恢复流程：

```
1. 重启，重新注册到 /sys/rwir-backend/<name>/（覆盖旧 status）
2. 进入 Watch 循环
3. 收到 task（可能是崩溃前未完成的旧 task）
4. 检查 /done/rwir/<request_id> 是否存在：
   - 存在 → 已完成，跳过
   - 不存在 → 重新执行（结果幂等写入 output key）
5. Notify done_key
```

## 与现有 dispatch.Compute 的迁移

`dispatch.Compute` 当前处理所有 `tensor.*` 指令。迁移分两步：

### Phase 1：共存

- 新的 `Delegate()` 路径优先：opcode 在 `/sys/rwir-backend/` 中注册 → 走 Delegate
- 回退到 `dispatch.Compute`：未注册但以 `tensor.` 开头 → 走旧路径
- 目的：允许 op-gpu 后端逐步迁移到新注册表，不破坏现有功能

```go
case IsDelegatedOp(inst.Opcode):
    execErr = Delegate(ctx, c.kv, vtid, pc, inst)
case strings.HasPrefix(inst.Opcode, "tensor."):
    execErr = dispatch.Compute(ctx, c.kv, vtid, pc, inst)
```

### Phase 2：统一

- op-gpu 成熟后，从 `/sys/op/` 迁移注册到 `/sys/rwir-backend/op-gpu/`
- `dispatch.Compute` 路径保留为空操作（或直接删除）
- `/sys/op/` 路径保留仅供遗留 deepx op-plat 使用

## keytree 路径常量补充

```go
// keytree/sys.go — 新增

const (
    SysRwirBackendRoot = "/sys/rwir-backend"
    DoneRwirRoot       = "/done/rwir"
)

func SysRwirBackend(name string) string {
    return SysRwirBackendRoot + "/" + name
}
func SysRwirBackendCmd(name string) string {
    return SysRwirBackend(name) + "/cmd"
}
func SysRwirBackendOp(name, opcode string) string {
    return SysRwirBackend(name) + "/op/" + opcode
}
func SysRwirBackendCategory(name, cat string) string {
    return SysRwirBackend(name) + "/category/" + cat
}
func DoneRwir(requestID string) string {
    return DoneRwirRoot + "/" + requestID
}
```
