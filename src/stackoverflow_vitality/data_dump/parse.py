"""Streaming reader for Posts.xml. Only the current row is retained."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import BinaryIO


class DumpXmlError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


def iter_rows(stream: BinaryIO) -> Iterator[tuple[int, dict[str, str]]]:
    stream.seek(0)
    yield from _iterate(stream)


def _iterate(stream: BinaryIO) -> Iterator[tuple[int, dict[str, str]]]:
    try:
        context = ET.iterparse(stream, events=("start", "end"))
        try:
            _, root = next(context)
        except StopIteration as error:
            raise DumpXmlError("DD-001") from error
        if _local(root.tag) != "posts":
            raise DumpXmlError("DD-002", _local(root.tag))
        number = 0
        for event, element in context:
            tag = _local(element.tag)
            if tag not in {"posts", "row"}:
                raise DumpXmlError("DD-002", tag)
            if event == "end" and tag == "row":
                number += 1
                attributes = {
                    key: value for key, value in element.attrib.items() if isinstance(value, str)
                }
                element.clear()
                root.clear()
                yield number, attributes
    except ET.ParseError as error:
        raise DumpXmlError("DD-001") from error


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
