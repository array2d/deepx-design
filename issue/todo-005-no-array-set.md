# 005: 数组只读，无法原地修改

**严重程度**: 高 — 阻断所有需要原地修改数组的算法

**现象**: 只有 `at(arr, i)` 读取，没有 `set(arr, i, val)` 写入。
Two-sum 不需要原地修改，但 Remove Element (#27)、Plus One (#66)、
Merge Sorted Array (#88) 等全部依赖原地修改。

**当前 workaround**: 无。只能创建新数组，但也没有 "build new array in loop" 的能力
（因为每次 `array(...)` 需要知道全部元素）。

**建议**: 增加 `set(arr, i, val)` builtin，修改数组指定位置的元素。
