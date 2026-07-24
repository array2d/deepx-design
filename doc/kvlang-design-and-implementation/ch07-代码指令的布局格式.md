# Chapter 7: 代码指令的布局格式

## 6. layoutrwir 的设计原理与函数调用 extindex 机制

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

### 6.1 函数调用 Link 机制

```
调用 add(3,4) -> s 的完整链路：

编译期（load 时，WriteFunc）：
  AST → KV 结构化写入 /lib/main.add/:
    /lib/main.add/[0,0] = "+"      /lib/main.add/[0,-1] = "A"
    /lib/main.add/[0,-2] = "B"     /lib/main.add/[0,1] = "C"
    /lib/main.add/[1,0] = "return"
    /lib/main.add                 = "def add(A:int,B:int)->(C:int)"  (签名)

调用时（HandleCall）：
  1. kv.Get("/lib/main.add") → 签名 → 解析参数名
  2. frameRoot = callPC              # /vthread/42/[3,0]
  3. kv.ExtIndex(frameRoot+"/", "/lib/main.add/")  # ★ extindex：帧根本身 → /lib/ 指令树
  4. 读参零拷贝：存储调用方值的绝对路径到 .rparam/<name>，CPU 读参时直接从此路径读取
  5. 写参零拷贝：存储调用方写目标的绝对路径到 .wparam/<name>，CPU 写参时直接写入此路径
  6. kv.Set(frameRoot+".rootfunc", funcName)    # 入口函数名
  7. kv.Set(frameRoot+".ro", paramList)          # 只读参数名单（fix-027）
  8. 返回 frameRoot+"/[0,0]"                     # 子帧第一条指令 PC

返回时（HandleReturn）：
  1. 写参已在子帧执行期间经 .wparam 直写父帧，无需搬运
  2. frameRoot 即 callPC，NextPC(frameRoot) 恢复父帧下一条指令
  3. kv.UnLink(frameRoot+"/")                           # 移除 extindex
  4. kv.DelTree(frameRoot)                         # 清整个子帧

TCO（goto/br）：不建子帧，仅 Unlink + ExtIndex 换帧根 extindex 指向目标块（.rootfunc 保持根函数名）。
顶层调用（Bootstrap）：frameRoot 即 callPC，直接 ExtIndex frameRoot → funcKey。
```

### 6.2 与传统 VM 的关键差异

| | 传统 VM | kvlang |
|--|---------|--------|
| 代码传递 | copy 字节码到新栈帧 | **ExtIndex**（所有帧共享 /lib/ 下同一份指令树） |
| TCO | 需特殊优化（复用帧 + 重定向参数） | Unlink + ExtIndex，已有的 extindex 机制天然支持 |
| 崩溃恢复 | 栈帧在内存，进程死即全失 | PC=路径字符串、`frameRoot=返回点落 KV——重启续跑 |
| 可观测 | 需调试器 attach | `kvspace tree /vthread/…` 看 extindex 指向、frameRoot 在哪，局部变量直读帧根 |

**`=` 操作码是值拷贝，不是函数调用**：`a -> b` 编码为 `[s0,0]="="`（值拷贝），
函数调用是 `call(name, args…) → writes`——opcode 位是 `call`，ExtIndex 发生在 HandleCall 内部。
二者在 KV 层无歧义，opcode 位永远不放变量引用（§2.3）。
