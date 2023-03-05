from typing import Sequence, TypeVar


def search(query: str, possibilities: Sequence[str]) -> Sequence[str]:
    direct_matches = {
        s for s in possibilities if s.startswith(query)
    }

    close_matches = {
        s for s in possibilities if letters_appear_in_order(query, s)
    }.difference(direct_matches)

    matches = list(sorted(direct_matches)) + list(sorted(close_matches))

    return matches


def letters_appear_in_order(part: str, full: str):
    part: list = list(part)
    while full and part:
        index = full.find(part.pop(0))
        if index == -1:
            return False
        full = full[index + 1:]
    return not part
