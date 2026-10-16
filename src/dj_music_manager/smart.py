"""Playlists intelligentes : règles JSON -> clause WHERE SQLite paramétrée.

Format : {"match": "all" | "any", "rules": [{"field": "bpm", "op": ">=", "value": 120}, ...]}
"""

FIELDS = {  # champ : (libellé, type)
    "artist": ("Artiste", "text"),
    "title": ("Titre", "text"),
    "album": ("Album", "text"),
    "tag": ("Tag", "tag"),
    "bpm": ("BPM", "number"),
    "duration": ("Durée (s)", "number"),
    "badge": ("Qualité", "text"),
    "format": ("Format", "text"),
    "source": ("Source", "text"),
}
OPS = {
    "text": {"contains": "contient", "not_contains": "ne contient pas", "is": "est", "is_not": "n'est pas"},
    "number": {">=": "≥", "<=": "≤"},
    "tag": {"has": "a le tag", "has_not": "n'a pas le tag"},
}
_HAS_TAG = "id IN (SELECT track_id FROM track_tags JOIN tags ON tags.id = tag_id WHERE tags.name = ?)"


def where(rules: dict) -> tuple[str, list]:
    """Les champs et opérateurs sont validés contre FIELDS/OPS : seules les valeurs vont en paramètres."""
    parts, params = [], []
    for r in rules.get("rules", []):
        field, op, value = r["field"], r["op"], r["value"]
        kind = FIELDS[field][1]
        if op not in OPS[kind]:
            raise ValueError(f"opérateur {op!r} invalide pour {field}")
        if kind == "tag":
            parts.append(_HAS_TAG if op == "has" else f"NOT {_HAS_TAG}")
            params.append(value)
        elif kind == "number":
            parts.append(f"{field} {op} ?")
            params.append(float(value))
        else:
            col = f"IFNULL({field}, '')"
            if op in ("contains", "not_contains"):
                escaped = str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                parts.append(f"{col} {'NOT ' if op == 'not_contains' else ''}LIKE ? ESCAPE '\\'")
                params.append(f"%{escaped}%")
            else:
                parts.append(f"{col} {'=' if op == 'is' else '!='} ? COLLATE NOCASE")
                params.append(value)
    if not parts:
        return "1", []
    joiner = " OR " if rules.get("match") == "any" else " AND "
    return "(" + joiner.join(f"({p})" for p in parts) + ")", params
