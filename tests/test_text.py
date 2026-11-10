import unittest

from pilot.text import slugify


class TextTest(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Hello, World 2026"), "hello-world-2026")
        self.assertEqual(slugify("  "), "")


if __name__ == "__main__":
    unittest.main()
