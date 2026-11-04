"""Serializable SSOT contract; no dependencies on generation models."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Evidence(StrictModel):
    document_id: str
    locator: str
    quote: str

class Candidate(StrictModel):
    value: Any
    authority: int | None = None
    sources: list[Evidence] = Field(min_length=1)
    basis: str
    actor: str | None = None
    confirmed_at: str | None = None

class Fact(StrictModel):
    value: Any = None
    candidates: list[Candidate] = Field(default_factory=list)
    status: Literal['resolved','missing','conflict','unranked','manual'] = 'missing'
    needs_confirmation: bool = True
    reason: str = '未找到可靠来源，待补录'

class Block(StrictModel):
    locator: str
    text: str
    kind: Literal['paragraph','table_row']
    section: str = ''
    contract_kind: str | None = None
    cells: list[str] = Field(default_factory=list)
    table_index: int | None = None
    row: int | None = None

class SourceDocument(StrictModel):
    document_id: str
    logical_path: str
    storage_path: str
    sha256: str
    role: str
    contract_kind: str | None = None
    blocks: list[Block]
    warnings: list[str] = Field(default_factory=list)

class Requirement(StrictModel):
    key: str
    category: str
    fact: Fact

class Lot(StrictModel):
    key: str
    fields: dict[str,Fact]
    row_sources: list[Evidence]
    scope_status: Literal['catalogue','package_explicit']

class TechnicalMaterial(StrictModel):
    solidified_id: str
    material_mapping: dict[str,Any]
    document_ids: list[str]
    parameter_points: list[Evidence]
    response_template_sources: list[Evidence] = Field(default_factory=list)
    response_table_ref: str
    response_table_status: Literal['input_only'] = 'input_only'

class SSOT(StrictModel):
    schema_version: Literal['tender-package-ssot/1'] = 'tender-package-ssot/1'
    rules_version: str
    rules_snapshot: dict[str,Any]
    rules_sha256: str
    fields: dict[str,Fact]
    documents: list[SourceDocument]
    inventory: list[dict[str,Any]]
    lots: list[Lot]
    requirements: list[Requirement]
    pricing_rows: list[dict[str,Any]]
    materials: list[TechnicalMaterial]
    module2_mapping: dict[str,Any]
    module2_payload_sha256: str
    strategy: Literal['goods','works','services','pending'] = 'pending'
    conflicts: list[str]
    pending: list[str]
    warnings: list[str]
    manual_history: list[dict[str,Any]] = Field(default_factory=list)

    @model_validator(mode='after')
    def references(self):
        docs={d.document_id:d for d in self.documents}
        if len(docs)!=len(self.documents):raise ValueError('来源文档ID重复')
        locators={k:{b.locator:b.text for b in d.blocks} for k,d in docs.items()}
        for fact in self.fields.values():
            for c in fact.candidates:
                for s in c.sources:self._check(s,locators)
        for requirement in self.requirements:
            for c in requirement.fact.candidates:
                for s in c.sources:self._check(s,locators)
        for lot in self.lots:
            for s in lot.row_sources:self._check(s,locators)
            for fact in lot.fields.values():
                for c in fact.candidates:
                    for s in c.sources:self._check(s,locators)
        for row in self.pricing_rows:
            self._check(Evidence.model_validate(row['source']),locators)
        ids=[m.solidified_id for m in self.materials]
        expected=[e['solidified_id'] for e in self.module2_mapping['entries']]
        if len(ids)!=len(set(ids)) or set(ids)!=set(expected):raise ValueError('固化ID集合与模块二不一致')
        if len({m.response_table_ref for m in self.materials})!=len(ids):raise ValueError('应答表引用不唯一')
        by_id={e['solidified_id']:e for e in self.module2_mapping['entries']}
        for material in self.materials:
            if material.material_mapping!=by_id[material.solidified_id]:raise ValueError('模块二映射被修改')
            if not material.document_ids or any(k not in docs for k in material.document_ids):raise ValueError('技术规范来源缺失')
            for s in [*material.parameter_points,*material.response_template_sources]:self._check(s,locators)
        return self

    @staticmethod
    def _check(s,locators):
        if s.document_id not in locators or s.locator not in locators[s.document_id]:raise ValueError('来源定位无效')
        if locators[s.document_id][s.locator]!=s.quote:raise ValueError('来源引文与原始文档块不一致')
