"""Offline connection-profile editing shared by desktop and Python callers."""
from __future__ import annotations

from dataclasses import replace
import json
import math

from fanuc_workbench.core.models import RobotProfile


def copy_profile(profile: RobotProfile) -> RobotProfile:
    return replace(profile, roots=list(profile.roots))


def profile_signature(profile: RobotProfile) -> str:
    """Non-secret settings that bind a displayed connection to its endpoint."""
    return json.dumps((profile.name, profile.host, profile.port, profile.transport, profile.username,
                       profile.roots, profile.timeout, profile.sftp_trust_unknown_host), separators=(',', ':'))


def profile_key(profile: RobotProfile) -> dict:
    return {key: getattr(profile, key) for key in ('name', 'host', 'port', 'transport', 'username')}


def normalized_profile(profile: RobotProfile) -> RobotProfile:
    name, host = profile.name.strip(), profile.host.strip()
    if not name or any(ord(c) < 32 for c in name):
        raise ValueError('Enter a connection name without control characters.')
    if not host or any(c.isspace() or ord(c) < 32 for c in host) or any(c in host for c in '/\\?#'):
        raise ValueError('Enter a hostname or IP address, without a URL or path.')
    transport = profile.transport.strip().lower()
    if transport not in ('ftp', 'sftp'):
        raise ValueError('Choose FTP or SFTP transport.')
    if type(profile.port) is not int or not 1 <= profile.port <= 65535:
        raise ValueError('Port must be between 1 and 65535.')
    if not isinstance(profile.timeout, (int, float)) or not math.isfinite(profile.timeout) or not 0 < profile.timeout <= 3600:
        raise ValueError('Timeout must be greater than zero and at most 3600 seconds.')
    if any(any(ord(c) < 32 for c in root) for root in profile.roots):
        raise ValueError('Backup roots must not contain control characters.')
    roots = [root.strip() for root in profile.roots if root.strip()]
    return replace(profile, name=name, host=host, transport=transport,
                   username=profile.username.strip() or 'anonymous', roots=roots or ['md:'])


class ProfileCatalog:
    """Edit copies; preserve original identities for remembered-profile migration."""

    def __init__(self, profiles):
        profiles = list(profiles)
        self._profiles = [copy_profile(profile) for profile in profiles]
        self._origins = [copy_profile(profile) for profile in profiles]
        self._originals = list(self._origins)

    @property
    def profiles(self) -> list[RobotProfile]:
        return [copy_profile(profile) for profile in self._profiles]

    def _checked(self, profile, index=None):
        candidate = normalized_profile(profile)
        if any(i != index and p.name.strip().casefold() == candidate.name.casefold()
               for i, p in enumerate(self._profiles)):
            raise ValueError('A connection with that name already exists. Choose a unique name.')
        return candidate

    def add(self, profile) -> int:
        self._profiles.append(self._checked(profile))
        self._origins.append(None)
        return len(self._profiles) - 1

    def update(self, index, profile) -> None:
        self._profiles[index] = self._checked(profile, index)

    def duplicate(self, index) -> int:
        original = self._profiles[index]
        names = {p.name.strip().casefold() for p in self._profiles}
        name, suffix = original.name + ' copy', 2
        while name.casefold() in names:
            name = f'{original.name} copy {suffix}'
            suffix += 1
        return self.add(replace(original, name=name))

    def remove(self, index) -> None:
        del self._profiles[index]
        del self._origins[index]

    def validated_profiles(self) -> list[RobotProfile]:
        return [copy_profile(self._checked(p, i)) for i, p in enumerate(self._profiles)]

    def preference_updates(self, preferences, selected_index) -> dict:
        updates = {}
        for setting in ('last_connected_robot', 'last_selected_robot'):
            remembered = preferences.get(setting)
            if any(profile_key(p) == remembered for p in self._originals):
                updates[setting] = None
                for origin, profile in zip(self._origins, self._profiles):
                    if origin is not None and profile_key(origin) == remembered:
                        profile = normalized_profile(profile)
                        old_endpoint = {k: v for k, v in profile_key(origin).items() if k != 'name'}
                        new_endpoint = {k: v for k, v in profile_key(profile).items() if k != 'name'}
                        if setting != 'last_connected_robot' or old_endpoint == new_endpoint:
                            updates[setting] = profile_key(profile)
                        break
        updates['last_selected_robot'] = profile_key(normalized_profile(self._profiles[selected_index])) if selected_index is not None else None
        return updates
