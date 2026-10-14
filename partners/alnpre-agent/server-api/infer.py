# partners/alnpre-agent/server-api/infer.py
import os
import argparse
import sys
from pathlib import Path

import torch
import pandas as pd
import torchvision
from PIL import Image
import numpy as np

# 添加 code 目录到 Python 路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'code'))

from models.mil_net import MILNetImageOnly, MILNetWithClinicalData
from utils.utils import merge_result
from torch.nn import functional as F
import random

# 本文件: <root>/partners/alnpre-agent/server-api/infer.py
# Path(__file__).resolve().parents[3] → <root>
_ROOT = Path(__file__).resolve()
_PROJECT_ROOT = os.environ.get("PROJECT_ROOT", str(_ROOT.parents[3]))

# 统一输出目录：<root>/wsi-result/brca-aln/
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "wsi-result", "brca-aln")

# ckpt 路径（保留原有逻辑）
_ALNPRE_API_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_CKPT = os.environ.get(
    "ALNPRE_CHECKPOINT_PATH",
    os.path.join(_ALNPRE_API_DIR, "checkpoints", "2.pth"),
)

print(
    f"[alnpre] _PROJECT_ROOT={_PROJECT_ROOT}, OUTPUT_DIR={OUTPUT_DIR}",
    flush=True,
)


# 与训练一致的图像预处理
_transform = torchvision.transforms.Compose(
    [
        torchvision.transforms.Resize((224, 224)),
        torchvision.transforms.ToTensor(),
        torchvision.transforms.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
    ]
)






def cut_image_into_patches(image_path, patch_size=256, blank_ratio_thresh=0.3):
    """将整图切为若干 256x256 patch，并过滤空白比率 > 阈值 的patch。
    空白判定：转灰度后统计像素>250(近白)比例。
    返回：合格patch的PIL.Image列表。
    """
    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    patches = []
    for top in range(0, height - patch_size + 1, patch_size):
        for left in range(0, width - patch_size + 1, patch_size):
            box = (left, top, left + patch_size, top + patch_size)
            patch = img.crop(box)
            gray = patch.convert("L")
            arr = np.asarray(gray, dtype=np.uint8)
            blank_ratio = float((arr > 250).sum()) / float(arr.size)
            if blank_ratio <= blank_ratio_thresh:
                patches.append(patch)
    return patches


