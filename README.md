# Cancer Multi-Agent Demo

病理切片多智能体协作分析系统。1 个 Leader + 4 个子智能体（乳腺 / 结肠预测、肿瘤分割、淋巴结转移预测），基于 ACPs 群组协议协作，前端由 Leader 提供。

---

## 1. 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Linux x86_64（Ubuntu 20.04 / 22.04 已验证） |
| Python | 3.10（主环境 `brcapre-dev`）；3.8（Caffe 子进程环境 `caffe-py38`） |
| Conda | 已安装并 `conda init`（`conda activate` 可用） |
| 内存 | ≥ 8 GB |
| 磁盘 | ≥ 20 GB |
| GPU | 可选（分割智能体默认绑 GPU 3，可在 `scripts/start_system.sh` 改） |
| 网络 | 能访问 ACPs 平台（`wt.ioa.pub`）和大模型 API |

---

## 2. 一键部署

```bash
# 1. 拉代码
git clone https://github.com/LemonWei111/cancer-demo.git ~/cancer-demo && cd ~/cancer-demo

# 2. 一键部署（建 conda 环境、建目录、生成 .env、装依赖）
bash deploy.sh

# 3. 填 API Key + 平台证书
nano leader-agent/server-api/.env
```

`deploy.sh` 会自动完成：

- 建 `brcapre-dev`（Python 3.10）+ 装 `requirements.txt`
- 建 `caffe-py38`（Python 3.8 + Caffe 1.0）
- 建运行时目录：`upload/ wsi-result/ logs/ pids/ tmp/`
- 从 `env.example` 为 5 个服务生成 `.env`

预计 20-40 分钟（主要是 `caffe-py38`）。

**部署后必做**：

1. 填 API Key + 证书（见 §3）
2. 放好模型文件（见 §4）
3. 启动：`bash scripts/start_system.sh`
4. 浏览器访问 `http://<本机IP>:8021/`

---

## 3. 配置（`.env`）

Leader 的 `leader-agent/server-api/.env` 必填：

```bash
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus
LEADER_PUBLIC_BASE_URL=http://<本机IP>:8021
```

每个 agent 的 `.env` 必填：

```bash
ACPS_CERT_FILE=/path/to/cert.pem
ACPS_KEY_FILE=/path/to/key.pem
ACPS_CA_FILE=/path/to/ca.pem
ACPS_PARTNER_AIC=<平台分配的 AIC>     # Leader 用 ACPS_AIC
```

支持的大模型 API：

