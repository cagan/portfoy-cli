"""Ortak fikstürler.

Testler GERÇEK veri dosyalarına asla dokunmaz: her test kendi geçici portföy
ve geçmiş dosyasıyla çalışır. `portfoy/config.py` yolları modül seviyesinde
sabitlediği için bu, dosya yollarını açıkça geçirerek sağlanır — testin
`veri/portfoy.json`'u bozması, aracın kendi verisini kaybettirmek olurdu.
"""

from __future__ import annotations

from datetime import date

import pytest

from portfoy import storage
from portfoy.storage import Portfolio


@pytest.fixture
def portfoy_dosyasi(tmp_path):
    return tmp_path / "portfoy.json"


@pytest.fixture
def cikti_dizini(tmp_path):
    yol = tmp_path / "ciktilar"
    yol.mkdir()
    return yol


@pytest.fixture
def bos_portfoy() -> Portfolio:
    return Portfolio()


@pytest.fixture
def ornek_portfoy(portfoy_dosyasi) -> Portfolio:
    """Açık bir pozisyon, bir de tamamı satılmış (kapanmış) pozisyon."""
    pf = Portfolio()
    pf.add_lot("TMV", 1000, date(2026, 8, 3), 8.0)
    pf.add_lot("TMV", 500, date(2026, 8, 13), 9.0)
    pf.add_lot("PHE", 200, date(2026, 7, 1), 4.0)
    pf.add_lot("PHE", -200, date(2026, 9, 2), 3.2)
    storage.save(pf, portfoy_dosyasi)
    return pf
