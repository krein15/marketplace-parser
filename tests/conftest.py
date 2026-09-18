from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def wb_search() -> dict:
    return load("wb_search.json")


@pytest.fixture
def wb_detail() -> dict:
    return load("wb_detail.json")


@pytest.fixture
def wb_feedbacks() -> dict:
    return load("wb_feedbacks.json")


@pytest.fixture
def ozon_search() -> dict:
    return load("ozon_search.json")


@pytest.fixture
def ozon_product() -> dict:
    return load("ozon_product.json")


@pytest.fixture
def ozon_reviews() -> dict:
    return load("ozon_reviews.json")
