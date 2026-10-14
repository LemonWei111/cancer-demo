import os
import math
import numpy as np
from PIL import Image, ImageOps
import torch
import torch.nn.functional as F
from torchvision import transforms

# 复用迁移后的加载与判空工具
from cancer_predict import _load_model as _load_model_internal
from cancer_predict import _is_blank_patch as _is_blank_patch_internal

# 默认权重位置：优先读环境变量，其次用相对于本文件的路径
DEFAULT_MODEL_NAME = "resnet50"
DEFAULT_MODEL_PATH = os.environ.get(
    "COAD_MODEL_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "val_highest.pth"),
)

_global_model = None
_global_device = None


def _get_cached_model(model_name: str, model_path: str, device: str):
    global _global_model, _global_device
    if _global_model is None or _global_device != device:
        torch.manual_seed(42)
        if torch.cuda.is_available():
            torch.cuda.manual_seed(42)
            torch.cuda.manual_seed_all(42)
        np.random.seed(42)
        _global_model = _load_model_internal(model_name, model_path, device)
        _global_device = device
    return _global_model


def classify_wsi(image_path: str, model_name: str = DEFAULT_MODEL_NAME, model_path: str = DEFAULT_MODEL_PATH, threshold: float = 0.5):
    """
    对单张WSI图像进行分类，返回(label_bool, pos_score_mean)。
    label_bool: True 表示癌症切片，False 表示正常切片。
    pos_score_mean: 属于预测标签的patch概率均值（正类时为p1均值，负类时为1-p1均值）。
    """
    device = 'cuda' if torch.cuda.is_available() else 'cpu'

    normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    test_transforms = transforms.Compose([
        transforms.Resize(224),
        transforms.ToTensor(),
        normalize,
    ])

    net = _get_cached_model(model_name, model_path, device)

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
            if _is_blank_patch_internal(patch):
                continue
            with torch.no_grad():
                imgblob = test_transforms(patch).unsqueeze(0).to(device)
                probability = F.softmax(net(imgblob), dim=1)
                score1 = probability[0, 1].item()
                label = int(torch.argmax(probability, dim=1).item())
            pre_labels.append(label)
            pre_scores.append(score1)

    if len(pre_labels) == 0:
        return False, 0.0

    frac_positive = np.sum(np.array(pre_labels)) / float(len(pre_labels))
    if frac_positive > threshold:
        pos_scores = np.array(pre_scores)[np.where(np.array(pre_labels) == 1)]
        score = float(np.mean(pos_scores)) if len(pos_scores) > 0 else 0.0
        return True, score
    else:
        neg_scores = np.array(pre_scores)[np.where(np.array(pre_labels) == 0)]
        score = float(np.mean(1.0 - neg_scores)) if len(neg_scores) > 0 else 0.0
        return False, score
