#!/usr/bin/env python3
"""Generate the two-page leader guide PDF.

The guide is a build artifact, not a hand-edited file — change the copy here and
re-run, so the PDF can never drift from a source nobody can find.

    pip install reportlab
    python scripts/make_leader_guide.py

Writes docs/ALUMNI_BOT_LEADER_GUIDE.pdf, split deliberately:

  page 1 — only what a leader does. Nothing to read around it, nothing to scroll
           past, so the two steps can't be missed.
  page 2 — reference: why it works that way, what members see, what to do when it
           looks broken. Read once, or never.

It must stay exactly two pages, and the script fails loudly otherwise: a third
page means page 2 has grown into a document nobody will finish.
"""
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

BOT = "@freshmanalumni_bot"
UPDATED = "1 August 2026"

# Read from the bot's own config so the guide can't quote a handle the bot has
# stopped using. Falls back only if .env isn't present (config reads it at import).
try:
    from config import ADMIN_CONTACT as CONTACT
except Exception:  # pragma: no cover - convenience for building without a .env
    CONTACT = "@gapyearingdoesntsuck"

OUT = Path(__file__).resolve().parent.parent / "docs" / "ALUMNI_BOT_LEADER_GUIDE.pdf"

INK = colors.HexColor("#12203A")
ACCENT = colors.HexColor("#1F5FA8")
MUTED = colors.HexColor("#5A6B85")
RULE = colors.HexColor("#D4DCE8")
PANEL = colors.HexColor("#F3F6FA")

PAGE = A4
MARGIN = 16 * mm

body = ParagraphStyle(
    "body", fontName="Helvetica", fontSize=9, leading=12.1,
    textColor=INK, alignment=TA_LEFT, spaceAfter=0,
)
small = ParagraphStyle("small", parent=body, fontSize=7.9, leading=10.4, textColor=MUTED)
symptom = ParagraphStyle("symptom", parent=body, fontName="Helvetica-Bold")
# Page 1 carries two steps and nothing else, so it can afford to be read at arm's
# length. Page 2 uses `body`.
lead = ParagraphStyle("lead", parent=body, fontSize=11.5, leading=15.5)
h1 = ParagraphStyle(
    "h1", fontName="Helvetica-Bold", fontSize=25, leading=28, textColor=INK,
)
h1b = ParagraphStyle("h1b", parent=h1, fontSize=17, leading=20)
kicker = ParagraphStyle(
    "kicker", fontName="Helvetica", fontSize=10, leading=13, textColor=MUTED,
)
kicker_small = ParagraphStyle("kicker_small", parent=kicker, fontSize=8.7, leading=11.2)
h2 = ParagraphStyle(
    "h2", fontName="Helvetica-Bold", fontSize=10, leading=12.5, textColor=ACCENT,
    spaceBefore=0, spaceAfter=3.5,
)


def step_table(rows, style=None, gap=1.4):
    """A numbered list that keeps its numbers off in their own column."""
    style = style or body
    number = ParagraphStyle("n", parent=style, textColor=ACCENT,
                            fontName="Helvetica-Bold")
    data = [
        [Paragraph(f"{i}", number), Paragraph(text, style)]
        for i, text in enumerate(rows, start=1)
    ]
    t = Table(data, colWidths=[style.fontSize * 0.95 + 4, None])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), gap),
        ("BOTTOMPADDING", (0, 0), (-1, -1), gap),
    ]))
    return t


def two_column(rows, label_style, width=32 * mm):
    """Label on the left, explanation on the right.

    The label style is passed in rather than fixed: a monospaced label reads as
    something you type, so the troubleshooting table — where the left column is a
    symptom, not a command — must not borrow the command styling.
    """
    data = [
        [Paragraph(f"<b>{label}</b>", label_style), Paragraph(what, body)]
        for label, what in rows
    ]
    t = Table(data, colWidths=[width, None])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def symptom_table(rows):
    return two_column(rows, symptom, width=38 * mm)


