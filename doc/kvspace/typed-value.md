 
# kvspace 类型化 Value 设计 (vtype 集成版)
 

## 编码格式: 统一 TLV
 
```go
type KVSpace interface {
    Get(key string) (Value, error)             // was (string, error)
    Gets(keys ...string) ([]Value, error)      // was ([]string, error)
    Set(key string, value Value) error         // was (key string, value any)
    Sets(kvs map[string]Value) error           // was (map[string]any)
    Del(keys ...string) error                  // 不变
    DelR(prefix string) error                  // 不变
    List(prefix string) ([]string, error)      // 不变
    Watch(key string, timeout time.Duration) (Value, error) // was (string, error)
    Notify(key string, value Value) error      // was (key string, value any)
    Link(target, linkpath string) error        // 不变
    Unlink(linkpath string) error              // 不变
    DisConn() error                            // 不变
}
` 
 