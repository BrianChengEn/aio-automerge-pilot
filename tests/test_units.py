import unittest

from pilot.units import convert


class UnitsTest(unittest.TestCase):
    def test_convert(self):
        self.assertEqual(convert(2, "u000"), 2.0)

    def test_unknown(self):
        with self.assertRaises(KeyError):
            convert(1, "nope")


if __name__ == "__main__":
    unittest.main()
