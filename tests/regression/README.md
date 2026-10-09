# 工程回归与自定义夹具

[测试导航](../README.md) · [公开评测](../../eval/README.md)

这些文件从 `eval/` 移入本目录，用于检测实现是否退化。问题、规则与预期值由项目维护者编写，不能作为公共 benchmark 成绩。旧的三份合成分数报告已从评测目录移除，不再作为项目能力数字引用。

| 入口 | 用途 |
| --- | --- |
| [run_governance.py](run_governance.py) / [governance_cases.json](governance_cases.json) | 二十四项治理机制回归；授权、撤回、版本、来源 |
| [governance_eval.py](governance_eval.py) / [test_governance_eval.py](test_governance_eval.py) | 夹具预测的独立评分及评分器测试 |
| [run_memory_layers.py](run_memory_layers.py) | 十五项分层机制与边界回归，包含固定时钟、容量和技能版本 |
| [run_seeded.py](run_seeded.py) / [seeded_memory_cases.json](seeded_memory_cases.json) | 自定义中文检索与门控回归 |
| [memory_eval.py](memory_eval.py) / [memory_cases.json](memory_cases.json) | 检索排名评分器的少量夹具 |
| [benchmark_shared_memory.py](benchmark_shared_memory.py) | 可选的合成存储微基准；不是公共数据集评测 |

```bash
python -m pytest -q tests/regression tests/test_memory_layers_eval.py
python -m tests.regression.run_governance --min-pass-rate 1 --max-leak-rate 0
python -m tests.regression.run_memory_layers --min-pass-rate 1
python -m tests.regression.run_seeded --min-recall 0.75
```

本次仅迁移与保留回归测试，没有重新运行 1000 条合成性能负载。公开集下载、固定五题选择、实际模型评测与报告归档均在 `eval/`。历史命令 `python -m eval.run_governance` 等已更换为上方的新入口。
