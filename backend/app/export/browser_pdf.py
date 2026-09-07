"""Print the app's self-contained theme snapshot with a real browser engine.

A child process isolates Playwright's Windows event loop from Uvicorn and keeps
browser lifecycle scoped to one export. Snapshot scripts/network/file loads are
blocked; fonts and images must already be embedded by the client.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
from app.export.document import ExportResult


def browser_executable():
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
