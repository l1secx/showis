"""Rapor üretimi — PDF (Türkçe) + JSON + HTML dışa aktarma.

PDF için reportlab kullanılır; sistemde DejaVuSans TTF bulunursa Türkçe
karakterler doğru gömülür, bulunamazsa Helvetica + ASCII-normalleştirme
yedeğine düşülür.

Güvenlik (T-313): tüm metin üretimi **Jinja2** ile ve `autoescape=True` ortamında
yapılır. Kullanıcı verisi hiçbir zaman şablon olarak derlenmez; `{{7*7}}`,
`${...}`, `<script>` gibi girdiler kaçışlanmış (escaped) metin olarak çıkar.
`reportlab` `Paragraph` hücreleri de XML-escape'li metinle beslenir; böylece
`<b>`/`<br/>` enjeksiyonu PDF'i bozamaz.
"""

from __future__ import annotations

import html
import json
import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment

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

#: HTML/PDF metin üretiminde kullanılan tek ortam — autoescape DAİMA açık.
#: Kullanıcı verisi `{{ }}` ile basılır ve Jinja tarafından kaçışlanır; şablon
#: olarak **derlenmez** (SSTI yok).
report_env = Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)

_STATUS_LABELS = {
    "confirmed": "Doğrulandı",
    "candidate": "Aday",
    "lead": "İpucu",
    "rejected": "Reddedildi",
}

#: Kronoloji için kabul edilen tarih attribute'ları (T-301 ile aynı adlar)
_DATE_ATTRIBUTES = ("created_at", "joined_at", "created", "ctime")


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
    """Kullanıcı verisi için Paragraph-güvenli metin: XML-escape + Türkçe fallback."""
    return _t(html.escape("" if text is None else str(text), quote=False))


# ---------------------------------------------------------------------------
# Ortak bölüm verisi (JSON + PDF + HTML aynı kaynaktan beslenir)
# ---------------------------------------------------------------------------


def _norm_username(value: Any) -> str:
    return str(value or "").strip().lstrip("@").lower()


