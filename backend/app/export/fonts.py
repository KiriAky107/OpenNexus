"""Embed an available CJK TrueType font; retain the portable CID fallback."""
import os
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

def register_font():
    candidates = [os.getenv('APP_EXPORT_FONT',''),
        str(Path(os.getenv('WINDIR','C:/Windows'))/'Fonts/simsun.ttc'),
        '/usr/share/fonts/truetype/arphic/uming.ttc']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            try:
                pdfmetrics.registerFont(TTFont('NotesExportCJK',candidate,subfontIndex=0))
                return 'NotesExportCJK', Path(candidate)
            except Exception:
                continue
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    return 'STSong-Light', None

FONT, FONT_PATH = register_font()
