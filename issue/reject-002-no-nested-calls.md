# 002: 禁止函数嵌套调用

**严重程度**: 高 — 大幅增加代码行数，降低可读性

**现象**: `int(n / 10)` 报错 "nested expression as argument is not allowed"

**必须写**:
```
n / 10 -> t
int(t) -> n
```

`print("x =", x)` 这种简单嵌套是合理的，但限制过于严格。写一个简单的公式需要 3-4 行中间变量，LeetCode 风格的函数体膨胀 3 倍。

**建议**: 至少允许单层嵌套: `int(n / 10) -> n`。或者在语义层面自动 flatten。
