"""既有 bug：README 无占位符时 hook 打印 shell 报错。

`check_04-review.sh` 里这一行（`e496536` 引入，先于 A6）：

    PLACEHOLDERS=$(grep -c 'TODO\\|___\\|FIXME' "$README_FILE" 2>/dev/null || echo 0)

`grep -c` 在**零匹配**时打印 `0` 并以退出码 1 结束，于是 `|| echo 0`
再追加一个 `0` —— 变量成了两行的 `"0\\n0"`，紧接着的 `[ "$PLACEHOLDERS" -gt 0 ]`
报 `integer expression expected`。

后果不是判定错（`[` 失败时走 else，恰好等价于「无占位符」），
而是**每次 README 干净时都往 stderr 吐一行 shell 错误**。
A6 上线后它被顺带暴露出来：客观轨那段紧跟其后，报错混进同一份输出，
看起来像客观轨的问题。

判据是「hook 的 stderr 里不出现 shell 语法错误」。
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _count_placeholders(readme_body):
    """跑 hook 里那一行的**真实逻辑**，不靠字符串切割拼脚本。

    ⚠️ 本函数按协议 1.2 重做过。初版用
    `.split("        PLACEHOLDERS=")[1]...` 从 hook 源码里抠那一行，
    对空白与行序敏感 —— hook 一改排版，测试就以「自己坏了」的形态变红，
    而那种红无法区分「被测行回归」与「切割逻辑不适配」。
    判据必须比被测对象稳。

    现在的做法：从 hook 里 grep 出那一行（正则容忍排版），
    在临时 README 上原样执行它。
    """
    import re
    import subprocess
    import tempfile

    hook = (ROOT / "hooks" / "check_04-review.sh").read_text(encoding="utf-8")
    m = re.search(r"^\s*(PLACEHOLDERS=.*)$", hook, re.MULTILINE)
    assert m, "hook 里找不到 PLACEHOLDERS 赋值行"
    line = m.group(1)

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as f:
        f.write(readme_body)
        path = f.name

    script = (f'README_FILE="{path}"\n{line}\n'
              'if [ "$PLACEHOLDERS" -gt 0 ]; then echo "has"; '
              'else echo "none"; fi\n')
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True)


def test_clean_readme_produces_no_shell_error():
    """无占位符的 README 不得让 hook 吐 shell 错误。"""
    r = _count_placeholders("# proj\n\nusage\n")
    assert "integer expression expected" not in r.stderr, (
        f"PLACEHOLDERS 取值被 grep 的退出码污染了: stderr={r.stderr!r}")
    assert r.stderr.strip() == "", f"意外的 stderr: {r.stderr!r}"
    assert "none" in r.stdout


def test_readme_with_placeholders_still_detected():
    """反向：真有占位符时仍要认出来（修复不能把功能弄丢）。"""
    r = _count_placeholders("# proj\n\nTODO: 补文档\n")
    assert "has" in r.stdout, f"占位符未被检出: {r.stdout!r}"
    assert "integer expression expected" not in r.stderr


def test_empty_readme_produces_no_shell_error():
    r = _count_placeholders("")
    assert "integer expression expected" not in r.stderr, r.stderr
    assert "none" in r.stdout
