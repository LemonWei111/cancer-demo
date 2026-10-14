# common/acps_config.py
"""
统一配置：MQ / Kafka / 路径 / 身份绑定。

设计原则：
- 与**进程无关**的常量（路径、Kafka topic、超时等）在 import 时固化；
- 与**进程相关**的变量（证书三件套、每个智能体可能不同的 AIC 等）
  通过函数延迟读取，避免多进程/多次 import 时串值；
- 所有值都支持环境变量覆盖，默认值保证单机可跑。

★ 单一权威：WSI_RESULT_DIR
  所有 agent 写盘 + Leader 读盘都从这里取，禁止在其他文件里
  再定义第二份（如 main.py 里写死、业务代码里写死）。
"""
import os
import ssl
from pathlib import Path

# ---------------------------------------------------------------------------
# 项目根目录
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(os.getenv(
    "PROJECT_ROOT",
    str(Path(__file__).resolve().parent.parent),
))

# ---------------------------------------------------------------------------
# 单一权威：WSI 结果发布目录
# ---------------------------------------------------------------------------
# 所有 agent（seg / brca / coad / alnpre）的**输出**都落在这里；
# Leader 的静态服务也从这里**读取**；
# URL 一律是 {LEADER_PUBLIC_BASE_URL}/wsi-result/<rel>
WSI_RESULT_DIR = Path(os.getenv(
    "WSI_RESULT_DIR",
    str(PROJECT_ROOT / "wsi-result"),   # 默认：<项目根>/wsi-result
))

# 兼容旧命名：SEG_RESULT_DIR 现在只是 WSI_RESULT_DIR 的别名
SEG_RESULT_DIR = WSI_RESULT_DIR

# 上传目录（用户上传的原图）
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(PROJECT_ROOT / "upload")))

# ---------------------------------------------------------------------------
# MQ 主机级配置（进程无关）
# ---------------------------------------------------------------------------
MQ_HOST = os.getenv("ACPS_MQ_HOST", "wt.ioa.pub")
MQ_PORT = int(os.getenv("ACPS_MQ_PORT", "5671"))
MQ_VHOST = os.getenv("ACPS_MQ_VHOST", "acps")
MQ_AUTH_SERVICE_URL = os.getenv("ACPS_AUTH_SERVICE_URL", "https://wt.ioa.pub:9007")

DISCOVERY_URL = os.getenv(
    "ACPS_DISCOVERY_URL",
    "https://wt.ioa.pub/discovery/acps-adp-v2/discover",
)

# ---------------------------------------------------------------------------
# 身份绑定（惰性读取，避免 import 时机问题）
# ---------------------------------------------------------------------------
def is_identity_binding_enabled() -> bool:
    return os.getenv("ACPS_IDENTITY_BINDING", "0") == "1"


# ---------------------------------------------------------------------------
# Kafka（进程无关）
# ---------------------------------------------------------------------------
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "wt.ioa.pub:19092")
KAFKA_TOPICS = {
    "heartbeat": os.getenv("KAFKA_TOPIC_HEARTBEAT", "amp.heartbeat"),
    "metrics":   os.getenv("KAFKA_TOPIC_METRICS",   "amp.metrics"),
    "access":    os.getenv("KAFKA_TOPIC_ACCESS",    "amp.access"),
    "message":   os.getenv("KAFKA_TOPIC_MESSAGE",   "amp.message"),
}
KAFKA_QUEUE_MAXSIZE = int(os.getenv("KAFKA_QUEUE_MAXSIZE", "10000"))

# ---------------------------------------------------------------------------
# 日志 / 临时文件
# ---------------------------------------------------------------------------
AMP_LOG_DIR = Path(os.getenv("AMP_LOG_DIR", str(PROJECT_ROOT / "logs" / "amp")))
ACPS_UPLOAD_DIR = Path(os.getenv("ACPS_UPLOAD_DIR", "/tmp"))

# ---------------------------------------------------------------------------
# Leader HTTP
# ---------------------------------------------------------------------------
LEADER_PUBLIC_BASE_URL = os.getenv("LEADER_PUBLIC_BASE_URL", "http://10.106.128.139:8021")
LEADER_AGENT_PORT = int(os.getenv("LEADER_AGENT_PORT", "8021"))

# ---------------------------------------------------------------------------
# 超时
# ---------------------------------------------------------------------------
TIMEOUT_PREDICTOR = int(os.getenv("TIMEOUT_PREDICTOR", "120"))
TIMEOUT_SEG = int(os.getenv("TIMEOUT_SEG", "300"))
TIMEOUT_ALNPRE = int(os.getenv("TIMEOUT_ALNPRE", "300"))
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "30"))
METRICS_INTERVAL = int(os.getenv("METRICS_INTERVAL", "60"))


# ===========================================================================
# 进程相关：证书三件套 & mq_config —— 每次调用都从 env 读
# ===========================================================================
def get_cert_paths() -> dict:
    return {
        "cert_file": os.getenv("ACPS_CERT_FILE", ""),
        "key_file": os.getenv("ACPS_KEY_FILE", ""),
        "ca_file": os.getenv("ACPS_CA_FILE", ""),
    }


def mq_config() -> dict:
    certs = get_cert_paths()
    return {
        "host": MQ_HOST,
        "port": MQ_PORT,
        "vhost": MQ_VHOST,
        "cert_file": certs["cert_file"],
        "key_file": certs["key_file"],
        "ca_file": certs["ca_file"],
        "auth_service_url": MQ_AUTH_SERVICE_URL,
    }


def build_mq_ssl_context(cert_file=None, key_file=None, ca_file=None):
    if cert_file is None or key_file is None or ca_file is None:
        certs = get_cert_paths()
        cert_file = cert_file or certs["cert_file"]
        key_file = key_file or certs["key_file"]
        ca_file = ca_file or certs["ca_file"]

    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
    ctx.load_verify_locations(cafile=ca_file)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def silence_noisy_loggers():
    import logging

    if not is_identity_binding_enabled():
        logging.getLogger("acps_sdk.aip.aip_group_partner").setLevel(logging.ERROR)
        logging.getLogger("acps_sdk.aip.aip_group_leader").setLevel(logging.ERROR)

    logging.getLogger("kafka").setLevel(logging.ERROR)

    # ★ 屏蔽 SDK 内部 DISBAND 时的 ChannelInvalidStateError 噪音
    class _DropChannelInvalidState(logging.Filter):
        def filter(self, record):
            try:
                msg = record.getMessage()
            except Exception:
                return True
            if "ChannelInvalidStateError" in msg:
                return False
            return True

    _flt = _DropChannelInvalidState()
    logging.getLogger("aio_pika").addFilter(_flt)
    logging.getLogger("aiormq").addFilter(_flt)
    logging.getLogger("asyncio").addFilter(_flt)


def ensure_dirs():
    """确保所有输出目录存在。各进程启动时调一次。"""
    for d in (WSI_RESULT_DIR, UPLOAD_DIR, AMP_LOG_DIR, ACPS_UPLOAD_DIR):
        Path(d).mkdir(parents=True, exist_ok=True)