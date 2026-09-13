"""Read a raw email and reduce its body to one canonical plain text.

ebookingonline sends some confirmations as plain text and others as HTML only,
and may change which at any time. Everything downstream parses the canonical
text, so both formats read the same way:

    Hi Ball,
    Date<TAB>11:30 - 12:30 pm , Sunday 13th September 2026
    Player 1<TAB>Jane Doe

Rules: the plain-text part is used when present, otherwise the HTML part is
converted (table rows become lines, cells are separated by a tab, scripts and
styles are dropped). Every line is then stripped, non-breaking spaces become
spaces, and any run of whitespace containing a tab or two or more spaces
becomes a single tab. So "Label:      value" and an HTML "<td>Label</td>
<td>value</td>" row both come out as Label<TAB>value. Empty lines are dropped.
"""

import re
from email import message_from_bytes, policy
from email.message import EmailMessage
from html.parser import HTMLParser

from .models import InboundEmail

_BLOCK_TAGS = {
    "p", "div", "br", "hr", "tr", "table", "thead", "tbody", "tfoot",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "section", "article", "header", "footer",
}
_CELL_TAGS = {"td", "th"}
_HIDDEN_TAGS = {"head", "title", "script", "style"}

# A run of horizontal whitespace that separates two fields on a line.
_FIELD_GAP = re.compile(r"[^\S\n]*\t[^\S\n]*|[^\S\n\t]{2,}")


def parse_raw_email(raw: bytes, message_id: str, thread_id: str) -> InboundEmail:
    """Build an InboundEmail from RFC 822 bytes (Gmail's format=raw)."""
    message = message_from_bytes(raw, policy=policy.default)
    return InboundEmail(
        id=message_id,
        thread_id=thread_id,
        subject=str(message.get("Subject", "")),
        sender=str(message.get("From", "")),
        date=str(message.get("Date", "")),
        message_id_header=str(message["Message-ID"]) if message["Message-ID"] else None,
        text=body_text(message),
    )


def body_text(message: EmailMessage) -> str:
    """Canonical text of the body, preferring plain text over HTML."""
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_subtype() == "html":
        content = html_to_text(content)
    return normalise_text(content)


def html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return "".join(extractor.parts)


def normalise_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = (_FIELD_GAP.sub("\t", line.strip()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


class _TextExtractor(HTMLParser):
    """Visible text of an HTML document, laid out the way a mail client shows it.

    Source line breaks inside an element are only formatting (the booking emails
    are hard-wrapped mid-name), so whitespace inside text is collapsed to single
    spaces. Only block elements start a new line and only table cells add a
    field separator, which keeps inline markup like <span> from splitting words.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _HIDDEN_TAGS:
            self._hidden_depth += 1
        elif tag in _CELL_TAGS:
            self.parts.append("\t")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _HIDDEN_TAGS:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._hidden_depth:
            self.parts.append(re.sub(r"\s+", " ", data))
