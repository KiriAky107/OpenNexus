"""处理栅格资源；PDF 不受导出配额限制，但仍执行路径和格式校验。"""
import base64
import hashlib
import threading
from io import BytesIO
from PIL import Image
from app.errors import ApiError

_math_lock = threading.Lock()

def enrich_document(document, file_path=None, unlimited=False, options=None, preserve_alpha=False):
    """内嵌 Vault 图片和 MathText，并按导出格式应用配额与主题配色。"""
    from app.config import get_settings
    from urllib.parse import unquote, urlsplit
    vault = get_settings().vault_path.resolve()
    base = (vault / (file_path or '')).parent if file_path else vault
    from app.export.themes import pdf_palette
    palette = pdf_palette(options, []) if unlimited and options else None
    warnings = []
    count = total = pixels = 0
    def visit(node):
        nonlocal count, total, pixels
        if node.type in {'image','math_block','math_inline'} or node.attributes.get('static_png'):
            count += 1
            try:
                if not unlimited and count > 64: raise ValueError('resource count')
                if node.attributes.get('static_png'):
                    raw = node.attributes['static_png']
                elif node.type == 'image':
                    src = str(node.attributes.get('src',''))
                    if urlsplit(src).scheme or src.startswith('//'): raise ValueError('remote image')
                    path = (base / unquote(src)).resolve()
                    if not path.is_relative_to(vault) or path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'} or (not unlimited and path.stat().st_size > 2_000_000):
                        raise ValueError('image path or budget')
                    raw = path.read_bytes()
                else:
                    source = node.text
                    depth = 0
                    for char in source:
                        depth += (char == '{') - (char == '}')
                        if not unlimited and depth > 20: raise ValueError('math depth')
                    if (not unlimited and len(source) > 512) or depth != 0: raise ValueError('math budget')
                    from matplotlib.mathtext import math_to_image
                    from matplotlib import rc_context
                    with _math_lock, rc_context({'savefig.transparent': bool(palette)}):
                        out = BytesIO()
                        math_to_image('$'+source+'$', out, dpi=180, format='png', color=palette['text'] if palette else 'black')
                        raw = out.getvalue()
                with Image.open(BytesIO(raw)) as image:
                    pixels += image.width * image.height
                    if not unlimited and pixels > 16_000_000: raise ValueError('document pixels')
                    if not unlimited and image.width * image.height > 4_000_000: raise ValueError('image dimensions')
                    out = BytesIO()
                    # 透明像素按 PDF 主题表面色合成；打印 HTML 与 Word 使用白色底色。
                    rgba=image.convert('RGBA'); background=Image.new('RGBA',rgba.size,palette['surface'] if palette else 'white')
                    background.alpha_composite(rgba); (rgba if preserve_alpha else background.convert('RGB')).save(out,'PNG')
                    png=out.getvalue();total += len(png)
                    if not unlimited and total > 8_000_000: raise ValueError('resource bytes')
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

def validate_assets(assets, unlimited=False):
    """校验前端静态资源并解码为 PNG；PDF 仅解除容量限制，不放宽格式要求。"""
    result = {}
    total = pixels = 0
    for asset in assets:
        try:
            raw = base64.b64decode(asset.png_base64, validate=True)
            total += len(raw)
            if not unlimited and total > 8 * 1024 * 1024:
                raise ValueError('asset budget')
            with Image.open(BytesIO(raw)) as image:
                pixels += image.width * image.height
                if not unlimited and pixels > 16_000_000: raise ValueError('document pixel budget')
                if image.format != 'PNG' or (not unlimited and image.width * image.height > 4_000_000):
                    raise ValueError('image budget')
                image.load()
                out = BytesIO()
                rgba = image.convert('RGBA')
                background = Image.new('RGBA', rgba.size, 'white')
                background.alpha_composite(rgba)
                (rgba if unlimited else background.convert('RGB')).save(out, 'PNG')
                key = (asset.kind, asset.source_hash)
                if key in result:
                    raise ValueError('duplicate asset')
                result[key] = out.getvalue()
        except Exception as exc:
            raise ApiError(422, 'EXPORT_ASSET_INVALID', 'Invalid PNG or resource budget exceeded.') from exc
    return result

def attach_assets(document, assets):
    """按资源类型和源码哈希把已验证图片挂载到对应文档节点。"""
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
    """按 SVG/PDF 共用的裁剪几何，以二倍分辨率生成 DOCX 图像。"""
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
        # 纵轴标题横排在左上边距，避免 CJK 文本在 Word 中旋转后不可读。
        draw.text((24, 24), geo.ylabel, fill='#1f2328', font=font)
    for index, expression in enumerate(plot.expressions):
        draw.text((48+(index%2)*620,geo.height*2+index//2*48),expression.label or 'y = '+expression.expression,fill=geo.colors[index],font=font)
    out=BytesIO(); image.save(out,'PNG')
    return out.getvalue(), geo.warnings
