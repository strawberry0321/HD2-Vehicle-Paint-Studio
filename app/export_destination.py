"""Validate user-named ZIP destinations without overwriting existing files."""
from pathlib import Path
import os,re,tempfile
from storage_paths import INSTALL_ROOT

DEFAULT_DIRECTORY=INSTALL_ROOT/'mod'
class DestinationError(ValueError):
    def __init__(self,message,code='destination'):
        super().__init__(message);self.code=code

def filename(value):
    if not isinstance(value,str) or not value.strip():raise DestinationError('请填写压缩包名称')
    stem=value.strip()
    if stem.lower().endswith('.zip'):stem=stem[:-4]
    if not stem or stem in ['.','..'] or stem.endswith(('.', ' ')):
        raise DestinationError('压缩包名称不能为空，也不能以句点或空格结尾')
    if re.search(r'[<>:"/\\|?*\x00-\x1f]',stem):raise DestinationError('压缩包名称不能包含 \\ / : * ? " < > | 等字符')
    if re.fullmatch(r'CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]',stem.split('.')[0],re.I):
        raise DestinationError('这个名称是系统保留名称，请换一个名称')
    if len(stem.encode('utf-16le'))//2>180:raise DestinationError('压缩包名称过长，请缩短后再导出')
    return stem+'.zip'

def resolve(payload,create=False):
    if not isinstance(payload,dict):raise DestinationError('请填写压缩包名称并选择保存文件夹')
    name=filename(payload.get('name'))
    value=payload.get('directory')
    if value is None or value=='':directory=DEFAULT_DIRECTORY
    elif not isinstance(value,str) or not value.strip():raise DestinationError('请选择有效的保存文件夹')
    else:
        directory=Path(value.strip())
        if not directory.is_absolute():
            if directory.drive:raise DestinationError('请填写完整的保存文件夹路径')
            directory=INSTALL_ROOT/directory
    try:
        directory=directory.resolve()
        if directory.exists() and not directory.is_dir():raise DestinationError('保存位置是一个文件，请选择文件夹')
        target=directory/name
        if target.exists():raise DestinationError('同名 ZIP 已存在，请修改压缩包名称或选择其他文件夹。已有文件不会被覆盖。','exists')
        if create:
            directory.mkdir(parents=True,exist_ok=True)
            # Fail before baking if the selected folder cannot receive files.
            with tempfile.TemporaryFile(prefix='.hd2-export-',dir=directory):pass
        return target
    except DestinationError:raise
    except (OSError,ValueError) as e:raise DestinationError('无法使用保存文件夹，请检查路径和写入权限：'+str(e)) from e

def inspect(payload):
    target=resolve(payload)
    return dict(name=target.name,directory=str(target.parent),path=str(target))

def publish(pending,target):
    """Publish complete files exclusively, including a late filename collision."""
    try:
        if os.name=='nt':Path(pending).rename(target)
        else:
            os.link(pending,target);Path(pending).unlink()
    except FileExistsError as e:
        raise DestinationError('同名 ZIP 已存在，请修改压缩包名称或选择其他文件夹。已有文件不会被覆盖。','exists') from e
