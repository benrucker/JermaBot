"""Helpers for fitting text into Discord's message limits."""

MESSAGE_LIMIT = 1990


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split text into Discord-sized chunks, preferring newline boundaries."""
    chunks = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind('\n', 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].strip()
    return chunks
