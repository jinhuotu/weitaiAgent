"""Optional archive readers. Decoders never choose filesystem destinations.

RAR uses rarfile plus its locally configured decoder. 7z uses py7zr.
Neither dependency is installed or downloaded automatically.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import zipfile
from .unpack import PackageError


@dataclass
class Member:
    name: str
    size: int
    compressed_size: int
    directory: bool = False
    link: bool = False
    token: object = None


@contextmanager
def open_reader(path, password=None):
    suffix = Path(path).suffix.lower()
    if suffix == '.zip':
        with zipfile.ZipFile(path) as archive:
            class Reader:
                def members(self):
                    import stat
                    result = []
                    for info in archive.infolist():
                        name = info.filename
                        if not info.flag_bits & 0x800:
                            try: name = name.encode('cp437').decode('gbk')
                            except UnicodeError: pass
                        mode = info.external_attr >> 16
                        result.append(Member(name, info.file_size, info.compress_size, info.is_dir(),
                                             stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR), info))
                    return result
                def stream(self, member):
                    return archive.open(member.token, pwd=password.encode() if password else None)
            yield Reader()
        return
    if suffix == '.rar':
        try:
            import rarfile
        except ImportError as exc:
            raise PackageError('本机缺少 RAR 解码能力；请将原包转换为 ZIP，或由部署方配置本地 rarfile 解码器') from exc
        with rarfile.RarFile(path) as archive:
            class Reader:
                def members(self):
                    return [Member(x.filename, x.file_size, x.compress_size, x.isdir(),
                                   bool(x.is_symlink() or getattr(x, 'file_redir', None)), x)
                            for x in archive.infolist()]
                def stream(self, member):
                    return archive.open(member.token, pwd=password)
            yield Reader()
        return
    if suffix == '.7z':
        raise PackageError('当前部署尚无经过验证的 7z 流式解码适配器；请转换为 ZIP 后重试')
    raise PackageError('不支持的压缩格式；请使用 ZIP 或配置本地 RAR 解码器')
