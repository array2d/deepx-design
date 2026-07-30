# 代码指令的布局格式

## layoutrwir 的设计原理与函数调用 extindex 机制

传统 VM：编译器产线性字节码，call = push 返回地址 + 跳转到函数入口。kvlang 不用字节码拷贝——**函数体永不被复制，调用 = ExtIndex 将帧根设为扩展索引**指向 `/lib/` 下的指令树。

layoutrwir 在五语言中的对标：

| 语言 | 阶段名 | 做什么 | 产物 |
|------|--------|------|------|
| **C (GCC)** | codegen + assemble + link | AST→GIMPLE→RTL→asm→.o，三个独立工具（cc1/as/ld） | 线性机器码 |
| **Go** | compile (walk + SSA) | AST→SSA→机器码，`gc` 一个二进制全包 | 线性机器码 |
| **Rust** | codegen | MIR→LLVM IR→机器码 | 线性机器码 |
| **Python** | compile | AST→bytecode，`compile()` 内置函数 | 线性字节码 `.pyc` |
| **V8** | bytecode gen (Ignition) + optimizing compile (TurboFan) | AST→Ignition bytecode→TurboFan 机器码 | 线性字节码→机器码 |
| **kvlang** | **layoutrwir** | AST→KV 结构化键值 `[s0,s1]` 二维铺入 kvspace | **树形 KV 键**，可逐槽寻址 |

五种语言都在生成线性序列。kvlang 的 layoutrwir 不是序列化——是**空间布局**：每条指令展开为一组 `[s0,s1]` 坐标，读参负轴、写参正轴、opcode 零点。产物可逐槽 `kv.Get`/`kv.List`，无需反汇编器。

### ExtIndex：帧根指向指令树

函数调用时，不是把指令字节码拷贝到新帧——而是通过 **ExtIndex** 让帧根成为指向 `/lib/` 指令树的扩展索引：

```
编译期（WriteFunc）：
  AST → KV 结构化写入 /lib/main.add/:
    /lib/main.add/[0,0] = "+"      /lib/main.add/[0,-1] = "A"
    /lib/main.add/[0,-2] = "B"     /lib/main.add/[0,1] = "C"
    /lib/main.add/[1,0] = "return"
    /lib/main.add                 = "rwfunc add(A:int,B:int)->(C:int)"  (签名)

调用时：
  kv.ExtIndex(frameRoot+"/", "/lib/main.add/")  # 帧根本身 → /lib/ 指令树
  # 所有帧共享 /lib/ 下同一份指令树，零拷贝
```

HandleCall/HandleReturn 执行机制见 parser篇-06。

### 与传统 VM 的关键差异

| | 传统 VM | kvlang |
|--|---------|--------|
| 代码传递 | copy 字节码到新栈帧 | **ExtIndex**（所有帧共享 /lib/ 下同一份指令树） |
| 帧模型 | 一种帧（call/return） | **两种帧**：rwfunc（函数调用）+ scope（goto/br，详见控制流篇） |
| 崩溃恢复 | 栈帧在内存，进程死即全失 | PC=路径字符串、`frameRoot=返回点落 KV——重启续跑 |
| 可观测 | 需调试器 attach | `kvspace tree /vthread/…` 看 extindex 指向、frameRoot 在哪，局部变量直读帧根 |

**`=` 操作码是值拷贝，不是函数调用**：`a -> b` 编码为 `[s0,0]="="`（值拷贝），
函数调用是 `call(name, args…) → writes`——opcode 位是 `call`，ExtIndex 发生在 HandleCall 内部。
二者在 KV 层无歧义，opcode 位永远不放变量引用（§2.3）。
