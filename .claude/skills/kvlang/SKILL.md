---
name: kvlang
description: kvlang CLI 命令参考。使用 kvlang 命令行工具时查阅。
---

# kvlang 命令

## run — 执行代码

```bash
kvlang <file.kv|dir>          # 加载并执行（目录递归 *.kv）
kvlang -c "code"              # 内联代码直接执行
echo "code" | kvlang           # 管道输入
kvlang {lib}.{func}            # 调用 /lib/ 下函数（如 kvlang main.add）
kvlang                         # 无参数 + stdin 终端 → 执行 .init
```

**flag**：
| flag | 默认 | 说明 |
|------|------|------|
| `--kvspace` | `redis://127.0.0.1:6379` | KVSpace DSN |
| `--debug` | false | 单步调试，每条 rwir 暂停、可查看变量 |

**环境变量**：`KVLANG_KVSPACE` 指定默认 DSN。

## layout — 加载到 kvspace，不执行

```bash
kvlang layout <file.kv|dir>...
```

flag：`--kvspace`。

## layoutandrun — layout + run

```bash
kvlang layoutandrun <file.kv|dir>...
```

flag：`--kvspace`。

## vet — 语法检查

```bash
kvlang vet <file.kv>
kvlang vet -c "code"
echo "code" | kvlang vet
```

## format / fmt — 格式化

```bash
kvlang format <file.kv>       # 输出到 stdout
kvlang format -w <file.kv>    # 原地写入（对标 gofmt -w）
kvlang format -c "code"       # 格式化内联代码
echo "code" | kvlang format    # 管道输入
```

## ps — 列出虚拟线程

```bash
kvlang ps
```

输出格式：`VTID  STATUS  PC`（如 Linux `ps`）。flag：`--kvspace`。

## 环境

- `GOPROXY`：Makefile 设为 `https://goproxy.cn,direct`
- `KVLANG_KVSPACE`：默认 kvspace DSN（可被 `--kvspace` 覆盖）
- 构建产物：`./kvlang`，`make build` 安装到 `~/.local/bin/`
