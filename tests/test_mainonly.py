import os
import unittest


class MainOnlyTest(unittest.TestCase):
    """Pilot T6: passes on the PR, fails on the push to main, on purpose."""

    def test_not_on_main_push(self):
        self.assertNotEqual(os.environ.get("GITHUB_EVENT_NAME"), "push",
                            "T6 deliberate post-merge failure")


if __name__ == "__main__":
    unittest.main()
