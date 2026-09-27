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


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def ym_snippets() -> list:
    return json.loads(load_text("ym_snippets.json"))


@pytest.fixture
def ym_card() -> str:
    return load_text("ym_card.html")


@pytest.fixture
def ym_reviews() -> str:
    return load_text("ym_reviews.html")


@pytest.fixture
def avito_search() -> dict:
    return load("avito_search.json")


@pytest.fixture
def avito_item() -> dict:
    return load("avito_item.json")


@pytest.fixture
def avito_reviews() -> list:
    return json.loads(load_text("avito_reviews.json"))
