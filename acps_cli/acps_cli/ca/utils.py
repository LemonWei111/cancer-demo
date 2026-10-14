import os

from acps_cli.shared.cli_logging import setup_cli_logging


def ensure_directory(path):
    if not os.path.exists(path):
        os.makedirs(path, mode=0o755)


def setup_logging(verbose=False):
    setup_cli_logging(verbose)
