"""Offline regressions for the ID mix-up, identity guard and private quality settings."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('music_bridge_test', ROOT / 'kugou/bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class MusicIdentityTests(unittest.TestCase):
    def test_search_id_uses_mixsongid_not_scid(self):
        song = bridge.normalize({'FileHash': 'A' * 32, 'SongName': '句号',
            'SingerName': 'G.E.M.邓紫棋', 'Scid': 63573826,
            'MixSongID': 245384269, 'Duration': 235})
        self.assertEqual(song['album_audio_id'], '245384269')
        self.assertNotEqual(song['album_audio_id'], '63573826')

    def test_scid_alone_is_not_a_valid_album_id(self):
        song = bridge.normalize({'FileHash': 'B' * 32, 'Scid': 63573826})
        self.assertEqual(song['album_audio_id'], '0')

    def test_explicit_album_id_takes_priority(self):
        song = bridge.normalize({'FileHash': 'C' * 32, 'AlbumAudioID': 42,
                                 'MixSongID': 43, 'Scid': 44})
        self.assertEqual(song['album_audio_id'], '42')

    def test_wrong_song_is_rejected(self):
        ok, _ = bridge.verify_identity({'fileName': 'Great Adventure', 'timeLength': 294},
            '句号', 'G.E.M.邓紫棋 - 句号', 235)
        self.assertFalse(ok)

    def test_preview_or_wrong_duration_is_rejected(self):
        self.assertFalse(bridge.verify_identity({'fileName': '句号', 'timeLength': 30},
            '句号', '', 235)[0])

    def test_verified_track_is_accepted(self):
        self.assertTrue(bridge.verify_identity({'fileName': '句号', 'timeLength': 235.7},
            '句号', 'G.E.M.邓紫棋 - 句号', 235)[0])

    def test_different_live_version_is_rejected(self):
        self.assertFalse(bridge.verify_identity({'fileName': '句号（现场版）', 'timeLength': 235},
            '句号', 'G.E.M.邓紫棋 - 句号', 235)[0])

    def test_missing_playback_identity_is_rejected(self):
        self.assertFalse(bridge.verify_identity({'timeLength': 235}, '句号', '', 235)[0])


class QualityPreferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_preferences_are_scoped_and_survive_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            source = (ROOT / 'plugins/astrbot_plugin_meting/main.py').read_text()
            source = source.replace('/opt/astrbot/data/plugin_data/xiaofeiyu_music', folder)
            nodes = [n for n in ast.walk(ast.parse(source))
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name in ('_xiaofeiyu_quality', 'xiaofeiyu_set_quality')]
            for n in nodes:
                n.decorator_list = []
            scope = {'hashlib': hashlib, 'os': os, 'json': json}
            exec(compile(ast.Module(body=nodes, type_ignores=[]), 'quality-preference', 'exec'), scope)
            plugin = types.SimpleNamespace()
            plugin._xiaofeiyu_quality = types.MethodType(scope['_xiaofeiyu_quality'], plugin)
            setter = types.MethodType(scope['xiaofeiyu_set_quality'], plugin)
            event = types.SimpleNamespace(unified_msg_origin='private:A', user='A', text='音质 标准')
            event.get_sender_id = lambda: event.user
            event.get_message_str = lambda: event.text
            event.stop_event = lambda: None
            event.plain_result = lambda text: text
            await setter(event)
            self.assertEqual(plugin._xiaofeiyu_quality(event), '128')
            event.user = 'B'
            self.assertEqual(plugin._xiaofeiyu_quality(event), '320')
            event.user = 'A'
            event.unified_msg_origin = 'group:A_G'
            self.assertEqual(plugin._xiaofeiyu_quality(event), '320')
            event.unified_msg_origin = 'private:A'
            event.text = '音质 无损'
            await setter(event)
            del plugin._xiaofeiyu_quality_preferences
            self.assertEqual(plugin._xiaofeiyu_quality(event), 'flac')
            self.assertEqual(os.stat(Path(folder) / 'quality.json').st_mode & 0o777, 0o600)

    async def test_numeric_choice_does_not_infer_song_from_title(self):
        tree = ast.parse((ROOT / 'plugins/astrbot_plugin_meting/main.py').read_text())
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                    and n.name == '_xiaofeiyu_choice')
        node.decorator_list = []
        scope = {'re': re}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'candidate-choice', 'exec'), scope)
        choose = scope['_xiaofeiyu_choice']
        self.assertIsNone(choose('放邓紫棋的句号'))
        self.assertIsNone(choose('推荐2首歌'))
        self.assertEqual(choose('2'), 2)
        self.assertEqual(choose('第2首'), 2)
        self.assertEqual(choose('/点歌 2'), 2)


if __name__ == '__main__':
    unittest.main()
