"""
=====================================================
product_validation_engine: /product_values.py
=====================================================
Value and record helpers shared across the engine, so "is this field empty?"
and "what does this field name mean?" have exactly one answer.
"""

import math


def is_empty(value) -> bool:
    """True for None, a blank/whitespace string, or NaN.

    NaN matters because a value read back through pandas carries it instead
    of None, and `bool(float("nan"))` is True.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, float):
        return math.isnan(value)
    return False


def split_csv(value) -> list:
    """"A, B , C" -> ["A", "B", "C"]. Empty/None -> []."""
    if value is None:
        return []
    text = str(value).strip()
    if text == "":
        return []
    return [part.strip() for part in text.split(",")]


class ProductCaseInsensitiveRecord(dict):
    """A dict whose ``get`` / ``in`` / ``[]`` also resolve case-insensitively.

    A product is validated as its flattened JSON, where nested keys become
    dotted paths such as ``ProductDefinition.ProductCategory.Name`` (see
    product_parser.flatten_product_fields). A rule must be able to name a
    field however it reads naturally, so field lookups fold case:
    ``producttype.name`` and ``ProductType.Name`` hit the same value. An
    exact-case key always wins over a folded match.
    """

    def __init__(self, data):
        super().__init__(data)
        self._folded = {}
        for key in self:
            if isinstance(key, str):
                self._folded.setdefault(key.casefold(), key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        if super().__contains__(key):
            return True
        return isinstance(key, str) and key.casefold() in self._folded

    def __getitem__(self, key):
        if super().__contains__(key):
            return super().__getitem__(key)
        if isinstance(key, str):
            real = self._folded.get(key.casefold())
            if real is not None:
                return super().__getitem__(real)
        raise KeyError(key)
