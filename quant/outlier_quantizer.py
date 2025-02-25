import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from torch.utils.checkpoint import checkpoint
from .quantizer import STEBinary, IrNetBinary, FdaBinary, BinaryInterface


class BinaryXnorExceptOutliersLinear(nn.Module, BinaryInterface):
    def __init__(self, weight, bias, outlier_scale=1) -> None:
        super().__init__()
        self.weight = nn.Parameter(weight.to(torch.float32).data)
        if bias is not None:
            self.bias = nn.Parameter(bias.to(torch.float32).data)
        else:
            self.bias = None
        self.printed = False
        self.outlier_mask = None
        self.outlier_scale = outlier_scale
        self.binary_scale = None

    def gen_outlier_mask(self):
        with torch.no_grad():
            w = self.weight
            w_flat = w.view(-1)
            # lower_threshold, upper_threshold = torch.quantile(w_flat, torch.tensor([0.01, 0.99]).to(w.device))

            mean = torch.mean(w_flat).to(w.device)
            std = torch.std(w_flat).to(w.device)
            lower_threshold = mean - 1.6 * std  # 1.6 : 90%, 0.67 : 50%, 1.0, 70%
            upper_threshold = mean + 1.6 * std  # 1.95 : 95%, 2.3 : 98%, 

            outliers = (w < lower_threshold) | (w > upper_threshold)
            print(
                f"Generat outlier_mask, outlier_fraction: {outliers.sum()}/{outliers.numel()}({outliers.sum()/outliers.numel()})"
            )
            self.outlier_mask = outliers.detach()

            # import matplotlib.pyplot as plt
            # import numpy as np

            # # Assuming you have run your provided code and obtained the outliers tensor
            # # Convert the tensor to numpy for visualization
            # outliers_np = outliers.cpu().numpy()
            # outliers_np = outliers_np[0:255,0:255]

            # plt.figure(figsize=(10, 10))
            # plt.imshow(outliers_np, cmap='hot', interpolation='nearest')
            # plt.colorbar()
            # plt.title('Outliers Matrix Visualization')
            # plt.savefig('./111.pdf')
            # # plt.show()

            self.binary_scale = (
                w[~self.outlier_mask].abs().mean(-1).view(-1, 1).detach()
            )

    def binarize_except_outliers(self):
        if self.outlier_mask is None:
            self.gen_outlier_mask()

        # if self.printed is not True:
        #     print(outliers.sum()/outliers.numel())
        #     self.printed = True
        if self.training:
            self.binary_scale = (
                self.weight[~self.outlier_mask].abs().mean(-1).view(-1, 1).detach()
            )
        scaled_weight = self.weight * self.outlier_scale
        binary_weight = STEBinary().apply(self.weight) * self.binary_scale
        w_sim = torch.where(self.outlier_mask, scaled_weight, binary_weight)
        return w_sim

    def forward(self, x):
        # w = STEBinary().apply(self.weight)
        # w = checkpoint(self.binarize_except_outliers)
        w = self.binarize_except_outliers()
        output = F.linear(x, w, self.bias)
        return output
