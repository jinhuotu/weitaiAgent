"""Format extension entry point; historical ZIP unpack remains unchanged."""
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
import shutil
from .readers import open_reader
from .unpack import Limits, PackageError, unpack

ARCHIVES = {'.zip', '.rar', '.7z'}


def unpack_extended(source, destination, *, limits=Limits(), password=None):
    """Explicit extended entry point; do not replace the historical ZIP entry."""
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    files, hashes = [], {}
    total, count = 0, 0

    def visit(archive, target, chain, depth):
        nonlocal total, count
        if depth > limits.max_depth:
            raise PackageError(f'嵌套层级超限：{chain}；请拆分压缩包')
        with open_reader(archive, password) as reader:
            members = reader.members()
            count += len(members)
            if count > limits.max_files:
                raise PackageError('文件数量超限；请拆分压缩包')
            for index, member in enumerate(members):
                name = member.name.replace('\\', '/')
                parts = PurePosixPath(name).parts
                if (not parts or name.startswith('/') or any(
                    p in ('.', '..') or ':' in p or p.startswith('.') or p.endswith((' ', '.'))
                    or re.fullmatch(r'(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p)
                    for p in parts)):
                    raise PackageError(f'不安全路径：{name}；请移除该条目')
                if member.link:
                    raise PackageError(f'不支持链接或特殊文件：{name}')
                if member.directory:
                    continue
                suffix = Path(name).suffix.lower()
                if suffix not in (*limits.allowed, *ARCHIVES):
                    raise PackageError(f'不支持的文件类型：{name}；请移除或转换为文档')
                if member.size / max(member.compressed_size, 1) > limits.max_ratio:
                    raise PackageError(f'压缩比超限：{name}；请拆分压缩包')
                output = target / str(index) / Path(*parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                if not output.resolve().is_relative_to(destination.resolve()):
                    raise PackageError(f'路径越界：{name}')
                digest, size = sha256(), 0
                try:
                    with reader.stream(member) as src, output.open('xb') as dst:
                        while block := src.read(65536):
                            total += len(block); size += len(block)
                            if total > limits.max_bytes or size > member.size:
                                raise PackageError(f'解压大小超限：{name}；请拆分或重新导出压缩包')
                            digest.update(block); dst.write(block)
                except PackageError:
                    raise
                except Exception as exc:
                    raise PackageError(f'无法读取 {name}：文件可能加密、损坏或缺少解码器；请检查密码或转换 ZIP') from exc
                if size != member.size:
                    raise PackageError(f'文件完整性校验失败：{name}；请重新导出')
                logical = '/'.join([*chain, name])
                value = digest.hexdigest()
                files.append(dict(logical_path=logical, source_chain=list(chain), depth=depth,
                                  file_type=suffix, size=size, sha256=value,
                                  duplicate_of=hashes.get(value), storage_path=str(output.relative_to(destination)),
                                  role='integrity' if suffix == '.sign' else 'pending'))
                hashes.setdefault(value, logical)
                if suffix in ARCHIVES:
                    visit(output, output.parent / 'nested', [*chain, name], depth + 1)
    try:
        visit(source, destination, [], 0)
        return dict(files=files, total_bytes=total, file_count=len(files), limits=asdict(limits))
    except Exception as exc:
        shutil.rmtree(destination)
        if isinstance(exc, PackageError): raise
        raise PackageError('压缩文件损坏或格式不支持；请重新导出 ZIP') from exc
