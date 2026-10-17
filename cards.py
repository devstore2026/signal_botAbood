"""Load the supplied 3D banners unchanged; never regenerate on startup."""
from pathlib import Path
ROOT = Path(__file__).resolve().parent/'assets'
def build():
    paths = {key: str(ROOT/f'{key}.png') for key in ('otc','expiration','confirmation','buy','sell')}
    for path in paths.values():
        if not Path(path).is_file():
            raise FileNotFoundError(path)
    return paths
def path_for(key):
    return str(ROOT/f'{key}.png')
def signal_card(direction):
    return path_for('buy' if direction == 'UP' else 'sell')
