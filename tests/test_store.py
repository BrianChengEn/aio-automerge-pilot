import unittest

from pilot.store import schema_path


class StoreTest(unittest.TestCase):
    def test_schema_path(self):
        self.assertTrue(schema_path().endswith(".sql"))


if __name__ == "__main__":
    unittest.main()
