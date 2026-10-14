"""
简化的癌症分割函数接口
只需要输入图片地址和输出图片地址，其他参数使用默认值
方便整合到其他项目中被调用
"""

import os
import torch
import cv2
import numpy as np
from PIL import Image
from pathlib import Path

# 导入模型相关模块
from code.sseg.models.segmentors.generalized_segmentor import GeneralizedSegmentor
from code.sseg.models.segmentors.uda_segmentor import UDASegmentor
from code.sseg.models.default import cfg
from code.sseg.models.backbones import resnet
from code.sseg.models.decoder import unet_decoder_v2
from code.sseg.models.predictor import base_predictor
from code.sseg.models.losses import bce_loss, dice_loss
from code.sseg.models.discriminator import base_discriminator

# 全局变量存储模型，避免重复加载
_global_model = None
_global_cfg = None
_global_device = None

# 可控打印（默认静默）
_VERBOSE = True

def set_verbose(flag: bool):
    global _VERBOSE
    _VERBOSE = bool(flag)

def _vprint(*args, **kwargs):
    if _VERBOSE:
        print(*args, **kwargs)

def _load_model_once():
    """只加载一次模型，提高效率"""
    global _global_model, _global_cfg, _global_device
    
    if _global_model is not None:
        return _global_model, _global_cfg, _global_device
    
    # 默认配置
    _seg_api_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.environ.get(
        "SEG_CONFIG_PATH",
        os.path.join(_seg_api_dir, "code", "config", "cac-unet-r50.yaml"),
    )
    checkpoint_path = os.environ.get(
        "SEG_CHECKPOINT_PATH",
        os.path.join(_seg_api_dir, "code", "config", "val_highest.pth"),
    )
    device_id = 0
    
    # 检查文件是否存在
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"配置文件不存在: {config_file}")
    if not os.path.exists(checkpoint_path):
        raise FileNotFoundError(f"模型权重文件不存在: {checkpoint_path}")
    
    # 设置设备
    _global_device = torch.device(f'cuda:{device_id}' if torch.cuda.is_available() else 'cpu')
    
    # 加载配置
    cfg.merge_from_file(config_file)
    cfg.freeze()
    _global_cfg = cfg
    
    # 创建模型
    if cfg.MODEL.TYPE == "Generalized_Segmentor":
        _global_model = GeneralizedSegmentor(cfg)
    elif cfg.MODEL.TYPE == "UDA_Segmentor" or cfg.MODEL.TYPE == "SDA_Segmentor":
        _global_model = UDASegmentor(cfg)
    else:
        raise ValueError(f"Unsupported model type: {cfg.MODEL.TYPE}")
    
    # 加载权重
    state_dict = torch.load(checkpoint_path, map_location=_global_device, weights_only=True)
    model_dict = _global_model.state_dict()
    pretrained_dict = {k: v for k, v in state_dict.items() if k in model_dict}
    model_dict.update(pretrained_dict)
    _global_model.load_state_dict(model_dict)
    
    _global_model.to(_global_device)
    _global_model.eval()
    
    _vprint(f"癌症分割模型已加载，使用设备: {_global_device}")
    return _global_model, _global_cfg, _global_device

def _preprocess_image(image_path, resize_size=None, normalize=True):
    """预处理图像"""
    # 读取图像
    if isinstance(image_path, str):
        image = Image.open(image_path).convert('RGB')
    else:
        image = image_path
    
    # 调整尺寸
    if resize_size:
        image = image.resize(resize_size, Image.LANCZOS)
    
    # 转换为tensor
    image_tensor = torch.from_numpy(np.array(image)).permute(2, 0, 1).float() / 255.0
    
    # 归一化（使用ImageNet统计值）
    if normalize:
        mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
        image_tensor = (image_tensor - mean) / std
    
    return image_tensor.unsqueeze(0)  # 添加batch维度

def _postprocess_prediction(logits, threshold=0.5):
    """后处理预测结果"""
    # 应用sigmoid激活
    probs = torch.sigmoid(logits)
    
    # 对于多类别输出，取前景类别（通常是非背景类别）
    if probs.shape[1] > 1:
        # 取第1个类别（前景）的预测
        foreground_probs = probs[:, 1:2, :, :]  # 保持4D形状
    else:
        foreground_probs = probs
    
    # 二值化
    pred_mask = (foreground_probs > threshold).float()
    
    # 移除batch维度，保留单通道
    return pred_mask.squeeze(0).squeeze(0).cpu().numpy()  # 最终得到2D数组

