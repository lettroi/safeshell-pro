# 黄金评测集执行报告

- 数据集：`C:\Users\Administrator\Desktop\margin-console\fullstack\tests\eval_dataset.jsonl`（24 条）
- Agent：`tests.eval_adapter:run_eval_agent`
- 重复采样：1 次/用例
- 时间：2026-11-13 15:16:42

## 数据集校验

- 字段、ID 唯一性、条数、四层覆盖、关键意图用例：全部通过

## 总体结果

- **通过率：100.0%**（24/24 次采样）
- 用例全通过率：100.0%（24/24 条）
- 延迟 p50 / p95：61 ms / 75 ms（均值 57 ms）
- 平均单轮 Token：239

## 分层通过率

| category | 用例数 | 通过 | 通过率 |
| --- | --- | --- | --- |
| boundary | 4 | 4 | 100.0% |
| functional | 12 | 12 | 100.0% |
| robustness | 4 | 4 | 100.0% |
| safety | 4 | 4 | 100.0% |

## 复现命令

```bash
python eval_runner.py --dataset "C:\Users\Administrator\Desktop\margin-console\fullstack\tests\eval_dataset.jsonl" --agent tests.eval_adapter:run_eval_agent --sys-path "." --repeat 1
```

> 本报告仅覆盖自动化指标。人工可用率、TTFT、故障注入兜底覆盖率须另行测量并写入 EVAL_REPORT.md。
