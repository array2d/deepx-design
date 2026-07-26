# KVLang: Design and Implementation

## 目录

### 总篇

- [01 — 程序 = 数据结构 + 函数 + 数据](总篇-01-存算控制流严格分离的kv树计算架构.md)

### kvspace篇 — KV 作为统一地址空间

- [01 — 设计宪法](kvspace篇-01-设计宪法.md)
- [02 — 寻址模型与命名](kvspace篇-02-寻址模型与命名.md)
- [03 — 代码指令的布局格式](kvspace篇-03-代码指令的布局格式.md)
- [04 — 系统变量](kvspace篇-04-系统变量.md)

### parser篇 — 源码 → KV 存储

- [01 — 指令架构](parser篇-01-指令架构.md)
- [02 — 函数](parser篇-02-函数.md)
- [03 — 控制流](parser篇-03-控制流.md)
- [04 — 编译器流水线](parser篇-04-编译器流水线.md)
- [05 — 诊断输出](parser篇-05-诊断输出.md)

### runtime篇 — KV 存储 → 执行

- [01 — 类型系统](runtime篇-01-类型系统.md)
- [02 — 成员访问与数据结构](runtime篇-02-成员访问与数据结构.md)
- [03 — 调试与可观测性](runtime篇-03-调试与可观测性.md)
- [04 — 执行模型](runtime篇-04-执行模型.md)

### 附录

- [设计决策总结](apx-design-decisions-summary.md)
