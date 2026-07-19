#!/usr/bin/env python3
"""agent_eval — 以 deep-dive.md 为教学文档，验证空记忆模型对 kvlang 设计实现的理解。

用法:
  export KVLANG_EVAL_API_BASE=https://...   # OpenAI 兼容 API base（不含 /v1）
  export KVLANG_EVAL_API_KEY=sk-...
  export KVLANG_EVAL_MODEL=qwen3.7-plus     # 可选
  python3 doc/kvlang/agent_eval.py

对每个任务：deep-dive.md + 设计问题 → LLM 用自己的话回答 → 与 kvlang 实际实现对比。
回答保存于 /tmp/agent_eval/answers/。
"""
from __future__ import annotations
import json, os, re, sys, urllib.request, uuid
from pathlib import Path

DOC = Path(__file__).resolve().parent / "deep-dive.md"
OUT = Path("/tmp/agent_eval")
ANSWER_DIR = OUT / "answers"

API_BASE = os.environ.get("KVLANG_EVAL_API_BASE", "").rstrip("/")
API_KEY  = os.environ.get("KVLANG_EVAL_API_KEY", "")
MODEL    = os.environ.get("KVLANG_EVAL_MODEL", "qwen3.7-plus")

SYSTEM = """你是 kvlang 架构评审员。你刚阅读了 kvlang 的 deep-dive.md 设计文档。
现在用你自己的话回答问题。如果文档对某个点没有说明或说得不清楚，请明确指出。
不要套用其它语言的默认假设——严格按照文档中的描述推理。"""

# (任务名, 问题) — 覆盖 deep-dive 核心设计点，每个问题对应 kvlang 实际实现的关键决策
QUESTIONS = {
    "01_rwir_model": """
kvlang 称"函数没有返回值"。请用你自己的话解释：
1. 既然没有返回值，调用 add(3,4) -> s 中的 -> s 是什么意思？
2. 如果 add 有两个写参，调用方怎么写？
3. 这和 Go 的 `func add(a, b int) int` + `s := add(3,4)` 在设计思路上有什么根本区别？
""",

    "02_instruction_slot": """
kvlang 指令在 KV 树中占据二维坐标 [s0,s1]。请回答：
1. [s0,0]、[s0,-1]、[s0,1] 分别存什么？
2. 为什么用负数表示读参、正数表示写参？
3. Decode 指令时怎么知道这条指令有多少个读参和写参？
""",

    "03_call_link": """
kvlang 的函数调用不是"把指令复制到子帧"，而是"软链接"。请回答：
1. HandleCall 的核心操作是什么？（写出关键步骤，不需要记具体函数名）
2. 同一条函数被多个 vthread 同时调用，它们是否共享同一份指令树？为什么？
3. TCO（尾调用优化）为什么在 Link 机制下特别简单？
""",

    "04_readonly_param": """
文档 §3.2 有"读参只读公理"。请回答：
1. `def f(A: int) -> () { 42 -> A }` 会通过编译吗？为什么？
2. 如果我想在函数体内修改一个从调用方传入的值然后返回，正确的签名应该怎么写？
3. 这个设计在崩溃恢复场景下有什么好处？
""",

    "05_local_variable": """
kvlang 的局部变量是裸名（不再用 ./ 前缀）。请回答：
1. `A + B -> C` 中，A、B、C 分别是哪种 slot？
2. 局部变量 C 的 kvspace 路径是怎么构成的？
3. "变量名即指针"是什么意思？这解释了为什么不需要 `&` 取址？
""",

    "06_lib_namespace": """
文档 §0.6 介绍了 lib 命名空间。请回答：
1. `lib math { def sum(A,B)->(C) {...} }` 后，sum 在 kvspace 中存成什么路径？
2. 调用 sum 时用什么名字？是 `sum(3,4)` 还是 `math.sum(3,4)`？
3. 无 lib 包裹的 def 会发生什么？
""",

    "07_write_slot_rules": """
写槽有严格的规则。请回答：
1. 哪些东西可以作为写槽？（列举三种合法形态）
2. 哪些不能？（列举两种非法形态）
3. `=` 和 `->` 在语义上有什么关系？
""",

    "08_kv_function_storage": """
函数编译后存到 kvspace。请回答：
1. 函数体指令存在哪个 KV 域下？路径格式是什么？
2. 函数名到包的映射存在哪里？
3. 运行时调用函数，kxvpu 怎么找到函数体？
""",

    "09_param_dedup": """
文档 §8 提到"参数不得同名"。请回答：
1. `def f(A:int) -> (A:int)` 这个签名为什么非法？
2. `def g(A:int, B:int) -> (C:int)` 中，函数体内写 `B -> C` 是否合法？为什么？
3. 断点后重启续跑时，读参不变性保证了什么？
""",

    "10_system_variables": """
kvspace 中有以 `.` 开头的系统变量（§12）。请回答：
1. `.pc` 和 `.callpc` 是什么？它们有什么区别？
2. 用户代码能直接写 `kv.Set("/vthread/7/.pc", ...)` 吗？为什么？
3. 帧的 `.fn` 键存的是什么？为什么它用 Link 而不是 Set？
""",
}


def chat(question: str) -> str:
    sid = str(uuid.uuid4())
    doc_text = DOC.read_text()
    req = urllib.request.Request(
        API_BASE + "/v1/chat/completions",
        data=json.dumps({
            "model": MODEL,
            "temperature": 0,
            "user": sid,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"请阅读以下 kvlang 设计文档，然后回答问题。\n\n---\n{doc_text}\n---\n\n问题：\n{question}\n\n请用自己的话逐一回答。如果文档没有说清楚某个点，请指出。"},
            ],
        }).encode(),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "X-Session-Id": sid,
            "Connection": "close",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        body = json.loads(r.read())
    return body["choices"][0]["message"]["content"]


def main() -> None:
    if not API_BASE or not API_KEY:
        sys.exit("需设置 KVLANG_EVAL_API_BASE / KVLANG_EVAL_API_KEY 环境变量")
    ANSWER_DIR.mkdir(parents=True, exist_ok=True)

    for name, question in QUESTIONS.items():
        try:
            answer = chat(question)
        except Exception as e:
            print(f"❌ {name}: API 失败 {e}")
            continue
        path = ANSWER_DIR / f"{name}.md"
        path.write_text(f"# {name}\n\n## 问题\n\n{question.strip()}\n\n## 模型回答（{MODEL}）\n\n{answer}\n")
        print(f"✅ {name} → {path}")

    print(f"\n══ {len(QUESTIONS)} 个问题已回答，见 {ANSWER_DIR}/ ══")
    print("下一步：人工比对模型回答 vs kvlang 实际实现，修正 deep-dive.md 中的歧义/缺失。")


if __name__ == "__main__":
    main()
