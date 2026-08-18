# Layoutrwir: 装载与执行

import kvspace篇-03-代码指令的布局格式

## lib 树与装载（fix-033/034/039）

**lib 树**：`kvlang layoutrwir` 将多个 `.kv` 文件拼接为单一源→parse→lower→写入 `/lib/`。
每个 `lib name { }` 块形成一个 lib 节点，每个 lib 有且仅有一个 `init` 函数（init 体 + 顶层代码合并）。

**CLI 命令**：
- `kvlang run`（无参数）→ 执行 `/lib/.init`（匿名 lib 的 init）
- `kvlang run {childlib}.{func}` → 执行 `/lib/{childlib}.{func}`（`/lib/` 前缀可省略，func 默认 `init`）
- `kvlang layoutrwirandrun <files…>` → 先 load，再 run
- `kvlang layoutrwir <file|dir>` → 多文件拼接为单源→parse→lower→写入 `/lib/`

**跨 lib 调用**：全路径 `/lib/{childlib}.{func}()`，无 `import` 关键字——lib 树已在 kvspace 中，调用即路径。

## `rwfunc init() -> () { }` 初始化（fix-036）

裸顶层代码自动封装为隐式 `rwfunc init() -> () { }`。

```kv
lib math { rwfunc sum(A:int64, B:int64) -> (C:int64) { A + B -> C } }
rwfunc init() -> () { /lib/math.sum(3, 4) -> s; print(s) }
```

- `lib name { }` 借鉴 C++ `namespace` / Rust `mod`
- 每个 lib 注册在 `/lib/<name>.{func}`（`/lib/math.sum`、`/lib/math.init`）
- 无 lib 包裹的 rwfunc 属于匿名 lib（路径 `/lib/.{func}`）
- 源码存储：`WriteFunc` 写入 `/lib/<pkg>.<name>.src`（fix-034）

## XValue Kind

`/lib/` 下 XValue kind：

| Kind | 示例 | 写入者 |
|------|------|--------|
| `rwir` | `/lib/func/[1,0] = "+"` | `writeStmt`（指令读写槽） |
| `rwfunc` | `/lib/func/[0,0] = r2/w1` | `WriteFunc`（函数签名，body=[nr\|nw]） |
| `ptr`（isptr=1） | `/lib/func/a = →[0,-1]` | `WriteFunc`（命名参数→slot 映射） |

帧类型判定：`funcFrameRoot` 沿帧树向上查找 `‥lib` 标记 → `rwfunc` 帧；无 `‥lib` → scope 帧。详见 **[控制流篇](parser&runtime-01控制流篇.md)**。

## WriteFunc：函数布局

```
WriteFunc(kv, pkg, fn):
  1. funcDir = /lib/<pkg>.<name>/
  2. kv.Set(funcDir+"/[0,0]", Rwfunc(nr, nw, al=numInsts))     ← 函数签名
  3. for each read param p: kv.Set(funcDir+"/p.Name", Ptr("[0,-j]", 1))
  4. for each return param r: kv.Set(funcDir+"/r.Name", Ptr("[0,+j]", 1))
  5. WriteBody(kv, pkg, name, body, typeMap, offset=1)          ← 指令从 [1,0] 开始
```

**EntryPC**：`root/[1,0]`（函数定义占 row 0）。

## HandleCall：运行时调用

```
HandleCall(ctx, kv, pc, inst):
  1. funcDir = /lib/<pkg>.<name>/
  2. GetOne(funcDir+"[0,0]") → Rwfunc(nr, nw)                  ← 读计数，无字符串解析
  3. List(funcDir) → 过滤非"["前缀 → [a, b, c, ...]             ← 命名参数列表
  4. ExtIndex(frameRoot, funcDir)                                ← 帧→指令树
  5. Set(.returnpc, .callpc, .lib)                               ← 系统变量
  6. for i in 0..nr: 读 inst.Reads[i+1] 实参→写 frameRoot/[0,-(i+1)]
  7. for i in 0..nw: 读 inst.Writes[i] 写槽→写 frameRoot/[0,(i+1)]
  8. return EntryPC(frameRoot)                                   ← [1,0]
```

**不再使用**：`parser.ParseFuncSig`（替换为 slot 读取）、`‥rparam/‥wparam` 索引（替换为 Ptr 链）。

## 帧模型与调用栈

详见 **[parser&runtime-01 — 控制流](parser&runtime-01控制流篇.md)**。

## `=` 操作码

`a -> b` 编码为 `[s0,0]="="`（值拷贝）。函数调用 opcode 位是 `call`。KV 层无歧义，opcode 位永远不放变量引用。
