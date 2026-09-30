import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from config import ConfigError, load_cameras, load_settings  # noqa: E402


class ConfigTests(unittest.TestCase):
    def base_env(self):
        return {
            "PORT": "8124",
            "API_KEY": "test-key",
            "TTS_GAIN_DB": "4",
            "TTS_SAMPLE_RATE": "16000",
            "TTS_BITRATE": "32k",
        }

    def test_no_camera_is_allowed_for_portainer_first_deploy(self):
        env = self.base_env()
        settings = load_settings(env)
        cameras, default_camera = load_cameras(settings, env)
        self.assertEqual(cameras, {})
        self.assertEqual(default_camera, "")

    def test_indexed_camera(self):
        env = self.base_env() | {
            "CAMERA_01_ENABLED": "true",
            "CAMERA_01_ID": "gate",
            "CAMERA_01_IP": "192.168.31.59",
            "CAMERA_01_PORT": "8000",
            "CAMERA_01_USER": "admin",
            "CAMERA_01_PASSWORD": "p@ss:$word!",
            "CAMERA_01_GAIN_DB": "6",
        }
        settings = load_settings(env)
        cameras, default_camera = load_cameras(settings, env)
        self.assertEqual(default_camera, "gate")
        self.assertEqual(cameras["gate"]["gain_db"], 6.0)
        self.assertEqual(cameras["gate"]["password"], "p@ss:$word!")

    def test_disabled_camera_is_ignored(self):
        env = self.base_env() | {
            "CAMERA_01_ENABLED": "false",
            "CAMERA_01_IP": "192.168.31.59",
            "CAMERA_01_USER": "admin",
            "CAMERA_01_PASSWORD": "password",
        }
        settings = load_settings(env)
        cameras, _ = load_cameras(settings, env)
        self.assertEqual(cameras, {})

    def test_camera_inherits_global_gain(self):
        env = self.base_env() | {
            "TTS_GAIN_DB": "5.5",
            "CAMERA_01_ENABLED": "true",
            "CAMERA_01_ID": "gate",
            "CAMERA_01_IP": "10.0.0.10",
            "CAMERA_01_USER": "admin",
            "CAMERA_01_PASSWORD": "password",
        }
        settings = load_settings(env)
        cameras, _ = load_cameras(settings, env)
        self.assertEqual(cameras["gate"]["gain_db"], 5.5)

    def test_json_camera_list(self):
        env = self.base_env()
        env["CAMERAS_JSON"] = json.dumps([
            {"id": "gate", "ip": "10.0.0.10", "user": "admin", "password": "a"},
            {"id": "yard", "ip": "10.0.0.11", "user": "admin", "password": "b", "gain_db": 3},
        ])
        settings = load_settings(env)
        cameras, default_camera = load_cameras(settings, env)
        self.assertEqual(set(cameras), {"gate", "yard"})
        self.assertEqual(default_camera, "")
        self.assertEqual(cameras["yard"]["gain_db"], 3.0)

    def test_camera_volume_level_is_normalized(self):
        env = self.base_env()
        env["CAMERAS_JSON"] = json.dumps([
            {"id": "gate", "ip": "10.0.0.10", "user": "admin", "password": "a", "volume_level": 0.42},
        ])
        settings = load_settings(env)
        cameras, _ = load_cameras(settings, env)
        self.assertAlmostEqual(cameras["gate"]["volume_level"], 0.42)

    def test_invalid_camera_volume_level_is_rejected(self):
        env = self.base_env()
        env["CAMERAS_JSON"] = json.dumps([
            {"id": "gate", "ip": "10.0.0.10", "user": "admin", "password": "a", "volume_level": 1.5},
        ])
        settings = load_settings(env)
        with self.assertRaises(ConfigError):
            load_cameras(settings, env)

    def test_duplicate_target_is_rejected(self):
        env = self.base_env() | {
            "CAMERA_01_ENABLED": "true",
            "CAMERA_01_ID": "gate-a",
            "CAMERA_01_IP": "10.0.0.10",
            "CAMERA_01_PASSWORD": "a",
            "CAMERA_02_ENABLED": "true",
            "CAMERA_02_ID": "gate-b",
            "CAMERA_02_IP": "10.0.0.10",
            "CAMERA_02_PASSWORD": "b",
        }
        settings = load_settings(env)
        with self.assertRaises(ConfigError):
            load_cameras(settings, env)

    def test_duplicate_target_can_be_explicitly_allowed(self):
        env = self.base_env() | {
            "ALLOW_DUPLICATE_CAMERA_TARGETS": "true",
            "CAMERA_01_ENABLED": "true",
            "CAMERA_01_ID": "gate-a",
            "CAMERA_01_IP": "10.0.0.10",
            "CAMERA_01_PASSWORD": "a",
            "CAMERA_02_ENABLED": "true",
            "CAMERA_02_ID": "gate-b",
            "CAMERA_02_IP": "10.0.0.10",
            "CAMERA_02_PASSWORD": "b",
        }
        settings = load_settings(env)
        cameras, _ = load_cameras(settings, env)
        self.assertEqual(len(cameras), 2)

    def test_invalid_default_camera_is_rejected(self):
        env = self.base_env() | {
            "DEFAULT_CAMERA": "yard",
            "CAMERA_01_ENABLED": "true",
            "CAMERA_01_ID": "gate",
            "CAMERA_01_IP": "10.0.0.10",
            "CAMERA_01_PASSWORD": "a",
        }
        settings = load_settings(env)
        with self.assertRaises(ConfigError):
            load_cameras(settings, env)

    def test_invalid_gain_is_rejected(self):
        env = self.base_env() | {"TTS_GAIN_DB": "25"}
        with self.assertRaises(ConfigError):
            load_settings(env)

    def test_invalid_json_is_rejected(self):
        env = self.base_env() | {"CAMERAS_JSON": "[{bad json]"}
        settings = load_settings(env)
        with self.assertRaises(ConfigError):
            load_cameras(settings, env)


if __name__ == "__main__":
    unittest.main()
