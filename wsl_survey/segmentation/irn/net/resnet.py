import torch.nn as nn
import torch.nn.functional as F
import torch.utils.model_zoo as model_zoo
from torchvision.models.segmentation import deeplabv3_resnet101

model_urls = {
    'resnet18':
        'https://download.pytorch.org/models/resnet18-5c106cde.pth',
    'resnet34':
        'https://download.pytorch.org/models/resnet34-333f7ec4.pth',
    'resnet50':
        'https://download.pytorch.org/models/resnet50-19c8e357.pth',
    'resnet101':
        'https://download.pytorch.org/models/resnet101-5d3b4d8f.pth',
    'resnet152':
        'https://download.pytorch.org/models/resnet152-b121ed2d.pth',
    'resnext50_32x4d':
        'https://download.pytorch.org/models/resnext50_32x4d-7cdf4587.pth',
    'resnext101_32x8d':
        'https://download.pytorch.org/models/resnext101_32x8d-8ba56ff5.pth',
    'wide_resnet50_2':
        'https://download.pytorch.org/models/wide_resnet50_2-95faca4d.pth',
    'wide_resnet101_2':
        'https://download.pytorch.org/models/wide_resnet101_2-32ee1156.pth',
}


class FixedBatchNorm(nn.BatchNorm2d):
    def forward(self, input):
        return F.batch_norm(input,
                            self.running_mean,
                            self.running_var,
                            self.weight,
                            self.bias,
                            training=False,
                            eps=self.eps)


def conv3x3(in_planes, out_planes, stride=1):
    "3x3 convolution with padding"
    return nn.Conv2d(in_planes,
                     out_planes,
                     kernel_size=3,
                     stride=stride,
                     padding=1,
                     bias=False)


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self,
                 inplanes,
                 planes,
                 stride=1,
                 downsample=None,
                 dilation=1):
        super(BasicBlock, self).__init__()
        self.conv1 = conv3x3(inplanes, planes, stride)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3(planes, planes)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample
        self.stride = stride
        self.dilation = dilation

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class Bottleneck(nn.Module):
    expansion = 4

    def __init__(self,
                 inplanes,
                 planes,
                 stride=1,
                 downsample=None,
                 dilation=1):
        super(Bottleneck, self).__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=1, bias=False)
        self.bn1 = FixedBatchNorm(planes)
        self.conv2 = nn.Conv2d(planes,
                               planes,
                               kernel_size=3,
                               stride=stride,
                               padding=dilation,
                               bias=False,
                               dilation=dilation)
        self.bn2 = FixedBatchNorm(planes)
        self.conv3 = nn.Conv2d(planes, planes * 4, kernel_size=1, bias=False)
        self.bn3 = FixedBatchNorm(planes * 4)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride
        self.dilation = dilation

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class ResNet(nn.Module):
    def __init__(self,
                 block,
                 layers,
                 strides=(2, 2, 2, 2),
                 dilations=(1, 1, 1, 1)):
        self.inplanes = 64
        super(ResNet, self).__init__()
        self.conv1 = nn.Conv2d(3,
                               64,
                               kernel_size=7,
                               stride=strides[0],
                               padding=3,
                               bias=False)
        self.bn1 = FixedBatchNorm(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(block,
                                       64,
                                       layers[0],
                                       stride=1,
                                       dilation=dilations[0])
        self.layer2 = self._make_layer(block,
                                       128,
                                       layers[1],
                                       stride=strides[1],
                                       dilation=dilations[1])
        self.layer3 = self._make_layer(block,
                                       256,
                                       layers[2],
                                       stride=strides[2],
                                       dilation=dilations[2])
        self.layer4 = self._make_layer(block,
                                       512,
                                       layers[3],
                                       stride=strides[3],
                                       dilation=dilations[3])
        self.inplanes = 1024

    def _make_layer(self, block, planes, blocks, stride=1, dilation=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes,
                          planes * block.expansion,
                          kernel_size=1,
                          stride=stride,
                          bias=False),
                FixedBatchNorm(planes * block.expansion),
            )

        layers = [block(self.inplanes, planes, stride, downsample, dilation=1)]
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes, dilation=dilation))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        return x


def resnet18(pretrained=True, **kwargs):
    """
    >>> import torch
    >>> x = torch.randn((2, 3, 512, 512))
    >>> y = resnet18()(x)
    >>> assert y.shape == torch.Size([2, 512, 16, 16])
    """
    model = ResNet(BasicBlock, [2, 2, 2, 2], **kwargs)
    if pretrained:
        state_dict = model_zoo.load_url(model_urls['resnet18'])
        state_dict.pop('fc.weight')
        state_dict.pop('fc.bias')
        model.load_state_dict(state_dict)
    return model


def resnet34(pretrained=True, **kwargs):
    """
    >>> import torch
    >>> x = torch.randn((2, 3, 512, 512))
    >>> y = resnet34()(x)
    >>> assert y.shape == torch.Size([2, 512, 16, 16])
    """
    model = ResNet(BasicBlock, [3, 4, 6, 3], **kwargs)
    if pretrained:
        state_dict = model_zoo.load_url(model_urls['resnet34'])
        state_dict.pop('fc.weight')
        state_dict.pop('fc.bias')
        model.load_state_dict(state_dict)
    return model


def resnet50(pretrained=True, **kwargs):
    """
    >>> import torch
    >>> x = torch.randn((2, 3, 512, 512))
    >>> y = resnet50()(x)
    >>> assert y.shape == torch.Size([2, 2048, 16, 16])
    """
    model = ResNet(Bottleneck, [3, 4, 6, 3], **kwargs)
    if pretrained:
        state_dict = model_zoo.load_url(model_urls['resnet50'])
        state_dict.pop('fc.weight')
        state_dict.pop('fc.bias')
        model.load_state_dict(state_dict)
    return model


def resnet101(pretrained=True, **kwargs):
    """
    >>> import torch
    >>> x = torch.randn((2, 3, 512, 512))
    >>> y = resnet101()(x)
    >>> assert y.shape == torch.Size([2, 2048, 16, 16])
    """
    model = ResNet(Bottleneck, [3, 4, 23, 3], **kwargs)
    if pretrained:
        state_dict = model_zoo.load_url(model_urls['resnet101'])
        state_dict.pop('fc.weight')
        state_dict.pop('fc.bias')
        model.load_state_dict(state_dict)
    return model


def resnet152(pretrained=True, **kwargs):
    """
    >>> import torch
    >>> x = torch.randn((2, 3, 512, 512))
    >>> y = resnet152()(x)
    >>> assert y.shape == torch.Size([2, 2048, 16, 16])
    """
    model = ResNet(Bottleneck, [3, 8, 36, 3], **kwargs)
    if pretrained:
        state_dict = model_zoo.load_url(model_urls['resnet152'])
        state_dict.pop('fc.weight')
        state_dict.pop('fc.bias')
        model.load_state_dict(state_dict)
    return model


def deeplabv3(pretrained=True, **kwargs):
    """
    >>> import torch
    >>> x = torch.randn((2, 3, 512, 512))
    >>> y = resnet152()(x)
    >>> assert y.shape == torch.Size([2, 2048, 16, 16])
    """
    model = deeplabv3_resnet101(pretrained=pretrained, progress=True, aux_loss=None, **kwargs)
    return model


if __name__ == '__main__':
    import doctest

    doctest.testmod()
