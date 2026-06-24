"""Restore the catalog from its Litestream replica, once, before replication starts.

The deploy image's entrypoint runs this and then hands off to ``litestream replicate -exec
"fishpage"``, so the restore finishes before replication begins and the two Litestream operations
never contend for the database file. With no replica configured this is a no-op, so it is also
harmless if run off the cloud path.
"""

import os

from fishpage.boot import restore_database
from fishpage.config import load_settings


def main() -> None:
    settings = load_settings(os.environ)
    if restore_database(settings):
        print(f"Restored {settings.db_path} from its Litestream replica")


if __name__ == "__main__":
    main()
