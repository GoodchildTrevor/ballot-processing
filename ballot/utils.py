import unicodedata


def _normalize(value: str) -> str:
    """
    Normalize a string by applying NFKC unicode normalization and stripping whitespace.
    Converts non-breaking spaces (\xa0) and other unicode variants to standard characters.
    Converts curly quotes and dashes to standard quotes and dashes.

    :param value: Input string
    :returns: Normalized string
    """
    if not value:
        return value
    value = unicodedata.normalize("NFKC", value).strip()
    value = value.replace('\u2018', "'").replace('\u2019', "'")
    value = value.replace('\u201c', '"').replace('\u201d', '"')
    value = value.replace('\u2010', '-').replace('\u2011', '-').replace('\u2013', '-').replace('\u2014', '-')
    return value
