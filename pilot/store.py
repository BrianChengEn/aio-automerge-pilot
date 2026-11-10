"""Pilot note store; reads the migration-defined schema."""

SCHEMA_FILE = "migrations/0001_init.sql"


def schema_path():
    return SCHEMA_FILE
