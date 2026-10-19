"""
Game Profile Library and Game Discovery Engine.
Scans for installed games (Steam, Epic Games, Custom) and maintains
per-game OptiScaler & Intel XeSS configuration profiles.
"""

import os
import re
import json
import copy
import threading
from functools import wraps
from pathlib import Path
from core.files import write_json
from typing import Dict, List, Optional
from core.detector import GameDetector
from core.safety import SafetyManager
from core.settings import defaults, migrate, HOOKS

def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class GameLibrary:
    """Manages game profiles and auto-detects installed titles."""

    def __init__(self, storage_path: Optional[str] = None):
        self._lock = threading.RLock()
        self.new_game_defaults = {}
        if storage_path:
            self.storage_path = storage_path
        else:
            from core.paths import data_root
            self.storage_path = str(data_root() / 'profiles.json')

        self.profiles: Dict[str, Dict] = {}
        self.load_profiles()

    @synchronized
    def load_profiles(self):
        """Loads profiles from JSON storage."""
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    self.profiles = json.load(f)
                if not isinstance(self.profiles, dict) or any(not isinstance(v, dict) for v in self.profiles.values()):
                    raise ValueError('Profiles must be a mapping of game records.')
                for profile in self.profiles.values():
                    if profile.get('settings_schema', 0) < 2:
                        profile['xess_network_model'] = None
                        profile['reflex_boost'] = False
                        profile['fg_input'] = 'dlssg' if profile.get('starting_upscaler', 'DLSS') == 'DLSS' else 'fsrfg'
                        profile['settings_schema'] = 2
                        profile['recovery_dirs'] = profile.get('all_target_dirs', [])
                    profile.update(migrate(profile))
            except Exception as exc:
                raise ValueError(f'Cannot read profiles at {self.storage_path}. Original file retained: {exc}') from exc
        else:
            self.profiles = {}

    @synchronized
    def save_profiles(self):
        """Persists profiles to JSON storage."""
        write_json(self.storage_path, self.profiles)

    @synchronized
    def add_game_by_path(self, path: str) -> Optional[Dict]:
        """Analyzes a game path and adds or updates its profile."""
        analysis = GameDetector.analyze_game(path)
        if not analysis.get("valid"):
            return None

        game_id = self._generate_id(analysis["target_exe"])
        existing = self.profiles.get(game_id, {})

        profile = {
            **defaults(),
            **self.new_game_defaults,
            **existing,
            "id": game_id,
            "name": analysis["game_name"],
            "target_exe": analysis["target_exe"],
            "target_dir": analysis["target_dir"],
            "all_target_dirs": analysis.get("all_target_dirs", [analysis["target_dir"]]),
            "discovered_locations": analysis.get("discovered_locations", []),
            "base_dir": analysis["base_dir"],
            "engine": analysis["engine"],
            "detected_upscalers": analysis["detected_upscalers"],
            "anti_cheat": analysis.get("anti_cheat"),
            "recommended_hook": analysis["recommended_hook"],
            "graphics_api": analysis.get('graphics_api', 'Unknown'),
            "architecture": analysis.get('architecture'),
            "steam_appid": analysis.get('steam_appid'),
            "is_injected": self._check_injected_status(analysis["target_dir"]),
            "has_backup": SafetyManager.has_active_backup(analysis["target_dir"])
        }

        previous = self.profiles.get(game_id)
        self.profiles[game_id] = profile
        try:
            self.save_profiles()
        except Exception:
            if previous is None:
                self.profiles.pop(game_id, None)
            else:
                self.profiles[game_id] = previous
            raise
        return copy.deepcopy(profile)

    @synchronized
    def update_profile(self, game_id: str, updates: Dict) -> bool:
        """Updates properties of a profile."""
        if game_id not in self.profiles:
            return False
        previous = copy.deepcopy(self.profiles[game_id])
        self.profiles[game_id].update(updates)
        try:
            self.save_profiles()
        except Exception:
            self.profiles[game_id] = previous
            raise
        return True

    @synchronized
    def remove_game(self, game_id: str) -> bool:
        """Removes a game from the library."""
        if game_id in self.profiles:
            previous = self.profiles.pop(game_id)
            try:
                self.save_profiles()
            except Exception:
                self.profiles[game_id] = previous
                raise
            return True
        return False

    @synchronized
    def get_all_profiles(self) -> List[Dict]:
        """Returns all game profiles with fresh injection status."""
        for p in self.profiles.values():
            target_dir = p.get("target_dir", "")
            if os.path.exists(target_dir):
                p["is_injected"] = self._check_injected_status(target_dir)
                p["has_backup"] = SafetyManager.has_active_backup(target_dir)
            else:
                p["is_injected"] = False
                p["has_backup"] = False
        return copy.deepcopy(list(self.profiles.values()))

    def auto_scan_installed_games(self) -> List[Dict]:
        """Automatically discovers installed games from Steam and Epic Games."""
        discovered = []

        # 1. Scan Steam libraries
        steam_paths = self._find_steam_libraries()
        for steam_lib in steam_paths:
            apps_dir = os.path.join(steam_lib, "steamapps")
            common_dir = os.path.join(apps_dir, "common")
            if not os.path.isdir(common_dir):
                continue
            for item in os.listdir(common_dir):
                from core.tasks import checkpoint
                checkpoint()
                game_folder = os.path.join(common_dir, item)
                if os.path.isdir(game_folder):
                    profile = self.add_game_by_path(game_folder)
                    if profile:
                        discovered.append(profile)

        # 2. Scan Epic Games
        epic_manifest_dir = r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests"
        if os.path.isdir(epic_manifest_dir):
            for f in os.listdir(epic_manifest_dir):
                from core.tasks import checkpoint
                checkpoint()
                if f.endswith(".item"):
                    try:
                        with open(os.path.join(epic_manifest_dir, f), "r", encoding="utf-8") as item_file:
                            data = json.load(item_file)
                            install_loc = data.get("InstallLocation")
                            if install_loc and os.path.isdir(install_loc):
                                profile = self.add_game_by_path(install_loc)
                                if profile:
                                    discovered.append(profile)
                    except Exception:
                        pass

        return discovered

    def _find_steam_libraries(self) -> List[str]:
        """Finds all configured Steam library folders on the system."""
        candidates = [
            r"C:\Program Files (x86)\Steam",
            r"C:\Program Files\Steam",
            r"D:\SteamLibrary",
            r"E:\SteamLibrary",
            r"F:\SteamLibrary"
        ]
        valid = []
        for c in candidates:
            if os.path.isdir(c):
                valid.append(c)

        # Read libraryfolders.vdf
        for base in list(valid):
            vdf = os.path.join(base, "steamapps", "libraryfolders.vdf")
            if os.path.exists(vdf):
                try:
                    with open(vdf, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                        paths = re.findall(r'"path"\s+"([^"]+)"', text)
                        for p in paths:
                            norm = os.path.normpath(p.replace("\\\\", "\\"))
                            if os.path.isdir(norm) and norm not in valid:
                                valid.append(norm)
                except Exception:
                    pass

        return valid

    def _check_injected_status(self, target_dir: str) -> bool:
        """Checks if injection files are present in target directory."""
        if not os.path.exists(target_dir):
            return False
        has_ini = os.path.exists(os.path.join(target_dir, "OptiScaler.ini"))
        hooks = HOOKS
        has_hook = any(os.path.exists(os.path.join(target_dir, h)) for h in hooks)
        return has_ini and has_hook and SafetyManager.has_active_backup(target_dir)

    def _generate_id(self, path: str) -> str:
        """Creates a stable unique ID for a game path."""
        import hashlib
        return hashlib.md5(path.lower().encode("utf-8")).hexdigest()[:12]
