"""Pin the welcome-only notice and mobile spacing in the qualified OCI."""
import argparse
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('notice_assets', HERE / 'verify-nonconfidential-notice-assets.py')
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)
assets.MARKERS['mobile_chat_spacing'] = 'md:pt-10'
assets.MARKERS['desktop_welcome_notice'] = 'cnooc-welcome-notice'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualified', type=Path, required=True)
    assets.verify(parser.parse_args().qualified)
