"""Rapor üretimi — PDF (Türkçe) + JSON dışa aktarma.

PDF için reportlab kullanılır; sistemde DejaVuSans TTF bulunursa Türkçe
karakterler doğru gömülür, bulunamazsa Helvetica + ASCII-normalleştirme
yedeğine düşülür.
"""

from __future__ import annotations

import html
import json
import logging
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger("showis.report")

_FONT_CANDIDATES = [
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
_FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]

_FONT_REGULAR = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_FONT_REGISTERED = False


def _find_font(candidates: list[str]) -> str | None:
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _ensure_fonts() -> None:
    """DejaVu TTF varsa reportlab'e kaydeder (Türkçe glif desteği)."""
    global _FONT_REGULAR, _FONT_BOLD, _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        regular = _find_font(_FONT_CANDIDATES)
        bold = _find_font(_FONT_BOLD_CANDIDATES)
        if regular:
            pdfmetrics.registerFont(TTFont("ShowisSans", regular))
            _FONT_REGULAR = "ShowisSans"
            if bold:
                pdfmetrics.registerFont(TTFont("ShowisSans-Bold", bold))
                _FONT_BOLD = "ShowisSans-Bold"
            else:
                _FONT_BOLD = "ShowisSans"
            _FONT_REGISTERED = True
    except Exception as exc:  # pragma: no cover
        logger.warning("TTF font kaydı başarısız, Helvetica yedeği: %s", exc)


def _ascii_safe(text: str) -> str:
    """Helvetica (Latin-1) yedeği için Türkçe karakterleri normalize eder."""
    trans = str.maketrans({"ş": "s", "Ş": "S", "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I"})
    text = text.translate(trans)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _t(text: Any) -> str:
    """PDF metni — font TTF ise orijinal, değilse ASCII güvenli."""
    s = "" if text is None else str(text)
    return s if _FONT_REGULAR != "Helvetica" else _ascii_safe(s)


def _e(text: Any) -> str:
    """Kullanici verisi icin Paragraph-guvenli metin: XML-escape + Turkce fallback.

    Paragraph mini-HTML parser & < > karakterlerini yorumlar; URL'lerdeki
    '&' veya '<script>' benzeri icerik PDF uretimini kirar.
    """
    return _t(html.escape("" if text is None else str(text), quote=False))