def _as_iso(value: Any) -> str | None:
    """Tarih değerini ISO 8601 metnine indirger (epoch / ISO / None)."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value <= 0:
            return None
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return text


def _entity_username(entity: dict[str, Any]) -> str:
    attrs = entity.get("attributes") or {}
    raw = attrs.get("username") or attrs.get("login") or entity.get("label")
    return _norm_username(raw)


def _one_line_reason(finding: dict[str, Any], limit: int = 120) -> str:
    """Bulgu için tek satır gerekçe: açıklama veya en güçlü sinyal."""
    explanation = (finding.get("explanation") or "").strip()
    if explanation:
        first_line = explanation.splitlines()[0].strip()
        return first_line[:limit] + ("…" if len(first_line) > limit else "")
    breakdown = {
        k: v
        for k, v in (finding.get("breakdown") or {}).items()
        if k != "total" and isinstance(v, (int, float)) and v
    }
    if breakdown:
        key, value = max(breakdown.items(), key=lambda item: float(item[1]))
        return f"En güçlü sinyal: {key}={float(value):.2f}"
    return "Sinyal kırılımı yok"


def target_display_name(target: dict[str, Any]) -> str:
    """Hedef etiketi: ad varsa "Ad Soyad", yoksa "@nick", ikisi de yoksa "".

    Handle hedeflerinde ad/soyad boştur; raporlarda boş "Ad Soyad:" satırı ya da
    `None` basılmaması için tek kaynak burasıdır (kontrat `Target.display_name`
    ile aynı semantik).
    """
    full = " ".join(
        part
        for part in (
            str(target.get("first_name") or "").strip(),
            str(target.get("last_name") or "").strip(),
        )
        if part
    ).strip()
    if full:
        return full
    nick = str(target.get("nickname") or "").strip().lstrip("@")
    if nick:
        return f"@{nick}"
    return str(
        target.get("email")
        or target.get("phone")
        or target.get("domain")
        or target.get("ip")
        or ""
    ).strip()


def build_report_sections(report_data: dict[str, Any]) -> dict[str, Any]:
    """Raporun karar-odaklı bölümlerini üretir (JSON/PDF/HTML ortak kaynak).

    Dönen yapı API sözleşmesine **eklenir**; mevcut alanlar değişmez.
    """
    case = report_data.get("case") or {}
    target = report_data.get("target") or {}
    findings: list[dict[str, Any]] = list(report_data.get("findings") or [])
    entities: list[dict[str, Any]] = list(report_data.get("entities") or [])
    evidence: list[dict[str, Any]] = list(report_data.get("evidence") or [])
    edges: list[dict[str, Any]] = list(report_data.get("edges") or [])
    scan = report_data.get("scan") or {}

    # --- durum dağılımı ---------------------------------------------------
    status_counts = {status: 0 for status in _STATUS_LABELS}
    for finding in findings:
        status = str(finding.get("status") or "")
        status_counts[status] = status_counts.get(status, 0) + 1

    # --- platform dağılımı ------------------------------------------------
    platform_scores: dict[str, list[float]] = {}
    for finding in findings:
        platform = str(finding.get("platform") or "unknown")
        platform_scores.setdefault(platform, []).append(float(finding.get("score") or 0.0))
    platform_distribution = [
        {
            "platform": platform,
            "findings": len(scores),
            "max_score": round(max(scores), 3) if scores else 0.0,
            "avg_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        }
        for platform, scores in sorted(
            platform_scores.items(), key=lambda item: (-max(item[1]), item[0])
        )
    ]

    # --- en yüksek 5 bulgu ------------------------------------------------
    top_findings = [
        {
            "platform": str(finding.get("platform") or ""),
            "label": str(finding.get("label") or ""),
            "status": str(finding.get("status") or ""),
            "score": round(float(finding.get("score") or 0.0), 3),
            "reason": _one_line_reason(finding),
            "url": finding.get("url"),
        }
        for finding in sorted(
            findings, key=lambda item: float(item.get("score") or 0.0), reverse=True
        )[:5]
    ]

    # --- kimlik / çapraz bağlantı ----------------------------------------
    username_groups: dict[str, dict[str, Any]] = {}
    for entity in entities:
        username = _entity_username(entity)
        if not username:
            continue
        group = username_groups.setdefault(
            username, {"username": username, "platforms": [], "entities": 0}
        )
        platform = str(entity.get("platform") or "unknown")
        if platform not in group["platforms"]:
            group["platforms"].append(platform)
        group["entities"] += 1

    groups = sorted(
        username_groups.values(), key=lambda item: (-len(item["platforms"]), item["username"])
    )
    shared_username_groups = [group for group in groups if len(group["platforms"]) >= 2]

    max_platforms = max((len(group["platforms"]) for group in groups), default=0)
    if max_platforms >= 3:
        confidence_note = (
            f"{max_platforms} platformda aynı kullanıcı adı görüldü → "
            "kimlik eşleşmesi **yüksek olasılıklı**."
        )
    elif max_platforms == 2:
        confidence_note = (
            "2 platformda aynı kullanıcı adı görüldü → orta olasılıklı eşleşme; "
            "kanıt URL'leriyle doğrulanmalı."
        )
    else:
        confidence_note = (
            "Platformlar arasında paylaşılan kullanıcı adı yok → eşleşme düşük olasılıklı, "
            "her bulgu tek başına değerlendirilmeli."
        )

    identity_links = {
        "edges": [
            {
                "type": str(edge.get("type") or ""),
                "source": str(edge.get("source_entity_id") or ""),
                "target": str(edge.get("target_entity_id") or ""),
                "confidence": round(float(edge.get("confidence") or 0.0), 3),
                "reason": edge.get("reason"),
            }
            for edge in sorted(
                edges, key=lambda item: float(item.get("confidence") or 0.0), reverse=True
            )
        ],
        "shared_username_groups": shared_username_groups,
        "confidence_note": confidence_note,
    }

    # --- kronoloji --------------------------------------------------------
    timeline: list[dict[str, Any]] = []
    for entity in entities:
        attrs = entity.get("attributes") or {}
        for key in _DATE_ATTRIBUTES:
            value = _as_iso(attrs.get(key))
            if value:
                timeline.append(
                    {
                        "date": value,
                        "platform": str(entity.get("platform") or "unknown"),
                        "label": str(entity.get("label") or _entity_username(entity)),
                        "event": key,
                        "url": entity.get("url"),
                    }
                )
                break
    timeline.sort(key=lambda item: item["date"])

    # --- kanıt özeti ------------------------------------------------------
    evidence_by_entity: list[dict[str, Any]] = []
    evidence_ids: set[str] = {str(ev.get("id")) for ev in evidence if ev.get("id")}
    for entity in entities:
        count = len((entity.get("evidence_ids") or []))
        if count:
            evidence_by_entity.append(
                {
                    "entity_id": str(entity.get("id") or ""),
                    "platform": str(entity.get("platform") or ""),
                    "label": str(entity.get("label") or ""),
                    "evidence": count,
                }
            )
    evidence_by_entity.sort(key=lambda item: item["evidence"], reverse=True)

    retention_until: str | None = None
    expiry_values: list[str] = [
        value for value in (_as_iso(ev.get("expires_at")) for ev in evidence) if value
    ]
    if expiry_values:
        retention_until = max(expiry_values)
    elif case.get("created_at") and case.get("retention_days"):
        try:
            created = datetime.fromisoformat(str(case["created_at"]))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            retention_until = (
                created + timedelta(days=int(case["retention_days"]))
            ).isoformat()
        except (TypeError, ValueError):
            retention_until = None

    executive_summary = {
        "target_name": target_display_name(target),
        "case_name": case.get("name"),
        "mode": scan.get("mode"),
        "scan_started_at": scan.get("started_at"),
        "scan_finished_at": scan.get("finished_at"),
        "generated_at": report_data.get("generated_at"),
        "totals": {
            "entities": len(entities),
            "platforms": len({str(e.get("platform") or "") for e in entities}),
            "findings": len(findings),
            "evidence": len(evidence_ids),
        },
        "status_counts": status_counts,
        "top_findings": top_findings,
        "confidence_note": confidence_note,
    }

    evidence_summary = {
        "total": len(evidence),
        "linked_to_entities": len(evidence_ids),
        "retention_until": retention_until,
        "by_entity": evidence_by_entity[:20],
    }

    return {
        "executive_summary": executive_summary,
        "platform_distribution": platform_distribution,
        "identity_links": identity_links,
        "timeline": timeline,
        "evidence_summary": evidence_summary,
    }


def generate_json_report(report_data: dict[str, Any]) -> bytes:
    """Rapor verisini JSON bytes olarak döndürür (bölümler dahil, geriye uyumlu)."""
    payload = dict(report_data)
    sections = build_report_sections(payload)
    for key, value in sections.items():
        payload.setdefault(key, value)
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")


# ---------------------------------------------------------------------------
# HTML (tarayıcı / arşiv görünümü) — Jinja2 autoescape
# ---------------------------------------------------------------------------

_HTML_TEMPLATE_SRC = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8"/>
<title>Showis OSINT Raporu — {{ report.case.name }}</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 32px;
         color: #111827; max-width: 960px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  h2 { font-size: 15px; margin: 24px 0 8px; border-bottom: 1px solid #e5e7eb;
       padding-bottom: 4px; }
  table { border-collapse: collapse; width: 100%; font-size: 12px; }
  th, td { border: 1px solid #e5e7eb; padding: 4px 6px; text-align: left;
           vertical-align: top; }
  th { background: #1f2937; color: #fff; }
  tr:nth-child(even) td { background: #f9fafb; }
  .muted { color: #6b7280; font-size: 11px; }
  .tag { font-family: ui-monospace, monospace; }
</style>
</head>
<body>
<h1>Showis OSINT İnceleme Raporu</h1>
<p class="muted">Gizlilik: bu rapor yalnızca yetkili kişi tarafından, belirtilen amaç
doğrultusunda kullanılabilir.</p>

<h2>1. Yönetici Özeti</h2>
<table>
  <tr><th>Hedef</th><td>{{ sections.executive_summary.target_name or "-" }}</td></tr>
  <tr><th>Vaka</th><td>{{ report.case.name }}</td></tr>
  <tr><th>Mod / tarama</th><td>{{ sections.executive_summary.mode or "-" }} ·
      {{ sections.executive_summary.scan_started_at or "-" }} →
      {{ sections.executive_summary.scan_finished_at or "-" }}</td></tr>
  <tr><th>Toplam</th><td>{{ sections.executive_summary.totals.entities }} entity ·
      {{ sections.executive_summary.totals.platforms }} platform ·
      {{ sections.executive_summary.totals.findings }} bulgu ·
      {{ sections.executive_summary.totals.evidence }} kanıt</td></tr>
  <tr><th>Durum dağılımı</th><td>
    {% for status, count in sections.executive_summary.status_counts.items() %}
      {{ status }}: {{ count }}{% if not loop.last %} · {% endif %}
    {% endfor %}
  </td></tr>
</table>
<p>{{ sections.executive_summary.confidence_note }}</p>

{% if sections.executive_summary.top_findings %}
<h2>En yüksek 5 bulgu</h2>
<table>
  <tr><th>Platform</th><th>Etiket</th><th>Skor</th><th>Durum</th><th>Gerekçe</th></tr>
  {% for finding in sections.executive_summary.top_findings %}
  <tr><td>{{ finding.platform }}</td><td>{{ finding.label }}</td>
      <td>{{ "%.2f"|format(finding.score) }}</td><td>{{ finding.status }}</td>
      <td>{{ finding.reason }}</td></tr>
  {% endfor %}
</table>
{% endif %}

<h2>2. Platform Dağılımı</h2>
{% if sections.platform_distribution %}
<table>
  <tr><th>Platform</th><th>Bulgu</th><th>En yüksek skor</th><th>Ortalama skor</th></tr>
  {% for row in sections.platform_distribution %}
  <tr><td>{{ row.platform }}</td><td>{{ row.findings }}</td>
      <td>{{ "%.2f"|format(row.max_score) }}</td>
      <td>{{ "%.2f"|format(row.avg_score) }}</td></tr>
  {% endfor %}
</table>
{% else %}<p>Bulgu yok.</p>{% endif %}

<h2>3. Kimlik / Çapraz Bağlantılar</h2>
{% if sections.identity_links.edges %}
<table>
  <tr><th>Tip</th><th>Kaynak</th><th>Hedef</th><th>Güven</th><th>Gerekçe</th></tr>
  {% for edge in sections.identity_links.edges %}
  <tr><td>{{ edge.type }}</td><td class="tag">{{ edge.source }}</td>
      <td class="tag">{{ edge.target }}</td>
      <td>{{ "%.2f"|format(edge.confidence) }}</td>
      <td>{{ edge.reason or "-" }}</td></tr>
  {% endfor %}
</table>
{% elif sections.identity_links.shared_username_groups %}
<table>
  <tr><th>Kullanıcı adı</th><th>Platformlar</th><th>Entity</th></tr>
  {% for group in sections.identity_links.shared_username_groups %}
  <tr><td class="tag">{{ group.username }}</td>
      <td>{{ group.platforms|join(", ") }}</td><td>{{ group.entities }}</td></tr>
  {% endfor %}
</table>
{% else %}<p>Platformlar arasında paylaşılan kullanıcı adı bulunmadı.</p>{% endif %}

{% if sections.timeline %}
<h2>4. Kronoloji (hesap oluşturma)</h2>
<table>
  <tr><th>Tarih</th><th>Platform</th><th>Etiket</th><th>Alan</th></tr>
  {% for row in sections.timeline %}
  <tr><td>{{ row.date }}</td><td>{{ row.platform }}</td>
      <td>{{ row.label }}</td><td>{{ row.event }}</td></tr>
  {% endfor %}
</table>
{% endif %}

<h2>5. Bulgular</h2>
{% if report.findings %}
<table>
  <tr><th>Platform</th><th>Etiket</th><th>Skor</th><th>Durum</th><th>URL</th></tr>
  {% for finding in report.findings %}
  <tr><td>{{ finding.platform }}</td><td>{{ finding.label }}</td>
      <td>{{ "%.2f"|format(finding.score) }}</td><td>{{ finding.status }}</td>
      <td class="tag">{{ finding.url or "-" }}</td></tr>
  {% endfor %}
</table>
{% else %}<p>Bulgu bulunamadı.</p>{% endif %}

<h2>6. Kanıt Özeti</h2>
<table>
  <tr><th>Toplam kanıt</th><td>{{ sections.evidence_summary.total }}</td></tr>
  <tr><th>Entity'ye bağlı</th><td>{{ sections.evidence_summary.linked_to_entities }}</td></tr>
  <tr><th>Saklama bitişi</th><td>{{ sections.evidence_summary.retention_until or "-" }}</td></tr>
</table>

{% if report.reasoning %}
<h2>7. Hipotezler, Kanıt Zinciri ve Çelişkiler</h2>
<p class="muted">Hipotez skorları kanıtlardan hesaplanır. Yalnızca aday/onaylı ilişkiler
kanıt zinciri sayılır; öneri düzeyindekiler dahil edilmez.</p>
{% for hyp in report.reasoning.hypotheses %}
<p><b>{{ hyp.statement }}</b> — güven {{ "%.2f"|format(hyp.confidence) }}
   <span class="muted">({{ hyp.status }})</span></p>
{% if hyp.rationale %}<p class="muted">{{ hyp.rationale }}</p>{% endif %}
{% if hyp.supporting %}<p class="muted">Destekleyen:
  {% for e in hyp.supporting %}
  {{ e.note or e.rule }} (+{{ "%.1f"|format(e.weight) }}){{ "; " if not loop.last }}
  {% endfor %}</p>{% endif %}
{% if hyp.contradicting %}<p class="muted">Çelişen:
  {% for e in hyp.contradicting %}
  {{ e.note or e.rule }} (−{{ "%.1f"|format(e.weight) }}){{ "; " if not loop.last }}
  {% endfor %}</p>{% endif %}
{% if hyp.next_checks %}<p class="muted">Sonraki kontroller:
  {{ hyp.next_checks|join("; ") }}</p>{% endif %}
{% else %}<p>Hipotez yok.</p>{% endfor %}
{% if report.reasoning.identity_chain %}
<h3>Kimlik zinciri</h3>
<p class="muted">Onaylı hesaplardan sahibin kendi yazdığı bağlantılarla (profil alanı,
kriptografik kanıt, rel=me, karşılıklı bağlantı) bulunan hesaplar. Güven, kanıt türü ve
bağımsız yol sayısından deterministik hesaplanır.</p>
<table>
  <tr><th>Hesap / sayfa</th><th>Zincir</th><th>Kanıt</th><th>Güven</th><th>Durum</th></tr>
  {% for row in report.reasoning.identity_chain %}
  <tr><td>{{ row.label }}<br><span class="tag">{{ row.value }}</span></td>
      <td>{{ row.path|join(" → ") or "-" }}</td><td>{{ row.evidence|join(", ") }}</td>
      <td>{{ "%.2f"|format(row.confidence) }}</td><td>{{ row.status }}</td></tr>
  {% endfor %}
</table>
{% endif %}
{% if report.reasoning.relations %}
<table>
  <tr><th>İlişki</th><th>Kaynak</th><th>Hedef</th><th>Güven</th><th>Durum</th><th>Kanıt</th></tr>
  {% for rel in report.reasoning.relations %}
  <tr><td>{{ rel.type }}</td><td>{{ rel.src }}</td><td>{{ rel.dst }}</td>
      <td>{{ "%.2f"|format(rel.confidence) }}</td><td>{{ rel.status }}</td>
      <td>{% for e in rel.evidence %}
        {{ e.note or e.rule }}{{ "; " if not loop.last }}{% endfor %}</td></tr>
  {% endfor %}
</table>
{% endif %}
{% if report.reasoning.contradictions %}
<h3>Çelişkiler</h3>
<ul>
  {% for c in report.reasoning.contradictions %}
  <li>{{ c.src }} ↔ {{ c.dst }}: {{ c.message }} <span class="muted">({{ c.status }})</span></li>
  {% endfor %}
</ul>
{% endif %}
{% if report.reasoning.snapshots %}
<h3>Arşivlenmiş sayfalar</h3>
<table>
  <tr><th>URL</th><th>Başlık</th><th>sha256</th><th>Alındı</th></tr>
  {% for snap in report.reasoning.snapshots %}
  <tr><td class="tag">{{ snap.url }}</td><td>{{ snap.title or "-" }}</td>
      <td class="tag">{{ snap.sha256[:16] }}…</td><td>{{ snap.fetched_at or "-" }}</td></tr>
  {% endfor %}
</table>
{% endif %}
{% endif %}

<h2>{{ 8 if report.reasoning else 7 }}. Sınırlamalar</h2>
<p class="muted">Bu rapor yalnızca kamuya açık verileri içerir. Platformlar verilerini
değiştirebilir; bulgular tarama anındaki durumdur. Olası yanlış eşleşmeler için kanıt
URL'lerini kontrol ediniz.</p>
</body>
</html>
"""

