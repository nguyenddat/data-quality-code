from contextlib import contextmanager
from collections.abc import Generator
from typing import Any
from urllib.parse import quote

import duckdb

from .settings import config

@contextmanager
def get_connection(read_only: bool = True) -> Generator[Any, None, None]:
    connection = None
    try:
        connection = duckdb.connect(database=":memory:")
        connection_string = (
            f"postgresql://{quote(config.db_user, safe='')}"
            f":{quote(config.db_password, safe='')}"
            f"@{config.db_host}:{config.db_port}"
            f"/{quote(config.db_name, safe='')}"
        )
        escaped_connection_string = connection_string.replace("'", "''")
        read_only_option = ", READ_ONLY" if read_only else ""
        connection.execute(
            f"ATTACH '{escaped_connection_string}' AS remote_db "
            f"(TYPE postgres{read_only_option})"
        )
        connection.execute("USE remote_db")
        yield connection
    finally:
        if connection is not None:
            connection.close()
