# coding: utf-8
"""自动续跑（DEEPSEEKER_AUTO_CONTINUE）判定逻辑回归测试。

只测 should_auto_continue() 的三重条件 —— 这是整个机制的**安全阀**：
误判（把正常回复当成撂挑子）会让正常短回复被多追问一轮，比漏判更糟。

跑法：  python tests/test-auto-continue.py

注意：这里**不 import app.py**（会拉起 aiohttp / tokenizer 等重依赖，
且沙箱下读写可疑）。改为直接读源码字符串、在隔离命名空间里 exec
出判定逻辑 —— 保证测的就是 app.py 里那份真代码，而不是副本。
"""

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(os.path.dirname(_HERE), "app.py")

MAX_TOK = 80


def _load_logic():
    """从 app.py 抽出判定逻辑（正则 + should_auto_continue），隔离 exec。"""
    with open(_APP, encoding="utf-8") as f:
        src = f.read()
    start = src.index("_AUTO_CONTINUE_INTENT_RE")
    end = src.index("return bool(_AUTO_CONTINUE_INTENT_RE.search(text))")
    end = src.index("\n", end) + 1
    seg = src[start:end]
    ns = {"re": re, "os": os}
    # 判定函数依赖这几个模块级配置，抽段里没有，按 app.py 默认值补上
    ns["AUTO_CONTINUE_MAX_TOKENS"] = MAX_TOK
    exec(seg, ns)          # noqa: S102 - 测试内固定路径、非用户输入
    return ns["should_auto_continue"]


should_auto_continue = _load_logic()

# (名称, 文本, 输出token数, parsed_tools, 期望触发?)
CASES = [
    # ---------- 必须触发：真实撂挑子原文（来自 2026-10-06 实测） ----------
    ("实测撂挑子①",
     "我发现了几个可疑点，逐一验证。先看 cn_to_int 和 parse_command 的边界。\n\n",
     26, [], True),
    ("实测撂挑子②",
     "这是车牌容错核心。我直接实际测路由和解析的边界情况，找真 bug。\n\n\n",
     21, [], True),
    ("英文宣布计划",
     "Now verify the fix behaves correctly and run the router regression test.\n\n",
     20, [], True),
    ("让我看看",
     "Let me check the row guard implementation.", 15, [], True),
    ("接下来",
     "接下来我需要确认一下时区处理。", 18, [], True),
    ("继续排查",
     "继续排查剩下的边界情况。", 12, [], True),

    # ---------- 必须不触发：正常收尾 ----------
    ("最终总结(无意图词)",
     "已排查完。共发现 3 个 bug，已全部修复并通过回归测试。",
     30, [], False),
    ("纯结论",
     "结论：这段代码没有问题。", 12, [], False),
    ("提交信息",
     "fix: 拒绝第 0/1 行编辑请求", 12, [], False),
    ("空文本", "", 0, [], False),
    ("只有空白", "   \n\n", 2, [], False),

    # ---------- 必须不触发：带工具调用（正常工作轮） ----------
    ("有工具调用(即使文本像撂挑子)",
     "我发现了几个可疑点，逐一验证。先看边界。", 26, [{"name": "read"}], False),

    # ---------- 必须不触发：输出够长（不是撂挑子） ----------
    ("长文本(超阈值)",
     "我发现了几个可疑点，逐一验证。" * 8, 150, [], False),

    # ---------- 边界：刚好低于/等于阈值 ----------
    ("阈值-1 (79)", "接下来看这个。", 79, [], True),
    ("等于阈值 (80)", "接下来看这个。", 80, [], False),
]


def main():
    failed = 0
    print("=== should_auto_continue 判定回归 ===")
    for name, text, out_tok, tools, expect in CASES:
        got = should_auto_continue(text, out_tok, tools)
        ok = (got == expect)
        if not ok:
            failed += 1
        print("[%s] %-30s out=%-4d tools=%d 期望=%-5s 实得=%s"
              % ("PASS" if ok else "FAIL", name, out_tok, len(tools), expect, got))

    print()
    total = len(CASES)
    print("=== %d/%d 通过 ===" % (total - failed, total))
    if failed:
        print("!!! %d 个用例失败 —— 判定逻辑有误判风险，不要部署 !!!" % failed)
        return 1
    print("判定逻辑全部正确。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
