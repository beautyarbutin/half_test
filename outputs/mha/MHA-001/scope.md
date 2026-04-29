# MHA-001 示例代码范围与输出结构确认

## 目标读者

本示例面向已经了解 Python 与 PyTorch 基础张量操作、希望手写理解多头注意力机制的读者。代码应优先展示张量形状变化与核心计算流程，而不是封装成生产级训练模块。

## 示例范围

- 实现自注意力场景：输入 `x` 同时生成 Query、Key、Value。
- 提供两版实现：
  - 不使用 `einops`：通过 PyTorch 原生 `view` / `reshape`、`transpose`、`contiguous` 完成拆头与合头。
  - 使用 `einops`：通过 `einops.rearrange` 完成同样的拆头与合头。
- 两版核心数学逻辑保持一致，便于对照学习。
- 包含缩放点积注意力：`scores = Q @ K.transpose(-2, -1) / sqrt(head_dim)`。
- 包含可选 mask 示例，使用布尔 mask，约定 `True` 表示该 key 位置可见，`False` 表示需要屏蔽。
- 包含最小前向运行 demo，打印关键张量形状并断言输出形状正确。

## 张量形状约定

- 输入 `x`: `(batch_size, seq_len, embed_dim)`
- `num_heads` 必须整除 `embed_dim`
- `head_dim = embed_dim // num_heads`
- 拆头后的 `q` / `k` / `v`: `(batch_size, num_heads, seq_len, head_dim)`
- 注意力分数 `scores`: `(batch_size, num_heads, query_len, key_len)`
- 注意力权重 `attn_weights`: `(batch_size, num_heads, query_len, key_len)`
- 合头后的上下文张量: `(batch_size, seq_len, embed_dim)`
- 输出 `out`: `(batch_size, seq_len, embed_dim)`

## Mask 约定

示例优先支持 key padding mask：

- 输入 mask 形状：`(batch_size, seq_len)`
- 类型：`torch.bool`
- 语义：`True` 表示该 token 可参与注意力，`False` 表示 padding 或不可见位置
- 内部扩展形状：`(batch_size, 1, 1, seq_len)`
- 屏蔽方式：`scores.masked_fill(~mask, -inf)`

如后续任务要扩展示例，可兼容更通用的 broadcastable mask，例如 `(batch_size, 1, query_len, key_len)`。

## 最小依赖

- Python 3.9+
- PyTorch
- `einops` 仅用于使用 einops 的版本

建议 README 中给出：

```bash
pip install torch einops
python outputs/mha/MHA-002/mha_no_einops.py
python outputs/mha/MHA-003/mha_with_einops.py
```

## 输出文件组织

后续任务采用按任务码分目录的协作结构，不依赖旧的单文件输出路径：

- `outputs/mha/MHA-001/scope.md`：本范围说明
- `outputs/mha/MHA-001/output_structure.json`：结构化范围与文件规划
- `outputs/mha/MHA-001/result.json`：本任务完成哨兵
- `outputs/mha/MHA-002/mha_no_einops.py`：不使用 einops 的实现
- `outputs/mha/MHA-003/mha_with_einops.py`：使用 einops 的实现
- `outputs/mha/MHA-004/README.md`：统一说明文档

