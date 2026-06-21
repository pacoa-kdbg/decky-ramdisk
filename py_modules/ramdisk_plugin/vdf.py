from collections.abc import Iterator
from typing import Any


class VdfParseError(ValueError):
    pass


def _tokens(text: str) -> Iterator[str]:
    i = 0
    while i < len(text):
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if char in "{}":
            yield char
            i += 1
            continue
        if char == '"':
            i += 1
            value: list[str] = []
            while i < len(text):
                char = text[i]
                if char == "\\" and i + 1 < len(text):
                    value.append(text[i + 1])
                    i += 2
                    continue
                if char == '"':
                    i += 1
                    break
                value.append(char)
                i += 1
            yield "".join(value)
            continue
        start = i
        while i < len(text) and not text[i].isspace() and text[i] not in "{}":
            i += 1
        yield text[start:i]


def loads(text: str) -> dict[str, Any]:
    token_list = list(_tokens(text))
    index = 0

    def parse_object() -> dict[str, Any]:
        nonlocal index
        result: dict[str, Any] = {}
        while index < len(token_list):
            token = token_list[index]
            index += 1
            if token == "}":
                return result
            if index >= len(token_list):
                raise VdfParseError(f"Missing value for key {token!r}")
            value = token_list[index]
            index += 1
            if value == "{":
                result[token] = parse_object()
            elif value == "}":
                raise VdfParseError(f"Unexpected closing brace after key {token!r}")
            else:
                result[token] = value
        return result

    parsed = parse_object()
    if index != len(token_list):
        raise VdfParseError("Unexpected trailing tokens")
    return parsed

