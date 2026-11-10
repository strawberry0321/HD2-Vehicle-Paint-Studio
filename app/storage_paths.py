"""Resolve user data separately from the application installation."""
from pathlib import Path
import json,os

INSTALL_ROOT=Path(__file__).resolve().parent.parent
def data_directory():
    value=os.environ.get('HD2_VEHICLE_DATA_DIR')
    if not value:
        settings=INSTALL_ROOT/'settings.json'
        config=json.loads(settings.read_text(encoding='utf8')) if settings.exists() else {}
        value=config.get('dataDirectory','../载具涂装数据')
    path=Path(value)
    if not path.is_absolute():path=INSTALL_ROOT/path
    path=path.resolve()
    if path==INSTALL_ROOT or path.is_relative_to(INSTALL_ROOT):
        raise ValueError('数据目录需要位于程序目录之外')
    return path

DATA_ROOT=data_directory()
CACHE_ROOT=DATA_ROOT/'tmp'
