#!/usr/bin/env python3
"""Generate the one-page leader guide PDF.

The guide is a build artifact, not a hand-edited file — change the copy here and
re-run, so the PDF can never drift from a source nobody can find.

    pip install reportlab
    python scripts/make_leader_guide.py

Writes docs/ALUMNI_BOT_LEADER_GUIDE.pdf. It must stay one page: the whole point
is that a group leader reads it in a single screen. The script fails loudly if it
spills onto a second.
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
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

BOT = "@freshmanalumni_bot"
CONTACT = "@gapyearingdoesntsuck"
UPDATED = "31 July 2026"

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
h1 = ParagraphStyle(
    "h1", fontName="Helvetica-Bold", fontSize=18, leading=20, textColor=INK,
)
kicker = ParagraphStyle(
    "kicker", fontName="Helvetica", fontSize=8.7, leading=11.2, textColor=MUTED,
)
h2 = ParagraphStyle(
    "h2", fontName="Helvetica-Bold", fontSize=10, leading=12.5, textColor=ACCENT,
    spaceBefore=0, spaceAfter=3.5,
)


def step_table(rows):
    """A numbered list that keeps its numbers off in their own column."""
    data = [
        [Paragraph(f"<b>{i}</b>", ParagraphStyle("n", parent=body, textColor=ACCENT)),
         Paragraph(text, body)]
        for i, text in enumerate(rows, start=1)
    ]
    t = Table(data, colWidths=[7 * mm, None])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1.4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.4),
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


def panel(title, lines):
    """A tinted box — used for the one thing leaders most often get wrong."""
    inner = [Paragraph(f"<b>{title}</b>", ParagraphStyle("pt", parent=body, textColor=INK))]
    for line in lines:
        inner.append(Spacer(1, 2.5))
        inner.append(Paragraph(line, body))
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


def build():
    story = []

    story.append(Paragraph("Freshman Alumni Bot", h1))
    story.append(Spacer(1, 2))
    story.append(Paragraph(
        f"A guide for group leaders &nbsp;·&nbsp; {BOT} &nbsp;·&nbsp; "
        "two steps, about a minute, no commands to remember.",
        kicker,
    ))
    story += rule(7, 7)

    story.append(section(
        "1 &nbsp; Add the bot to your group  —  this is the whole setup",
        step_table([
            f"In the group: <b>Members → Add member → {BOT}</b>.",
            "Promote it to <b>Administrator</b>, and turn on <b>Pin messages</b>.",
            "Repeat in each group you want it working in. Every group is handled "
            "independently and gets its own announcement — you choose which ones, "
            "and you can add more whenever you like.",
        ]),
        Spacer(1, 4),
        panel("That's it. Promoting it is the switch-on.", [
            "The bot enrols the group by itself, then posts and pins the join "
            "announcement <b>within the hour</b>. You don't run a command, you "
            "don't send anyone an ID, and nobody has to change a setting on our "
            "side to add your group. If you'd rather not wait for the announcement "
            "to appear, ask the core team to trigger it.",
        ]),
    ))
    story += rule()

    story.append(section(
        "2 &nbsp; Why Administrator matters",
        Paragraph(
            "Admin rights are not a formality — the bot is inert without them. "
            "Telegram only tells a bot who joins a group if it is an administrator, "
            "and pinning needs the <b>Pin messages</b> right specifically. If you add "
            "the bot as an ordinary member, nothing happens at all: no enrolment, no "
            "announcement, no error. <b>That is the single most common reason a "
            "group looks broken.</b>",
            body,
        ),
    ))
    story += rule()

    story.append(section(
        "3 &nbsp; What the bot puts in your group",
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
        "4 &nbsp; What a member goes through",
        Paragraph(
            "So you can answer “what is this bot?” without asking us: they tap "
            "<b>Join the Alumni group</b>, which opens the bot in a private chat. "
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
        "5 &nbsp; If something looks wrong",
        symptom_table([
            ("No announcement, an hour later",
             "Nine times out of ten the bot is a member but not an "
             "<b>Administrator</b>. Check that first; otherwise tell us and we'll "
             "look at whether the gate is switched on."),
            ("It isn't pinned",
             "The bot has admin rights but not <b>Pin messages</b>. The "
             "announcement still works unpinned."),
            ("“But I <i>am</i> in the group!”",
             "Almost always a second Telegram account. Ask which account they use "
             "in the alumni group, and have them open the bot from that one."),
            ("“The bot won't reply to me”",
             "They need to tap <b>Start</b> in the private chat once. Telegram won't "
             "let a bot message anyone who hasn't."),
            ("Anything else", f"Message {CONTACT}."),
        ]),
        Spacer(1, 5),
        Paragraph(
            f"The core team also has <b>/gate_stats</b>, <b>/gate_groups</b>, "
            "<b>/gate_list</b>, <b>/gate_announce</b> and <b>/gate_unwatch</b>. Those "
            "check an internal admin list first and stay silent for everyone else, so "
            f"if you type one and nothing happens, that's why — ask {CONTACT} rather "
            "than retrying.",
            small,
        ),
    ))

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"Freshman Academy &nbsp;·&nbsp; Alumni Gate &nbsp;·&nbsp; updated {UPDATED}",
        small,
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
    doc.addPageTemplates([PageTemplate(id="single", frames=[frame])])
    doc.build(story)
    return doc.page


if __name__ == "__main__":
    pages = build()
    print(f"Wrote {OUT} ({pages} page{'s' if pages != 1 else ''})")
    if pages != 1:
        print("ERROR: the leader guide must fit on one page — trim the copy.",
              file=sys.stderr)
        sys.exit(1)
