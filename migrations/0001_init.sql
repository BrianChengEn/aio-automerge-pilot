-- 0001_init: first schema migration for the pilot (High: DB migration)
CREATE TABLE notes (
    id INTEGER PRIMARY KEY,
    body TEXT NOT NULL
);