def _extract_cancer_region(original_image, pred_mask, threshold=0.5):
    """提取癌症区域（过滤掉正常组织部分）"""
    # 将预测mask转换为二值图像
    binary_mask = (pred_mask > threshold).astype(np.uint8)
    
    # 如果原图是PIL图像，转换为numpy数组
    if isinstance(original_image, Image.Image):
        original_array = np.array(original_image)
    else:
        original_array = original_image
    
    # 调整mask尺寸以匹配原图
    if binary_mask.shape != original_array.shape[:2]:
        binary_mask = cv2.resize(binary_mask, (original_array.shape[1], original_array.shape[0]), interpolation=cv2.INTER_NEAREST)
    
    # 创建透明背景的癌症区域图像
    cancer_region_rgba = None
    if len(original_array.shape) == 3:
        cancer_region_rgba = np.zeros((original_array.shape[0], original_array.shape[1], 4), dtype=np.uint8)
        cancer_region_rgba[:, :, :3] = original_array
        cancer_region_rgba[:, :, 3] = binary_mask * 255  # Alpha通道
    
    return binary_mask, cancer_region_rgba

def _process_single_image(input_image_path, output_image_path, net, cfg, device, threshold=0.5):
    """
    处理单张图像的内部函数
    
    Args:
        input_image_path (str): 输入图像路径
        output_image_path (str): 输出图像路径
        net: 模型
        cfg: 配置
        device: 设备
        threshold (float): 预测阈值
    
    Returns:
        dict: 包含输出文件路径的字典
    """
    # 读取原始图像
    original_image = Image.open(input_image_path).convert('RGB')
    
    # 预处理
    image_tensor = _preprocess_image(input_image_path, cfg.DATASET.RESIZE_SIZE if hasattr(cfg.DATASET, 'RESIZE_SIZE') else None)
    image_tensor = image_tensor.to(device)
    
    # 推理
    with torch.no_grad():
        logits = net(image_tensor)
        if isinstance(logits, dict):
            logits = logits.get('logits', logits)
    
    # 后处理
    pred_mask = _postprocess_prediction(logits, threshold)
    
    # 提取癌症区域
    binary_mask, cancer_region_rgba = _extract_cancer_region(original_image, pred_mask, threshold)
    
    # 生成输出文件名
    base_path = os.path.splitext(output_image_path)[0]
    output_files = {}
    
    # 1. 保存透明背景的癌症区域图像（正常组织透明）
    if cancer_region_rgba is not None:
        cancer_transparent_path = f"{base_path}_cancer_transparent.png"
        try:
            cancer_rgba_pil = Image.fromarray(cancer_region_rgba, 'RGBA')
            cancer_rgba_pil.save(cancer_transparent_path)
            output_files['cancer_transparent'] = cancer_transparent_path
            _vprint(f"透明背景癌症区域图像已保存到: {cancer_transparent_path}")
        except Exception as e:
            _vprint(f"保存透明背景图像失败: {e}")
    
    # 2. 保存蓝色轮廓标记的癌症区域图片
    cancer_contour_path = f"{base_path}_cancer_contour.png"
    try:
        # 在原图上绘制蓝色轮廓
        original_array = np.array(original_image)
        if len(original_array.shape) == 3:
            # 调整mask尺寸以匹配原图
            if binary_mask.shape != original_array.shape[:2]:
                binary_mask_resized = cv2.resize(binary_mask, (original_array.shape[1], original_array.shape[0]), interpolation=cv2.INTER_NEAREST)
            else:
                binary_mask_resized = binary_mask
            
            # 找到轮廓
            contours, _ = cv2.findContours(binary_mask_resized, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            # 在原图上绘制蓝色轮廓
            contour_image = original_array.copy()
            cv2.drawContours(contour_image, contours, -1, (0, 0, 255), 2)  # 蓝色轮廓，线宽为2
            
            # 保存图片
            contour_pil = Image.fromarray(contour_image.astype(np.uint8))
            contour_pil.save(cancer_contour_path)
            output_files['cancer_contour'] = cancer_contour_path
            _vprint(f"蓝色轮廓标记癌症区域图像已保存到: {cancer_contour_path}")
        else:
            _vprint("原图不是彩色图像，无法绘制蓝色轮廓")
    except Exception as e:
        _vprint(f"保存蓝色轮廓图像失败: {e}")
    
    return output_files

def segment_cancer(input_path, output_path, threshold=0.5):
    """
    智能癌症分割函数 - 自动识别单张图片或批量处理
    
    Args:
        input_path (str): 输入路径（可以是单张图片或包含图片的文件夹）
        output_path (str): 输出路径
            - 如果输入是单张图片：输出路径为输出文件的基础名称
            - 如果输入是文件夹：输出路径为输出文件夹路径
        threshold (float): 预测阈值，默认为0.5
    
    Returns:
        dict: 包含处理结果的字典
            - 单张图片：包含输出文件路径的字典
            - 批量处理：包含所有处理结果的字典列表
    """
    # 检查输入路径是否存在
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"输入路径不存在: {input_path}")
    
    # 加载模型（只加载一次）
    net, cfg, device = _load_model_once()
    
    # 判断输入类型
    if os.path.isfile(input_path):
        # 单张图片处理
        _vprint(f"检测到单张图片: {input_path}")
        
        # 确保输出目录存在
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        
        # 处理单张图片
        return _process_single_image(input_path, output_path, net, cfg, device, threshold)
        
    elif os.path.isdir(input_path):
        # 批量处理
        _vprint(f"检测到图片文件夹: {input_path}")
        
        # 确保输出目录存在
        os.makedirs(output_path, exist_ok=True)
        
        # 支持的图片格式
        image_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif')
        
        # 获取所有图片文件
        image_files = []
        for ext in image_extensions:
            image_files.extend(Path(input_path).glob(f"*{ext}"))
            image_files.extend(Path(input_path).glob(f"*{ext.upper()}"))
        
        image_files = [str(f) for f in image_files]
        
        if not image_files:
            _vprint(f"在文件夹 {input_path} 中未找到支持的图片文件")
            return {}
        
        _vprint(f"找到 {len(image_files)} 张图片需要处理")
        
        # 批量处理结果
        all_results = {}
        successful_count = 0
        
        for i, image_file in enumerate(image_files):
            try:
                # 生成输出文件名
                base_name = Path(image_file).stem
                output_file_path = os.path.join(output_path, base_name)
                
                _vprint(f"处理图片 {i+1}/{len(image_files)}: {os.path.basename(image_file)}")
                
                # 处理单张图片
                result = _process_single_image(image_file, output_file_path, net, cfg, device, threshold)
                
                if result:
                    all_results[os.path.basename(image_file)] = result
                    successful_count += 1
                
            except Exception as e:
                _vprint(f"处理图片 {image_file} 失败: {e}")
                continue
        
        _vprint(f"批量处理完成！成功处理 {successful_count}/{len(image_files)} 张图片")
        return all_results
        
    else:
        raise ValueError(f"输入路径既不是文件也不是文件夹: {input_path}")

# 为了向后兼容，提供一个别名
cancer_segmentation = segment_cancer

if __name__ == "__main__":
    # 示例用法
    import sys
    
    if len(sys.argv) != 3:
        print("用法: python simple_cancer_segmentation.py <输入路径> <输出路径>")
        print("输入路径可以是：")
        print("  - 单张图片文件: python simple_cancer_segmentation.py image.jpg output")
        print("  - 图片文件夹: python simple_cancer_segmentation.py input_folder/ output_folder/")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2]
    
    try:
        results = segment_cancer(input_path, output_path)
        print("分割完成！")
        
        if isinstance(results, dict):
            if 'cancer_transparent' in results:
                # 单张图片结果
                print("输出文件：")
                for key, path in results.items():
                    print(f"  {key}: {path}")
            else:
                # 批量处理结果
                print(f"批量处理结果：共处理 {len(results)} 张图片")
                for filename, file_results in results.items():
                    print(f"  {filename}:")
                    for key, path in file_results.items():
                        print(f"    {key}: {path}")
        else:
            print("处理结果：", results)
            
    except Exception as e:
        print(f"分割失败: {e}")
        sys.exit(1)
