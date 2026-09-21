"""Pure fragment parsing: original HTML is never rewritten."""

from html import unescape
from html.parser import HTMLParser


def clean_title(value: str | None) -> str | None:
    return None if value is None else " ".join(unescape(value).split())


class ContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.blocks: list[str] = []
        self.code: list[str] | None = None
        self.suppressed: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.text.append(" ")
            self.suppressed.append(tag)
        if self.suppressed:
            return
        if tag == "code":
            self.finish_code()
            self.code = []
            self.text.append(" ")
        elif tag == "br" and self.code is not None:
            self.code.append("\n")
        elif self.code is None:
            self.text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self.suppressed:
            if tag in self.suppressed:
                del self.suppressed[self.suppressed.index(tag) :]
            return
        if tag == "code":
            self.finish_code()
        elif self.code is None:
            self.text.append(" ")

    def handle_data(self, data: str) -> None:
        if self.suppressed:
            return
        if self.code is not None:
            self.code.append(data)
        else:
            self.text.append(data)

    def finish_code(self) -> None:
        if self.code is not None:
            self.blocks.append("".join(self.code))
            self.code = None
        self.text.append(" ")


def clean_body(value: str | None) -> tuple[str | None, tuple[str, ...] | None]:
    if value is None:
        return None, None
    parser = ContentParser()
    parser.feed(value)
    parser.close()
    parser.finish_code()
    return " ".join("".join(parser.text).split()), tuple(parser.blocks)
