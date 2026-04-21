from agents.ai_reply.src.text_normalization import normalize_for_compare, sanitize_for_display


def test_sanitize_for_display_preserves_textcircled_formula() -> None:
    text = r"区别特征：$\textcircled{1}$ 为测试步骤。"

    result = sanitize_for_display(text)

    assert r"$\textcircled{1}$" in result


def test_sanitize_for_display_preserves_delta_and_math_relations() -> None:
    text = (
        r"其中，$\Delta \mathrm { I L } 1 = | \mathrm { I L } 1 1 - \mathrm { I L } 1 2 |$，"
        r"并满足 $\Delta \mathrm { I L } 1 <= 0.50 \mathrm { d B }$。"
    )

    result = sanitize_for_display(text)

    assert r"\Delta \mathrm{I L}1=|\mathrm{I L}1 1-\mathrm{I L}1 2|" in result
    assert r"\Delta \mathrm{I L}1 <= 0.50 \mathrm{d B}" in result


def test_normalize_for_compare_tolerates_formula_spacing_noise() -> None:
    left = r"所述车内压力预测值的计算公式为: $$P_{1i}^{*}= \left( P_{1(i-1)} + P_{2i}^{*} \times \Delta t / \tau \right)$$"
    right = r"所述车内压力预测值的计算公式为: $$P_{1i}^{*}=\left(P_{1(i-1)}+P_{2i}^{*}\times \Delta t/\tau\right)$$"

    assert normalize_for_compare(left) == normalize_for_compare(right)