| 服务 | `OPENAI_BASE_URL` | 模型示例 |
|------|-------------------|---------|
| 阿里云 DashScope | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| 月之暗面 | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:7b` |

---

## 4. 模型文件（全部需要自备）

> **出于隐私和版权考虑，所有模型文件均不随代码分发。**

各智能体模型期望路径（可用 `.env` 覆盖）：

| 智能体 | 模型 | 默认期望路径 | `.env` 覆盖键 |
|--------|------|-------------|--------------|
| brca-pre | 乳腺全局模型 | `~/BreastCancerCNN-master/model/*.caffemodel` | `BRCA_GLOBAL_MODEL_PATH` |
| brca-pre | 乳腺 patch 模型 | `~/BreastCancerCNN-master/model-patch/*.caffemodel` | `BRCA_PATCH_MODEL_PATH` |
| brca-pre | 训练集均值 | `partners/brca-pre-agent/server-api/train_224_mean.npy` | `BRCA_MEAN_FILE_PATH` |
| coad-pre | 结肠分类模型 | `partners/coad-pre-agent/server-api/checkpoints/` | `COAD_CHECKPOINT_PATH` |
| seg | 分割模型 | `partners/seg-agent/server-api/checkpoints/` | `SEG_CHECKPOINT_PATH` |
| alnpre | 淋巴结模型 | `partners/alnpre-agent/server-api/checkpoints/2.pth` | `ALNPRE_CHECKPOINT_PATH` |

**验证模型是否就位**：

```bash
# 重新运行部署脚本会做严格检查
bash deploy.sh
```

`deploy.sh` 会检查每个模型的**扩展名白名单**（`.caffemodel / .pth / .pt / .ckpt / .h5 / .onnx / .pb / .bin`），缺失或目录里没有有效模型文件时会明确报出来。

---

## 5. 目录结构

```
cancer-demo/
├── deploy.sh                       # 一键部署
├── env.example                     # .env 模板
├── requirements.txt
├── caffe-py38-environment.yml
│
├── common/                         # 公共模块（PYTHONPATH 注入）
│   ├── acps_config.py              # ★ 唯一配置来源
│   ├── acps_emitters.py
│   ├── acps_utils.py
│   └── acps_subagent_adapter.py
│
├── leader-agent/server-api/        # Leader（HTTP + ACPs Partner + Group Leader）
├── partners/
│   ├── brca-pre-agent/server-api/
│   ├── coad-pre-agent/server-api/
│   ├── seg-agent/server-api/
│   └── alnpre-agent/server-api/
│
├── scripts/
│   ├── start_system.sh
│   ├── stop_system.sh
│   └── check_paths.sh              # 排查写盘/读盘不一致
│
├── acps_cli/                         # 可信注册工具包，用于获取证书
├── upload/                         # 运行时生成
├── wsi-result/                     # ★ 结果发布目录（运行时生成）
├── logs/ pids/ tmp/
```

---

## 6. 关键路径约定

**唯一权威：`<项目根>/wsi-result/`**

- 所有 agent 写盘 → `wsi-result/{brca-contour,brca-transparent,brca-aln}/`
- Leader 静态服务读盘 → `app.mount("/wsi-result", StaticFiles(directory=WSI_RESULT_DIR))`
- 对外 URL → `{LEADER_PUBLIC_BASE_URL}/wsi-result/<rel>`

`common/acps_config.py` 里：

```python
PROJECT_ROOT = Path(__file__).resolve().parent.parent   # = 项目根
WSI_RESULT_DIR = PROJECT_ROOT / "wsi-result"
SEG_RESULT_DIR = WSI_RESULT_DIR      # 别名
```

业务代码里统一用：

```python
_PROJECT_ROOT = os.environ.get("PROJECT_ROOT", str(Path(__file__).resolve().parents[3]))
WSI_RESULT_ROOT = os.path.join(_PROJECT_ROOT, "wsi-result")
```

`parents[N]` 的 N 错一层就 404。对照：

| 文件 | N |
|------|---|
| `common/acps_config.py` | `parent.parent`（= 2） |
| `partners/seg-agent/server-api/integrated_wsi_pipeline.py` | 4 |
| `partners/alnpre-agent/server-api/infer.py` | 4 |

**排查 404 第一步**：确认所有 agent 启动日志里的 `_PROJECT_ROOT` 一致。

---

## 7. 启动 / 停止 / 日志

```bash
conda activate brcapre-dev && cd ~/cancer-demo
bash scripts/start_system.sh           # 启动
bash scripts/stop_system.sh            # 停止
tail -f logs/leader-agent.log          # 日志
curl http://localhost:8021/health      # 健康检查
bash scripts/check_paths.sh            # 路径自检
```

| 服务 | 端口 |
|------|------|
| leader-agent | 8021 |
| brca-pre-agent | 8011 |
| coad-pre-agent | 8012 |
| seg-agent | 8013 |
| alnpre-agent | 8014 |

---

## 8. 常见问题

| 现象 | 排查 |
|------|------|
| 前端"连接失败" | ① `curl :8021/health` ② 检查 `OPENAI_API_KEY` ③ 用服务器 IP ④ 检查防火墙 |
| 前端拉图 404 | 跑 `bash scripts/check_paths.sh`；确认所有 agent 日志的 `_PROJECT_ROOT` 一致 |
| `AIP identity binding disabled` | `load_dotenv()` 必须在所有 `from acps_* import ...` 之前 |
| `ChannelInvalidStateError` | `on_mgmt_command` 收到 DISBAND 时**不要 close**，只 pop 引用 |
| `Idempotence will be disabled` | `acps_emitters.py` 里 KafkaProducer 用 `acks="all" + enable_idempotence=True` |
| `PartnerInviteError: already has a pending invitation` | 已在 `_open_group_impl` 里做 3 次退避重试；仍失败则跳过该 agent 继续 |
| 推理 `confidence=0.0` | 模型文件缺失或路径不对，见 §4 |
| `Address already in use` | `bash scripts/stop_system.sh` 再 `start_system.sh` |

---

## 9. 隐私与合规提醒

**本仓库不含任何敏感数据**。以下内容请自行获取。

- 平台证书 / 私钥 / CA（`*.pem` / `*.key`）（请**通过acps_cli获取**，参见scripts/ask_certs.sh）
- EAB 凭据（`*_eab.json`）
- 模型权重（`*.caffemodel` / `*.pth` / `*.onnx` / ...）
- 训练集均值（`*.npy`）
...

---

## 附：支持的大模型 API

（见 §3 表格）

## 致谢与许可

本项目包含来自 **[ACPs-community](https://github.com/AIP-PUB/ACPs-community/tree/v2.2.0)** (v2.2.0) 的代码，具体位于 `acps_cli` 目录。

该部分代码适用 **木兰宽松许可证，第2版（MulanPSL-2.0）**。

本项目其余代码适用 MIT。
