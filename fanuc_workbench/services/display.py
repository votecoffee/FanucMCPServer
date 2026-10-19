"""Human-readable watch values; recorded values remain unchanged."""
import json
import math
import re


def watch_value(value, error=None):
    detail = json.dumps(value, indent=2, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    if error is not None:
        return 'Unavailable', str(error) + ('\n\nCaptured value:\n' + detail if value is not None else '')
    if value is None:
        return 'Unavailable', 'No value was captured.'
    if isinstance(value, dict):
        axes = value.get('values', value)
        if isinstance(axes, dict) and axes and all(re.fullmatch(r'(?:[XYZWPR]|J[0-9]+|E[0-9]+)', key) for key in axes):
            def order(key):
                return (0, 'XYZWPR'.index(key)) if key in 'XYZWPR' else (1 if key.startswith('J') else 2, int(key[1:]))
            parts = []
            for key in sorted(axes, key=order):
                number = axes[key]
                text = f'{number:.6f}'.rstrip('0').rstrip('.') if type(number) in (int, float) and math.isfinite(number) else str(number)
                parts.append(f'{key}={text}')
            return '\n'.join('   '.join(parts[i:i + 3]) for i in range(0, len(parts), 3)), detail
        return json.dumps(value, ensure_ascii=False, sort_keys=True), detail
    return str(value), detail
