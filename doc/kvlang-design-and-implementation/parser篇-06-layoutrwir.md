# Layoutrwir: 装载与执行

import kvspace篇-03-代码指令的布局格式

## lib 树与装载（fix-033/034/039）

**lib 树**：`kvlang layoutrwir` 将多个 `.kv` 文件拼接为单一源→parse→lower→写入 `/lib/`。
每个 `lib name { }` 块形成一个 lib 节点，每个 lib 有且仅有一个 `init` 函数（init 体 + 顶层代码合并）。
`kvlang layoutrwir` 完成后形成一棵 `/lib/` 下的 lib 树。

**CLI 命令**：
- `kvlang run`（无参数）→ 执行 `/lib/.init`（匿名 lib 的 init）
- `kvlang run {childlib}.{func}` → 执行 `/lib/{childlib}.{func}`（`/lib/` 前缀可省略，func 默认 `init`）
- `kvlang layoutrwirandrun <files…>` → 先 load，再 run
- `kvlang layoutrwir <file|dir>` → 多文件拼接为单源→parse→lower→写入 `/lib/`。目录递归收集 `.kv` 并拼接

**跨 lib 调用**：全路径 `/lib/{childlib}.{func}()`，kvcpu 经 `LibIdx` 查 pkg→`LibFunc(pkg, name)` 定位指令树→ExtIndex→执行。无 `import` 关键字——lib 树已在 kvspace 中，调用即路径。

## `def init() -> () { }` 初始化（fix-036）

与普通 def 语法一致，支持 `if`/`while`/`for`/赋值/函数调用。裸顶层代码自动封装为隐式 `def init() -> () { }`。

```kv
lib math {
    def sum(A:int, B:int) -> (C:int) { A + B -> C }
}

def init() -> () {
    /lib/math.sum(3, 4) -> s
    print(s)
}
```

- `lib name { }` 借鉴 C++ `namespace` / Rust `mod`。支持嵌套 `lib a { lib b { … } }`（`a.b`）和扁平 `lib a/b/c { }`
- 每个 lib 注册在 `/lib/<name>.{func}`（`/lib/math.sum`、`/lib/math.init`）
- 无 lib 包裹的 def 属于匿名 lib（路径 `/lib/.{func}`，`kvlang run` 默认执行 `/lib/.init`）
- 源码存储：`WriteFunc` 写入 `/lib/<pkg>.<name>.src`（fix-034），与指令树同目录

## 帧模型与调用栈

函数调用帧、label 帧、系统变量（`.callpc` / `.returnpc` / `.pc`）、HandleCall/HandleReturn/HandleLabel 详见 **[parser&runtime-01 — 控制流](parser&runtime-01控制流篇.md)**。

## `=` 操作码

`a -> b` 编码为 `[s0,0]="="`（值拷贝）。函数调用是 `call(name, args…) → writes`——opcode 位是 `call`。二者在 KV 层无歧义，opcode 位永远不放变量引用。
