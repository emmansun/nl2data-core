"""Scratch: list event kinds used by the catalog store."""
import re

with open(
    "packages/nl2data-semantic-catalog-postgres/src/"
    "nl2data_semantic_catalog_postgres/store.py",
    encoding="utf-8",
) as source:
    text = source.read()
for match in re.finditer(r'_insert_event\(\s*conn,\s*"([a-z_]+)"', text):
    print(match.group(1))
