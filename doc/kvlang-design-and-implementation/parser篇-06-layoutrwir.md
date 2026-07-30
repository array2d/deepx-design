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
lib math { rwfunc sum(A:int, B:int) -> (C:int) { A + B -> C } }
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
| `rwir` | `/lib/func/[0,0] = "+"` | `writeStmt`（指令读写槽） |
| `rwfunc` | `/lib/func` | `WriteFunc`（函数签名） |

帧类型判定：`funcFrameRoot` 沿帧树向上查找 `.lib` 标记 → `rwfunc` 帧；无 `.lib` → scope 帧。详见 **[控制流篇](parser&runtime-01控制流篇.md)**。

## 帧模型与调用栈

详见 **[parser&runtime-01 — 控制流](parser&runtime-01控制流篇.md)**。

## `=` 操作码

`a -> b` 编码为 `[s0,0]="="`（值拷贝）。函数调用 opcode 位是 `call`。KV 层无歧义，opcode 位永远不放变量引用。
