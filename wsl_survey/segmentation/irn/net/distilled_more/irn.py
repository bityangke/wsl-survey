import torch
import torch.nn as nn
import torch.nn.functional as F


class Net(nn.Module):
    def __init__(self, backbone=None):
        super(Net, self).__init__()

        # backbone
        self.backbone_model = backbone

        self.stage1 = nn.Sequential(self.backbone_model.conv1,
                                    self.backbone_model.bn1,
                                    self.backbone_model.relu,
                                    self.backbone_model.maxpool)
        self.stage2 = nn.Sequential(self.backbone_model.layer1)
        self.stage3 = nn.Sequential(self.backbone_model.layer2)
        self.mean_shift = Net.MeanShift(2)

        # branch: class boundary detection
        self.fc_edge1 = nn.Sequential(
            nn.Conv2d(64, 32, 1, bias=False),
            nn.GroupNorm(4, 32),
            nn.ReLU(inplace=True),
        )
        self.fc_edge2 = nn.Sequential(
            nn.Conv2d(256, 32, 1, bias=False),
            nn.GroupNorm(4, 32),
            nn.ReLU(inplace=True),
        )
        self.fc_edge3 = nn.Sequential(
            nn.Conv2d(512, 32, 1, bias=False),
            nn.GroupNorm(4, 32),
            nn.Upsample(scale_factor=2, mode='bilinear', align_corners=False),
            nn.ReLU(inplace=True),
        )
        self.fc_edge6 = nn.Conv2d(96, 1, 1, bias=True)

        # branch: displacement field
        self.fc_dp1 = nn.Sequential(
            nn.Conv2d(64, 64, 1, bias=False),
            nn.GroupNorm(8, 64),
            nn.ReLU(inplace=True),
        )
        self.fc_dp2 = nn.Sequential(
            nn.Conv2d(256, 128, 1, bias=False),
            nn.GroupNorm(16, 128),
            nn.ReLU(inplace=True),
        )
        self.fc_dp3 = nn.Sequential(
            nn.Conv2d(512, 256, 1, bias=False),
            nn.GroupNorm(16, 256),
            nn.ReLU(inplace=True),
        )
        self.fc_dp6 = nn.Sequential(
            nn.Conv2d(256, 256, 1, bias=False),
            nn.GroupNorm(16, 256),
            nn.Upsample(scale_factor=2, mode='bilinear', align_corners=False),
            nn.ReLU(inplace=True),
        )
        self.fc_dp7 = nn.Sequential(nn.Conv2d(448, 256, 1, bias=False),
                                    nn.GroupNorm(16,
                                                 256), nn.ReLU(inplace=True),
                                    nn.Conv2d(256, 2, 1, bias=False),
                                    self.mean_shift)

        self.backbone = nn.ModuleList(
            [self.stage1, self.stage2, self.stage3])
        self.edge_layers = nn.ModuleList([
            self.fc_edge1, self.fc_edge2, self.fc_edge3, self.fc_edge6
        ])
        self.dp_layers = nn.ModuleList([
            self.fc_dp1, self.fc_dp2, self.fc_dp3, self.fc_dp6, self.fc_dp7
        ])

    class MeanShift(nn.Module):
        def __init__(self, num_features):
            super(Net.MeanShift, self).__init__()
            self.register_buffer('running_mean', torch.zeros(num_features))

        def forward(self, input):
            if self.training:
                return input
            return input - self.running_mean.view(1, 2, 1, 1)

    def forward(self, x):
        x1 = self.stage1(x).detach()
        x2 = self.stage2(x1).detach()
        x3 = self.stage3(x2).detach()

        edge1 = self.fc_edge1(x1)
        edge2 = self.fc_edge2(x2)
        edge3 = self.fc_edge3(x3)[..., :edge2.size(2), :edge2.size(3)]
        edge_out = self.fc_edge6(
            torch.cat([edge1, edge2, edge3], dim=1))

        dp1 = self.fc_dp1(x1)
        dp2 = self.fc_dp2(x2)
        dp3 = self.fc_dp3(x3)

        dp_up3 = self.fc_dp6(torch.cat([dp3],
                                       dim=1))[..., :dp2.size(2), :dp2.size(3)]
        dp_out = self.fc_dp7(torch.cat([dp1, dp2, dp_up3], dim=1))

        return edge_out, dp_out

    def trainable_parameters(self):
        return (tuple(self.edge_layers.parameters()),
                tuple(self.dp_layers.parameters()))

    def train(self, mode=True):
        super().train(mode)
        self.backbone.eval()


