import torch
import torch.nn as nn
from torch.autograd import Variable
import torchvision
from sklearn.metrics import roc_auc_score
from torchvision import datasets, transforms
import time
import datetime
import os
from PIL import Image, ImageOps
import sys
import torch.nn.functional as F
import numpy as np
import pdb
import argparse
import glob
import re
from tqdm import tqdm
import math
import cv2
import csv

Image.MAX_IMAGE_PIXELS = 933120000


class classify:
    def __init__(self, model, model_path, img_path, threshold):
        self.model = model
        self.img_path = img_path
        self.model_path = model_path
        self.threshold = threshold
        self.normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])

        self.test_transforms = transforms.Compose([
            transforms.Resize(224),
            transforms.ToTensor(),
            self.normalize,
        ])

    def is_blank(self, source):
        source = np.array(source).astype('uint8')
        channels = cv2.split(source)
        return all([c.std() < 30 for c in channels])

    def load_net(self, model_path):
        state_dict = torch.load(model_path)
        loaded_model = state_dict['model']
        if self.model == 'resnet50':
            net = torchvision.models.resnet50(num_classes=2)
        if self.model == 'densenet161':
            net = torchvision.models.densenet161(num_classes=2)
        if self.model == 'resnet101':
            net = torchvision.models.resnet101(num_classes=2)
        net.eval()
        net.load_state_dict(loaded_model)
        net.to('cuda')
        return net

    def predict(self, patch):
        net = self.load_net(self.model_path)
        imgblob = self.test_transforms(patch).unsqueeze(0).to("cuda")
        imgblob = Variable(imgblob)
        probability = F.softmax(net(imgblob), dim=1)
        _, label = torch.max(probability, 1)
        return label, probability[0, 1]

    def compute_wsi(self, pre_labels, pre_scores):
        if np.sum(np.array(pre_labels) / float(len(pre_labels))) > self.threshold:
            wsi_pre_label = 1
            wsi_pre_score = np.mean(np.array(pre_scores)[np.where(np.array(pre_labels) == 1)])
        else:
            wsi_pre_label = 0
            wsi_pre_score = np.mean(np.array(pre_scores)[np.where(np.array(pre_labels) == 0)])
        return wsi_pre_label, wsi_pre_score

    def run(self):
        IM_WIDTH = 1024
        IM_HEIGHT = 1024
        size = 1.5
        record = open('../output/predict.csv', 'a')
        writer = record.writer(record)
        # 预测每张大图的类别
        for wsi in self.img_path:
            pre_labels = []
            pre_scores = []
            img = Image.open(wsi)
            w, h = img.size
            cols = int(math.ceil((w - 1536) / IM_WIDTH))
            rows = int(math.ceil((h - 1536) / IM_HEIGHT))
            _right = cols * IM_WIDTH + 1536 - w
            _bottom = rows * IM_HEIGHT + 1536 - h
            img = ImageOps.expand(img, (0, 0, _right, _bottom), fill='white')
            for i in tqdm(range(rows + 1)):
                for j in range(cols + 1):
                    # 将一张大图切成很多小图
                    patch = img.crop((j * IM_WIDTH, i * IM_HEIGHT, int((j + size) * IM_WIDTH), int((i + size) * IM_HEIGHT))).convert('RGB')
                    if not self.is_blank(patch):
                        label, score = self.predict(patch)
                        pre_labels.append(label)
                        pre_scores.append(score)
            wsi_pre_label, wsi_pre_score = self.compute_wsi(pre_labels, pre_scores)
            writer.writerows([wsi, wsi_pre_label, wsi_pre_score])
        record.close()


