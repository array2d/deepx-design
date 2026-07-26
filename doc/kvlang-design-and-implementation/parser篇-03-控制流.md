# Chapter 5: Control Flow（控制流）

import kvlang-design-and-implementation

## 4. 控制流的 KV 寻址优势

### 4.1 label 即路径

```
def 分支示例(flag, X) -> (R) {
    entry: { X + 1 -> a; br(flag, then, else) }
    then:  { a * 2 -> b; goto(merge) }
    else:  { a * 3 -> b; goto(merge) }
    merge: { b + 10 -> R; return }
}
```

label `then` 不是符号表条目，是 KV 路径段：

```
/vthread/tid/[0,0]/entry/[0,0]  = "+"
/vthread/tid/[0,0]/entry/[0,-1] = "X"
/vthread/tid/[0,0]/entry/[0,1]  = "a"

/vthread/tid/[0,0]/then/[0,0]   = "*"
/vthread/tid/[0,0]/merge/[0,0]  = "+"
```

`goto(merge)` → `PC = funcRoot + "/merge/[0,0]"` → **零查表，零计算，纯字符串拼接**。

### 4.2 label = 无参 call

```
goto(merge)  ≡  call(父函数/merge)   ← 相同语义，不同语法
```

block 就是无参函数。控制流统一为 `call` + `return`，无需 `jmp`/`br`/`goto` 等额外原语。lower 阶段将 `goto`/`br` 降级为 `call(block_label)`，kvcpu 不感知结构化控制流。

### 4.3 与传统对比

| 操作 | x86 | Python | kvlang |
|------|-----|--------|--------|
| 条件跳转 | `cmp; je label` | `POP_JUMP_IF_FALSE` + offset | `br(cond, t, f)` → `call(t)` or `call(f)` |
| 无条件跳转 | `jmp label` | `JUMP_ABSOLUTE` + offset | `call(then)` |
| 函数调用 | `call addr` | `CALL_FUNCTION` | `call(funcName)` |
| 返回 | `ret` | `RETURN_VALUE` | `return` (PC 回父路径) |

kvlang 不区分"跳转"和"调用"——label block 就是无参函数，控制流就是 `call`/`return`。
