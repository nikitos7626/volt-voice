"""
Unit and integration tests for modular VOLT MUSIC engine.
Tests AudioEngine af_chain, Voice-Match attenuation (-18.5 dB), loop modes, resolver, and aiohttp API.
"""
import unittest
import os
import sys
import asyncio
from unittest.mock import patch, MagicMock

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
BOT_DIR = os.path.join(ROOT_DIR, "music-bot")


class TestModularMusicBot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Clear potentially colliding packages and submodules from other test suites
        for mod in list(sys.modules.keys()):
            if any(mod == p or mod.startswith(p + '.') for p in ['config', 'models', 'services', 'controllers', 'router', 'bot', 'app']):
                sys.modules.pop(mod, None)
        sys.path = [p for p in sys.path if not p.endswith("server")]
        if BOT_DIR not in sys.path:
            sys.path.insert(0, BOT_DIR)

        import config
        import models.playback_state as ps
        import services.audio_engine as ae
        import services.token_service as ts
        import services.track_resolver as tr
        import controllers.music_controller as mc
        import bot

        cls.config = config
        cls.PlaybackState = ps.PlaybackState
        cls.AudioEngine = ae.AudioEngine
        cls.generate_bot_token = staticmethod(ts.generate_bot_token)
        cls.TrackResolver = tr.TrackResolver
        cls.MusicController = mc.MusicController
        cls.create_app = staticmethod(bot.create_app)

    def test_audio_engine_voice_match_attenuation_and_chain(self):
        state = self.PlaybackState()
        engine = self.AudioEngine(state, None)

        # 1. Studio preset (default: bass=3.5, treble=2.0)
        chain = engine.build_af_chain()
        self.assertIn("aresample=48000:resampler=soxr:precision=28", chain)
        self.assertIn("equalizer=f=55:t=q:w=1.0:g=3.5", chain)
        self.assertIn("alimiter=limit=0.96", chain)
        # Expected volume factor incorporates -18.5 dB:
        # vol_db_factor = 1.0 * 10 ** ((-2.5 - 18.5) / 20) = 10 ** (-1.05) ~= 0.089
        self.assertIn("volume=0.089", chain)

        # 2. Flat preset
        state.eq_config["preset"] = "flat"
        state.eq_config["bass_db"] = 0.0
        state.eq_config["treble_db"] = 0.0
        state.eq_config["stereo_width"] = 1.0
        chain_flat = engine.build_af_chain()
        # base_vol = 1.0 * 10 ** (-18.5 / 20) ~= 0.119
        self.assertIn("aresample=48000:resampler=soxr:precision=28,volume=0.119", chain_flat)

    def test_loop_mode_cycling_and_queue(self):
        state = self.PlaybackState()
        self.assertEqual(state.loop_mode, "none")
        self.assertEqual(state.cycle_loop_mode(), "all")
        self.assertEqual(state.cycle_loop_mode(), "one")
        self.assertEqual(state.cycle_loop_mode(), "none")
        self.assertEqual(state.cycle_loop_mode("all"), "all")

        state.queue.append({"id": "t1", "title": "Track 1"})
        state.queue.append({"id": "t2", "title": "Track 2"})
        state.remove_from_queue("t1")
        self.assertEqual(len(state.queue), 1)
        self.assertEqual(state.queue[0]["id"], "t2")

    def test_bot_token_minting(self):
        token = self.generate_bot_token("lounge")
        parts = token.split('.')
        self.assertEqual(len(parts), 3)

    def test_radio_resolver_query(self):
        resolver = self.TrackResolver()
        loop = asyncio.new_event_loop()
        try:
            item = loop.run_until_complete(resolver.resolve_query_to_item("lofi"))
            self.assertIsNotNone(item)
            self.assertTrue(item["is_radio"])
            self.assertEqual(item["url"], self.config.RADIO_STATIONS["lofi"][1])

            # Empty query returns None
            self.assertIsNone(loop.run_until_complete(resolver.resolve_query_to_item("  ")))
        finally:
            loop.close()

    def test_controller_initial_state(self):
        ctrl = self.MusicController()
        st = ctrl.get_state()
        self.assertTrue(st["ok"])
        self.assertFalse(st["connected"])
        self.assertIsNone(st["room"])
        self.assertFalse(st["is_playing"])
        self.assertEqual(st["loop_mode"], "none")
        self.assertEqual(len(st["radios"]), len(self.config.RADIO_STATIONS))

    def test_aiohttp_app_creation_and_routes(self):
        app = self.create_app()
        routes = [r.resource.canonical for r in app.router.routes()]
        self.assertIn("/status", routes)
        self.assertIn("/state", routes)
        self.assertIn("/summon", routes)
        self.assertIn("/dismiss", routes)
        self.assertIn("/search", routes)
        self.assertIn("/play", routes)
        self.assertIn("/queue_add", routes)
        self.assertIn("/queue_remove", routes)
        self.assertIn("/queue_clear", routes)
        self.assertIn("/next", routes)
        self.assertIn("/loop", routes)
        self.assertIn("/stop", routes)
        self.assertIn("/eq", routes)


if __name__ == '__main__':
    unittest.main()
