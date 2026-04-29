# MHA-005 本地验证与交付报告

## 前置检查

- 已在项目仓库目录执行 `git pull`，结果为 `Already up to date.`
- 已确认前序任务哨兵存在：`outputs/mha/MHA-004/result.json`
- 已确认 `outputs/mha/MHA-004/result.json` 中 `task_code` 为 `MHA-004`

## 本地环境

- Python: `3.12.10`
- PyTorch: `2.11.0+cpu`
- einops: `0.8.2`

本地验证前环境缺少 `torch` 和 `einops`。为执行完整前向验证，已运行：

```bash
python -m pip install torch einops
```

## 验证命令

```bash
python -m py_compile outputs/mha/MHA-002/mha_no_einops.py outputs/mha/MHA-003/mha_with_einops.py
python outputs/mha/MHA-002/mha_no_einops.py
python outputs/mha/MHA-003/mha_with_einops.py
```

## 非 einops 版本验证结果

命令：

```bash
python outputs/mha/MHA-002/mha_no_einops.py
```

结果：通过。

关键输出形状：

```text
input_x: (2, 4, 8)
q_linear: (2, 4, 8)
k_linear: (2, 4, 8)
v_linear: (2, 4, 8)
q_split_heads: (2, 2, 4, 4)
k_split_heads: (2, 2, 4, 4)
v_split_heads: (2, 2, 4, 4)
attention_scores: (2, 2, 4, 4)
attention_mask: (2, 1, 1, 4)
attention_weights: (2, 2, 4, 4)
context_per_head: (2, 2, 4, 4)
context_merged: (2, 4, 8)
output: (2, 4, 8)
shape checks passed
mask check passed
```

## einops 版本验证结果

命令：

```bash
python outputs/mha/MHA-003/mha_with_einops.py
```

结果：通过。

关键输出形状：

```text
input_x: (2, 4, 8)
q_linear: (2, 4, 8)
k_linear: (2, 4, 8)
v_linear: (2, 4, 8)
q_split_heads: (2, 2, 4, 4)
k_split_heads: (2, 2, 4, 4)
v_split_heads: (2, 2, 4, 4)
attention_scores: (2, 2, 4, 4)
attention_mask: (2, 1, 1, 4)
attention_weights: (2, 2, 4, 4)
context_per_head: (2, 2, 4, 4)
context_merged: (2, 4, 8)
output: (2, 4, 8)
shape checks passed
mask check passed
```

## 备注

运行 PyTorch 时出现 `Failed to initialize NumPy: No module named 'numpy'` warning。该 warning 不影响本示例，因为脚本没有使用 NumPy，两个示例均正常完成前向计算、形状断言和 mask 断言。

## Git Diff 范围

本任务只新增 MHA-005 交付产物：

```text
outputs/mha/MHA-005/validation_report.md
outputs/mha/MHA-005/result.json
```

