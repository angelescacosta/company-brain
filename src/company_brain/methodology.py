"""First draft of the owner's consulting methodology, in English and Spanish (T13).

Original wording written for this project; the owner reviews and approves it
in H4. `build(out)` writes one Word file per language into `out` and returns
their manifest entries; `scripts/make_methodology.py` writes them into the
root's `Methodology/` folder and records them.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from docx import Document

from company_brain.corpus import docx_bytes

PREFIX = "Methodology"
TASK = "T13"

TITLE = {
    "en": "Methodology: how we scope, price and agree consulting work (draft)",
    "es": "Metodología: cómo definimos, valoramos y acordamos un proyecto (borrador)",
}
NOTE = {
    "en": "Draft for the owner's review (H4). Written in original wording for the Company "
    "Brain demonstration.",
    "es": "Borrador para revisión (H4). Redactado con palabras propias para la demostración "
    "de Company Brain.",
}
FILE_NAMES = {"en": "Methodology (EN).docx", "es": "Metodologia (ES).docx"}

SECTIONS: dict[str, list[tuple[str, list[str]]]] = {
    "en": [
        (
            "Value-based fees",
            [
                "We price a project by the value it creates for the client, not by the "
                "hours we expect to spend. Before any figure is discussed, we agree with the "
                "buyer what improvement the work should bring and what that improvement is "
                "worth to the business over a reasonable period, usually one to three years.",
                "Each option has its own fixed fee for the whole project, stated in the "
                "proposal and paid in equal monthly instalments, each one in advance at the "
                "start of the month. The fee is a modest share of the agreed value, so the "
                "client's return is clear and large. Fixed fees remove the client's fear of a "
                "running meter and let us spend the time the work really needs without "
                "renegotiating.",
                "We do not quote a fee before the conceptual agreement is in place. If a "
                "prospect insists on a day rate, we explain the difference once and, if they "
                "still prefer hours, we politely decline or offer a small diagnostic instead.",
            ],
        ),
        (
            "Conceptual agreement",
            [
                "The conceptual agreement is the shared understanding with the economic buyer, "
                "the person who can approve the investment, of three things: the objectives, "
                "how we will measure success, and the value to the organisation. It is reached "
                "in conversation, before any proposal is written.",
                "We check the agreement by summarising it back in the buyer's own words and "
                "asking whether anything is missing. A proposal written without it becomes a "
                "price quote, and the discussion turns to cost instead of results.",
                "If we cannot reach the economic buyer, we help our contact prepare the "
                "conversation, but we do not send a proposal to someone who cannot say yes.",
            ],
        ),
        (
            "Objectives",
            [
                "Objectives describe the business outcomes the client wants, not the "
                "activities we will carry out. 'Cut missed appointments by half' is an "
                "objective; 'run four workshops' is a method and belongs in the plan, not here.",
                "We aim for three to five objectives, each written so a manager outside the "
                "project would understand it. We ask the buyer to rank them; the ranking "
                "guides the options later and stops the scope from growing quietly.",
                "Useful questions are: what would be different in the organisation if this "
                "went perfectly, why does it matter now, and what happens if nothing changes "
                "over the next year.",
            ],
        ),
        (
            "Measures of success",
            [
                "Each objective gets at least one measure that tells both sides whether it "
                "was met. Measures can be numbers taken from existing reports, such as "
                "waiting time, margin or staff turnover, or observable evidence agreed in "
                "advance, such as a decision that is now taken in one meeting instead of three.",
                "We record the starting point of every measure before work begins, together "
                "with who reports it and how often. Without a baseline, improvement is a "
                "matter of opinion.",
                "We prefer measures the client already tracks. Where a new measure is "
                "needed, we agree who collects it and keep it simple enough to survive after "
                "the project ends.",
            ],
        ),
        (
            "Value",
            [
                "Value is what reaching the objectives means to the client, expressed in "
                "money where possible: extra revenue, lower costs, avoided risk, or time "
                "freed for better work. We also capture the non-financial value the buyer "
                "cares about, such as reputation, calm or personal credibility.",
                "We ask the buyer to estimate the value themselves and write down their "
                "figures. A conservative number the buyer believes is more useful than a "
                "large one they doubt.",
                "The value statement is the anchor for the fee. When the value is small, we "
                "say so honestly and suggest a smaller engagement or none at all.",
            ],
        ),
        (
            "Options",
            [
                "Every proposal offers three options that reach the same objectives with "
                "increasing depth, scope or support. The client chooses how, not whether, to "
                "work with us, and the middle option is usually the one we recommend.",
                "Each option names what is included, the timeline, its fixed fee and the "
                "monthly instalments in which that fee is paid, always in advance. Higher "
                "options add value the client can see, such as more sites, training for "
                "managers or a period of follow-up, never just more hours.",
                "After the options, the proposal offers an optional monthly retainer, also "
                "paid in advance. It covers only answering the client's questions and "
                "supporting them in meetings; it includes no work beyond what the chosen "
                "option describes. Any new piece of work is agreed in a new proposal.",
                "We keep the options simple enough to compare on one page and avoid "
                "conditions that would make the client ask for a fourth, custom option.",
            ],
        ),
        (
            "Phrases to use and avoid",
            [
                "Phrases to use: 'What would success look like for you?', 'How would we "
                "know we got there?', 'What is that worth to the business?', 'Which option "
                "suits you best?', 'Here is what I heard; did I miss anything?'.",
                "Phrases to avoid: 'My day rate is...', 'It depends how many hours it "
                "takes', 'We can do whatever you need', 'That is just a ballpark figure', "
                "and any discount offered before the client has asked for one.",
                "In general, talk about outcomes and value rather than tasks and time, ask "
                "before proposing, and confirm agreement in writing after each important "
                "conversation.",
            ],
        ),
    ],
    "es": [
        (
            "Honorarios basados en el valor",
            [
                "Fijamos el precio de un proyecto según el valor que crea para el cliente, no "
                "según las horas que pensamos dedicar. Antes de hablar de cifras, acordamos "
                "con quien decide qué mejora debe aportar el trabajo y cuánto vale esa mejora "
                "para el negocio durante un periodo razonable, normalmente de uno a tres años.",
                "Cada opción tiene sus propios honorarios fijos por todo el proyecto, "
                "indicados en la propuesta y pagados en cuotas mensuales iguales, cada una por "
                "adelantado al inicio del mes. Son una parte moderada del valor acordado, de "
                "modo que el retorno para el cliente es claro y grande. El precio fijo elimina "
                "el miedo al taxímetro y nos permite dedicar el tiempo que el trabajo necesita.",
                "No damos un precio antes de tener el acuerdo conceptual. Si alguien insiste "
                "en una tarifa por día, explicamos la diferencia una vez y, si sigue "
                "prefiriendo horas, declinamos con educación u ofrecemos un diagnóstico breve.",
            ],
        ),
        (
            "Acuerdo conceptual",
            [
                "El acuerdo conceptual es el entendimiento compartido con quien aprueba la "
                "inversión sobre tres cosas: los objetivos, cómo mediremos el éxito y el valor "
                "para la organización. Se alcanza conversando, antes de escribir ninguna "
                "propuesta.",
                "Lo comprobamos resumiéndolo con las palabras de la otra persona y preguntando "
                "si falta algo. Una propuesta escrita sin este acuerdo se convierte en un "
                "presupuesto, y la conversación pasa del resultado al coste.",
                "Si no llegamos a quien decide, ayudamos a nuestro contacto a preparar esa "
                "conversación, pero no enviamos una propuesta a alguien que no puede decir que "
                "sí.",
            ],
        ),
        (
            "Objetivos",
            [
                "Los objetivos describen los resultados de negocio que quiere el cliente, no "
                "las actividades que haremos. 'Reducir a la mitad las citas perdidas' es un "
                "objetivo; 'hacer cuatro talleres' es un método y va en el plan de trabajo.",
                "Buscamos entre tres y cinco objetivos, cada uno redactado para que lo "
                "entienda un directivo ajeno al proyecto. Pedimos que se ordenen por "
                "importancia; ese orden guía después las opciones y evita que el alcance "
                "crezca sin que nadie lo decida.",
                "Preguntas útiles: qué cambiaría en la organización si todo saliera bien, por "
                "qué importa ahora y qué pasa si nada cambia en el próximo año.",
            ],
        ),
        (
            "Medidas de éxito",
            [
                "Cada objetivo tiene al menos una medida que indica a ambas partes si se ha "
                "cumplido. Puede ser una cifra de informes que ya existen, como tiempos de "
                "espera, margen o rotación, o una evidencia observable acordada de antemano, "
                "como una decisión que ahora se toma en una reunión en lugar de tres.",
                "Antes de empezar anotamos el punto de partida de cada medida, quién la "
                "informa y con qué frecuencia. Sin punto de partida, la mejora es una opinión.",
                "Preferimos medidas que el cliente ya sigue. Si hace falta una nueva, "
                "acordamos quién la recoge y la mantenemos sencilla para que sobreviva al "
                "final del proyecto.",
            ],
        ),
        (
            "Valor",
            [
                "El valor es lo que significa para el cliente alcanzar los objetivos, en "
                "dinero cuando es posible: más ingresos, menos costes, riesgos evitados o "
                "tiempo liberado para un trabajo mejor. También recogemos el valor no "
                "económico que importa a quien decide, como la reputación, la tranquilidad o "
                "su credibilidad personal.",
                "Pedimos a quien decide que estime el valor y anotamos sus cifras. Una cifra "
                "prudente en la que cree es más útil que una grande de la que duda.",
                "La declaración de valor es la referencia para los honorarios. Si el valor es "
                "pequeño, lo decimos con honestidad y proponemos un proyecto menor o ninguno.",
            ],
        ),
        (
            "Opciones",
            [
                "Cada propuesta ofrece tres opciones que alcanzan los mismos objetivos con "
                "más profundidad, alcance o acompañamiento. El cliente elige cómo trabajar "
                "con nosotros, no si hacerlo, y normalmente recomendamos la opción intermedia.",
                "Cada opción indica qué incluye, el calendario, sus honorarios fijos y las "
                "cuotas mensuales en que se pagan, siempre por adelantado. Las opciones "
                "superiores añaden valor visible, como más centros, formación para mandos o "
                "un periodo de seguimiento, nunca solo más horas.",
                "Después de las opciones, la propuesta ofrece una iguala mensual opcional, "
                "también pagada por adelantado. Cubre solo responder a las preguntas del "
                "cliente y acompañarle en reuniones; no incluye ningún trabajo adicional a lo "
                "que describe la opción elegida. Cualquier trabajo nuevo se acuerda en una "
                "propuesta nueva.",
                "Mantenemos las opciones sencillas para compararlas en una página y evitamos "
                "condiciones que lleven al cliente a pedir una cuarta opción a medida.",
            ],
        ),
        (
            "Frases que usar y evitar",
            [
                "Frases que usar: '¿Cómo sería un buen resultado para ti?', '¿Cómo sabremos "
                "que lo hemos conseguido?', '¿Cuánto vale eso para el negocio?', '¿Qué opción "
                "te encaja mejor?', 'Esto es lo que he entendido; ¿falta algo?'.",
                "Frases que evitar: 'Mi tarifa por día es...', 'Depende de las horas que "
                "lleve', 'Hacemos lo que necesites', 'Es una cifra orientativa' y cualquier "
                "descuento ofrecido antes de que el cliente lo pida.",
                "En general, hablamos de resultados y valor en lugar de tareas y tiempo, "
                "preguntamos antes de proponer y confirmamos por escrito lo acordado después "
                "de cada conversación importante.",
            ],
        ),
    ],
}


def build(out: Path) -> list[dict[str, Any]]:
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    for language, sections in SECTIONS.items():
        document = Document()
        document.add_heading(TITLE[language], level=1)
        document.add_paragraph(NOTE[language])
        for heading, paragraphs in sections:
            document.add_heading(heading, level=2)
            for text in paragraphs:
                document.add_paragraph(text)
        data = docx_bytes(document)
        (out / FILE_NAMES[language]).write_bytes(data)
        entries.append(
            {
                "path": f"{PREFIX}/{FILE_NAMES[language]}",
                "sha256": hashlib.sha256(data).hexdigest(),
                "task": TASK,
                "role": "methodology",
                "language": language,
            }
        )
    return entries
