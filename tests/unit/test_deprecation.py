import unittest
from datetime import date
from unittest.mock import patch

from sima_cli.utils.deprecation import should_show_post_neat_ga_deprecation_notice


class TestDeprecationDateGate(unittest.TestCase):
    def test_post_neat_ga_notice_is_hidden_before_ga_date(self):
        with patch("sima_cli.utils.deprecation.date") as date_cls:
            date_cls.today.return_value = date(2026, 6, 19)

            self.assertFalse(should_show_post_neat_ga_deprecation_notice())

    def test_post_neat_ga_notice_is_hidden_on_ga_date(self):
        with patch("sima_cli.utils.deprecation.date") as date_cls:
            date_cls.today.return_value = date(2026, 6, 20)

            self.assertFalse(should_show_post_neat_ga_deprecation_notice())

    def test_post_neat_ga_notice_is_shown_after_ga_date(self):
        with patch("sima_cli.utils.deprecation.date") as date_cls:
            date_cls.today.return_value = date(2026, 6, 21)

            self.assertTrue(should_show_post_neat_ga_deprecation_notice())


if __name__ == "__main__":
    unittest.main()
