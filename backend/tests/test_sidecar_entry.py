from pathlib import Path

import sidecar_entry
from app.export import browser_pdf


def test_frozen_entry_dispatches_pdf_worker_before_core_bootstrap(monkeypatch, tmp_path):
    source = tmp_path / 'snapshot.html'
    output = tmp_path / 'document.pdf'
    source.write_text('<h1>Worker</h1>', encoding='utf-8')
    calls = []

    def print_snapshot(source_path: Path, output_path: Path, page_size: str):
        calls.append((source_path, output_path, page_size))

    monkeypatch.setattr(browser_pdf, 'print_snapshot', print_snapshot)
    result = sidecar_entry.main([
        'opennexus-core.exe', '--opennexus-pdf-render', str(source), str(output), 'A4'
    ])
    assert result == 0
    assert calls == [(source, output, 'A4')]
