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
