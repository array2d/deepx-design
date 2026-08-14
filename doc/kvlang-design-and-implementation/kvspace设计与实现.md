# KVSpace redis-impl设计与实现

## 第零部分:我的原则

绝对上帝设计美学：代码必须按我定义的清晰优雅的方式运行，非我的定义，直接fatal/panic，绝不容许用控制流if包庇错误。
绝不容许代码以奇怪的方式成功运行，就像用四个乳头走路的牛，必须立即杀死这样的牛。
写代码要像疯子一样洁癖偏执，让代码走唯一的正确方向，其它方向直接fatal/panic，就像大树只容许主干向上生长，要砍掉所有分支，这些分支都是累赘！是bug的温床！。

## 第一部分：基础模型

### 1. 核心概念

+ **文件 key**：普通值 key，例如 `/a/b` → `int64:42`
+ **目录 key**：以 `/` 结尾，value 为 Index（子名列表），例如 `/a/` → `index: ["b"]`
+ **dict 目录 key**：以 `.` 结尾，value 为 DictIndex，隔离于父 `/` 目录，例如 `/a/obj.` → `dict: ["x", "y"]`
+ 同一路径下 `/a` 和 `/a/` 可独立共存（文件与目录同名）

KVSpace 是文件系统风格的 KV 存储抽象。`/` 管层级，`.` 管成员。

### 2. XValue 类型系统

TLV 编码格式：

```
[1B kind_len][N B kind_name][1B isptr][4B arraylength LE][4B raw_len LE][M B raw]
isptr=0 → raw 为类型化数据
isptr=1 → raw 为目标 key 路径（软链接）
```

**kind 继承树**：`uint8` 为基类，`ElemSize(kind)` 判定定长成员，`IsByteDerived(kind)` 判定继承。

| kind | 说明 | elemSize |
|------|------|----------|
| `uint8` | 基础字节 | 1B |
| `bool`, `int8`, `stringbyte` | → uint8 | 1B |
| `int16`, `uint16` | → uint8 | 2B |
| `int32`, `uint32`, `float32` | → uint8 | 4B |
| `int64`, `uint64`, `float64` | → uint8 | 8B |
| `dict` | dict 成员目录 | — |
| `index` | 通用目录 | — |
| `extindex` | 扩展索引 | — |
| `None` | 空值 | — |

**Ptr（软链接）**：kind=目标类型，isptr=1，raw=目标路径。Set 写入 `*kind:target`。读/写/List 透明穿透，Del 末段作用于链接本体。

### 3. Dict 成员目录（`.`）

`a.`（尾 `.`）是 dict 目录，等价于 `a/`（尾 `/`）之于是常规目录。dict 目录独立于父 `/` 目录：

```
/dir/      → index: ["obj.", "other"]
/dir/obj.  → dict: ["x", "y"]
/dir/obj.x → int64:42
```

`SplitDictParent` 自动检测 member access 并路由 child 到 dict 目录。`JoinPath` 对 `.` 尾缀直接拼接。

### 4. ExtIndex

ExtIndex 是写时复制叠加层。`ExtIndex("/merge/", "/base/")` 后：
- 读 `/merge/x`：先查本地，后回落 `/base/x`
- 写 `/merge/x`：写入本地，不影响 `/base/`
- `List("/merge/")`：合并本地 + 扩展目标

不容许级联（exttarget 本身不能是 extindex）。

### 5. Arridx（数组零拷贝读写）

`KVPair.Arridx` 和 `Get(..., arridx)` — arridx<0 读写全数组，arridx>=0 直接定位 raw[arridx] 位置。
`SliceElem` / `WriteElem` 基于 elemSize 计算偏移，零拷贝。

## 第二部分：操作与索引

kvspace tree
    对子成员中的二维地址[s0,s1],需要用table打印。这是kvlang所需要的重要的栈指令二维布局，二维打印更直观。

完成后补充

## 第三部分：辅助设施

### 1. Watch / Notify

redis-impl
Watch/Notify 用 Redis BLPOP/LPUSH 实现一次性通知，link 路径穿透解析。

### 2. 编码与工具函数

`JoinPath` 拼路径避免 `//`，
`SepPath` 拆路径为前缀+末段。

### 3. Redis 日志

Redis 日志由 `KVSPACE_REDIS_LOG` 控制等级：1=命令名，2=完整参数+耗时。

go-redis Hook 在每条命令前后记录，pipeline 显示批次数和总耗时。

### 4. 测试与构建

tutorial/ 下的 .sh 脚本头部 `# expected:` 注释预期输出，test.py 自动执行并对比。

`make build` 编译 kvspace 到 `~/.local/bin/kvspace`。

### 5.严禁hardcode
不容许grep到乱丢的字符串，必须集中在const.go