from __future__ import annotations

import os
from pathlib import Path


def tail_csv_source(
    path: str | os.PathLike[str],
    limit: int,
    *,
    encoding: str = "utf-8",
    block_size: int = 64 * 1024,
) -> list[str]:
    """Return a CSV header and at most the last ``limit`` physical rows.

    Greenhouse logs contain one record per physical line. Reading backwards
    keeps chart requests proportional to the requested number of points rather
    than to the age of the complete log file.
    """

    try:
        row_limit = int(limit)
    except (TypeError, ValueError):
        return []
    if row_limit <= 0:
        return []

    file_path = Path(path)
    with file_path.open("rb") as handle:
        header_bytes = handle.readline()
        if not header_bytes:
            return []
        data_start = handle.tell()
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        if position <= data_start:
            return [header_bytes.decode(encoding)]

        chunks: list[bytes] = []
        newline_count = 0
        chunk_size = max(1024, int(block_size))
        while position > data_start:
            read_size = min(chunk_size, position - data_start)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")
            # One extra newline is required when the first chunk begins in
            # the middle of a row that must be discarded.
            if newline_count > row_limit:
                break

    buffer = b"".join(reversed(chunks))
    lines = buffer.splitlines()
    if position > data_start and lines:
        lines = lines[1:]
    selected = lines[-row_limit:]
    return [header_bytes.decode(encoding)] + [
        line.decode(encoding) for line in selected
    ]
