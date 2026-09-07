"""使用真实浏览器引擎打印应用生成的自包含主题快照。

子进程隔离 Playwright 在 Windows 上的事件循环与 Uvicorn，并把浏览器生命周期限制在
单次导出内。快照禁止脚本、网络和文件加载，字体与图片必须由客户端提前内嵌。
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
from app.export.document import ExportResult


def browser_executable():
    """优先使用显式配置，再查找系统已安装的 Chromium 系浏览器。"""
    configured = os.environ.get('APP_PDF_BROWSER')
    if configured:
        return configured
    for root in (os.environ.get('PROGRAMFILES(X86)', ''), os.environ.get('PROGRAMFILES', ''), os.environ.get('LOCALAPPDATA', '')):
        if not root:
            continue
        for suffix in ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe'):
            candidate = Path(root) / suffix
            if candidate.is_file():
                return str(candidate)
    return next((p for name in ('chromium','chromium-browser','google-chrome','microsoft-edge') if (p := shutil.which(name))), None)


def render_snapshot(snapshot: str, page_size: str) -> ExportResult:
    """在隔离子进程中打印快照，避免阻塞或污染服务进程的事件循环。"""
    with tempfile.TemporaryDirectory(prefix='notes-pdf-') as directory:
        source = Path(directory) / 'snapshot.html'
        output = Path(directory) / 'document.pdf'
        source.write_text(snapshot, encoding='utf-8')
        process = subprocess.run([sys.executable, '-m', 'app.export.browser_pdf', str(source), str(output), page_size],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            cwd=Path(__file__).resolve().parents[2])
        if process.returncode:
            raise RuntimeError('PDF browser rendering failed: ' + process.stderr[-2000:])
        return ExportResult(content=output.read_bytes(), mime_type='application/pdf', warnings=[])


def print_snapshot(source: Path, output: Path, page_size: str):
    """在离线、禁用 JavaScript 的上下文中将自包含 HTML 打印为 PDF。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=browser_executable(), headless=True)
        try:
            context = browser.new_context(java_script_enabled=False, offline=True)
            context.route('**/*', lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(0)
            page.emulate_media(media='screen')
            csp = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
            page.set_content('<meta http-equiv="Content-Security-Policy" content="'+csp+'">'+source.read_text(encoding='utf-8'), wait_until='load', timeout=0)
            page.evaluate('async () => { await document.fonts.ready; await Promise.all([...document.images].map(image => image.decode().catch(() => {}))); }')
            page.pdf(path=str(output), format='Letter' if page_size.lower()=='letter' else 'A4',
                print_background=True, display_header_footer=False, prefer_css_page_size=False)
        finally:
            browser.close()


if __name__ == '__main__':
    print_snapshot(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
