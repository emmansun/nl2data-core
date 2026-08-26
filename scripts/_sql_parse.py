"""Scratch: parse every catalog SQL template with sqlglot (postgres dialect)."""

import sys

sys.path.insert(0, "packages/nl2data-semantic-catalog-postgres/src")

from sqlglot import parse_one

from nl2data_semantic_catalog_postgres.schema import MIGRATIONS
from nl2data_semantic_catalog_postgres.store import SQL_TEMPLATES

SCHEMA = '"catalog"'
failures = []
for name, template in SQL_TEMPLATES.items():
    sql = template.format(schema=SCHEMA)
    try:
        parse_one(sql, read="postgres")
        print(f"  ok  {name}")
    except Exception as error:  # noqa: BLE001
        failures.append((name, error))
        print(f" FAIL {name}: {error}")

for version, statements in MIGRATIONS.items():
    for index, statement in enumerate(statements):
        sql = statement.format(schema=SCHEMA)
        try:
            parse_one(sql, read="postgres")
        except Exception as error:  # noqa: BLE001
            failures.append((f"migration-{version}-{index}", error))
            print(f" FAIL migration-{version}-{index}: {error}")

print()
if failures:
    print(f"SQL PARSE FAILED: {len(failures)} failure(s)")
    raise SystemExit(1)
print("SQL PARSE OK")
