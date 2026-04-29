"""Multi-head self-attention example implemented with einops.rearrange.

Run:
    python outputs/mha/MHA-003/mha_with_einops.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

try:
    import torch
    from torch import Tensor, nn
except ModuleNotFoundError as exc:
    if exc.name == "torch":
        raise SystemExit("Missing dependency: install PyTorch with `pip install torch`.") from exc
    raise

try:
    from einops import rearrange
except ModuleNotFoundError as exc:
    if exc.name == "einops":
        raise SystemExit("Missing dependency: install einops with `pip install einops`.") from exc
    raise


ShapeMap = Dict[str, Tuple[int, ...]]


@dataclass(frozen=True)
class DemoConfig:
    batch_size: int = 2
    seq_len: int = 4
    embed_dim: int = 8
    num_heads: int = 2
    dropout: float = 0.0
    random_seed: int = 0


class MultiHeadSelfAttentionEinops(nn.Module):
    """Small educational MHA module using einops for head reshape steps."""

    def __init__(
        self,
        embed_dim: int,
        num_heads: int,
        dropout: float = 0.0,
        bias: bool = True,
    ) -> None:
        super().__init__()
        if embed_dim % num_heads != 0:
            raise ValueError("embed_dim must be divisible by num_heads")

        self.embed_dim = embed_dim
        self.num_heads = num_heads
        self.head_dim = embed_dim // num_heads

        self.q_proj = nn.Linear(embed_dim, embed_dim, bias=bias)
        self.k_proj = nn.Linear(embed_dim, embed_dim, bias=bias)
        self.v_proj = nn.Linear(embed_dim, embed_dim, bias=bias)
        self.out_proj = nn.Linear(embed_dim, embed_dim, bias=bias)
        self.dropout = nn.Dropout(dropout)

    def _split_heads(self, x: Tensor) -> Tensor:
        """(batch, seq, embed) -> (batch, heads, seq, head_dim)."""
        return rearrange(x, "batch seq (heads head_dim) -> batch heads seq head_dim", heads=self.num_heads)

    def _merge_heads(self, x: Tensor) -> Tensor:
        """(batch, heads, seq, head_dim) -> (batch, seq, embed)."""
        batch_size, num_heads, _, head_dim = x.shape
        if num_heads != self.num_heads or head_dim != self.head_dim:
            raise ValueError("unexpected head shape")

        merged = rearrange(x, "batch heads seq head_dim -> batch seq (heads head_dim)")
        if tuple(merged.shape)[:1] != (batch_size,) or merged.shape[-1] != self.embed_dim:
            raise ValueError("unexpected merged shape")
        return merged

    @staticmethod
    def _prepare_mask(mask: Tensor, batch_size: int, seq_len: int, device: torch.device) -> Tensor:
        """Return a boolean mask broadcastable to attention scores.

        The demo's primary mask shape is (batch, seq), where True means the
        key position is visible and False means it is masked.
        """
        if mask.dtype != torch.bool:
            raise TypeError("mask must use dtype torch.bool")

        mask = mask.to(device=device)

        if mask.dim() == 2:
            if tuple(mask.shape) != (batch_size, seq_len):
                raise ValueError("2D mask must have shape (batch_size, seq_len)")
            if not mask.any(dim=-1).all():
                raise ValueError("each batch item must keep at least one visible key")
            return mask[:, None, None, :]

        if mask.dim() == 4:
            if mask.shape[0] != batch_size or mask.shape[-1] != seq_len:
                raise ValueError("4D mask must be broadcastable to (batch, heads, query, key)")
            return mask

        raise ValueError("mask must be either 2D (batch, seq) or 4D broadcastable mask")

    def forward(self, x: Tensor, mask: Optional[Tensor] = None) -> Tuple[Tensor, Tensor, ShapeMap]:
        """Compute self-attention and return output, weights, and debug shapes."""
        if x.dim() != 3:
            raise ValueError("x must have shape (batch_size, seq_len, embed_dim)")

        batch_size, seq_len, embed_dim = x.shape
        if embed_dim != self.embed_dim:
            raise ValueError(f"expected embed_dim={self.embed_dim}, got {embed_dim}")

        shapes: ShapeMap = {"input_x": tuple(x.shape)}

        q = self.q_proj(x)
        k = self.k_proj(x)
        v = self.v_proj(x)
        shapes["q_linear"] = tuple(q.shape)
        shapes["k_linear"] = tuple(k.shape)
        shapes["v_linear"] = tuple(v.shape)

        q = self._split_heads(q)
        k = self._split_heads(k)
        v = self._split_heads(v)
        shapes["q_split_heads"] = tuple(q.shape)
        shapes["k_split_heads"] = tuple(k.shape)
        shapes["v_split_heads"] = tuple(v.shape)

        scores = torch.matmul(q, k.transpose(-2, -1)) / math.sqrt(self.head_dim)
        shapes["attention_scores"] = tuple(scores.shape)

        if mask is not None:
            attention_mask = self._prepare_mask(mask, batch_size, seq_len, x.device)
            shapes["attention_mask"] = tuple(attention_mask.shape)
            scores = scores.masked_fill(~attention_mask, float("-inf"))

        attn_weights = torch.softmax(scores, dim=-1)
        attn_weights = self.dropout(attn_weights)
        shapes["attention_weights"] = tuple(attn_weights.shape)

        context = torch.matmul(attn_weights, v)
        shapes["context_per_head"] = tuple(context.shape)

        context = self._merge_heads(context)
        shapes["context_merged"] = tuple(context.shape)

        output = self.out_proj(context)
        shapes["output"] = tuple(output.shape)

        return output, attn_weights, shapes


def run_demo() -> None:
    config = DemoConfig()
    torch.manual_seed(config.random_seed)

    x = torch.randn(config.batch_size, config.seq_len, config.embed_dim)
    mask = torch.tensor(
        [
            [True, True, True, True],
            [True, True, False, False],
        ],
        dtype=torch.bool,
    )

    model = MultiHeadSelfAttentionEinops(
        embed_dim=config.embed_dim,
        num_heads=config.num_heads,
        dropout=config.dropout,
    )

    output, attn_weights, shapes = model(x, mask=mask)

    print("Multi-head self-attention with einops.rearrange")
    print(f"head_dim: {model.head_dim}")
    for name, shape in shapes.items():
        print(f"{name:>20}: {shape}")

    expected_output_shape = (config.batch_size, config.seq_len, config.embed_dim)
    expected_weights_shape = (
        config.batch_size,
        config.num_heads,
        config.seq_len,
        config.seq_len,
    )
    assert tuple(output.shape) == expected_output_shape
    assert tuple(attn_weights.shape) == expected_weights_shape

    masked_tail = attn_weights[1, :, :, 2:]
    assert torch.allclose(masked_tail, torch.zeros_like(masked_tail), atol=1e-6)

    print("shape checks passed")
    print("mask check passed")


if __name__ == "__main__":
    run_demo()
