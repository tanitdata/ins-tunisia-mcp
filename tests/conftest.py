import sys
from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).parent / "fixtures"

_PARSER = etree.XMLParser(recover=True, encoding="utf-8")


@pytest.fixture
def fixture_root():
    def _load(name: str) -> etree._Element:
        data = (FIXTURES / name).read_bytes()
        return etree.fromstring(data, parser=_PARSER)
    return _load
