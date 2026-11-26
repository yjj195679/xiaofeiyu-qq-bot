import unittest
from pathlib import Path


class PluginMetadataTests(unittest.TestCase):
    def test_core_is_transport_agnostic(self):
        """The deployed QQ adapter is registered as ``xiaofeiyu``.

        A platform allow-list for only the stock adapter lets import preflight
        pass while AstrBot silently declines to activate the actual plugin.
        """
        metadata = (
            Path(__file__).parents[1]
            / "plugins"
            / "dafeiyu_agent_core"
            / "metadata.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn('astrbot_version: ">=4.29.4"', metadata)
        self.assertNotIn("support_platforms:", metadata)


if __name__ == "__main__":
    unittest.main()
