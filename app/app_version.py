"""Shared release identity for the service and export descriptions."""
from pathlib import Path
import json
INFO=json.loads(Path(__file__).with_name('version.json').read_text(encoding='utf8'))
VERSION=INFO['version']
TITLE=INFO['title']