class AffinityDisplacementLoss(Net):
    path_indices_prefix = "path_indices"

    def __init__(self, path_index, backbone=None):

        super(AffinityDisplacementLoss, self).__init__(backbone=backbone)

        self.path_index = path_index

        self.n_path_lengths = len(path_index.path_indices)
        for i, pi in enumerate(path_index.path_indices):
            self.register_buffer(
                AffinityDisplacementLoss.path_indices_prefix + str(i),
                torch.from_numpy(pi))

        self.register_buffer(
            'disp_target',
            torch.unsqueeze(
                torch.unsqueeze(
                    torch.from_numpy(path_index.search_dst).transpose(1, 0),
                    0), -1).float())

    def to_affinity(self, edge):
        aff_list = []
        edge = edge.view(edge.size(0), -1)

        for i in range(self.n_path_lengths):
            ind = self._buffers[AffinityDisplacementLoss.path_indices_prefix +
                                str(i)]
            ind_flat = ind.view(-1)
            dist = torch.index_select(edge, dim=-1, index=ind_flat)
            dist = dist.view(dist.size(0), ind.size(0), ind.size(1),
                             ind.size(2))
            aff = torch.squeeze(1 - F.max_pool2d(dist, (dist.size(2), 1)),
                                dim=2)
            aff_list.append(aff)
        aff_cat = torch.cat(aff_list, dim=1)

        return aff_cat

    def to_pair_displacement(self, disp):
        height, width = disp.size(2), disp.size(3)
        radius_floor = self.path_index.radius_floor

        cropped_height = height - radius_floor
        cropped_width = width - 2 * radius_floor

        disp_src = disp[:, :, :cropped_height,
                   radius_floor:radius_floor + cropped_width]

        disp_dst = [
            disp[:, :, dy:dy + cropped_height,
            radius_floor + dx:radius_floor + dx + cropped_width]
            for dy, dx in self.path_index.search_dst
        ]
        disp_dst = torch.stack(disp_dst, 2)

        pair_disp = torch.unsqueeze(disp_src, 2) - disp_dst
        pair_disp = pair_disp.view(pair_disp.size(0), pair_disp.size(1),
                                   pair_disp.size(2), -1)

        return pair_disp

    def to_displacement_loss(self, pair_disp):
        return torch.abs(pair_disp - self.disp_target)

    def forward(self, *inputs):
        x, return_loss = inputs
        edge_out, dp_out = super().forward(x)

        if return_loss is False:
            return edge_out, dp_out

        aff = self.to_affinity(torch.sigmoid(edge_out))
        pos_aff_loss = (-1) * torch.log(aff + 1e-5)
        neg_aff_loss = (-1) * torch.log(1. + 1e-5 - aff)

        pair_disp = self.to_pair_displacement(dp_out)
        dp_fg_loss = self.to_displacement_loss(pair_disp)
        dp_bg_loss = torch.abs(pair_disp)

        return pos_aff_loss, neg_aff_loss, dp_fg_loss, dp_bg_loss


class EdgeDisplacement(Net):
    def __init__(self, crop_size=512, stride=4, backbone=None):
        super(EdgeDisplacement, self).__init__(backbone=backbone)
        self.crop_size = crop_size
        self.stride = stride

    def forward(self, x):
        feat_size = (x.size(2) - 1) // self.stride + 1, (x.size(3) -
                                                         1) // self.stride + 1

        x = F.pad(
            x, [0, self.crop_size - x.size(3), 0, self.crop_size - x.size(2)])
        edge_out, dp_out = super().forward(x)
        edge_out = edge_out[..., :feat_size[0], :feat_size[1]]
        dp_out = dp_out[..., :feat_size[0], :feat_size[1]]

        edge_out = torch.sigmoid(edge_out[0] / 2 + edge_out[1].flip(-1) / 2)
        dp_out = dp_out[0]

        return edge_out, dp_out
