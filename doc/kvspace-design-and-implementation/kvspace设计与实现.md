# KVSpace redis-impl设计与实现

## 第零部分:我的原则

绝对上帝设计美学：代码必须按我定义的清晰优雅的方式运行，非我的定义，直接fatal/panic，绝不容许用控制流if包庇错误。
绝不容许代码以奇怪的方式成功运行，就像用四个乳头走路的牛，必须立即杀死这样的牛。
写代码要像疯子一样洁癖偏执，让代码走唯一的正确方向，其它方向直接fatal/panic，就像大树只容许主干向上生长，要砍掉所有分支，这些分支都是累赘！是bug的温床！。

## 第一部分：基础模型

### 1. 核心概念

+ 值key：也叫文件，普通key
+ 索引key：也叫目录，index，包括extindex。其xvalue bytes内存储着下级目录直接子名列表，用换行符(\\n)拼接，以 Redis string SET 命令存储。不再使用 Redis Set 数据结构。

KVSpace 是文件系统风格的 KV 存储抽象，当前是Rediscli实现。

路径 `/a/b` 的值存为 Redis key `/a/b`。

目录（index，包括extindex），必须以/结尾 `/a/` 存为 Redis string key，value 是 XValue bytes。

link可以是目录和文件key

/a 是 string key（文件值），/a/ 是 string key（目录索引），独立共存

`/` 是根目录，`/` 的 string key 存顶级条目名。

### 2. XValue 类型系统

XValue必须有kind类型，包括kindnull类型。

XValue 是带 kind 标签的不可变值，TLV 编码存入 Redis。

TLV 编码格式。实际是 [1B kind_len][N B kind_name][4B arraylength LE][4B raw_len LE][M B raw]

普通xvalue结构定义简单，请直接看代码

index类型的kind，value=换行符拼接的成员名字符串

KindIndex     = "index"，
KindLinkIndex = "linkindex" // 纯链接，写穿透到目标
KindExtIndex  = "extindex"  // 扩展索引，bytes首行为=extpath，后续行为自身成员

### 3. Link / ExtIndex

Link(target, linkpath)，link可以理解为文件系统软链接
kind=KindLinkIndex,xvalue=存string即可"targetpath"，link来说，target和linkpath要么都是值key，要么都是目录key以/结尾。


ExtIndex(path, extpath)，ExtIndex可以理解为写时复制的叠加层
    一句话代码正确理解ExtIndex：list(extindexpath)=append(extindex.value[1:],list(extindex.exttarget))
    只能是目录,如extindex="/a/a2/",exttargetindex="/lib/funca/"，下级key(如如 "/a/a2/be")的读写访问，均跳转/lib/funca/be
ExtIndex是index，exttargetindex也是index，key必须都以/结尾
key不容许在2者重复！（extIndex自身存在的key，不容许出现在exttargetindex中）所以读操作覆盖二者，下级成员的写/创建/删除操作都只更新extIndex自身
逻辑上，我们通常不对extIndex/下的只读路径（也就是exttargetindex内的成员）执行任何写操作，一旦操作，需要直接panic，警告开发者，强制修改
kind=KindExtIndex,xvalue bytes格式：首行为 "=exttargetindex_path"，后续行为自身成员名，换行符分隔。例：`=/lib/funca/\nnode1\nnode2`
只容许有1个exttargetindex, exttargetindex_path必须是首个。
extindex 不容许级联,exttargetindex必须是普通index
exttargetindex前缀符号=定义在const文件！

ExtIndex是kvlang的函数调用开辟新栈的需求，中间变量需要写入ExtIndex，而指令则直接来自/lib/funca/下，而/lib是代码库函数



### 4.kvspace
kvspace是一个完全分布式的元数据存储，禁止kvspace client本地存储任何信息。

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