class MultiBagFromWholeImageDataset(torch.utils.data.Dataset):
    """从整图切patch、过滤，再随机打乱按 bag_size 分组形成多个bag。"""

    def __init__(self, whole_image_path, bag_size, seed, patch_size, blank_ratio_thresh, clinical_vector=None):
        self.whole_image_path = whole_image_path
        self.bag_size = bag_size
        self.clinical_vector = clinical_vector

        patch_images = cut_image_into_patches(whole_image_path, patch_size=patch_size, blank_ratio_thresh=blank_ratio_thresh)
        if len(patch_images) < bag_size:
            raise ValueError(f"有效patch数量不足：仅{len(patch_images)}，至少需要{bag_size}")
        rng = random.Random(seed)
        rng.shuffle(patch_images)
        total = (len(patch_images) // bag_size) * bag_size
        self.patch_images = patch_images[:total]

    def __len__(self):
        return len(self.patch_images) // self.bag_size

    def __getitem__(self, index):
        start = index * self.bag_size
        imgs = self.patch_images[start:start + self.bag_size]
        patch_tensor_list = []
        for pil_img in imgs:
            t = _transform(pil_img)
            t = torch.unsqueeze(t, dim=0)
            patch_tensor_list.append(t)
        bag_tensor = torch.cat(patch_tensor_list, dim=0)

        data = {
            "bag_tensor": bag_tensor,
            "label": torch.tensor(0, dtype=torch.long),
            "patient_id": ["input"],
            "patch_paths": [[f"mem_patch_{i}"] for i in range(self.bag_size)],
        }
        if self.clinical_vector is not None:
            data["clinical_data"] = self.clinical_vector
        return data



@torch.no_grad()
def run_inference(model, data_loader, device, num_classes, need_clinical=False):
    id_list = []
    label_list = []
    score_list = []

    model.eval()
    for index, item in enumerate(data_loader, start=1):
        bag_tensor, label = item["bag_tensor"].to(device), item["label"].to(device)
        if "clinical_data" in item:
            clinical_data = item["clinical_data"][0].to(device)
        else:
            clinical_data = torch.zeros((1, 5), dtype=torch.float32, device=device) if need_clinical else None

        output, _ = model(bag_tensor, clinical_data)
        prob = F.softmax(output, dim=-1).squeeze(0).detach().cpu().numpy()

        if num_classes == 2:
            score = float(prob[1])
        else:
            score = prob  # 多分类保留向量

        id_list.append(item["patient_id"][0])
        label_list.append(int(label.item()))
        score_list.append(score)

    return id_list, label_list, score_list


def predict_whole_image(
    image_path,
    ckpt_path=_DEFAULT_CKPT,
    output_csv=None,
    clinical_data=None,
    num_classes=3,
    backbone="vgg16_bn",
    bag_size=10,
    merge_method="mean",
    seed=8888,
    patch_size=256,
    blank_ratio_thresh=0.3,
    write_per_image_csv=False,
):
    """
    整图预测函数：从整图切patch组成多个bag进行推理，返回预测结果和保存位置。
    
    Args:
        image_path (str): 输入图像路径
        ckpt_path (str): 模型权重路径，默认使用2.pth
        output_csv (str): 输出CSV路径（仅当 write_per_image_csv=True 时生效）
        clinical_data (list): 临床数据列表[5个数值]，可选
        num_classes (int): 分类数量，默认3
        backbone (str): 骨干网络，默认vgg16_bn
        bag_size (int): 每个bag的patch数量，默认10
        merge_method (str): 合并方法，默认mean
        seed (int): 随机种子，默认8888
        patch_size (int): patch尺寸，默认256
        blank_ratio_thresh (float): 空白过滤阈值，默认0.3
    
    Returns:
        dict: {
            'pred_name': str,  # 预测类别名
            'pred_idx': int,   # 预测类别索引
            'pred_prob_top1': float,  # Top-1概率
            'all_probs': list,  # 所有类别概率
            'output_csv': str,  # 输出CSV路径
            'bag_count': int    # bag数量
        }
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    # 不默认写单图CSV，只有在明确要求时才写
    if write_per_image_csv and output_csv is None:
        base_name = os.path.splitext(os.path.basename(image_path))[0]
        output_csv = f"{base_name}_prediction.csv"
    
    # 构建临床向量
    clinical_tensor = None
    if clinical_data is not None:
        if len(clinical_data) != 5:
            raise ValueError("clinical_data 需要提供5个数值")
        clinical_tensor = torch.tensor(clinical_data, dtype=torch.float32)
    
    # 构建数据集
    dataset = MultiBagFromWholeImageDataset(
        whole_image_path=image_path,
        bag_size=bag_size,
        seed=seed,
        patch_size=patch_size,
        blank_ratio_thresh=blank_ratio_thresh,
        clinical_vector=clinical_tensor,
    )
    data_loader = torch.utils.data.DataLoader(dataset=dataset, batch_size=1, shuffle=False, pin_memory=True, num_workers=0)
    
    # 加载模型
    state_dict = torch.load(ckpt_path, map_location=device)
    model = None
    need_clinical = False
    try:
        model_try = MILNetWithClinicalData(num_classes=num_classes, backbone_name=backbone)
        model_try.load_state_dict(state_dict, strict=True)
        model = model_try
        need_clinical = True
    except Exception:
        model_try = MILNetImageOnly(num_classes=num_classes, backbone_name=backbone)
        model_try.load_state_dict(state_dict, strict=True)
        model = model_try
        need_clinical = False
    model = model.to(device)
    
    # 推理
    id_list, label_list, score_list = run_inference(model, data_loader, device, num_classes, need_clinical=need_clinical)
    
    # 合并结果
    id_list = [str(x) for x in id_list]
    label_list = [int(x) for x in label_list]
    id_list, label_list, score_list, bag_num_list = merge_result(id_list, label_list, score_list, merge_method)
    
    # 计算预测结果
    class_names = ["N0", "N+(1-2)", "N+(>2)"] if num_classes == 3 else ["N0", "N+"]
    s = score_list[0]  # 合并后只有一个结果
    if num_classes == 2:
        prob_pos = float(s)
        idx = 1 if prob_pos >= 0.5 else 0
        prob_top1 = prob_pos if idx == 1 else 1.0 - prob_pos
        all_probs = [1.0 - prob_pos, prob_pos]
    else:
        vec = np.asarray(s, dtype=float)
        idx = int(vec.argmax())
        prob_top1 = float(vec[idx])
        all_probs = vec.tolist()
    
    # 可选：保存单图结果CSV
    if write_per_image_csv and output_csv is not None:
        df = pd.DataFrame({
            "id": id_list,
            "label": label_list,
            "bag_num": bag_num_list,
            "score": score_list,
            "pred_idx": [idx],
            "pred_name": [class_names[idx]],
            "pred_prob_top1": [prob_top1],
        })
        os.makedirs(os.path.dirname(output_csv) or ".", exist_ok=True)
        df.to_csv(output_csv, index=False)
    
    return {
        'pred_name': class_names[idx],
        'pred_idx': idx,
        'pred_prob_top1': prob_top1,
        'all_probs': all_probs,
        'output_csv': output_csv,
        'bag_count': bag_num_list[0] if bag_num_list else 1
    }





def _is_image_file(path):
    exts = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
    return os.path.splitext(path.lower())[1] in exts


def alnpre_predict_auto(input_path):
    """自动识别输入路径；若为文件则单图预测；若为目录则批量预测。

    始终返回统一结构：{"results": [dict...], "aggregate_csv": str, "summary_text": str}
    """
    # 设置默认参数
    ckpt_path = os.environ.get(
        "ALNPRE_CHECKPOINT_PATH",
        os.path.join(_ALNPRE_API_DIR, "checkpoints", "2.pth"),
    )
    # 直接用全局 OUTPUT_DIR，不再每次重算
    output_dir = OUTPUT_DIR
    clinical_data = None
    num_classes = 3
    backbone = "vgg16_bn"
    bag_size = 10
    merge_method = "mean"
    seed = 8888
    patch_size = 256
    blank_ratio_thresh = 0.3
    aggregate_csv = None
    
    input_path = os.path.abspath(input_path)
    is_file = os.path.isfile(input_path)
    if is_file:
        # 单图：确保输出目录
        if output_dir is None:
            output_dir = os.path.dirname(input_path) or "."
        os.makedirs(output_dir, exist_ok=True)

        base_name = os.path.splitext(os.path.basename(input_path))[0]
        per_image_csv = os.path.join(output_dir, f"{base_name}_prediction.csv")

        r = predict_whole_image(
            image_path=input_path,
            ckpt_path=ckpt_path,
            output_csv=per_image_csv,
            clinical_data=clinical_data,
            num_classes=num_classes,
            backbone=backbone,
            bag_size=bag_size,
            merge_method=merge_method,
            seed=seed,
            patch_size=patch_size,
            blank_ratio_thresh=blank_ratio_thresh,
        )
        r["image"] = os.path.basename(input_path)
        results = [r]

        # 构建聚合CSV路径
        if aggregate_csv is None:
            aggregate_csv = os.path.join(output_dir, f"{base_name}_batch_predictions.csv")
    else:
        if not os.path.isdir(input_path):
            raise FileNotFoundError(f"找不到输入路径: {input_path}")

        image_files = [
            os.path.join(input_path, f)
            for f in sorted(os.listdir(input_path))
            if _is_image_file(f)
        ]
        if len(image_files) == 0:
            raise ValueError(f"目录中未找到图像文件: {input_path}")

        if output_dir is None:
            output_dir = input_path
        os.makedirs(output_dir, exist_ok=True)

        results = []
        for img_path in image_files:
            base_name = os.path.splitext(os.path.basename(img_path))[0]
            out_csv = os.path.join(output_dir, f"{base_name}_prediction.csv")
            r = predict_whole_image(
                image_path=img_path,
                ckpt_path=ckpt_path,
                output_csv=out_csv,
                clinical_data=clinical_data,
                num_classes=num_classes,
                backbone=backbone,
                bag_size=bag_size,
                merge_method=merge_method,
                seed=seed,
                patch_size=patch_size,
                blank_ratio_thresh=blank_ratio_thresh,
            )
            r["image"] = os.path.basename(img_path)
            results.append(r)

        if aggregate_csv is None:
            dir_name = os.path.basename(os.path.normpath(input_path)) or "batch"
            aggregate_csv = os.path.join(output_dir, f"{dir_name}_batch_predictions.csv")

    # 写聚合CSV：每行“图片名: 预测结果”
    summary_lines = [f"{x['image']}: {x['pred_name']}" for x in results]
    df = pd.DataFrame({"summary": summary_lines})
    df.to_csv(aggregate_csv, index=False, header=False)

    summary_text = "\n".join(summary_lines)

    return {"results": results, "aggregate_csv": aggregate_csv, "summary_text": summary_text}


if __name__ == "__main__":


    out = alnpre_predict_auto(input_path="/home/user/weijing/cancer-demo/wsi-result/brca-transparent/test3_cancer_transparent.png")


    print(f"已生成聚合CSV: {out['aggregate_csv']}")
    print(out["summary_text"]) 