_HTML_TEMPLATE = report_env.from_string(_HTML_TEMPLATE_SRC)


def render_html_report(report_data: dict[str, Any]) -> str:
    """HTML raporu (autoescape açık Jinja2 ortamı) döndürür."""
    sections = build_report_sections(report_data)
    return _HTML_TEMPLATE.render(report=report_data, sections=sections)


def generate_html_report(report_data: dict[str, Any]) -> bytes:
    return render_html_report(report_data).encode("utf-8")


# ---------------------------------------------------------------------------
# PDF (reportlab) — Jinja2 ile üretilen, kaçışlanmış paragraflar
# ---------------------------------------------------------------------------

_PDF_FRAGMENTS = {
    "case_summary": report_env.from_string(
        "<b>Vaka:</b> {{ case.name or '-' }}<br/>"
        "<b>Amaç:</b> {{ case.purpose or '-' }}<br/>"
        "<b>Hukuki dayanak:</b> {{ case.legal_basis or '-' }}<br/>"
        "<b>Oluşturulma:</b> {{ case.created_at or '-' }}<br/>"
        "<b>Rapor tarihi:</b> {{ generated_at or '-' }}"
    ),
    "target": report_env.from_string(
        "<b>Ad Soyad:</b> {{ target_label or '-' }}<br/>"
        "<b>İpuçları:</b> {{ hints }}"
    ),
    "executive": report_env.from_string(
        "<b>Mod:</b> {{ scan.mode or '-' }} &nbsp; "
        "<b>Tarama:</b> {{ scan.started_at or '-' }} → {{ scan.finished_at or '-' }}<br/>"
        "<b>Toplam:</b> {{ totals.entities }} entity, {{ totals.platforms }} platform, "
        "{{ totals.findings }} bulgu, {{ totals.evidence }} kanıt<br/>"
        "<b>Durum:</b> {{ status_text }}<br/>"
        "<b>Yorum:</b> {{ confidence_note }}"
    ),
    "finding_line": report_env.from_string(
        "<b>{{ platform }}</b> — {{ label }} · skor {{ score }} · {{ status }}<br/>{{ reason }}"
    ),
    "signal_line": report_env.from_string(
        "Sinyal kırılımı: {{ signals }}"
    ),
    "identity_note": report_env.from_string("{{ note }}"),
    "evidence_meta": report_env.from_string(
        "Toplam {{ total }} kanıt · entity'ye bağlı {{ linked }} · saklama bitişi "
        "{{ retention or '-' }}"
    ),
}


