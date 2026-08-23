"""e2e 必须只跑 MockAgent —— 防止真实 agent 模式偷偷回来。

`driver.py --no-mock` 与 `sw test --no-mock` 已删除：那两条路径依赖模型输出，
同一份代码两次运行结果不同，失败无法区分是回归还是模型这次答得不一样。

这类删除很容易被"顺手加回来"（看着只是多一个开关），而代价是整套 e2e 重新
变得不可复现。所以用测试钉住结论，而不是靠注释提醒。

注意 `sw init --no-mock` 是**生产功能**，用户要用真实 agent 干活，必须保留 ——
这里只约束测试入口。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DRIVER = ROOT / "tests" / "e2e-flow" / "driver.py"


def test_driver_has_no_real_agent_mode():
    src = DRIVER.read_text(encoding="utf-8")
    assert "--no-mock" not in src, "e2e driver 不该再有真实 agent 开关"
    assert "_drive_real" not in src, "真实 agent 驱动逻辑不该回来"


def test_driver_forces_mock_flag():
    """子进程必须带 --mock：不带就会继承 config.yaml 的 enabled 值。"""
    src = DRIVER.read_text(encoding="utf-8")
    assert '"--mock"' in src


def test_driver_pins_mock_inputs():
    """路由与节奏都要由 driver 固定，不能从 config.yaml 继承。"""
    src = DRIVER.read_text(encoding="utf-8")
    assert "SW_MOCK_REVIEW_ROUTE" in src
    assert "SW_MOCK_RESPONSE_DELAY" in src


def test_sw_test_cli_has_no_mock_switches():
    """`sw test` 的参数里不该再有 mock/no-mock。"""
    main_src = (ROOT / "sw_lib" / "cli" / "main.py").read_text(encoding="utf-8")
    p_test = re.search(r'p_test = subparsers\.add_parser.*?(?=\n\s*#|\n\s*p_\w+ =)',
                       main_src, re.DOTALL)
    assert p_test, "找不到 sw test 的 parser 定义"
    assert "--no-mock" not in p_test.group(0)
    assert "--mock" not in p_test.group(0)


def test_sw_init_keeps_real_agent_option():
    """反向守卫：sw init 的 --no-mock 是生产功能，不许被顺手删掉。"""
    main_src = (ROOT / "sw_lib" / "cli" / "main.py").read_text(encoding="utf-8")
    assert 'p_init.add_argument("--no-mock"' in main_src
