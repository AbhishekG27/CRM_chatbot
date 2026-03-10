from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import get_db


def main() -> None:
    load_dotenv()
    db = get_db()

    stages = list(
        db["funnel_stages"].find(
            {},
            {"name": 1, "order": 1, "stage_type": 1, "is_default": 1},
        )
    )

    stages.sort(
        key=lambda s: (
            s.get("order") is None,
            s.get("order", 10**9),
            s.get("name", ""),
        )
    )

    print(f"COUNT\t{len(stages)}")
    print("order\tname\tstage_type\tis_default\t_id")
    for s in stages:
        print(
            "{}\t{}\t{}\t{}\t{}".format(
                s.get("order", ""),
                s.get("name", ""),
                s.get("stage_type", ""),
                bool(s.get("is_default", False)),
                s.get("_id", ""),
            )
        )


if __name__ == "__main__":
    main()

