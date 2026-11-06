"""Build the fictional test corpus and its ground truth (T11).

`build(out)` writes every client's documents into `out/<client>/` and returns
the manifest entries and typed fact records. Output is byte-for-byte
reproducible: fixed creation dates, fixed zip timestamps and seeded noise, so
the committed manifest can be checked by rebuilding. `write_records` merges the
T11 part into `data/manifests/fictional.json` and `data/facts.json`, keeping
entries written by other authoring tasks.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from docx import Document
from fpdf import FPDF
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from company_brain.corpus_data import CLIENTS, CONSULTANT, FIRM, Call, Client

PREFIX = "Test data (fictional)/Clients"
SCAN_FONT = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "DejaVuSans.ttf"
TASK = "T11"
FIXED = datetime(2026, 1, 1, tzinfo=UTC)
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
MODIFIED = re.compile(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)")
BANNER = {
    "en": "FICTIONAL test data: every name, figure and event in this document is invented.",
    "es": "FICTIONAL / FICTICIO: todos los nombres, cifras y hechos de este documento son "
    "inventados.",
}
MONTHS = {
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    "es": (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
}
WORDS: dict[str, dict[str, str]] = {
    "en": {
        "call": "Call",
        "transcript": "Call transcript",
        "date": "Date",
        "participants": "Participants",
        "proposal": "Proposal",
        "agreement": "Consulting agreement",
        "draft": "Consulting agreement draft v1",
        "report": "Progress report",
        "notes": "Internal notes",
        "prepared_for": "Prepared for",
        "prepared_by": "Prepared by",
        "situation": "Situation",
        "objectives": "Objectives",
        "measures": "Measures of success",
        "value": "Value",
        "options": "Options",
        "terms": "Fees and terms",
        "metric": "Metric",
        "baseline": "Baseline",
        "current": "Current",
        "label": "Period",
    },
    "es": {
        "call": "Llamada",
        "transcript": "Transcripción de la llamada",
        "date": "Fecha",
        "participants": "Participantes",
        "proposal": "Propuesta",
        "agreement": "Contrato de consultoría",
        "draft": "Contrato de consultoría borrador v1",
        "report": "Informe de avance",
        "notes": "Notas internas",
        "prepared_for": "Preparado para",
        "prepared_by": "Preparado por",
        "situation": "Situación",
        "objectives": "Objetivos",
        "measures": "Medidas de éxito",
        "value": "Valor",
        "options": "Opciones",
        "terms": "Honorarios y condiciones",
        "metric": "Indicador",
        "baseline": "Inicio",
        "current": "Actual",
        "label": "Periodo",
    },
}
FILE_NAMES = {
    "en": {
        "proposal": "Proposal.pdf",
        "contract": "Consulting agreement (signed).docx",
        "draft": "Consulting agreement draft v1.docx",
        "report": "Progress report",
        "planted": "Internal notes.docx",
        "scanned": "Scanned acceptance letter.pdf",
    },
    "es": {
        "proposal": "Propuesta.pdf",
        "contract": "Contrato de consultoria (firmado).docx",
        "draft": "Contrato de consultoria borrador v1.docx",
        "report": "Informe de avance",
        "planted": "Notas internas.docx",
        "scanned": "Carta de aceptacion escaneada.pdf",
    },
}


def spoken(day: date, language: str) -> str:
    month = MONTHS[language][day.month - 1]
    return (
        f"{day.day} {month} {day.year}"
        if language == "en"
        else (f"{day.day} de {month} de {day.year}")
    )


def money(amount: int, language: str) -> str:
    if language == "en":
        return f"EUR {amount:,}"
    return f"{amount:,} EUR".replace(",", ".")


def days(count: int, language: str) -> str:
    return f"{count} days" if language == "en" else f"{count} días"


def weeks(count: int, language: str) -> str:
    return f"{count} weeks" if language == "en" else f"{count} semanas"


def slug(text: str) -> str:
    return "-".join(text.lower().split())


def decision_line(text: str, language: str) -> str:
    return f"Agreed: {text}." if language == "en" else f"Acordado: {text}."


def action_line(owner: str, action: str, due: date, language: str) -> str:
    if language == "en":
        return f"{owner} will {action} by {spoken(due, language)}."
    return f"{owner} se encarga de {action} antes del {spoken(due, language)}."


def normalise_zip(data: bytes) -> bytes:
    """Rewrite an Office file's zip with fixed timestamps, so equal content gives equal bytes."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            info = zipfile.ZipInfo(item.filename, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            content = source.read(item.filename)
            if item.filename == "docProps/core.xml":
                content = MODIFIED.sub(rb"\g<1>2026-01-01T00:00:00Z\g<2>", content)
            target.writestr(info, content)
    return out.getvalue()


