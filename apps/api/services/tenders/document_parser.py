"""一次性预解析招标文件文档结构，所有后续规则共享结果，消除重复全文正则。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class DocumentParagraph:
    """文档段落。"""
    index: int
    text: str
    start: int  # 在全文中的起始位置
    end: int
    is_heading: bool = False
    heading_level: int = 0  # 1=章, 2=节, 3=条
    heading_number: str = ""  # 如 "1.2.3"
    parent_heading: str = ""  # 上级标题


@dataclass
class DocumentStructure:
    """预解析的文档结构，供所有后续规则复用。"""
    # 原始文本
    full_text: str

    # 段落列表
    paragraphs: list[DocumentParagraph] = field(default_factory=list)

    # 索引
    headings: list[DocumentParagraph] = field(default_factory=list)  # 所有标题
    heading_index: dict[str, DocumentParagraph] = field(default_factory=dict)  # 标题文本 -> 段落

    # 缓存的提取结果（避免重复计算）
    _chapter_cache: str | None = None
    _tender_no_cache: str | None = None
    _unmashed_text_cache: str | None = None

    # 常用正则匹配结果缓存
    _regex_cache: dict[str, list[re.Match]] = field(default_factory=dict)

    def get_regex_matches(self, pattern: str | re.Pattern) -> list[re.Match]:
        """获取正则匹配结果（带缓存）。"""
        if isinstance(pattern, re.Pattern):
            key = pattern.pattern
            compiled = pattern
        else:
            key = pattern
            compiled = re.compile(pattern)

        if key not in self._regex_cache:
            self._regex_cache[key] = list(compiled.finditer(self.full_text))

        return self._regex_cache[key]

    def get_text_range(self, start: int, end: int) -> str:
        """获取指定范围的文本。"""
        return self.full_text[start:end]

    def find_paragraphs_in_range(self, start: int, end: int) -> list[DocumentParagraph]:
        """查找指定范围内的段落。"""
        result = []
        for para in self.paragraphs:
            # 段落与范围有交集
            if para.start < end and para.end > start:
                result.append(para)
        return result

    def find_section_by_title(self, title_pattern: str) -> DocumentParagraph | None:
        """根据标题模式查找章节（正则）。"""
        pattern = re.compile(title_pattern, re.IGNORECASE)
        for heading in self.headings:
            if pattern.search(heading.text):
                return heading
        return None

    def get_section_content(self, heading: DocumentParagraph) -> str:
        """获取某章节标题后的内容（直到下一个同级或上级标题）。"""
        start_idx = heading.index + 1
        end_idx = len(self.paragraphs)

        # 找到下一个同级或上级标题
        for i in range(start_idx, len(self.paragraphs)):
            para = self.paragraphs[i]
            if para.is_heading and para.heading_level <= heading.heading_level:
                end_idx = i
                break

        # 合并段落
        content_paras = self.paragraphs[start_idx:end_idx]
        return "\n".join(p.text for p in content_paras)


# 标题识别正则
_HEADING_PATTERNS = [
    # 第X章/部分/节
    re.compile(r"^(?:第[一二三四五六七八九十百\d]+[章部分节])\s*[^\n]{0,100}$", re.MULTILINE),
    # 一、二、三、
    re.compile(r"^[一二三四五六七八九十百]+[、．.]\s*[^\n]{1,100}$", re.MULTILINE),
    # 1. 1.1 1.1.1
    re.compile(r"^(\d+(?:\.\d+)*)[、．.\s]\s*[^\n]{1,100}$", re.MULTILINE),
    # (一) (1) 【1】
    re.compile(r"^[（(【][一二三四五六七八九十\d]+[)）】]\s*[^\n]{1,100}$", re.MULTILINE),
]


def _parse_heading_level(text: str) -> tuple[int, str]:
    """解析标题级别和编号。

    Returns:
        (level, number): level=1(章), 2(节), 3(条), 0(非标题)
    """
    text = text.strip()

    # 第X章 -> level 1
    if re.match(r"^第[一二三四五六七八九十百\d]+章", text):
        return 1, ""

    # 第X部分/节 -> level 2
    if re.match(r"^第[一二三四五六七八九十百\d]+[部分节]", text):
        return 2, ""

    # 一、二、 -> level 2
    if re.match(r"^[一二三四五六七八九十百]+[、．.]", text):
        return 2, ""

    # 1. 1.1 1.1.1 -> level by dots
    match = re.match(r"^(\d+(?:\.\d+)*)[、．.\s]", text)
    if match:
        number = match.group(1)
        dots = number.count(".")
        return min(2 + dots, 5), number  # 1->2, 1.1->3, 1.1.1->4

    # (一) (1) -> level 3
    if re.match(r"^[（(【][一二三四五六七八九十\d]+[)）】]", text):
        return 3, ""

    return 0, ""


def parse_document_structure(text: str) -> DocumentStructure:
    """一次性预解析文档结构。

    Args:
        text: 招标文件全文

    Returns:
        DocumentStructure: 包含段落、标题、索引的结构化对象
    """
    from time import perf_counter
    start = perf_counter()

    structure = DocumentStructure(full_text=text)

    # 按段落分割（保留空行作为段落分隔符）
    lines = text.split("\n")
    paragraphs: list[DocumentParagraph] = []

    current_para_lines: list[str] = []
    current_start = 0
    pos = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for \n

        # 空行或标题行：结束当前段落
        stripped = line.strip()
        is_potential_heading = any(pat.match(stripped) for pat in _HEADING_PATTERNS)

        if not stripped or is_potential_heading:
            # 保存当前段落
            if current_para_lines:
                para_text = "\n".join(current_para_lines)
                para_end = pos
                paragraphs.append(DocumentParagraph(
                    index=len(paragraphs),
                    text=para_text,
                    start=current_start,
                    end=para_end,
                ))
                current_para_lines = []

            # 标题行单独成段
            if is_potential_heading:
                level, number = _parse_heading_level(stripped)
                paragraphs.append(DocumentParagraph(
                    index=len(paragraphs),
                    text=stripped,
                    start=pos,
                    end=pos + line_len,
                    is_heading=True,
                    heading_level=level,
                    heading_number=number,
                ))
                current_start = pos + line_len
            else:
                current_start = pos + line_len
        else:
            current_para_lines.append(line)

        pos += line_len

    # 最后一段
    if current_para_lines:
        para_text = "\n".join(current_para_lines)
        paragraphs.append(DocumentParagraph(
            index=len(paragraphs),
            text=para_text,
            start=current_start,
            end=len(text),
        ))

    structure.paragraphs = paragraphs

    # 构建标题索引
    parent_stack: list[DocumentParagraph] = []
    for para in paragraphs:
        if para.is_heading:
            # 找到父标题
            while parent_stack and parent_stack[-1].heading_level >= para.heading_level:
                parent_stack.pop()

            if parent_stack:
                para.parent_heading = parent_stack[-1].text

            parent_stack.append(para)
            structure.headings.append(para)
            structure.heading_index[para.text.strip()] = para

    elapsed = perf_counter() - start
    logger.info(
        "document_structure_parsed: paragraphs=%d headings=%d chars=%d seconds=%.3f",
        len(paragraphs),
        len(structure.headings),
        len(text),
        elapsed,
    )

    return structure


def extract_chapter_from_structure(structure: DocumentStructure) -> str:
    """从结构中提取格式章节名称（缓存）。"""
    if structure._chapter_cache is not None:
        return structure._chapter_cache

    # 查找格式相关章节
    format_patterns = [
        r"投标.*文件.*格式",
        r"响应.*文件.*格式",
        r"投标.*文件.*编制",
        r"响应.*文件.*编制",
    ]

    for pattern in format_patterns:
        heading = structure.find_section_by_title(pattern)
        if heading:
            structure._chapter_cache = heading.text.strip()
            return structure._chapter_cache

    structure._chapter_cache = ""
    return ""


def extract_tender_no_from_structure(structure: DocumentStructure) -> str:
    """从结构中提取招标编号（缓存）。"""
    if structure._tender_no_cache is not None:
        return structure._tender_no_cache

    # 复用已有的正则
    patterns = [
        r"(?:招标|采购|询价)(?:项目)?编号\s*[:：为|]?\s*([A-Za-z0-9][A-Za-z0-9\-_/]{2,63})",
        r"项目编号\s*[:：为|]?\s*([A-Za-z0-9][A-Za-z0-9\-_/]{2,63})",
    ]

    for pattern in patterns:
        matches = structure.get_regex_matches(pattern)
        if matches:
            structure._tender_no_cache = matches[0].group(1)
            return structure._tender_no_cache

    structure._tender_no_cache = ""
    return ""


def unmash_text_from_structure(structure: DocumentStructure) -> str:
    """从结构中生成 unmashed 文本（缓存）。"""
    if structure._unmashed_text_cache is not None:
        return structure._unmashed_text_cache

    # 简化版 unmash：移除页码、分页符
    text = structure.full_text
    text = re.sub(r"\[第\s*\d+\s*页\]", "", text)
    text = re.sub(r"(?m)^-\s*\d+\s*-\s*$", "", text)
    text = re.sub(r"\f+", "\n", text)

    structure._unmashed_text_cache = text
    return text
