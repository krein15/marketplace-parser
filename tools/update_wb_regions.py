"""Recompute the WB region codes in mpparser/regions.py.

Every WB pickup point carries the ``dest`` code of its delivery zone; the code used for a city is the most
common one among its pickup points. The source file is public but large (~70 MB), so this is a manual tool.

Usage: python tools/update_wb_regions.py [--write]
"""

from __future__ import annotations

import collections
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

PICKUPS_URL = "https://static-basket-01.wbbasket.ru/vol0/data/all-poo-fr-v12.json"
REGIONS_FILE = Path(__file__).resolve().parents[1] / "mpparser" / "regions.py"
CITIES = [
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань", "Нижний Новгород", "Челябинск",
    "Красноярск", "Самара", "Уфа", "Ростов-на-Дону", "Омск", "Краснодар", "Воронеж", "Пермь", "Волгоград",
    "Тюмень", "Саратов", "Иркутск", "Хабаровск", "Владивосток", "Калининград", "Сочи", "Ярославль", "Томск",
]


def download() -> Path:
    target = Path(tempfile.gettempdir()) / "wb-all-poo.json"
    if target.exists():
        print(f"using cached {target}")
        return target
    print(f"downloading {PICKUPS_URL} …")
    request = urllib.request.Request(PICKUPS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=300) as response, target.open("wb") as file:
        while chunk := response.read(1 << 20):
            file.write(chunk)
    return target


def main() -> None:
    countries = json.loads(download().read_text(encoding="utf-8"))
    points = next(c for c in countries if c["country"] == "ru")["items"]
    print(f"{len(points)} pickup points")

    codes = {}
    for city in CITIES:
        counter = collections.Counter(
            point["dest"] for point in points if point.get("address", "").split(",")[0].strip() == city
        )
        if not counter:
            print(f"  {city}: not found, keeping the current code")
            continue
        code, count = counter.most_common(1)[0]
        codes[city] = code
        print(f"  {city}: dest={code} ({count} of {sum(counter.values())} points)")

    block = "\n".join(f'    "{city}": {code},' for city, code in codes.items())
    print("\nWB_REGIONS: dict[str, int] = {\n" + block + "\n}")
    if "--write" in sys.argv:
        source = REGIONS_FILE.read_text(encoding="utf-8")
        start = source.index("WB_REGIONS: dict[str, int] = {")
        end = source.index("}", start) + 1
        REGIONS_FILE.write_text(
            source[:start] + "WB_REGIONS: dict[str, int] = {\n" + block + "\n}" + source[end:], encoding="utf-8"
        )
        print(f"\nwritten to {REGIONS_FILE}")


if __name__ == "__main__":
    main()
