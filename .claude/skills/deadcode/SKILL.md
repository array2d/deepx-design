---
name: deadcode
description: Go 死代码检测工具使用指南。分析未使用的函数、变量、不可达代码时使用。
---

# Go 死代码检测

## deadcode — 全程序未使用符号

```bash
~/go-pkg/bin/deadcode ./...                    # 当前模块
~/go-pkg/bin/deadcode -test=false ./...        # 跳过测试文件
~/go-pkg/bin/deadcode -filter '<pkg>' ./...    # 只看指定包
```

输出格式：`<file>:<line>:<col>: <msg>`。

## staticcheck — 更多检查项

```bash
~/go-pkg/bin/staticcheck ./...                 # 全部检查
~/go-pkg/bin/staticcheck -checks SA ./...      # 只看 staticcheck 规则
~/go-pkg/bin/staticcheck -explain SA4006       # 解释某条规则
```

**常用规则**：
| 编号 | 含义 |
|------|------|
| SA4006 | 值赋值后未使用 |
| SA4017 | 纯函数返回值被丢弃 |
| SA4023 | 不可能的比较（如 `== nil` 与非指针） |
| U1000 | 未使用的常量/变量/函数/类型 |

## go vet — 编译器内置

```bash
go vet ./...                      # 不可达代码、printf 格式错误等
```

## kvlang 的死代码检测

kvlang 不需要单独的死代码 pass。Compile 阶段做依赖分析时自然得出：

- rwir 写参无人读 → 死代码（IR 级）
- 函数不被任何调用路径引用 → 死函数
- `rwir/` 声明无对应调用 → 未使用符号

依赖图即可达性分析，不需额外工具。
