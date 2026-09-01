_MAP = {
    "in": "IN",
    "india": "IN",
    "bharat": "IN",
    "us": "US",
    "usa": "US",
    "united states": "US",
    "united states of america": "US",
    "uk": "GB",
    "gb": "GB",
    "united kingdom": "GB",
    "england": "GB",
    "ae": "AE",
    "uae": "AE",
    "united arab emirates": "AE",
    "ca": "CA",
    "canada": "CA",
    "au": "AU",
    "australia": "AU",
    "de": "DE",
    "germany": "DE",
    "sg": "SG",
    "singapore": "SG",
}


def normalize_country(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().lower()
    if not key:
        return None
    if key in _MAP:
        return _MAP[key]
    if len(key) == 2 and key.isalpha():
        return key.upper()
    return None
