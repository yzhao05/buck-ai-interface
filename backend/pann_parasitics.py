"""Differentiable Euler step with fixed or trainable series resistances.

Recurrent/public states are [iL, vo]. Internal vC is reconstructed exactly
at each step, including sensing-current endpoints supplied in training.
"""
import torch
from torch import nn


class SeriesResistanceCell(nn.Module):
    def __init__(self, base, rl, rc):
        super().__init__()
        self.base, self.rl, self.rc = base, rl, rc

    def forward(self, inputs, states):
        b = self.base
        rc, rl = getattr(b, "rC", self.rc), getattr(b, "rL", self.rl)
        current, vo = states[:, 0], states[:, 1]
        sense = inputs[:, 1] if inputs.shape[1] > 1 else torch.zeros_like(current)
        next_sense = inputs[:, 2] if inputs.shape[1] > 2 else sense
        vc = (1+rc/b.Ro)*vo-rc*(current-sense)
        drive = torch.where((inputs[:, 0] == 0) & (current <= 0), vo, b.Vin*inputs[:, 0])
        next_i = torch.relu(current+b.dt/b.L*(drive-vo-rl*current))
        next_vc = vc+b.dt/b.Co*(current-vo/b.Ro-sense)
        next_vo = (next_vc+rc*(next_i-next_sense))/(1+rc/b.Ro)
        return torch.stack((next_i, next_vo), dim=1)
