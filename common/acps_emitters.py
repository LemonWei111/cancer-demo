# common/acps_emitters.py
"""
AMP 埋点 Emitter 集合 + Kafka 异步转发器。

关键改动：
- Kafka 发送走后台线程 + 有界队列，绝不阻塞事件循环
- get_forwarder 加锁，避免并发首次初始化竞态
- WsiMetricsSampler 支持并发安全的计数增减
- KafkaProducer 配置显式化（acks/enable_idempotence/retries 一致，无警告）
"""
import json
import logging
import threading
import time
from queue import Queue, Full, Empty
from typing import Optional

import psutil
from kafka import KafkaProducer

from acps_sdk.amp import (
    HeartbeatEmitter, MetricsEmitter, AccessEmitter, MessageEmitter,
    MetricsBody, LoadMetrics,
)

from acps_config import KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPICS, KAFKA_QUEUE_MAXSIZE

logger = logging.getLogger("AmpForwarder")

# ===========================================================================
# Kafka 异步转发器
# ===========================================================================
class AmpKafkaForwarder:
    """
    有界队列 + 后台线程，send() 永不阻塞。

    KafkaProducer 配置说明（一致性 vs 吞吐）：
    - 本实现选择 **不丢不重**（acks=all + enable_idempotence=True）
      理由：access/message 是审计日志，丢/重都会破坏可追溯性；
            埋点量不大（心跳 30s、指标 60s），吞吐不是瓶颈。
    - 如果未来切换为追求极致吞吐，需同时改三个字段：
        acks=1, retries=3, enable_idempotence=False
      否则 kafka-python 会打印 "Idempotence will be disabled" 警告。
    """

    def __init__(self, kafka_config: dict):
        self._bootstrap = kafka_config.get("bootstrap_servers", KAFKA_BOOTSTRAP_SERVERS)
        self._producer: Optional[KafkaProducer] = None
        self._producer_lock = threading.Lock()
        self._queue: Queue = Queue(maxsize=KAFKA_QUEUE_MAXSIZE)
        self._stopped = threading.Event()
        self._worker = threading.Thread(target=self._run, daemon=True, name="amp-kafka")
        self._worker.start()
        self._dropped = 0

    def _get_producer(self) -> KafkaProducer:
        if self._producer is None:
            with self._producer_lock:
                if self._producer is None:
                    self._producer = KafkaProducer(
                        bootstrap_servers=self._bootstrap,
                        security_protocol="PLAINTEXT",
                        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                        # ---- 一致性：不丢不重 ----
                        acks="all",
                        retries=5,
                        enable_idempotence=True,
                        # ---- 性能 / 缓冲 ----
                        linger_ms=50,
                        compression_type="gzip",
                        max_block_ms=10_000,
                        # buffer_memory 已移除（kafka-python 3.0.0 不再支持）
                    )
        return self._producer

    def send(self, log_type: str, record_dict: dict):
        """非阻塞入队。队列满则丢弃并计数（不阻塞业务）。"""
        if log_type not in KAFKA_TOPICS:
            return
        try:
            self._queue.put_nowait((log_type, record_dict))
        except Full:
            self._dropped += 1
            if self._dropped % 100 == 1:
                logger.warning("Kafka 队列已满，累计丢弃 %d 条", self._dropped)

    def _run(self):
        while not self._stopped.is_set():
            try:
                log_type, record = self._queue.get(timeout=0.5)
            except Empty:
                continue
            try:
                producer = self._get_producer()
                future = producer.send(KAFKA_TOPICS[log_type], record)
                future.get(timeout=10)
            except Exception as exc:
                logger.error("Kafka 发送失败 (%s): %s", log_type, exc)

    def close(self):
        self._stopped.set()
        self._worker.join(timeout=5)
        if self._producer is not None:
            try:
                self._producer.flush(timeout=5)
                self._producer.close(timeout=5)
            except Exception:
                pass


# 模块级单例 + 锁
_forwarder: Optional[AmpKafkaForwarder] = None
_forwarder_lock = threading.Lock()


def get_forwarder(kafka_config: Optional[dict] = None) -> AmpKafkaForwarder:
    global _forwarder
    if _forwarder is None:
        with _forwarder_lock:
            if _forwarder is None:
                _forwarder = AmpKafkaForwarder(
                    kafka_config or {"bootstrap_servers": KAFKA_BOOTSTRAP_SERVERS}
                )
    return _forwarder


# ===========================================================================
# Kafka Forwarding Mixin
# ===========================================================================
class KafkaForwardingMixin:
    """写本地文件后自动发 Kafka（异步，不阻塞）。"""

    _log_type: Optional[str] = None

    def _write_line(self, record):
        super()._write_line(record)
        try:
            record_dict = record.model_dump(mode="json", by_alias=True, exclude_none=True)
            get_forwarder().send(self._log_type, record_dict)
        except Exception:
            logger.warning("Kafka 转发失败", exc_info=True)


class KafkaHeartbeatEmitter(KafkaForwardingMixin, HeartbeatEmitter):
    _log_type = "heartbeat"


class KafkaMetricsEmitter(KafkaForwardingMixin, MetricsEmitter):
    _log_type = "metrics"


class KafkaAccessEmitter(KafkaForwardingMixin, AccessEmitter):
    _log_type = "access"


class KafkaMessageEmitter(KafkaForwardingMixin, MessageEmitter):
    _log_type = "message"


# ===========================================================================
# 指标采样器（并发安全）
# ===========================================================================
class WsiMetricsSampler:
    """并发安全的指标采样器。"""

    def __init__(self, max_active: int = 10, max_queued: int = 20):
        self._start_time = time.monotonic()
        self._max_active = max_active
        self._max_queued = max_queued
        self._active_tasks = 0
        self._queued_tasks = 0
        self._lock = threading.Lock()

    def inc_active(self, delta: int = 1):
        with self._lock:
            self._active_tasks = max(0, self._active_tasks + delta)

    def inc_queued(self, delta: int = 1):
        with self._lock:
            self._queued_tasks = max(0, self._queued_tasks + delta)

    def set_task_counts(self, active: int, queued: int):
        """兼容旧接口：直接设值（不推荐并发下使用）。"""
        with self._lock:
            self._active_tasks = max(0, active)
            self._queued_tasks = max(0, queued)

    def sample(self) -> MetricsBody:
        with self._lock:
            active = self._active_tasks
            queued = self._queued_tasks
        uptime = round(time.monotonic() - self._start_time, 3)
        load = LoadMetrics(
            active_tasks=active,
            queued_tasks=queued,
            max_active_tasks=self._max_active,
            max_queued_tasks=self._max_queued,
            cpu_usage=psutil.cpu_percent(interval=None),
            memory_usage=psutil.virtual_memory().percent,
        )
        return MetricsBody(uptime_seconds=uptime, load_metrics=load)