def docx_bytes(document: Any) -> bytes:
    props = document.core_properties
    props.author = FIRM
    props.last_modified_by = FIRM
    props.created = FIXED.replace(tzinfo=None)
    props.modified = FIXED.replace(tzinfo=None)
    props.revision = 1
    buffer = io.BytesIO()
    document.save(buffer)
    return normalise_zip(buffer.getvalue())


class _Pdf(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4")
        self.set_creation_date(FIXED)
        self.set_author(FIRM)
        self.set_auto_page_break(auto=True, margin=15)
        self.set_margins(20, 20, 20)

    def heading(self, text: str, size: int = 13) -> None:
        self.set_font("Helvetica", "B", size)
        self.multi_cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def para(self, text: str) -> None:
        self.set_font("Helvetica", "", 10)
        self.multi_cell(0, 5.5, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)


@dataclass
class Built:
    files: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)


class _ClientBuilder:
    def __init__(self, client: Client, out: Path, built: Built) -> None:
        self.c = client
        self.lang = client.language
        self.w = WORDS[client.language]
        self.names = FILE_NAMES[client.language]
        self.folder = out / client.key
        self.folder.mkdir(parents=True, exist_ok=True)
        self.built = built
        self.slug = slug(client.key)

    # Records -------------------------------------------------------------

    def subfolder(self, name: str) -> str:
        """The owner's layout: Clients/<client>/{Transcripts,Proposals,Contracts,Reports}/."""
        names = self.names
        if name.startswith(self.w["call"]):
            return "Transcripts"
        if name == names["proposal"]:
            return "Proposals"
        if name in {names["contract"], names["draft"], names["scanned"]}:
            return "Contracts"
        if name.startswith(names["report"]) or name == names["planted"]:
            return "Reports"
        raise ValueError(f"no folder for {name}")

    def rel(self, name: str) -> str:
        return f"{PREFIX}/{self.c.key}/{self.subfolder(name)}/{name}"

    def save(self, name: str, role: str, data: bytes) -> str:
        (self.folder / self.subfolder(name)).mkdir(exist_ok=True)
        (self.folder / self.subfolder(name) / name).write_bytes(data)
        self.built.files.append(
            {
                "path": self.rel(name),
                "sha256": hashlib.sha256(data).hexdigest(),
                "task": TASK,
                "client": self.c.key,
                "role": role,
                "language": self.lang,
            }
        )
        return self.rel(name)

    def fact(self, fact_id: str, kind: str, location: dict[str, Any], **fields: Any) -> None:
        record = {
            "id": f"{self.slug}.{fact_id}",
            "task": TASK,
            "type": kind,
            "client": self.c.key,
            "language": self.lang,
            **fields,
            "location": location,
        }
        self.built.facts.append(record)

    def value(
        self,
        fact_id: str,
        attribute: str,
        value: Any,
        unit: str,
        text: str,
        location: dict[str, Any],
        status: str = "current",
        counterpart: str | None = None,
    ) -> None:
        extra: dict[str, Any] = {"status": status}
        if counterpart:
            extra["counterpart"] = f"{self.slug}.{counterpart}"
        self.fact(
            fact_id,
            "value",
            location,
            entity=self.c.name,
            attribute=attribute,
            value=value,
            unit=unit,
            text=text,
            **extra,
        )

    # Calls -----------------------------------------------------------------

    def call_lines(self, number: int, call: Call) -> list[tuple[str, str, str, dict[str, Any]]]:
        """Each line as (speaker, text, language, record to place at that line, if any)."""
        lines = []
        for speaker, text, language in call.lines:
            record: dict[str, Any] = {}
            if text.startswith("DECISION "):
                index = int(text.split()[1])
                decision = call.decisions[index - 1]
                text = decision_line(decision, language)
                record = {
                    "id": f"call{number}.decision{index}",
                    "type": "decision",
                    "decision": decision,
                    "spoken": language,
                }
            elif text.startswith("ACTION "):
                index = int(text.split()[1])
                item = call.actions[index - 1]
                text = action_line(item.owner, item.action, item.due, language)
                record = {
                    "id": f"call{number}.action{index}",
                    "type": "action",
                    "action": item.action,
                    "owner": item.owner,
                    "due": item.due.isoformat(),
                    "spoken": language,
                }
            lines.append((speaker, text, language, record))
        return lines

    def place(self, record: dict[str, Any], location: dict[str, Any]) -> None:
        if record:
            fields = {k: v for k, v in record.items() if k not in ("id", "type")}
            self.fact(record["id"], record["type"], location, **fields)

    def call_vtt(self, number: int, call: Call) -> None:
        name = f"{self.w['call']} {call.day.isoformat()}.vtt"
        rel = self.rel(name)
        blocks = ["WEBVTT", f"NOTE {BANNER[self.lang]}"]
        seconds = 0

        def cue(text: str) -> str:
            nonlocal seconds
            start = seconds
            seconds += 2 + len(text.split()) // 2
            return f"{_stamp(start)} --> {_stamp(seconds)}\n{text}"

        blocks.append(cue(f"[FICTIONAL] {call.title}, {spoken(call.day, self.lang)}"))
        for index, (speaker, text, _language, record) in enumerate(self.call_lines(number, call)):
            start = _stamp(seconds)
            blocks.append(cue(f"<v {speaker}>{text}"))
            self.place(record, {"file": rel, "cue": index + 2, "start": start})
        self.save(name, "call-vtt", ("\n\n".join(blocks) + "\n").encode("utf-8"))

    def call_docx(self, number: int, call: Call) -> None:
        name = f"{self.w['call']} {call.day.isoformat()}.docx"
        rel = self.rel(name)
        document = Document()
        document.add_heading(f"{self.w['transcript']}: {call.title} (FICTIONAL)", level=1)
        document.add_paragraph(BANNER[self.lang])
        document.add_paragraph(
            f"{self.w['date']}: {spoken(call.day, self.lang)}. "
            f"{self.w['participants']}: {', '.join(call.participants)}."
        )
        for speaker, text, _language, record in self.call_lines(number, call):
            index = len(document.paragraphs)
            document.add_paragraph(f"{speaker}: {text}")
            self.place(record, {"file": rel, "paragraph": index})
        self.save(name, "call-docx", docx_bytes(document))

    # Proposal ----------------------------------------------------------------

    def option_fees(self) -> tuple[int, int, int]:
        fee = self.c.fee
        return round(fee * 0.6 / 500) * 500, fee, round(fee * 1.5 / 500) * 500

    def proposal(self) -> None:
        c, w, lang = self.c, self.w, self.lang
        rel = self.rel(self.names["proposal"])
        pdf = _Pdf()
        pdf.add_page()
        pdf.para(BANNER[lang])
        pdf.heading(f"{w['proposal']}: {c.project}", 15)
        pdf.para(
            f"{w['prepared_for']}: {c.contact}, {c.contact_role}, {c.name}. "
            f"{w['prepared_by']}: {CONSULTANT}, {FIRM}. "
            f"{w['date']}: {spoken(c.proposal_day, lang)}."
        )
        self.value(
            "proposal.contact",
            "main contact",
            c.contact,
            "name",
            c.contact,
            {"file": rel, "page": 1},
        )
        pdf.heading(w["situation"])
        pdf.para(c.situation)
        pdf.heading(w["objectives"])
        for item in c.objectives:
            pdf.para(f"- {item}.")
        pdf.heading(w["measures"])
        for item in c.measures:
            pdf.para(f"- {item}.")
        pdf.add_page()
        pdf.heading(w["value"])
        pdf.para(c.value)
        pdf.heading(w["options"])
        label = "Option" if lang == "en" else "Opción"
        for number, (option, fee) in enumerate(
            zip(c.options, self.option_fees(), strict=True), start=1
        ):
            text = money(fee, lang)
            pdf.para(f"{label} {number}: {option}. {text}.")
            self.value(
                f"proposal.option{number}",
                f"fee of option {number}",
                fee,
                "EUR",
                text,
                {"file": rel, "page": 2},
            )
        pdf.heading(w["terms"])
        duration = weeks(c.weeks, lang)
        if lang == "en":
            pdf.para(f"Fees exclude VAT. The work takes {duration} from the start date.")
        else:
            pdf.para(f"Los honorarios no incluyen IVA. El trabajo dura {duration} desde el inicio.")
        self.value(
            "proposal.duration", "duration", c.weeks, "weeks", duration, {"file": rel, "page": 2}
        )
        self.save(self.names["proposal"], "proposal", bytes(pdf.output()))

    # Contracts ----------------------------------------------------------------

    def clauses(self, fee: int, notice: int) -> list[tuple[str, str, str, Any, str]]:
        """(clause, text, attribute, value, unit) per clause; attribute empty if no fact."""
        c, lang = self.c, self.lang
        if lang == "en":
            return [
                (
                    "1",
                    f"1. Parties. This agreement is between {c.name} (the Client) and {FIRM} "
                    "(the Consultant).",
                    "",
                    None,
                    "",
                ),
                (
                    "2",
                    f"2. Scope. The Consultant supports the Client with {c.project}, as "
                    f"described in the proposal of {spoken(c.proposal_day, lang)}.",
                    "",
                    None,
                    "",
                ),
                (
                    "3",
                    f"3. Fees. The total fixed fee is {money(fee, lang)}, excluding VAT.",
                    "fee",
                    fee,
                    "EUR",
                ),
                (
                    "4",
                    f"4. Payment. Invoices are payable within {days(c.payment_days, lang)}.",
                    "payment terms",
                    c.payment_days,
                    "days",
                ),
                (
                    "5",
                    f"5. Term. The work starts on {spoken(c.start, lang)} and lasts "
                    f"{weeks(c.weeks, lang)}.",
                    "start date",
                    c.start.isoformat(),
                    "date",
                ),
                (
                    "6",
                    "6. Confidentiality. Each party keeps the other's information "
                    "confidential for five years.",
                    "",
                    None,
                    "",
                ),
                (
                    "7",
                    f"7. Termination. Either party may end this agreement with "
                    f"{days(notice, lang)}' written notice.",
                    "notice period",
                    notice,
                    "days",
                ),
                (
                    "8",
                    "8. Governing law. This agreement is governed by the laws of England "
                    "and Wales.",
                    "",
                    None,
                    "",
                ),
            ]
        return [
            (
                "1",
                f"1. Partes. Este contrato se firma entre {c.name} (el Cliente) y {FIRM} "
                "(la Consultora).",
                "",
                None,
                "",
            ),
            (
                "2",
                f"2. Objeto. La Consultora apoya al Cliente en {c.project}, según la "
                f"propuesta del {spoken(c.proposal_day, lang)}.",
                "",
                None,
                "",
            ),
            (
                "3",
                f"3. Honorarios. Los honorarios fijos totales son {money(fee, lang)}, IVA no "
                "incluido.",
                "fee",
                fee,
                "EUR",
            ),
            (
                "4",
                f"4. Pago. Las facturas se pagan en un plazo de {days(c.payment_days, lang)}.",
                "payment terms",
                c.payment_days,
                "days",
            ),
            (
                "5",
                f"5. Duración. El trabajo empieza el {spoken(c.start, lang)} y dura "
                f"{weeks(c.weeks, lang)}.",
                "start date",
                c.start.isoformat(),
                "date",
            ),
            (
                "6",
                "6. Confidencialidad. Cada parte mantiene confidencial la información de la "
                "otra durante cinco años.",
                "",
                None,
                "",
            ),
            (
                "7",
                f"7. Resolución. Cualquiera de las partes puede resolver este contrato con un "
                f"preaviso de {days(notice, lang)}.",
                "notice period",
                notice,
                "days",
            ),
            (
                "8",
                "8. Ley aplicable. Este contrato se rige por la legislación española.",
                "",
                None,
                "",
            ),
        ]

    def contract(self, draft: bool) -> None:
        c, w, lang = self.c, self.w, self.lang
        role = "draft" if draft else "contract"
        rel = self.rel(self.names[role])
        fee = c.draft_fee if draft else c.fee
        notice = c.draft_notice_days if draft else c.notice_days
        document = Document()
        document.add_heading(f"{w['draft' if draft else 'agreement']} (FICTIONAL)", level=1)
        document.add_paragraph(BANNER[lang])
        day = c.draft_day if draft else c.signed_day
        document.add_paragraph(f"{w['date']}: {spoken(day, lang)}.")
        other = "contract" if draft else "draft"
        for clause, text, attribute, value, unit in self.clauses(fee, notice):
            index = len(document.paragraphs)
            document.add_paragraph(text)
            if not attribute or (draft and attribute not in ("fee", "notice period")):
                continue
            shown = {
                "EUR": lambda v: money(v, lang),
                "days": lambda v: days(v, lang),
                "date": lambda v: spoken(date.fromisoformat(v), lang),
            }[unit](value)
            linked = attribute in ("fee", "notice period")
            key = slug(attribute)
            self.value(
                f"{role}.{key}",
                attribute,
                value,
                unit,
                shown,
                {"file": rel, "paragraph": index, "clause": clause},
                status="superseded" if draft else "current",
                counterpart=f"{other}.{key}" if linked else None,
            )
        if draft:
            document.add_paragraph(
                "Draft for discussion. Not signed."
                if lang == "en"
                else "Borrador para comentar. Sin firmar."
            )
        elif lang == "en":
            document.add_paragraph(
                f"Signed for the Client: {c.contact}, {c.contact_role}, "
                f"{spoken(c.signed_day, lang)}. Signed for the Consultant: {CONSULTANT}."
            )
        else:
            document.add_paragraph(
                f"Firmado por el Cliente: {c.contact}, {c.contact_role}, "
                f"{spoken(c.signed_day, lang)}. Firmado por la Consultora: {CONSULTANT}."
            )
        self.save(self.names[role], role, docx_bytes(document))

    # Reports -------------------------------------------------------------------

    def report(self) -> None:
        if self.c.report_format == "xlsx":
            self.report_xlsx()
        else:
            self.report_pdf()

    def report_pdf(self) -> None:
        c, w, lang = self.c, self.w, self.lang
        name = self.names["report"] + ".pdf"
        rel = self.rel(name)
        pdf = _Pdf()
        pdf.add_page()
        pdf.para(BANNER[lang])
        pdf.heading(f"{w['report']}: {c.project}", 15)
        pdf.para(f"{w['date']}: {spoken(c.report_day, lang)}. {c.report_intro}")
        pdf.set_font("Helvetica", "", 10)
        with pdf.table(col_widths=(110, 30, 30), text_align=("LEFT", "RIGHT", "RIGHT")) as table:
            table.row([w["metric"], w["baseline"], w["current"]])
            for number, (metric, baseline, current) in enumerate(c.report_rows, start=1):
                table.row([metric, str(baseline), str(current)])
                self.value(
                    f"report.row{number}",
                    metric,
                    [baseline, current],
                    "baseline, current",
                    f"{metric} {baseline} {current}",
                    {"file": rel, "page": 1},
                )
        pdf.add_page()
        pdf.heading(c.chart_title)
        top, height, left, width = 50.0, 90.0, 30.0, 150.0
        biggest = max(v for _label, v in c.chart)
        bar = width / len(c.chart)
        pdf.set_draw_color(60, 60, 60)
        pdf.line(left, top + height, left + width, top + height)
        for number, (label, value) in enumerate(c.chart, start=1):
            size = height * value / biggest
            x = left + (number - 1) * bar + bar * 0.2
            pdf.set_fill_color(70, 110, 160)
            pdf.rect(x, top + height - size, bar * 0.6, size, style="F")
            pdf.set_font("Helvetica", "", 9)
            pdf.set_xy(x, top + height - size - 6)
            pdf.cell(bar * 0.6, 5, str(value), align="C")
            pdf.set_xy(x - bar * 0.1, top + height + 2)
            pdf.cell(bar * 0.8, 5, label, align="C")
            self.fact(
                f"chart.point{number}",
                "chart",
                {"file": rel, "page": 2},
                chart=c.chart_title,
                label=label,
                value=value,
            )
        self.save(name, "report", bytes(pdf.output()))

    def report_xlsx(self) -> None:
        c, w, lang = self.c, self.w, self.lang
        name = self.names["report"] + ".xlsx"
        rel = self.rel(name)
        book = Workbook()
        book.properties.creator = FIRM
        book.properties.created = FIXED.replace(tzinfo=None)
        book.properties.modified = FIXED.replace(tzinfo=None)
        summary = book.worksheets[0]
        summary.title = "Summary"
        summary["A1"] = BANNER[lang]
        summary["A2"] = f"{w['report']}: {c.project}"
        summary["A3"] = f"{w['date']}: {spoken(c.report_day, lang)}. {c.report_intro}"
        summary.append([w["metric"], w["baseline"], w["current"]])
        for number, (metric, baseline, current) in enumerate(c.report_rows, start=1):
            summary.append([metric, baseline, current])
            row = 4 + number
            self.value(
                f"report.row{number}",
                metric,
                [baseline, current],
                "baseline, current",
                f"{metric} {baseline} {current}",
                {"file": rel, "cell": f"Summary!A{row}:C{row}"},
            )
        data = book.create_sheet("Chart")
        data.append([w["label"], c.chart_title])
        for number, (label, value) in enumerate(c.chart, start=1):
            data.append([label, value])
            row = 1 + number
            self.fact(
                f"chart.point{number}",
                "chart",
                {"file": rel, "cell": f"Chart!A{row}:B{row}"},
                chart=c.chart_title,
                label=label,
                value=value,
            )
        chart = BarChart()
        chart.title = c.chart_title
        chart.add_data(
            Reference(data, min_col=2, min_row=1, max_row=1 + len(c.chart)), titles_from_data=True
        )
        chart.set_categories(Reference(data, min_col=1, min_row=2, max_row=1 + len(c.chart)))
        data.add_chart(chart, "D2")
        buffer = io.BytesIO()
        book.save(buffer)
        self.save(name, "report", normalise_zip(buffer.getvalue()))

    # Planted instructions and the scanned page ------------------------------------------

    def planted(self) -> None:
        if not self.c.planted:
            return
        rel = self.rel(self.names["planted"])
        document = Document()
        document.add_heading(f"{self.w['notes']} (FICTIONAL)", level=1)
        document.add_paragraph(BANNER[self.lang])
        for text in self.c.planted:
            index = len(document.paragraphs)
            document.add_paragraph(text)
            if text == self.c.planted_instruction:
                self.fact(
                    "planted.instruction", "planted", {"file": rel, "paragraph": index}, text=text
                )
        self.save(self.names["planted"], "planted", docx_bytes(document))

    def scanned(self) -> None:
        if not self.c.scanned:
            return
        rel = self.rel(self.names["scanned"])
        page = Image.new("L", (1240, 1754), 250)
        draw = ImageDraw.Draw(page)
        font = ImageFont.truetype(str(SCAN_FONT), size=34)
        for number, line in enumerate(self.c.scanned):
            draw.text((140, 200 + number * 70), line, fill=25, font=font)
        rng = random.Random(4417)  # noqa: S311 - reproducible scan noise, not security
        for _ in range(9000):
            x, y = rng.randrange(1240), rng.randrange(1754)
            page.putpixel((x, y), rng.randrange(150, 235))
        page = page.rotate(0.6, resample=Image.Resampling.BICUBIC, fillcolor=250)
        page = page.filter(ImageFilter.GaussianBlur(0.7))
        image = io.BytesIO()
        page.save(image, format="JPEG", quality=80)
        pdf = FPDF(format="A4")
        pdf.set_creation_date(FIXED)
        pdf.add_page()
        pdf.image(io.BytesIO(image.getvalue()), x=0, y=0, w=210, h=297)
        for attribute, text in self.c.scanned_facts:
            self.value(
                f"scanned.{slug(attribute)}",
                attribute,
                text,
                "text",
                text,
                {"file": rel, "page": 1, "scanned": True},
            )
        self.save(self.names["scanned"], "scanned", bytes(pdf.output()))

    def build(self) -> None:
        for number, call in enumerate(self.c.calls, start=1):
            {"vtt": self.call_vtt, "docx": self.call_docx}[call.file_kind](number, call)
        self.proposal()
        self.contract(draft=False)
        self.contract(draft=True)
        self.report()
        self.planted()
        self.scanned()


def _stamp(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}.000"


def build(out: Path) -> Built:
    built = Built()
    for client in CLIENTS:
        _ClientBuilder(client, out, built).build()
    return built


def merge_records(path: Path, key: str, task: str, ours: list[dict[str, Any]]) -> None:
    """Replace one authoring task's entries in a manifest or facts file, keeping the others."""
    existing: list[dict[str, Any]] = []
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))[key]
    kept = [entry for entry in existing if entry.get("task") != task]
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({key: kept + ours}, ensure_ascii=False, indent=1)
    path.write_text(text + "\n", encoding="utf-8")


def write_records(repo: Path, built: Built) -> None:
    merge_records(repo / "data" / "manifests" / "fictional.json", "files", TASK, built.files)
    merge_records(repo / "data" / "facts.json", "facts", TASK, built.facts)
