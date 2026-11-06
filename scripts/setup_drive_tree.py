"""Find the shared Company Brain folder in Drive, record it and create its folders (T10).

Safe to run again. Reads only the folder IDs of the top-level folders in My Drive.
"""

import sys
from pathlib import Path

from company_brain.drive import FOLDER_ID, RootNotFound, ensure_tree, find_root, write_sources

REPO = Path(__file__).resolve().parents[1]
CLOUD_STORAGE = Path.home() / "Library" / "CloudStorage"


def main() -> int:
    mounts = sorted(CLOUD_STORAGE.glob("GoogleDrive-*/My Drive"))
    if not mounts:
        print("Google Drive is not signed in (no Drive folder in ~/Library/CloudStorage).")
        return 1
    for my_drive in mounts:
        try:
            root = find_root(my_drive, FOLDER_ID)
        except RootNotFound:
            continue
        backups = ensure_tree(root)
        write_sources(REPO / "config" / "sources.toml", root, FOLDER_ID)
        print(f"Drive root: {root}\nBackups:    {backups}")
        return 0
    print(f"No folder with ID {FOLDER_ID} at the top of My Drive.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
