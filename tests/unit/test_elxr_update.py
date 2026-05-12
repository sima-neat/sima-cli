import unittest

from sima_cli.update.elxr import (
    EXTERNAL_REPO_URL,
    INTERNAL_REPO_URL,
    _select_elxr_repo_channel,
)

EXTERNAL_BOOKWORM_REPO_LINE = f"deb {EXTERNAL_REPO_URL} bookworm non-free"
INTERNAL_BOOKWORM_REPO_LINE = f"deb {INTERNAL_REPO_URL} bookworm non-free"
EXTERNAL_TRIXIE_REPO_LINE = f"deb {EXTERNAL_REPO_URL} trixie non-free"
INTERNAL_TRIXIE_REPO_LINE = f"deb {INTERNAL_REPO_URL} trixie non-free"


class TestElxrRepoChannel(unittest.TestCase):
    def test_selects_internal_channel_and_comments_external(self):
        content = "\n".join([
            "deb http://deb.debian.org/debian bookworm main non-free-firmware",
            EXTERNAL_BOOKWORM_REPO_LINE,
            f"# {INTERNAL_BOOKWORM_REPO_LINE}",
            "",
        ])

        updated, changed, switching = _select_elxr_repo_channel(content, internal=True)

        self.assertTrue(changed)
        self.assertTrue(switching)
        self.assertIn(f"# {EXTERNAL_BOOKWORM_REPO_LINE}", updated)
        self.assertIn(INTERNAL_BOOKWORM_REPO_LINE, updated)

    def test_selects_external_channel_and_comments_internal(self):
        content = "\n".join([
            "deb http://deb.debian.org/debian bookworm main non-free-firmware",
            f"# {EXTERNAL_BOOKWORM_REPO_LINE}",
            INTERNAL_BOOKWORM_REPO_LINE,
            "",
        ])

        updated, changed, switching = _select_elxr_repo_channel(content, internal=False)

        self.assertTrue(changed)
        self.assertTrue(switching)
        self.assertIn(EXTERNAL_BOOKWORM_REPO_LINE, updated)
        self.assertIn(f"# {INTERNAL_BOOKWORM_REPO_LINE}", updated)

    def test_appends_missing_target_without_switch_warning(self):
        updated, changed, switching = _select_elxr_repo_channel(
            "deb http://deb.debian.org/debian bookworm main non-free-firmware\n",
            internal=False,
        )

        self.assertTrue(changed)
        self.assertFalse(switching)
        self.assertIn(EXTERNAL_BOOKWORM_REPO_LINE, updated)
        self.assertIn(f"# {INTERNAL_BOOKWORM_REPO_LINE}", updated)

    def test_appends_missing_inactive_channel(self):
        updated, changed, switching = _select_elxr_repo_channel(
            "\n".join([
                "deb http://deb.debian.org/debian bookworm main non-free-firmware",
                EXTERNAL_BOOKWORM_REPO_LINE,
                "",
            ]),
            internal=False,
        )

        self.assertTrue(changed)
        self.assertFalse(switching)
        self.assertIn(EXTERNAL_BOOKWORM_REPO_LINE, updated)
        self.assertIn(f"# {INTERNAL_BOOKWORM_REPO_LINE}", updated)

    def test_preserves_future_debian_suite_when_switching_channels(self):
        content = "\n".join([
            "deb http://deb.debian.org/debian trixie main non-free-firmware",
            EXTERNAL_TRIXIE_REPO_LINE,
            f"# {INTERNAL_TRIXIE_REPO_LINE}",
            "",
        ])

        updated, changed, switching = _select_elxr_repo_channel(content, internal=True)

        self.assertTrue(changed)
        self.assertTrue(switching)
        self.assertIn(f"# {EXTERNAL_TRIXIE_REPO_LINE}", updated)
        self.assertIn(INTERNAL_TRIXIE_REPO_LINE, updated)
        self.assertNotIn("bookworm non-free", updated)


if __name__ == "__main__":
    unittest.main()
