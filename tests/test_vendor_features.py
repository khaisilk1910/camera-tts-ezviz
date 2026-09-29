import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from config import ConfigError, load_cameras, load_settings  # noqa: E402
from intercom_server import alaw_to_pcm16, intercom_token, iter_voice_bursts, pcm_dbfs  # noqa: E402
from ptz import PTZError, ptz_move  # noqa: E402
from vendor_talk import resample_pcm16_mono  # noqa: E402


class VendorConfigTests(unittest.TestCase):
    def base_env(self):
        return {
            "PORT": "8124",
            "API_KEY": "test-key",
            "TTS_GAIN_DB": "4",
            "TTS_SAMPLE_RATE": "16000",
            "TTS_BITRATE": "32k",
        }

    def test_imou_vendor_defaults_to_local_dahua_talk_port(self):
        env = self.base_env() | {
            "CAMERAS_JSON": '[{"id":"yard","vendors":"imou","ip":"10.0.0.20","user":"admin","password":"pw","ptz":true}]'
        }
        settings = load_settings(env)
        cameras, default_camera = load_cameras(settings, env)
        camera = cameras["yard"]
        self.assertEqual(default_camera, "yard")
        self.assertEqual(camera["vendor"], "imou")
        self.assertEqual(camera["port"], 37777)
        self.assertEqual(camera["ptz_protocol"], "dahua")
        self.assertEqual(camera["ptz_speed"], 50)

    def test_hikvision_alias_uses_ezviz_adapter(self):
        env = self.base_env() | {
            "CAMERAS_JSON": '{"gate":{"vendor":"hikvision","ip":"10.0.0.21","user":"admin","password":"pw","ptz":true}}'
        }
        settings = load_settings(env)
        cameras, _ = load_cameras(settings, env)
        self.assertEqual(cameras["gate"]["vendor"], "ezviz")
        self.assertEqual(cameras["gate"]["port"], 8000)
        self.assertEqual(cameras["gate"]["ptz_protocol"], "isapi")

    def test_empty_vendor_falls_back_to_vendors(self):
        env = self.base_env() | {
            "CAMERAS_JSON": '{"yard":{"vendor":"","vendors":"imou","ip":"10.0.0.20","password":"pw"}}'
        }
        settings = load_settings(env)
        cameras, _ = load_cameras(settings, env)
        self.assertEqual(cameras["yard"]["vendor"], "imou")

    def test_invalid_vendor_is_rejected(self):
        env = self.base_env() | {
            "CAMERAS_JSON": '{"x":{"vendors":"unknown","ip":"10.0.0.22","password":"pw"}}'
        }
        settings = load_settings(env)
        with self.assertRaises(ConfigError):
            load_cameras(settings, env)

    def test_mic_url_scheme_is_validated(self):
        env = self.base_env() | {
            "CAMERAS_JSON": '{"x":{"vendors":"dahua","ip":"10.0.0.22","password":"pw","mic_url":"file:///etc/passwd"}}'
        }
        settings = load_settings(env)
        with self.assertRaises(ConfigError):
            load_cameras(settings, env)


class PTZTests(unittest.TestCase):
    def cfg(self, protocol):
        return {
            "ip": "10.0.0.10",
            "username": "admin",
            "password": "pw",
            "ptz_enabled": True,
            "ptz_protocol": protocol,
            "ptz_port": 80,
            "ptz_channel": 1 if protocol == "isapi" else 0,
            "ptz_speed": 50,
        }

    @patch("ptz.time.sleep")
    @patch("ptz.requests.Session")
    def test_dahua_ptz_sends_start_then_stop(self, session_cls, _sleep):
        response = Mock(status_code=200, text="OK")
        session = session_cls.return_value
        session.request.return_value = response
        ptz_move(self.cfg("dahua"), "left", speed=50, duration=0.1)
        self.assertEqual(session.request.call_count, 2)
        first = session.request.call_args_list[0]
        second = session.request.call_args_list[1]
        self.assertEqual(first.args[0], "GET")
        self.assertEqual(first.kwargs["params"]["action"], "start")
        self.assertEqual(first.kwargs["params"]["code"], "Left")
        self.assertEqual(second.kwargs["params"]["action"], "stop")

    @patch("ptz.time.sleep")
    @patch("ptz.requests.Session")
    def test_isapi_ptz_sends_motion_then_zero_stop(self, session_cls, _sleep):
        response = Mock(status_code=200, text="OK")
        session = session_cls.return_value
        session.request.return_value = response
        ptz_move(self.cfg("isapi"), "zoom_in", speed=60, duration=0.1)
        self.assertEqual(session.request.call_count, 2)
        first_xml = session.request.call_args_list[0].kwargs["data"].decode()
        stop_xml = session.request.call_args_list[1].kwargs["data"].decode()
        self.assertIn("<zoom>60</zoom>", first_xml)
        self.assertIn("<zoom>0</zoom>", stop_xml)

    def test_disabled_ptz_fails_without_network(self):
        cfg = self.cfg("dahua")
        cfg["ptz_enabled"] = False
        with self.assertRaises(PTZError):
            ptz_move(cfg, "left")


class AudioHelperTests(unittest.TestCase):
    def test_alaw_decoder_doubles_byte_count(self):
        self.assertEqual(len(alaw_to_pcm16(bytes(range(256)))), 512)

    def test_intercom_token_is_camera_scoped(self):
        self.assertEqual(intercom_token("key", "cam1"), intercom_token("key", "cam1"))
        self.assertNotEqual(intercom_token("key", "cam1"), intercom_token("key", "cam2"))

    def test_resample_8k_to_16k_roughly_doubles_samples(self):
        pcm = (b"\x00\x00\x01\x00") * 80
        out = resample_pcm16_mono(pcm, 8000, 16000)
        self.assertEqual(len(out), len(pcm) * 2)

    def test_voice_gate_splits_on_silence(self):
        # 100 ms PCM chunks at 8 kHz: ten loud chunks, sixteen silent chunks, then loud again.
        loud = (b"\xff\x3f") * 800
        quiet = b"\x00\x00" * 800
        chunks = [quiet] * 3 + [loud] * 2 + [quiet] * 16 + [loud] * 2
        bursts = iter_voice_bursts(chunks)
        first = list(next(bursts))
        second = list(next(bursts))
        self.assertGreater(len(first), 2)
        self.assertEqual(len(second), 3)
        self.assertGreater(pcm_dbfs(loud), -45.0)
        self.assertLess(pcm_dbfs(quiet), -45.0)


if __name__ == "__main__":
    unittest.main()
