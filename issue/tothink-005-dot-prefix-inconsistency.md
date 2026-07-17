# Tothink-005: `./` 前缀在文档和示例中不一致

**严重程度**：🟡 中 — agent 从不同文档学到的语法互相矛盾。

---

## 现象

**dev-guide.md §2.2**：
```kvlang
def add(A:int, B:int) -> (C:int) {
    A + B -> './C'       # ./C 带点前缀
}
```

**tutorial/02-func/main.kv**（实际可运行的示例）：
```kvlang
def add(A:int, B:int) -> (C:int) {
    A + B -> C           # 裸 C 无前缀
}
```

两份权威文档给出不同的写法。

## 根本原因

`./` 前缀反映 KV 路径模型（帧内相对路径：`/vthread/1/[3,0]/C`）。裸标识符 `C` 是 parser/layoutcode 层的便利简写 — 解析时自动补全为帧内路径。但文档没有统一说明哪个是 canonical。

## 影响

1. agent 从 dev-guide 学到 `./C`，写出来可能无法运行（或行为与 tutorial 不同）
2. 如果两种都合法，agent 需要知道区别 — 有没有同名遮蔽的场景？
3. 新 agent 上手第一件事就是遇到文档矛盾

## 建议

统一文档和示例。推荐：**裸标识符为 canonical**（与 tutorial 一致）。`./` 前缀保留用于少数需要显式区分同名变量的场景。dev-guide 修正为与 tutorial 一致。
