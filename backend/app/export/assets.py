"""Bounded raster-only resource boundary. No URLs, XML or filesystem paths accepted."""
import base64
import hashlib
import threading
from io import BytesIO
from PIL import Image
from app.errors import ApiError

_math_lock = threading.Lock()

def enrich_document(document, file_path=None):
    """Embed local vault images and bounded MathText. Unsupported TeX stays explicit."""
    from app.config import get_settings
    from urllib.parse import unquote, urlsplit
    vault = get_settings().vault_path.resolve()
    base = (vault / (file_path or '')).parent if file_path else vault
    warnings = []
    count = total = pixels = 0
    def visit(node):
        nonlocal count, total, pixels
        if node.type in {'image','math_block','math_inline'} or node.attributes.get('static_png'):
            count += 1
            try:
                if count > 64: raise ValueError('resource count')
                if node.attributes.get('static_png'):
                    raw = node.attributes['static_png']
                elif node.type == 'image':
                    src = str(node.attributes.get('src',''))
                    if urlsplit(src).scheme or src.startswith('//'): raise ValueError('remote image')
                    path = (base / unquote(src)).resolve()
                    if not path.is_relative_to(vault) or path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'} or path.stat().st_size > 2_000_000:
                        raise ValueError('image path or budget')
                    raw = path.read_bytes()
                else:
                    source = node.text
                    depth = 0
                    for char in source:
                        depth += (char == '{') - (char == '}')
                        if depth > 20: raise ValueError('math depth')
                    if len(source) > 512 or depth != 0: raise ValueError('math budget')
                    from matplotlib.mathtext import math_to_image
                    with _math_lock:
                        out = BytesIO()
                        math_to_image('$'+source+'$', out, dpi=180, format='png', color='black')
                        raw = out.getvalue()
                with Image.open(BytesIO(raw)) as image:
                    pixels += image.width * image.height
                    if pixels > 16_000_000: raise ValueError('document pixels')
                    if image.width * image.height > 4_000_000: raise ValueError('image dimensions')
                    out = BytesIO()
                    # Flatten alpha on white for portable print/Word output.
                    rgba=image.convert('RGBA'); background=Image.new('RGBA',rgba.size,'white')
                    background.alpha_composite(rgba); background.convert('RGB').save(out,'PNG')
                    png=out.getvalue();total += len(png)
                    if total > 8_000_000: raise ValueError('resource bytes')
                    node.attributes['static_png']=png
            except Exception:
                node.attributes.pop('static_png', None)
                warnings.append('图片无法内嵌（仅支持 Vault 内 PNG/JPEG/WebP），已保留替代文字' if node.type=='image'
                    else '公式超出 MathText 语法或资源预算，已保留源码' if node.type.startswith('math')
                    else '静态图表超过文档资源预算，已保留源码')
        for child in node.children: visit(child)
    for child in document.children: visit(child)
    return warnings

def source_hash(source):
    return hashlib.sha256(source.strip().encode()).hexdigest()

def validate_assets(assets):
    result = {}
    total = pixels = 0
    for asset in assets:
        try:
            raw = base64.b64decode(asset.png_base64, validate=True)
            total += len(raw)
            if total > 8 * 1024 * 1024:
                raise ValueError('asset budget')
            with Image.open(BytesIO(raw)) as image:
                pixels += image.width * image.height
                if pixels > 16_000_000: raise ValueError('document pixel budget')
                if image.format != 'PNG' or image.width * image.height > 4_000_000:
                    raise ValueError('image budget')
                image.load()
                out = BytesIO()
                rgba = image.convert('RGBA')
                background = Image.new('RGBA', rgba.size, 'white')
                background.alpha_composite(rgba)
                background.convert('RGB').save(out, 'PNG')
                key = (asset.kind, asset.source_hash)
                if key in result:
                    raise ValueError('duplicate asset')
                result[key] = out.getvalue()
        except Exception as exc:
            raise ApiError(422, 'EXPORT_ASSET_INVALID', 'Invalid PNG or resource budget exceeded.') from exc
    return result

def attach_assets(document, assets):
    def visit(node):
        source = node.attributes.get('src', '') if node.type == 'image' else node.text
        key = (node.type, source_hash(source))
        if key in assets:
            node.attributes['static_png'] = assets[key]
        for child in node.children:
            visit(child)
    for child in document.children:
        visit(child)

def plot_png(plot):
    """DOCX consumes the same clipped geometry as SVG/PDF, rendered at 2x."""
    from app.plot.render import compute_geometry, _sx, _sy, _fmt_num
    from PIL import ImageDraw, ImageFont
    geo = compute_geometry(plot)
    image = Image.new('RGB', (geo.width * 2, (geo.height + ((len(plot.expressions)+1)//2)*24) * 2), 'white')
    draw = ImageDraw.Draw(image)
    from app.export.fonts import FONT_PATH
    font = ImageFont.truetype(str(FONT_PATH), 20) if FONT_PATH else ImageFont.load_default(size=20)
    def line(points, color, width=2):
        draw.line([(x * 2, y * 2) for x, y in points], fill=color, width=width)
    sx = lambda x: _sx(x, geo.xmin, geo.xmax)
    sy = lambda y: _sy(y, geo.ymin, geo.ymax)
    for x in geo.xticks:
        if geo.grid: line([(sx(x),52),(sx(x),428)], '#d0d7de')
        draw.text((sx(x)*2, sy(geo.x_axis_y)*2+8), _fmt_num(x), fill='#57606a', font=font)
    for y in geo.yticks:
        if geo.grid: line([(52,sy(y)),(588,sy(y))], '#d0d7de')
        draw.text((max(0,sx(geo.y_axis_x)*2-75),sy(y)*2), _fmt_num(y), fill='#57606a', font=font)
    line([(52,sy(geo.x_axis_y)),(588,sy(geo.x_axis_y))], '#57606a')
    line([(sx(geo.y_axis_x),52),(sx(geo.y_axis_x),428)], '#57606a')
    for segments, color in zip(geo.polylines,geo.colors):
        for segment in segments:
            if len(segment)>1: line(segment,color,3)
    if geo.xlabel:
        draw.text((geo.width, (geo.height - 18)*2), geo.xlabel, fill='#1f2328', font=font, anchor='mm')
    if geo.ylabel:
        # Horizontal at the upper-left margin keeps CJK labels readable in Word.
        draw.text((24, 24), geo.ylabel, fill='#1f2328', font=font)
    for index, expression in enumerate(plot.expressions):
        draw.text((48+(index%2)*620,geo.height*2+index//2*48),expression.label or 'y = '+expression.expression,fill=geo.colors[index],font=font)
    out=BytesIO(); image.save(out,'PNG')
    return out.getvalue(), geo.warnings
