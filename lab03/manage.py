#!/usr/bin/env python
"""Django 命令行入口。"""

import os
import sys

from django.core.management import execute_from_command_line


def main() -> None:
    """按 DJANGO_SETTINGS_MODULE 载入配置并执行子命令。"""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