def panel(title, lines, style=None):
    """A tinted box — used for the one thing leaders most often get wrong."""
    style = style or body
    inner = [Paragraph(f"<b>{title}</b>", ParagraphStyle(
        "pt", parent=style, textColor=INK, fontName="Helvetica-Bold"))]
    for line in lines:
        inner.append(Spacer(1, 3))
        inner.append(Paragraph(line, style))
    t = Table([[inner]], colWidths=[None])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def rule(space_before=7, space_after=6):
    return [
        Spacer(1, space_before),
        HRFlowable(width="100%", thickness=0.7, color=RULE, spaceBefore=0, spaceAfter=0),
        Spacer(1, space_after),
    ]


def section(title, *flowables):
    """Heading and its content travel together or not at all."""
    return KeepTogether([Paragraph(title, h2), *flowables])


def footer(canvas, doc):
    """Drawn on both pages, so page 2 can't be mistaken for a loose sheet."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(MUTED)
    y = 8 * mm
    canvas.drawString(
        doc.leftMargin, y,
        f"Freshman Academy  ·  Alumni Gate  ·  updated {UPDATED}",
    )
    canvas.drawRightString(
        doc.leftMargin + doc.width, y, f"Page {doc.page} of 2",
    )
    canvas.restoreState()


def build():
    story = []

    # ── Page 1: only what a leader does ─────────────────────────────────────────
    story.append(Paragraph("Freshman Alumni Bot", h1))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"A guide for group leaders &nbsp;·&nbsp; {BOT}", kicker))
    story += rule(9, 14)

    story.append(Paragraph("How to set it up", h1b))
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        "Three steps, about a minute.", kicker))
    story.append(Spacer(1, 12))

    story.append(step_table([
        f"Open the group you want the bot working in, and add it as a member: "
        f"<b>Members → Add member → {BOT}</b>",
        "Promote it to <b>Administrator</b>, and turn on the <b>Pin messages</b> "
        "right.",
        "Send <b>/gate_announce</b> in the group. The bot posts and pins the join "
        "announcement — and that command is also what switches the group on.",
    ], style=lead, gap=6))

    story.append(Spacer(1, 14))
    story.append(panel("Step 3 is department leads only.", [
        "If you're not a dep lead, do steps 1 and 2 and ask yours to run the "
        "command in the group — it takes them a second.",
        "Switching a group on is what makes being a member of it count towards "
        "joining the Alumni Group, so it's kept to dep leads. For anyone else "
        "<b>/gate_announce</b> stays silent rather than refusing.",
    ], style=lead))

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "<b>Repeat all three in every group you want covered.</b> Each one is "
        "switched on separately and gets its own announcement, so you choose which "
        "groups take part and you can add more whenever you like.",
        lead,
    ))

    # ── Page 2: reference ───────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Reference", h1b))
    story.append(Spacer(1, 2))
    story.append(Paragraph(
        "Read once, or only when something looks wrong.", kicker_small))
    story += rule(7, 7)

    story.append(section(
        "Why Administrator matters",
        Paragraph(
            "Admin rights are not a formality — the bot is inert without them. "
            "Telegram only tells a bot who joins a group if it is an administrator, "
            "and pinning needs the <b>Pin messages</b> right specifically. If you add "
            "the bot as an ordinary member, nothing happens at all: no enrolment, no "
            "announcement, no error. <b>That is the single most common reason a "
            "group looks broken.</b>",
            body,
        ),
        Spacer(1, 4),
        Paragraph(
            "Promoting the bot also tells it the group exists — but on its own that "
            "grants nothing, because anyone can promote a bot in a group they just "
            "made. Step 3 is the part that counts, and it's why that command is "
            "restricted to dep leads.",
            body,
        ),
    ))
    story += rule()

    story.append(section(
        "What the bot puts in your group",
        Paragraph("Exactly two things ever appear in the group chat:", body),
        Spacer(1, 3),
        step_table([
            "<b>The pinned announcement</b> — two buttons, <i>Join the Alumni "
            "group</i> and <i>I'm already in it</i>. It re-posts itself every five "
            "days and deletes the previous one, so only one is ever live.",
            "<b>A follow-up message</b> naming the people who still haven't tapped "
            "either button, sent a few days after the announcement.",
        ]),
        Spacer(1, 4),
        Paragraph(
            "<b>Everything else is a private DM.</b> Nobody is tagged in the group "
            "for not having joined, and nobody is corrected in front of everyone. "
            "You can tell your members that safely.",
            body,
        ),
    ))
    story += rule()

    story.append(section(
        "What a member goes through",
        Paragraph(
            "So you can answer “what is this bot?” without asking us: they tap "
            "<b>Join the Alumni Group</b>, which opens the bot in a private chat. "
            "There it asks them to fill in a short form, read the values doc, and "
            "send a 50–100 word intro. Once all three are done it hands them a "
            "personal one-time invite link and they're admitted automatically. "
            "Tapping <b>I'm already in it</b> is checked against the real group, so "
            "it can't be used to skip the process.",
            body,
        ),
    ))
    story += rule()

    story.append(section(
        "If something looks wrong",
        symptom_table([
            ("/gate_announce does nothing",
             "Usually you're not a dep lead — it stays silent rather than refusing. "
             "Otherwise the bot is a member but not an <b>Administrator</b>, and "
             "can't post or read the group without that."),
            ("Nothing happened at all",
             "Step 3 hasn't been done. Adding and promoting the bot alone leaves the "
             "group switched off — that's deliberate, not a fault."),
            ("It isn't pinned",
             "The bot has admin rights but not <b>Pin messages</b>. The "
             "announcement still works unpinned."),
            ("“But I <i>am</i> in the group!”",
             "Almost always a second Telegram account. Ask which account they use "
             "in the Alumni Group, and have them open the bot from that one."),
            ("“The bot won't reply to me”",
             "They need to tap <b>Start</b> in the private chat once. Telegram won't "
             "let a bot message anyone who hasn't."),
            ("Anything else", f"Message {CONTACT}."),
        ]),
        Spacer(1, 5),
        Spacer(1, 6),
        Paragraph("<b>Someone who isn't in any of our groups</b>", h2),
        Paragraph(
            "A guest speaker, or a graduate who left every chat years ago, can't be "
            f"found by the checks above. <b>Message Samandar ({CONTACT})</b> for a "
            "one-off invite code — it looks like <code>FA-K7M2Q-XR94T</code>. Give it to the "
            "person; they paste it to the bot in a private chat and go through the "
            "same onboarding, skipping the group check. Each code works once and "
            "expires after 3 days — ask again if it lapses.",
            body,
        ),
        Spacer(1, 5),
        Paragraph(
            "Dep leads also have <b>/gate_stats</b> (how many are in, "
            "mid-onboarding, or unengaged), <b>/gate_groups</b> (every group "
            "switched on) and <b>/gate_unwatch</b> (switch a group back off) — all "
            "in a private chat with the bot. Every one of them answers you "
            "privately, even when typed in a group.",
            small,
        ),
    ))

    doc = BaseDocTemplate(
        str(OUT), pagesize=PAGE,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=12 * mm,
        title="Freshman Alumni Bot — Guide for Group Leaders",
        author="Freshman Academy",
        subject="How to add and run the Alumni Bot in a community group",
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="page",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    doc.addPageTemplates([PageTemplate(id="single", frames=[frame], onPage=footer)])
    doc.build(story)
    return doc.page


if __name__ == "__main__":
    pages = build()
    print(f"Wrote {OUT} ({pages} page{'s' if pages != 1 else ''})")
    if pages != 2:
        print(f"ERROR: the leader guide must be exactly 2 pages, got {pages} — "
              "page 1 is the steps, page 2 is the reference. Trim the copy.",
              file=sys.stderr)
        sys.exit(1)
