"""单元测试 — utils 模块。"""

import logging
import os

import pytest

from acps_cli.ca.utils import ensure_directory, setup_logging


@pytest.mark.unit
class TestEnsureDirectory:
    def test_creates_new_directory(self, tmp_path):
        target = str(tmp_path / "new_dir")
        assert not os.path.exists(target)
        ensure_directory(target)
        assert os.path.isdir(target)

    def test_existing_directory_no_error(self, tmp_path):
        target = str(tmp_path / "exist")
        os.makedirs(target)
        ensure_directory(target)  # 不应抛出异常
        assert os.path.isdir(target)


@pytest.mark.unit
class TestSetupLogging:
    def test_default_level_info(self):
        # basicConfig 只在 root logger 无 handler 时生效，先清空 handlers
        root = logging.getLogger()
        root.handlers.clear()
        setup_logging(verbose=False)
        assert root.level == logging.INFO

    def test_verbose_level_debug(self):
        root = logging.getLogger()
        root.handlers.clear()
        setup_logging(verbose=True)
        assert root.level == logging.DEBUG
