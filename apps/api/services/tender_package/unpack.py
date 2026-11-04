"""Bounded ZIP extraction with provenance. Never extracts into original inputs."""
from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path, PurePosixPath
import stat
import re
import zipfile


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    max_files: int = 2000
    max_bytes: int = 512 * 1024 * 1024
    max_ratio: int = 200
    max_depth: int = 4
    allowed: tuple = ('.docx', '.doc', '.pdf', '.xlsx', '.xls', '.csv', '.txt', '.png', '.jpg', '.jpeg', '.zip', '.sign')


def unpack(source: Path, destination: Path, *, limits=Limits(), password=None):
    """All-or-nothing workspace: caller must supply a new directory."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    records, hashes, total = [], {}, 0
    entries = 0

    def visit(archive, target, chain, depth):
        nonlocal total, entries
        if depth > limits.max_depth:
            raise PackageError(f'嵌套层级超限：{chain}；请拆分压缩包')
        if not zipfile.is_zipfile(archive):
            raise PackageError(f'不是有效 ZIP 或文件已损坏：{chain}；请重新导出 ZIP')
        try:
            with zipfile.ZipFile(archive) as z:
                members = z.infolist()
                entries += len(members)
                if entries > limits.max_files:
                    raise PackageError('文件数量超限；请拆分压缩包')
                for index, info in enumerate(members):
                    name = info.filename
                    if not info.flag_bits & 0x800:
                        try: name = name.encode('cp437').decode('gbk')
                        except (UnicodeError, LookupError): pass
                    name = name.replace('\\', '/')
                    parts = PurePosixPath(name).parts
                    if not parts or name.startswith('/') or any(p in ('..', '.') or ':' in p or p.startswith('.') or p.endswith((' ', '.')) or re.fullmatch(r'(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p) for p in parts):
                        raise PackageError(f'不安全路径：{name}；请移除该条目')
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                        raise PackageError(f'不支持链接或特殊文件：{name}')
                    if info.is_dir():
                        continue
                    suffix = Path(name).suffix.lower()
                    if suffix not in limits.allowed:
                        raise PackageError(f'不支持的文件类型：{name}；请移除或转换为受支持文档')
                    if info.file_size / max(info.compress_size, 1) > limits.max_ratio:
                        raise PackageError(f'压缩比超限：{name}；请拆分或取消高度压缩')
                    # Each member gets its own directory: duplicate names never overwrite.
                    output = target / str(index) / Path(*parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    if not output.resolve().is_relative_to(destination.resolve()):
                        raise PackageError(f'路径越界：{name}')
                    digest, size = sha256(), 0
                    try:
                        with z.open(info, pwd=password.encode() if password else None) as src, output.open('xb') as dst:
                            while block := src.read(64 * 1024):
                                total += len(block); size += len(block)
                                if total > limits.max_bytes:
                                    raise PackageError(f'解压总大小超限：{name}；请拆分压缩包')
                                digest.update(block); dst.write(block)
                    except RuntimeError as exc:
                        raise PackageError(f'加密文件需要正确密码：{name}；请重新提交密码') from exc
                    logical = '/'.join([*chain, name])
                    hash_value = digest.hexdigest()
                    records.append(dict(logical_path=logical, source_chain=[*chain], depth=depth,
                                        file_type=suffix, size=size, sha256=hash_value,
                                        duplicate_of=hashes.get(hash_value), storage_path=str(output.relative_to(destination)),
                                        role='integrity' if suffix == '.sign' else 'pending'))
                    hashes.setdefault(hash_value, logical)
                    if len(records) > limits.max_files:
                        raise PackageError('文件数量超限；请拆分压缩包')
                    if suffix == '.zip': visit(output, output.parent / 'nested', [*chain, name], depth + 1)
        except (zipfile.BadZipFile, EOFError, NotImplementedError) as exc:
            raise PackageError(f'压缩文件损坏或压缩算法不受支持：{chain}；请重新导出 ZIP') from exc

    try:
        visit(Path(source), destination, [], 0)
        return dict(files=records, total_bytes=total, file_count=len(records), limits=asdict(limits))
    except Exception:
        import shutil
        shutil.rmtree(destination)
        raise
