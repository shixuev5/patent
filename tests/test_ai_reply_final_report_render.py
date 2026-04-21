from pathlib import Path

from agents.ai_reply.src.nodes.final_report_render import FinalReportRenderNode


def test_final_report_render_enables_mathjax(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "agents.ai_reply.src.nodes.final_report_render.write_markdown",
        lambda md_text, output_path: Path(output_path).write_text(md_text, encoding="utf-8"),
    )

    def _fake_render_markdown_to_pdf(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "agents.ai_reply.src.nodes.final_report_render.render_markdown_to_pdf",
        _fake_render_markdown_to_pdf,
    )

    report = {
        "summary": {},
        "amendment_section": {
            "substantive_change_groups": [
                {
                    "claim_id": "10",
                    "claim_type": "dependent",
                    "items": [
                        {
                            "amendment_id": "A5",
                            "feature_text": "公式特征",
                            "feature_before_text": "",
                            "feature_after_text": "所述车内压力预测值的计算公式为: $$P_{1i}^{*}=\\left(P_{1(i-1)}+P_{2i}^{*}\\times \\Delta t / \\tau\\right)$$",
                            "contains_added_text": True,
                            "amendment_kind": "claim_feature_merge",
                            "content_origin": "old_claim",
                            "source_claim_ids": ["11"],
                            "has_ai_assessment": False,
                            "assessment": {},
                            "evidence": [],
                            "final_review_reason": "",
                        }
                    ],
                }
            ],
            "structural_adjustments": [],
        },
        "response_dispute_section": {"items": []},
        "response_reply_section": {"items": []},
        "claim_review_section": {"items": []},
    }

    node = FinalReportRenderNode()
    artifacts = node._render_report({"final_report": report, "output_dir": str(tmp_path)})

    assert Path(artifacts["markdown_path"]).exists()
    assert calls
    assert calls[0]["enable_mathjax"] is True


def test_final_report_render_skips_mathjax_when_markdown_has_no_formula(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "agents.ai_reply.src.nodes.final_report_render.write_markdown",
        lambda md_text, output_path: Path(output_path).write_text(md_text, encoding="utf-8"),
    )

    def _fake_render_markdown_to_pdf(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "agents.ai_reply.src.nodes.final_report_render.render_markdown_to_pdf",
        _fake_render_markdown_to_pdf,
    )

    report = {
        "summary": {},
        "amendment_section": {"substantive_change_groups": [], "structural_adjustments": []},
        "response_dispute_section": {"items": []},
        "response_reply_section": {"items": []},
        "claim_review_section": {"items": []},
    }

    node = FinalReportRenderNode()
    node._render_report({"final_report": report, "output_dir": str(tmp_path)})

    assert calls
    assert calls[0]["enable_mathjax"] is False
