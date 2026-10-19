"""Core-owned optional SNPX reads and hash-manifested transport evidence."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import struct
import time

from fanuc_workbench.bridge.storage import atomic_json
from fanuc_workbench.services.monitoring import WATCH_RE
from fanuc_workbench.services.snpx import SnpxError, SnpxReader


class SnpxReads:
    def __init__(self, bridge):
        self.bridge = bridge

    def _access(self, robot_id):
        access = self.bridge._access(robot_id)
        if access.snpx_read is None:
            raise ValueError("SNPX reads are disabled; configure an exact snpx_read policy first")
        return access

    def _capture(self, robot_id, access, client, result, kind):
        folder = self.bridge._folder()
        wire = json.dumps(client.transcript, sort_keys=True, separators=(',', ':')).encode('ascii')
        (folder / 'wire.json').write_bytes(wire)
        identity = {'robot_id': robot_id, 'host': access.profile.host, 'port': access.snpx_read.port,
                    'provider': 'snpx', 'physical_identity': 'not_verified'}
        result = {**result, **identity, 'capture_is_atomic': False, 'controller_written': False,
                  'wire_sha256': hashlib.sha256(wire).hexdigest(),
                  'policy': asdict(access.snpx_read), 'mapping_verified': False,
                  'source': 'SNPX/HMI read evidence; controller compatibility unverified'}
        atomic_json(folder / 'sample.json', result)
        item = self.bridge.operations.store.register(folder, kind, robot_id=robot_id)
        self.bridge._audit('snpx_read', robot_id=robot_id, artifact_id=item['artifact_id'],
                           kind=kind, wire_sha256=result['wire_sha256'], errors=result['errors'])
        return {**result, 'artifact_id': item['artifact_id']}

    def read_words(self, robot_id, address, count=1):
        with self.bridge._robot_lock(robot_id):
            access = self._access(robot_id)
            access.snpx_read.check_words(address, count)
            client = SnpxReader(access.profile.host, access.snpx_read, access.profile.timeout)
            payload, errors = None, {}
            try:
                with client:
                    payload = client.read_words(address, count)
            except (SnpxError, OSError):
                errors['words'] = 'SNPX read unavailable; inspect retained wire evidence and controller setup'
            words = list(struct.unpack('<' + 'H' * count, payload)) if payload is not None else None
            result = {'timestamp': time.time(), 'address': address, 'count': count,
                      'address_space': 'one-based SNPX %R words; not robot R[] or PR[]',
                      'raw_hex': payload.hex() if payload is not None else None, 'words_u16': words,
                      'words_i16': [word if word < 32768 else word - 65536 for word in words] if words is not None else None,
                      'errors': errors}
            return self._capture(robot_id, access, client, result, 'snpx_words')

    def sample(self, robot_id, expressions, *, checkpoint=None):
        # The caller holds the robot lock and enforces the shared sampling clock.
        access = self._access(robot_id)
        targets, formats = [], {}
        for expression in expressions:
            match = WATCH_RE.fullmatch(expression)
            if match is None or match[3]:
                raise ValueError("SNPX samples support configured digital/group/analog I/O only")
            target = f'{match[1].upper()}[{int(match[2])}]'
            formats[expression] = access.snpx_read.io_format(target)
            targets.append(target)
        if len(set(targets)) != len(targets):
            raise ValueError("SNPX samples require distinct targets")
        client = SnpxReader(access.profile.host, access.snpx_read, access.profile.timeout, checkpoint)
        values, errors = dict.fromkeys(expressions), {}
        try:
            with client:
                for expression, target in zip(expressions, targets):
                    values[expression] = client.read_io(target)
        except (SnpxError, OSError):
            errors = {name: 'SNPX read unavailable; capture stopped without fallback'
                      for name, value in values.items() if value is None}
        return self._capture(robot_id, access, client,
            {'timestamp': time.time(), 'values': values, 'errors': errors, 'unavailable_files': [],
             'value_formats': formats, 'scaling_applied': False}, 'sample')
