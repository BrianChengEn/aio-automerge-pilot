import unittest

from pilot.calc import add, clamp


class CalcTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_clamp(self):
        self.assertEqual(clamp(5, 0, 3), 3)
        self.assertEqual(clamp(-1, 0, 3), 0)
        with self.assertRaises(ValueError):
            clamp(1, 3, 0)


if __name__ == "__main__":
    unittest.main()