def render_target_block(report_data: dict[str, Any]) -> str:
    """Hedef bloğu (PDF "Ad Soyad" satırı) — etiket tek kaynaktan.

    Yalnızca PDF üretiminde kullanılır; HTML raporu hedef etiketini yönetici
    özetinden (`target_name`) alır. Ad yoksa `@nick`, o da yoksa `-` basılır;
    boş satır veya `None` çıkmaz.
    """
    target = report_data.get("target") or {}
    return _PDF_FRAGMENTS["target"].render(
        target_label=target_display_name(target),
        hints=target.get("hints") or {},
    )


def _pdf_cell(value: Any) -> str:
    """Tablo hücresi: Paragraph olarak yorumlanacak kaçışlanmış metin."""
    return _pdf_text(value)


def _pdf_text(value: Any) -> str:
    """PDF'e giden kullanıcı metni: XML-escape + açı parantezleri nötrlenir.

    reportlab `Paragraph` mini-HTML yorumlar; `_e()` etiketleri kaçışlar ama
    reportlab bunları çözerek yeniden `<script>` metnine dönüştürür. PDF'te
    meta karakter bulunmasın diye `<`/`>` görünür ama etkisiz karakterlere
    (`‹`/`›`) çevrilir — çıktı hem inert hem de taranabilir kalır.
    """
    escaped = _e(value)
    return escaped.replace("&lt;", "‹").replace("&gt;", "›")


