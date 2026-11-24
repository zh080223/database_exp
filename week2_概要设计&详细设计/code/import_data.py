"""Import a local nuScenes dataset into the configured scene database."""

import argparse
import os
import sys
from pathlib import Path

from database import DatabaseError, add_database_arguments, connect, database_options, replace_dataset
from nuscenes_data import ROOT, prepare_dataset


def main():
    parser = argparse.ArgumentParser(description="导入本地 nuScenes 场景和 CAM_FRONT 关键帧")
    add_database_arguments(parser)
    parser.add_argument("--dataset", type=Path, default=os.environ.get("NUSCENES_ROOT", str(ROOT / "v1")))
    parser.add_argument("--version", default="v1.0-mini")
    parser.add_argument("--regions", type=Path, default=ROOT / "regions.json")
    args = parser.parse_args()
    try:
        dataset = prepare_dataset(args.dataset.resolve(), args.version, args.regions)
        with connect(initialize=True, **database_options(parser, args)) as database:
            replace_dataset(database, dataset)
    except (OSError, ValueError, KeyError, TypeError, DatabaseError) as exc:
        print("导入失败：{}".format(exc), file=sys.stderr)
        return 1

    print("导入完成（{}）".format("PostgreSQL/PostGIS" if database.postgres else "SQLite"))
    for label, table in (("场景", "scenes"), ("轨迹点", "track_points"), ("估算急刹", "events"),
                         ("前向相机关键帧", "keyframes"), ("预设路口", "regions")):
        print("{}：{}".format(label, len(dataset[table])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
