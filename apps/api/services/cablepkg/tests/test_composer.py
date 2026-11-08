from apps.api.services.cablepkg.composer import SectionRequest, SourceSnippet, compose_section


def test_public_text_hides_internal_sources_but_draft_keeps_them():
    request = SectionRequest(
        title="质量保证措施",
        snippets=(SourceSnippet("产品出厂前进行例行检验。", "product-master-1", "L1"),),
    )

    draft = compose_section(request, lambda _: "产品出厂前进行例行检验。[来源: product-master-1]\n来源：内部资料")

    assert draft.public_text() == "产品出厂前进行例行检验。"
    assert draft.source_ids == ["product-master-1"]
    assert draft.evidence_levels == ["L1"]


def test_offline_fallback_is_deterministic_and_warns_on_weak_evidence():
    request = SectionRequest(
        title="售后服务",
        snippets=(SourceSnippet("提供技术支持。", "service-template", "L2"),),
    )
    first = compose_section(request).public_text()
    second = compose_section(request).public_text()
    assert first == second == "提供技术支持。"
    assert any("L2-L4" in warning for warning in compose_section(request).warnings)
