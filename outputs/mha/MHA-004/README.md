# Python 多头注意力机制示例

本目录说明两个 PyTorch 多头自注意力示例的运行方式和对照重点：

- `outputs/mha/MHA-002/mha_no_einops.py`：不使用 `einops`，只用 PyTorch 原生 `reshape`、`transpose`、`contiguous` 完成拆头和合头。
- `outputs/mha/MHA-003/mha_with_einops.py`：使用 `einops.rearrange` 表达同样的拆头和合头逻辑。

两个脚本的核心计算保持一致，均覆盖 Q/K/V 线性映射、缩放点积注意力、布尔 mask、softmax、dropout、头拼接和输出投影。

## 依赖安装

建议使用 Python 3.9 或更高版本。

只运行非 `einops` 版本：

```bash
pip install torch
```

运行两个版本：

```bash
pip install torch einops
```

如果本地 Python 环境未安装依赖，脚本会输出明确的安装提示。

## 运行命令

在仓库根目录执行：

```bash
python outputs/mha/MHA-002/mha_no_einops.py
python outputs/mha/MHA-003/mha_with_einops.py
```

脚本会打印关键张量形状，并执行以下断言：

- 输出张量形状为 `(batch_size, seq_len, embed_dim)`
- 注意力权重形状为 `(batch_size, num_heads, seq_len, seq_len)`
- mask 掉的位置对应的注意力权重接近 0

## 张量形状约定

| 名称 | 形状 | 说明 |
| --- | --- | --- |
| `x` | `(batch_size, seq_len, embed_dim)` | 输入序列表示 |
| `q_linear` / `k_linear` / `v_linear` | `(batch_size, seq_len, embed_dim)` | 线性投影后的 Q/K/V |
| `q_split_heads` / `k_split_heads` / `v_split_heads` | `(batch_size, num_heads, seq_len, head_dim)` | 拆成多个 attention head 后的 Q/K/V |
| `attention_scores` | `(batch_size, num_heads, seq_len, seq_len)` | 缩放点积注意力分数 |
| `attention_mask` | `(batch_size, 1, 1, seq_len)` | 从 key padding mask 扩展得到的广播 mask |
| `attention_weights` | `(batch_size, num_heads, seq_len, seq_len)` | softmax 后的注意力权重 |
| `context_per_head` | `(batch_size, num_heads, seq_len, head_dim)` | 每个 head 的加权 Value |
| `context_merged` | `(batch_size, seq_len, embed_dim)` | 多个 head 拼接后的上下文 |
| `output` | `(batch_size, seq_len, embed_dim)` | 输出投影后的结果 |

其中：

```text
head_dim = embed_dim // num_heads
embed_dim 必须能被 num_heads 整除
```

## Mask 约定

示例使用布尔 key padding mask：

- mask 形状：`(batch_size, seq_len)`
- `True`：该 key 位置可见
- `False`：该 key 位置被屏蔽

脚本内部会把 mask 扩展为 `(batch_size, 1, 1, seq_len)`，再通过：

```python
scores = scores.masked_fill(~attention_mask, float("-inf"))
```

让被屏蔽位置在 softmax 后得到接近 0 的注意力权重。

## 两版实现差异

非 `einops` 版本的拆头逻辑：

```python
x = x.reshape(batch_size, seq_len, num_heads, head_dim)
x = x.transpose(1, 2).contiguous()
```

非 `einops` 版本的合头逻辑：

```python
x = x.transpose(1, 2).contiguous()
x = x.reshape(batch_size, seq_len, embed_dim)
```

`einops` 版本的拆头逻辑：

```python
rearrange(x, "batch seq (heads head_dim) -> batch heads seq head_dim", heads=num_heads)
```

`einops` 版本的合头逻辑：

```python
rearrange(x, "batch heads seq head_dim -> batch seq (heads head_dim)")
```

两版的数学步骤一致。主要区别是：原生 PyTorch 版本更直接展示底层维度变换；`einops` 版本把维度含义写进表达式，可读性更强。

## 常见参数

| 参数 | 含义 | 示例默认值 |
| --- | --- | --- |
| `batch_size` | 一次前向计算中的样本数量 | `2` |
| `seq_len` | 每个样本的 token 数量 | `4` |
| `embed_dim` | 每个 token 的向量维度，也是 Q/K/V 投影维度 | `8` |
| `num_heads` | attention head 数量 | `2` |
| `head_dim` | 每个 head 的维度，等于 `embed_dim // num_heads` | `4` |
| `dropout` | attention 权重上的 dropout 概率 | `0.0` |
| `random_seed` | demo 中用于构造随机输入的随机种子 | `0` |

## 建议阅读顺序

1. 先阅读并运行 `outputs/mha/MHA-002/mha_no_einops.py`，理解原生张量维度变换。
2. 再阅读并运行 `outputs/mha/MHA-003/mha_with_einops.py`，对照 `rearrange` 如何表达同样的拆头和合头。
3. 重点观察 demo 输出中的 `q_split_heads`、`attention_scores`、`context_merged` 和 `output` 形状。

