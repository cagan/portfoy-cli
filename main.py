#!/usr/bin/env python3
"""Depodan dogrudan calistirmak icin giris noktasi: `python main.py status`.

Asil arayuz portfoy/cli.py'de. Paket kurulduysa ayni islevi `portfoy` komutu
gorur (bkz. pyproject.toml -> project.scripts).
"""

import sys

from portfoy.cli import main

if __name__ == "__main__":
    sys.exit(main())
