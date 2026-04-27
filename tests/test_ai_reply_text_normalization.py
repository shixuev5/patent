from agents.ai_reply.src.text_normalization import normalize_for_compare, sanitize_for_display


def test_sanitize_for_display_normalizes_textcircled_marker_to_unicode() -> None:
    text = r"区别特征：$\textcircled{1}$ 为测试步骤。"

    result = sanitize_for_display(text)

    assert "①" in result
    assert r"\textcircled" not in result
    assert "$①$" not in result


def test_sanitize_for_display_normalizes_textcircle_variants_to_unicode() -> None:
    text = r"步骤\textcircled1、步骤\textcircle(d)和步骤$\textcircled{2}$。"

    result = sanitize_for_display(text)

    assert "①" in result
    assert "②" in result
    assert "ⓓ" in result
    assert r"\textcircle" not in result


def test_sanitize_for_display_downgrades_broken_inline_math_to_stable_plain_text() -> None:
    text = (
        r"其中，$\Delta \mathrm { I L } 1 = | \mathrm { I L } 1 1 - \mathrm { I L } 1 2 |$，"
        r"并满足 $\Delta \mathrm { I L } 1 <= 0.50 \mathrm { d B }$。"
    )

    result = sanitize_for_display(text)

    assert "ΔIL1=|IL11-IL12|" in result
    assert "ΔIL1<=0.50dB" in result
    assert r"\mathrm" not in result


def test_sanitize_for_display_normalizes_ocr_broken_threshold_formula() -> None:
    text = r"当 $\Delta \mathrm { I L } 1 { < } = 0 . 5 0 \mathrm { d B }$ 且 $\Delta \mathrm { I L } 2 { < } =$ 0.50dB。"

    result = sanitize_for_display(text)

    assert "ΔIL1<=0.50dB" in result
    assert "ΔIL2<=0.50dB" in result


def test_normalize_for_compare_tolerates_formula_spacing_noise() -> None:
    left = r"所述车内压力预测值的计算公式为: $$P_{1i}^{*}= \left( P_{1(i-1)} + P_{2i}^{*} \times \Delta t / \tau \right)$$"
    right = r"所述车内压力预测值的计算公式为: $$P_{1i}^{*}=\left(P_{1(i-1)}+P_{2i}^{*}\times \Delta t/\tau\right)$$"

    assert normalize_for_compare(left) == normalize_for_compare(right)


def test_sanitize_for_display_compacts_digit_spacing_inside_formula() -> None:
    text = r"$$P=\left(\frac{h}{1 0 0}+\rho_{1}\right)\times \gamma$$"

    result = sanitize_for_display(text)

    assert r"\frac{h}{100}" in result


def test_sanitize_for_display_normalizes_plain_text_noise_outside_math() -> None:
    text = r"满足 \<= 0.50 dB，以及 以及 光功率计 \| 输出稳定。"

    result = sanitize_for_display(text)

    assert r"\<=" not in result
    assert r"\|" not in result
    assert "<=0.50dB" in result
    assert "以及 以及" not in result


def test_sanitize_for_display_tolerates_unclosed_dollar_sign() -> None:
    text = r"分光比变化量为 $\Delta IL1，且满足 \<= 0.50 dB。"

    result = sanitize_for_display(text)

    assert "$" in result
    assert "<=0.50dB" in result