def _load_model(model_name, model_path, device):
    ckpt = torch.load(model_path, map_location=device)

    # 解析权重：兼容多种保存格式
    weights = None
    if isinstance(ckpt, dict):
        if 'model' in ckpt and isinstance(ckpt['model'], (dict, torch.nn.Module)):
            weights = ckpt['model'] if isinstance(ckpt['model'], dict) else ckpt['model'].state_dict()
        elif 'state_dict' in ckpt and isinstance(ckpt['state_dict'], dict):
            weights = ckpt['state_dict']
        else:
            # 可能直接是state_dict
            # 检查是否像state_dict（key中包含'.weight'或'.bias'）
            if all(isinstance(k, str) for k in ckpt.keys()):
                weights = ckpt
    else:
        # 其他类型不支持
        raise ValueError('Unsupported checkpoint format at {}'.format(model_path))

    if weights is None:
        raise KeyError('No state_dict/model found in checkpoint: {}'.format(model_path))

    # 去除可能的分布式前缀 'module.'
    def strip_module_prefix(state_dict):
        needs_strip = any(k.startswith('module.') for k in state_dict.keys())
        if not needs_strip:
            return state_dict
        return {k.replace('module.', '', 1): v for k, v in state_dict.items()}

    weights = strip_module_prefix(weights)

    if model_name == 'resnet50':
        net = torchvision.models.resnet50(num_classes=2)
    elif model_name == 'densenet161':
        net = torchvision.models.densenet161(num_classes=2)
    elif model_name == 'resnet101':
        net = torchvision.models.resnet101(num_classes=2)
    else:
        raise ValueError('Unsupported model: {}'.format(model_name))
    net.load_state_dict(weights, strict=False)
    net.eval()
    net.to(device)
    return net


def _is_blank_patch(pil_image):
    arr = np.array(pil_image).astype('uint8')
    channels = cv2.split(arr)
    return all([c.std() < 30 for c in channels])


def predict_wsi_image(image_path, model_name, model_path, threshold=0.5):
    """
    输入单张WSI图像路径，返回(预测标签, 预测分数)。
    标签: 1 表示癌症切片，0 表示正常切片。
    分数: 属于该标签的patch概率均值。
    """
    device = 'cuda' if torch.cuda.is_available() else 'cpu'

    normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    test_transforms = transforms.Compose([
        transforms.Resize(224),
        transforms.ToTensor(),
        normalize,
    ])

    net = _load_model(model_name, model_path, device)

    IM_WIDTH = 1024
    IM_HEIGHT = 1024
    size = 1.5

    img = Image.open(image_path)
    w, h = img.size
    cols = int(math.ceil((w - 1536) / IM_WIDTH))
    rows = int(math.ceil((h - 1536) / IM_HEIGHT))
    _right = cols * IM_WIDTH + 1536 - w
    _bottom = rows * IM_HEIGHT + 1536 - h
    img = ImageOps.expand(img, (0, 0, _right, _bottom), fill='white')

    pre_labels = []
    pre_scores = []

    for i in range(rows + 1):
        for j in range(cols + 1):
            patch = img.crop((j * IM_WIDTH, i * IM_HEIGHT, int((j + size) * IM_WIDTH), int((i + size) * IM_HEIGHT))).convert('RGB')
            if _is_blank_patch(patch):
                continue
            with torch.no_grad():
                imgblob = test_transforms(patch).unsqueeze(0).to(device)
                probability = F.softmax(net(imgblob), dim=1)
                score1 = probability[0, 1].item()
                label = int(torch.argmax(probability, dim=1).item())
            pre_labels.append(label)
            pre_scores.append(score1)

    if len(pre_labels) == 0:
        return 0, 0.0

    frac_positive = np.sum(np.array(pre_labels)) / float(len(pre_labels))
    if frac_positive > threshold:
        wsi_pre_label = 1
        pos_scores = np.array(pre_scores)[np.where(np.array(pre_labels) == 1)]
        wsi_pre_score = float(np.mean(pos_scores)) if len(pos_scores) > 0 else 0.0
    else:
        wsi_pre_label = 0
        neg_scores = np.array(pre_scores)[np.where(np.array(pre_labels) == 0)]
        # 对于正常类，分数定义为(1 - p1)的均值
        if len(neg_scores) > 0:
            wsi_pre_score = float(np.mean(1.0 - neg_scores))
        else:
            wsi_pre_score = 0.0

    return int(wsi_pre_label), float(wsi_pre_score)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='WSI癌症切片预测')
    parser.add_argument('--image', required=True, help='输入图片路径')
    parser.add_argument('--model', default='resnet50', choices=['resnet50', 'densenet161', 'resnet101'], help='模型名称')
    parser.add_argument('--model_path', default='code/log/cac-unet-r50/val_highest.pth', help='模型权重路径(包含key"model")')
    parser.add_argument('--threshold', type=float, default=0.5, help='阳性patch占比阈值')
    args = parser.parse_args()

    label, score = predict_wsi_image(args.image, args.model, args.model_path, args.threshold)
    print('{},{}'.format(label, score))
