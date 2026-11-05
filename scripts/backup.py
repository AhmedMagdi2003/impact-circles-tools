"""Create a consistent SQLite backup with retained source files."""

import argparse
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from app.core.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up Impact Circles Tools data")
    parser.add_argument("--output", required=True, type=Path, help="Output .zip path")
    args = parser.parse_args()
    config = load_config()
    if not config.database_path.is_file():
        raise SystemExit("Database does not exist")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        copy = Path(temp_dir) / "impact_tools.db"
        source = sqlite3.connect(config.database_path)
        target = sqlite3.connect(copy)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(copy, "impact_tools.db")
            uploads = config.database_path.parent / "uploads"
            if uploads.exists():
                for path in uploads.iterdir():
                    if path.is_file():
                        archive.write(path, f"uploads/{path.name}")
    print(f"Backup written to {output}")


if __name__ == "__main__":
    main()
