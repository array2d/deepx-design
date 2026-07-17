# Tothink-001: if/while 条件不支持复合表达式

**严重程度**：🔴 高 — 每条条件判断产生 1-2 行额外的命名变量。

---

## 现象

```kvlang
n % d -> rem          # 必须命名中间值
rem == 0 -> divisible # 必须命名布尔条件
if (divisible) {      # 最终才能 if
    ...
}
```

agent 直觉（不被支持）：
```kvlang
if (n % d == 0) {     # 一行搞定
    ...
}
```

## 影响

fizzbuzz 的 3 个条件判断需要 6 行中间变量：`m3→d3`, `m5→d5`, `d3 && d5→fb`。

prime_sieve 的每条判断都多出 2 行：`n % d -> rem; rem == 0 -> divisible; if (divisible)`。

agent 生成代码时必须记住"每个条件步骤→命名→再用 `if` 检查"的三步模式，认知负荷显著高于直接写条件表达式。

## 根本原因

读写码约束要求每条指令的操作数为叶节点（issue 002 的设计决策）。
`if (n % d == 0)` 中，`n % d` 的结果没有具名写槽。

## 建议

lower 层支持 `if (expr)` 和 `while (expr)` 自动展开。agent 写自然条件表达式，lower 注入临时变量 `_cond_N` 和必要的中间步骤。

```kvlang
# agent 写：
if (n % d == 0) { ... }

# lower 展开为：
n % d -> _t0
_t0 == 0 -> _cond_0
if (_cond_0) { ... }
```