def generate_json_report(report_data: dict[str, Any]) -> bytes:
    """Rapor verisini JSON bytes olarak döndürür."""
    return json.dumps(report_data, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def generate_pdf_report(report_data: dict[str, Any]) -> bytes:
    """Rapor verisinden Türkçe PDF üretir."""
    from io import BytesIO

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    _ensure_fonts()

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=f"Showis OSINT Raporu — {report_data.get('case', {}).get('name', '')}",
        author="Showis OSINT",
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ShowisTitle",
        parent=styles["Title"],
        fontName=_FONT_BOLD,
        fontSize=18,
        spaceAfter=12,
    )
    h2_style = ParagraphStyle(
        "ShowisH2",
        parent=styles["Heading2"],
        fontName=_FONT_BOLD,
        fontSize=13,
        spaceBefore=14,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "ShowisBody",
        parent=styles["BodyText"],
        fontName=_FONT_REGULAR,
        fontSize=10,
        leading=14,
    )
    small_style = ParagraphStyle(
        "ShowisSmall",
        parent=body_style,
        fontSize=8,
        textColor=colors.HexColor("#555555"),
    )

    case = report_data.get("case", {})
    target = report_data.get("target", {})
    findings = report_data.get("findings", [])
    evidence = report_data.get("evidence", [])
    generated_at = report_data.get("generated_at", "")

    story: list[Any] = [
        Paragraph(_t("Showis OSINT İnceleme Raporu"), title_style),
        Spacer(1, 6),
        Paragraph(
            _t(
                "<b>GİZLİLİK DAMGASI:</b> Bu rapor yalnızca yetkili kişi tarafından, "
                "belirtilen amaç doğrultusunda hazırlanmıştır. İçerik üçüncü taraflarla "
                "paylaşılamaz, kamuya açık platformlarda yayınlanamaz."
            ),
            body_style,
        ),
        Spacer(1, 10),
        Paragraph(_t("1. Vaka Özeti"), h2_style),
        Paragraph(
            _t(
                f"<b>Vaka:</b> {_e(case.get('name', '-'))}<br/>"
                f"<b>Amaç:</b> {_e(case.get('purpose', '-'))}<br/>"
                f"<b>Hukuki dayanak:</b> {_e(case.get('legal_basis', '-'))}<br/>"
                f"<b>Oluşturulma:</b> {_e(case.get('created_at', '-'))}<br/>"
                f"<b>Rapor tarihi:</b> {_e(generated_at)}"
            ),
            body_style,
        ),
        Paragraph(_t("2. Hedef"), h2_style),
        Paragraph(
            _t(
                f"<b>Ad Soyad:</b> {_e(target.get('first_name', ''))} "
                f"{_e(target.get('last_name', ''))}<br/>"
                f"<b>İpuçları:</b> {_e(target.get('hints', {}))}"
            ),
            body_style,
        ),
        Paragraph(_t("3. Yöntem"), h2_style),
        Paragraph(
            _t(
                "Kamuya açık kaynaklar tarandı: GitHub REST API (Tier A), "
                "TryHackMe ve HackTheBox herkese açık profil sayfaları (Tier B, "
                "tarayıcı render), web arama sağlayıcı indeksleri (Tier C). "
                "Hiçbir giriş duvarı arkasına erişilmedi, CAPTCHA/bot koruması "
                "aşılmadı, yasak host'lara istek atılmadı. Bulgular deterministik "
                "sinyal skoru (0.00-1.00) ile sıralandı; LLM yalnızca kanıta "
                "dayalı açıklama metni üretti."
            ),
            body_style,
        ),
        Paragraph(_t("4. Bulgular"), h2_style),
    ]

    if findings:
        rows = [["Platform", "Etiket", "Skor", "Durum", "URL"]]
        for f in findings:
            rows.append(
                [
                    _e(f.get("platform", "")),
                    _e(f.get("label", "")),
                    f"{float(f.get('score', 0.0)):.2f}",
                    _e(f.get("status", "")),
                    _e(f.get("url") or "-"),
                ]
            )
        table = Table(rows, colWidths=[2.2 * cm, 4.5 * cm, 1.6 * cm, 2.2 * cm, 5.5 * cm])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f5f5f5")],
                    ),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)

        for f in findings:
            breakdown = f.get("breakdown") or {}
            story.append(Spacer(1, 8))
            story.append(
                Paragraph(
                    _t(
                        f"<b>{_e(f.get('label', ''))}</b> ({_e(f.get('platform', ''))}) — "
                        f"skor {float(f.get('score', 0.0)):.2f}, durum {_e(f.get('status', ''))}"
                    ),
                    body_style,
                )
            )
            if f.get("explanation"):
                story.append(Paragraph(_e(f["explanation"]), small_style))
            story.append(
                Paragraph(
                    _t(
                        "Sinyal kırılımı: "
                        + ", ".join(
                            f"{_e(k)}={_e(v)}"
                            for k, v in breakdown.items()
                            if k != "total" and v
                        )
                    ),
                    small_style,
                )
            )
    else:
        story.append(Paragraph(_t("Bulgu bulunamadı."), body_style))

    story.append(Paragraph(_t("5. Kanıt Ekleri"), h2_style))
    if evidence:
        for ev in evidence:
            story.append(
                Paragraph(
                    _t(
                        f"• [{_e(ev.get('kind', ''))}] {_e(ev.get('source_url', '-'))} "
                        f"(hash: {_e(str(ev.get('content_hash', ''))[:16])}…, "
                        f"tarih: {_e(ev.get('fetched_at', '-'))})"
                    ),
                    small_style,
                )
            )
    else:
        story.append(Paragraph(_t("Kayıtlı kanıt yok."), body_style))

    story.append(Paragraph(_t("6. Sınırlamalar"), h2_style))
    story.append(
        Paragraph(
            _t(
                "Bu rapor yalnızca kamuya açık verileri içerir. Platformlar "
                "verilerini değiştirebilir; bulgular tarama anındaki durumdur. "
                "Olası yanlış eşleşmeler için lütfen kanıt URL'lerini kontrol "
                "ediniz. Kişisel veri minimizasyonu ilkesiyle yalnızca amaca "
                "hizmet eden alanlar toplanmıştır."
            ),
            body_style,
        )
    )

    doc.build(story)
    return buffer.getvalue()