def _evidence_text(entries: list[dict[str, Any]], sign: str) -> str:
    return "; ".join(
        f"{_pdf_text(e.get('note') or e.get('rule') or '')} "
        f"({sign}{float(e.get('weight') or 0):.1f})"
        for e in entries
    )


def _reasoning_pdf_story(
    reasoning: dict[str, Any], h2_style: Any, body_style: Any, small_style: Any, table: Any
) -> list[Any]:
    """PDF: hipotezler, kanıt zinciri, çelişkiler ve arşivlenmiş sayfalar bölümü."""
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Spacer

    story: list[Any] = [Paragraph(_t("11. Hipotezler, Kanıt Zinciri ve Çelişkiler"), h2_style)]
    story.append(
        Paragraph(
            _t(
                "Hipotez skorları kanıtlardan hesaplanır. Yalnızca aday/onaylı ilişkiler "
                "kanıt zinciri sayılır."
            ),
            small_style,
        )
    )
    for hyp in reasoning.get("hypotheses") or []:
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(
                _t(
                    f"<b>{_pdf_text(hyp.get('statement', ''))}</b> — güven "
                    f"{float(hyp.get('confidence') or 0):.2f} ({_pdf_text(hyp.get('status', ''))})"
                ),
                body_style,
            )
        )
        for label, key, sign in (
            ("Destekleyen", "supporting", "+"),
            ("Çelişen", "contradicting", "-"),
        ):
            if hyp.get(key):
                story.append(
                    Paragraph(_t(f"{label}: {_evidence_text(hyp[key], sign)}"), small_style)
                )
        if hyp.get("next_checks"):
            checks = "; ".join(_pdf_text(c) for c in hyp["next_checks"])
            story.append(Paragraph(_t(f"Sonraki kontroller: {checks}"), small_style))
    if reasoning.get("identity_chain"):
        story.append(Spacer(1, 6))
        story.append(Paragraph(_t("<b>Kimlik zinciri</b>"), body_style))
        story.append(
            table(
                [
                    ["Hesap / sayfa", "Zincir", "Kanıt", "Güven", "Durum"],
                    *[
                        [
                            f"{row['label']}\n{row['value']}",
                            " → ".join(row["path"]) or "-",
                            ", ".join(row["evidence"]) or "-",
                            f"{float(row['confidence']):.2f}",
                            row["status"],
                        ]
                        for row in reasoning["identity_chain"]
                    ],
                ],
                [5.0 * cm, 4.0 * cm, 4.2 * cm, 1.4 * cm, 2.4 * cm],
            )
        )
    if reasoning.get("relations"):
        story.append(Spacer(1, 6))
        story.append(
            table(
                [
                    ["İlişki", "Kaynak", "Hedef", "Güven", "Durum", "Kanıt"],
                    *[
                        [
                            r["type"],
                            r["src"],
                            r["dst"],
                            f"{float(r['confidence']):.2f}",
                            r["status"],
                            "; ".join(e["note"] or e["rule"] for e in r["evidence"]) or "-",
                        ]
                        for r in reasoning["relations"]
                    ],
                ],
                [2.0 * cm, 3.2 * cm, 3.2 * cm, 1.2 * cm, 1.6 * cm, 5.8 * cm],
            )
        )
    for conflict in reasoning.get("contradictions") or []:
        story.append(
            Paragraph(
                _t(
                    f"• Çelişki: {_pdf_text(conflict['src'])} ↔ {_pdf_text(conflict['dst'])}: "
                    f"{_pdf_text(conflict['message'])} ({_pdf_text(conflict['status'])})"
                ),
                small_style,
            )
        )
    if reasoning.get("snapshots"):
        story.append(Spacer(1, 6))
        story.append(
            table(
                [
                    ["URL", "Başlık", "sha256", "Alındı"],
                    *[
                        [
                            s["url"],
                            s.get("title") or "-",
                            f"{str(s['sha256'])[:16]}…",
                            s.get("fetched_at") or "-",
                        ]
                        for s in reasoning["snapshots"]
                    ],
                ],
                [6.5 * cm, 4.0 * cm, 3.0 * cm, 3.5 * cm],
            )
        )
    return story


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
        # Meta veri de kullanıcı verisi içerir: aynı nötrleme uygulanır
        title=_pdf_text(f"Showis OSINT Raporu — {report_data.get('case', {}).get('name', '')}"),
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
    cell_style = ParagraphStyle(
        "ShowisCell",
        parent=body_style,
        fontSize=7.5,
        leading=9,
    )
    small_style = ParagraphStyle(
        "ShowisSmall",
        parent=body_style,
        fontSize=8,
        textColor=colors.HexColor("#555555"),
    )

    def _table(rows: list[list[Any]], widths: list[float]) -> Table:
        data = [
            [
                cell if isinstance(cell, Paragraph) else Paragraph(_pdf_cell(cell), cell_style)
                for cell in row
            ]
            for row in rows
        ]
        header = [
            Paragraph(f"<b>{_pdf_cell(cell)}</b>", cell_style)
            for cell in rows[0]
        ]
        data[0] = header
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
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
        return table

    case = report_data.get("case", {})
    findings = report_data.get("findings", [])
    evidence = report_data.get("evidence", [])
    generated_at = report_data.get("generated_at", "")
    sections = build_report_sections(report_data)
    summary = sections["executive_summary"]
    scan = report_data.get("scan") or {}

    status_text = " · ".join(
        f"{_STATUS_LABELS.get(status, status)}: {count}"
        for status, count in summary["status_counts"].items()
    )

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
        # 1) Yönetici özeti — ilk bölüm: tek sayfada karar
        Paragraph(_t("1. Yönetici Özeti"), h2_style),
        Paragraph(
            _t(
                _PDF_FRAGMENTS["executive"].render(
                    scan=scan,
                    totals=summary["totals"],
                    status_text=status_text,
                    confidence_note=summary["confidence_note"],
                )
            ),
            body_style,
        ),
    ]

    if summary["top_findings"]:
        story.append(Spacer(1, 6))
        story.append(_table(
            [
                ["#", "Platform", "Etiket", "Skor", "Durum", "Gerekçe"],
                *[
                    [
                        str(index + 1),
                        item["platform"],
                        item["label"],
                        f"{item['score']:.2f}",
                        item["status"],
                        item["reason"],
                    ]
                    for index, item in enumerate(summary["top_findings"])
                ],
            ],
            [0.8 * cm, 2.2 * cm, 3.2 * cm, 1.3 * cm, 1.8 * cm, 7.7 * cm],
        ))

    story.extend(
        [
            Paragraph(_t("2. Vaka Özeti"), h2_style),
            Paragraph(
                _t(
                    _PDF_FRAGMENTS["case_summary"].render(
                        case=case, generated_at=generated_at
                    )
                ),
                body_style,
            ),
            Paragraph(_t("3. Hedef"), h2_style),
            Paragraph(_t(render_target_block(report_data)), body_style),
            Paragraph(_t("4. Yöntem"), h2_style),
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
            Paragraph(_t("5. Platform Dağılımı"), h2_style),
        ]
    )

    if sections["platform_distribution"]:
        story.append(_table(
            [
                ["Platform", "Bulgu", "En yüksek skor", "Ortalama skor"],
                *[
                    [
                        row["platform"],
                        str(row["findings"]),
                        f"{row['max_score']:.2f}",
                        f"{row['avg_score']:.2f}",
                    ]
                    for row in sections["platform_distribution"]
                ],
            ],
            [5.0 * cm, 3.0 * cm, 4.0 * cm, 5.0 * cm],
        ))
    else:
        story.append(Paragraph(_t("Bulgu yok."), body_style))

    story.append(Paragraph(_t("6. Kimlik / Çapraz Bağlantılar"), h2_style))
    identity = sections["identity_links"]
    identity_note = _PDF_FRAGMENTS["identity_note"].render(
        note=identity["confidence_note"]
    )
    story.append(Paragraph(_t(identity_note), body_style))
    if identity["edges"]:
        story.append(_table(
            [
                ["Tip", "Kaynak", "Hedef", "Güven", "Gerekçe"],
                *[
                    [
                        edge["type"],
                        edge["source"],
                        edge["target"],
                        f"{edge['confidence']:.2f}",
                        edge["reason"] or "-",
                    ]
                    for edge in identity["edges"][:30]
                ],
            ],
            [2.2 * cm, 4.4 * cm, 4.4 * cm, 1.4 * cm, 4.6 * cm],
        ))
    elif identity["shared_username_groups"]:
        story.append(_table(
            [
                ["Kullanıcı adı", "Platformlar", "Entity"],
                *[
                    [
                        group["username"],
                        ", ".join(group["platforms"]),
                        str(group["entities"]),
                    ]
                    for group in identity["shared_username_groups"][:30]
                ],
            ],
            [4.0 * cm, 9.0 * cm, 4.0 * cm],
        ))
    else:
        story.append(Paragraph(_t("Paylaşılan kullanıcı adı bulunmadı."), body_style))

    if sections["timeline"]:
        story.append(Paragraph(_t("7. Kronoloji (hesap oluşturma)"), h2_style))
        story.append(_table(
            [
                ["Tarih", "Platform", "Etiket", "Alan"],
                *[
                    [row["date"], row["platform"], row["label"], row["event"]]
                    for row in sections["timeline"][:40]
                ],
            ],
            [4.6 * cm, 3.2 * cm, 6.2 * cm, 3.0 * cm],
        ))

    story.append(Paragraph(_t("8. Bulgular"), h2_style))
    if findings:
        story.append(_table(
            [
                ["Platform", "Etiket", "Skor", "Durum", "URL"],
                *[
                    [
                        f.get("platform", ""),
                        f.get("label", ""),
                        f"{float(f.get('score', 0.0)):.2f}",
                        f.get("status", ""),
                        f.get("url") or "-",
                    ]
                    for f in findings
                ],
            ],
            [2.2 * cm, 4.0 * cm, 1.4 * cm, 2.0 * cm, 7.4 * cm],
        ))

        for f in findings[:25]:
            story.append(Spacer(1, 6))
            story.append(
                Paragraph(
                    _t(
                        _PDF_FRAGMENTS["finding_line"].render(
                            platform=f.get("platform", ""),
                            label=f.get("label", ""),
                            score=f"{float(f.get('score', 0.0)):.2f}",
                            status=f.get("status", ""),
                            reason=f.get("explanation") or _one_line_reason(f),
                        )
                    ),
                    body_style,
                )
            )
            breakdown = f.get("breakdown") or {}
            if breakdown:
                signals = ", ".join(
                    f"{key}={value}"
                    for key, value in breakdown.items()
                    if key != "total" and value
                )
                story.append(
                    Paragraph(
                        _t(_PDF_FRAGMENTS["signal_line"].render(signals=signals)),
                        small_style,
                    )
                )
    else:
        story.append(Paragraph(_t("Bulgu bulunamadı."), body_style))

    evidence_summary = sections["evidence_summary"]
    story.append(Paragraph(_t("9. Kanıt Özeti"), h2_style))
    story.append(
        Paragraph(
            _t(
                _PDF_FRAGMENTS["evidence_meta"].render(
                    total=evidence_summary["total"],
                    linked=evidence_summary["linked_to_entities"],
                    retention=evidence_summary["retention_until"],
                )
            ),
            body_style,
        )
    )
    if evidence_summary["by_entity"]:
        story.append(_table(
            [
                ["Platform", "Etiket", "Kanıt"],
                *[
                    [row["platform"], row["label"], str(row["evidence"])]
                    for row in evidence_summary["by_entity"][:20]
                ],
            ],
            [4.0 * cm, 9.0 * cm, 4.0 * cm],
        ))

    story.append(Paragraph(_t("10. Kanıt Ekleri"), h2_style))
    if evidence:
        for ev in evidence:
            story.append(
                Paragraph(
                    _t(
                        f"• [{_pdf_text(ev.get('kind', ''))}] "
                        f"{_pdf_text(ev.get('source_url', '-'))} "
                        f"(hash: {_pdf_text(str(ev.get('content_hash', ''))[:16])}…, "
                        f"tarih: {_pdf_text(ev.get('fetched_at', '-'))})"
                    ),
                    small_style,
                )
            )
    else:
        story.append(Paragraph(_t("Kayıtlı kanıt yok."), body_style))

    reasoning = report_data.get("reasoning") or {}
    limits_no = 11
    if reasoning:
        limits_no = 12
        story.extend(_reasoning_pdf_story(reasoning, h2_style, body_style, small_style, _table))

    story.append(Paragraph(_t(f"{limits_no}. Sınırlamalar"), h2_style))
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
