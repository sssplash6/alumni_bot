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
mono = ParagraphStyle("mono", parent=body, fontName="Courier-Bold", fontSize=8.7,
                      textColor=ACCENT)
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


def command_table(rows):
    return two_column(rows, mono)


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
        "everything below takes about five minutes.",
        kicker,
    ))
    story += rule(7, 7)

    story.append(section(
        "1 &nbsp; Set yourself up  (once, before anything else)",
        step_table([
            f"Open a private chat with <b>{BOT}</b> and tap <b>Start</b>. The bot "
            "replies to you privately, and Telegram won't let it message you until "
            "you've done this.",
            "Send <b>/id</b> in that chat. It replies with your Telegram ID number.",
            f"Send that number to <b>{CONTACT}</b> and ask to be added as a bot admin.",
        ]),
        Spacer(1, 4),
        panel("Until step 3 is done, nothing else works.", [
            "Every command below checks the admin list first, and for everyone else "
            "does <b>nothing at all</b> — no error, just silence. That's deliberate: "
            "it stops members discovering the commands. Don't read it as the bot "
            "being broken.",
        ]),
    ))
    story += rule()

    story.append(section(
        "2 &nbsp; Add the bot to your group",
        step_table([
            f"In your group: <b>Members → Add member → {BOT}</b>.",
            "Promote it to <b>Administrator</b> and turn on <b>Pin messages</b>. "
            "Admin rights are not optional — without them Telegram never tells the "
            "bot who joins.",
            "Promoting it <b>is</b> the switch-on: the bot starts watching the group "
            "by itself and DMs you to confirm. There is nothing to configure.",
            "Send <b>/gate_announce</b> in the group. The bot posts the join "
            "announcement and pins it. You're done.",
        ]),
    ))
    story += rule()

    story.append(section(
        "3 &nbsp; What the bot puts in your group",
        Paragraph(
            "Exactly two things ever appear in the group chat:", body),
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
            "Replies to your own commands are DMed to you too, even when you type "
            "the command in the group.",
            body,
        ),
    ))
    story += rule()

    story.append(section(
        "4 &nbsp; The commands you'll use",
        Paragraph("<b>Type these in the group:</b>", body),
        Spacer(1, 3),
        command_table([
            ("/gate_announce", "Post and pin the announcement now, without waiting "
                               "for the five-day cycle."),
            ("/gate_unwatch", "Stop the bot working in this group. Records are kept."),
            ("/id", "Get this group's ID number — useful when reporting a problem."),
        ]),
        Spacer(1, 5),
        Paragraph("<b>Type these in your private chat with the bot:</b>", body),
        Spacer(1, 3),
        command_table([
            ("/gate_stats", "How many people are already in, mid-onboarding, or "
                            "still unengaged."),
            ("/gate_groups", "Every group the bot is currently watching."),
            ("/gate_list", "Everyone who has completed registration through the bot."),
        ]),
    ))
    story += rule()

    story.append(section(
        "5 &nbsp; When something looks wrong",
        symptom_table([
            ("Commands do nothing",
             "You're not on the admin list yet — go back to section 1."),
            ("No announcement",
             "The bot isn't an admin in the group. Check its rights, then re-run "
             "<b>/gate_announce</b>."),
            ("“But I <i>am</i> in it!”",
             "Almost always a second Telegram account. Ask which account they use "
             "in the alumni group, and have them message the bot from that one."),
            ("Anything else", f"Message {CONTACT}."),
        ]),
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
