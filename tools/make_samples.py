"""Draw the fictional sample deliveries: a simple dog portrait on blue (matches) and on red (doesn't)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "samples"


def dog(bg, name):
    W = H = 900
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    fur, dark = (214, 160, 98), (120, 78, 40)
    d.ellipse([250, 520, 650, 980], fill=fur)                       # body
    d.ellipse([260, 220, 640, 600], fill=fur)                       # head
    d.ellipse([200, 250, 330, 520], fill=dark)                      # left ear
    d.ellipse([570, 250, 700, 520], fill=dark)                      # right ear
    d.ellipse([340, 360, 390, 410], fill=(30, 30, 30))              # eyes
    d.ellipse([510, 360, 560, 410], fill=(30, 30, 30))
    d.ellipse([355, 370, 370, 385], fill="white"); d.ellipse([525, 370, 540, 385], fill="white")
    d.ellipse([400, 430, 500, 540], fill=(240, 205, 160))           # muzzle
    d.ellipse([425, 440, 475, 480], fill=(40, 30, 30))              # nose
    d.arc([410, 470, 490, 530], 20, 160, fill=(40, 30, 30), width=6)
    d.rectangle([330, 590, 570, 625], fill=(200, 40, 60))           # collar
    d.ellipse([430, 610, 470, 650], fill=(250, 200, 60))            # tag
    try:
        f = ImageFont.truetype("DejaVuSans-Bold.ttf", 30)
    except OSError:
        f = ImageFont.load_default()
    d.text((W - 400, H - 56), "SAMPLE · FICTIONAL", fill="white", font=f)
    im.save(OUT / name, quality=90)


OUT.mkdir(exist_ok=True)
dog((70, 130, 220), "biscuit_portrait_blue.png")
dog((210, 60, 50), "biscuit_portrait_red.png")
print("ok")


CV = {
    "en": ["Lina Haddad", "Marketing assistant", "Profile", "Marketing assistant with three years of experience in social media and event planning.",
           "Experience", "2022 to 2025: Marketing assistant, Atelier Lumen, Lyon. Planned twelve product launches and ran the brand's Instagram account.",
           "2021: Intern, Agence Mistral, Paris. Wrote newsletters and prepared client reports.",
           "Education", "2021: Bachelor's degree in Communication, Université Lumière Lyon 2.",
           "Languages", "French (native), English (fluent), Arabic (conversational).",
           "Skills", "Canva, Meta Business Suite, Excel, event planning, copywriting."],
    "fr": ["Lina Haddad", "Assistante marketing", "Profil", "Assistante marketing avec trois ans d'expérience en réseaux sociaux et en organisation d'événements.",
           "Expérience", "2022 à 2025 : Assistante marketing, Atelier Lumen, Lyon. Organisation de douze lancements de produits et gestion du compte Instagram de la marque.",
           "2021 : Stagiaire, Agence Mistral, Paris. Rédaction de newsletters et préparation de rapports clients.",
           "Formation", "2021 : Licence en communication, Université Lumière Lyon 2.",
           "Langues", "Français (langue maternelle), anglais (courant), arabe (conversationnel).",
           "Compétences", "Canva, Meta Business Suite, Excel, organisation d'événements, rédaction."],
}


def cv(lang, name):
    """A fictional two-page CV, as delivered in the sample translation deal."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(OUT / name), pagesize=A4)
    lines = CV[lang]
    for page, chunk in enumerate((lines[:7], lines[7:]), 1):
        y = 790
        if page == 1:
            c.setFont("Helvetica-Bold", 22); c.drawString(60, y, chunk[0]); y -= 26
            c.setFont("Helvetica", 13); c.drawString(60, y, chunk[1]); y -= 40
            chunk = chunk[2:]
        for i, line in enumerate(chunk):
            heading = len(line) < 20
            c.setFont("Helvetica-Bold" if heading else "Helvetica", 13 if heading else 11)
            words, cur = line.split(), ""
            for w in words:
                if c.stringWidth(cur + " " + w, "Helvetica", 11) > 470:
                    c.drawString(60, y, cur.strip()); y -= 16; cur = ""
                cur += " " + w
            c.drawString(60, y, cur.strip()); y -= 28 if heading else 24
        c.setFont("Helvetica-Bold", 9); c.drawString(60, 40, f"SAMPLE · FICTIONAL · page {page} of 2")
        c.showPage()
    c.save()


if __name__ == "__main__":
    cv("en", "cv_lina_english.pdf")
    cv("fr", "cv_lina_french.pdf")
