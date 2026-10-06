"""Verify ordinary experiment links through actual native tree review dialogs."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import re
import time


def exercise(page, process, work: Path, vault: Path):
    if vault.resolve() != work.resolve() / 'vault':
        raise RuntimeError('Link checks require the verifier-owned synthetic vault')
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads((root / 'frontend/src/services/fixtures/experiment-links-v1.json').read_text('utf-8'))
    if fixture['schema'] != 1:
        raise RuntimeError('Unknown experiment link contract')
    expected = {}
    for file in fixture['files']:
        path = vault / file['path']
        if path.exists():
            raise RuntimeError('Preserve an existing test document: ' + file['path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file['content'].encode('utf-8'))
        expected[file['path']] = file['content']
    (vault / '资料').mkdir(exist_ok=True)
    page.evaluate("async()=>{await smokeInvoke('workspace_tree');await smokePinia._s.get('workspace').refreshFileTree()}")
    entries = {item['path']: item['file_id'] for item in page.evaluate("()=>smokeInvoke('workspace_tree')") if item['path'] in expected}
    if len(entries) != len(expected):
        raise RuntimeError('Host did not recognize all experiment and result files')

    def history():
        return page.evaluate("()=>smokeInvoke('experiment_request',{request:{vault_id:smokePinia._s.get('workspace').vaultId,action:{kind:'history',limit:50,cursor:null}}})")['items']

    before_history = history()
    note_path = fixture['initial_note_path']

    def open_note():
        page.evaluate("async path=>{await smokePinia._s.get('editor').loadFile('/'+path);await smokePinia._s.get('editor').checkExternalFile()}", note_path)
        page.locator('.ProseMirror').first.wait_for()

    def links(paths):
        for file, path in zip(fixture['files'][:4], paths):
            open_note()
            page.locator('.ProseMirror a').filter(has_text=file['label']).first.click(modifiers=['Control'])
            page.wait_for_function("path=>smokePinia._s.get('editor').currentFilePath==='/'+path", arg=path)
            if page.evaluate("()=>smokePinia._s.get('editor').content") != expected[path]:
                raise RuntimeError('Rendered link opened different bytes: ' + path)

    def start_move(step):
        open_note()
        page.get_by_label('全部展开文件夹', exact=True).click()
        scope = page.locator('#workspace-files-panel .tree')
        parts = step['old_path'].split('/')
        for index, name in enumerate(parts):
            node = scope.locator(':scope > div > .tree-node').filter(has=page.locator('.name', has_text=re.compile('^' + re.escape(name) + '$')))
            node.wait_for()
            if index < len(parts) - 1:
                scope = node.locator('xpath=..').locator(':scope > .children')
        node.click(button='right')
        same_parent = PurePosixPath(step['old_path']).parent == PurePosixPath(step['new_path']).parent
        page.locator('.context-menu').get_by_role('button', name='重命名' if same_parent else '移动到文件夹', exact=True).click()
        prompt = page.get_by_role('dialog', name='新名称' if same_parent else '目标文件夹（知识库内路径）', exact=True)
        prompt.wait_for()
        prompt.locator('input').fill(step['new_path'].rsplit('/', 1)[-1] if same_parent else '/' + step['new_path'].rsplit('/', 1)[0])
        prompt.get_by_role('button', name='确定', exact=True).click()
        dialog = page.get_by_role('dialog', name='引用影响预览', exact=True)
        dialog.wait_for()
        return dialog

    links([file['path'] for file in fixture['files'][:4]])
    completed = []
    for index, step in enumerate(fixture['steps']):
        print('EXPERIMENT_LINK_UI', step['old_path'], '->', step['new_path'], flush=True)
        # Changing a basename and its parent are separate reviewable UI actions.
        # The contract's referring-note move first uses a basename rename below.
        if PurePosixPath(step['old_path']).name != PurePosixPath(step['new_path']).name and PurePosixPath(step['old_path']).parent != PurePosixPath(step['new_path']).parent:
            raise RuntimeError('Native link fixture must express one tree action per step')
        review = start_move(step)
        if index == 0:
            review.get_by_role('button', name='取消', exact=True).click()
            review.wait_for(state='hidden')
            if (vault / note_path).read_bytes() != expected[note_path].encode('utf-8') or not (vault / step['old_path']).is_file():
                raise RuntimeError('Cancelling reference review changed a document')
            review = start_move(step)
        if review.get_by_role('button', name='更新引用并移动', exact=True).count():
            review.locator('details summary').first.click()
            review.locator('.reference-preview').first.wait_for()
            review.get_by_role('button', name='更新引用并移动', exact=True).click()
        else:
            if step['note_content'] != expected[note_path]:
                raise RuntimeError('Reference review omitted a required content update')
            review.get_by_role('button', name='移动并保留引用原文', exact=True).click()
        review.wait_for(state='hidden')
        prefix = step['old_path'] + '/'
        expected = {(step['new_path'] + path[len(step['old_path']):] if path == step['old_path'] or path.startswith(prefix) else path): content for path, content in expected.items()}
        entries = {(step['new_path'] + path[len(step['old_path']):] if path == step['old_path'] or path.startswith(prefix) else path): file_id for path, file_id in entries.items()}
        note_path = step['note_path']
        expected[note_path] = step['note_content']
        page.wait_for_function("async path=>(await smokeInvoke('workspace_tree')).some(item=>item.path===path)", arg=step['new_path'] if step['kind'] != 'folder' else step['linked_paths'][0])
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if (vault / note_path).is_file() and (vault / note_path).read_bytes() == step['note_content'].encode('utf-8'):
                break
            alert = page.locator('.file-tree-panel .create-error')
            if alert.count() and alert.is_visible():
                raise RuntimeError('Actual tree operation failed: ' + alert.inner_text())
            time.sleep(.1)
        else:
            raise RuntimeError('Reviewed note bytes differ from the shared contract')
        actual = {item['path']: item['file_id'] for item in page.evaluate("()=>smokeInvoke('workspace_tree')") if item['path'] in entries}
        if actual != entries:
            raise RuntimeError('Native rename changed a stable file identity')
        try:
            page.wait_for_function("([path,content])=>{const editor=smokePinia._s.get('editor'),workspace=smokePinia._s.get('workspace');return editor.currentFilePath==='/'+path && workspace.activeFilePath==='/'+path && editor.saveStatus==='saved' && editor.content===content}", arg=[note_path, step['note_content']], timeout=15000)
        except Exception:
            state = page.evaluate("()=>{const e=smokePinia._s.get('editor'),w=smokePinia._s.get('workspace');return {editor_path:e.currentFilePath,active_path:w.activeFilePath,status:e.saveStatus,external_read_error:e.externalReadError}}")
            (work / 'native-link-navigation-failure.json').write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            raise
        links(step['linked_paths'])
        completed.append({'old_path': step['old_path'], 'new_path': step['new_path'], 'links_opened': 4, 'note_sha256': hashlib.sha256(step['note_content'].encode('utf-8')).hexdigest()})
    if history() != before_history or (vault / 'must-not-run.txt').exists():
        raise RuntimeError('Reading or moving an experiment unexpectedly ran code')
    open_note()
    page.screenshot(path=str(work / 'native-experiment-links.png'))
    return {'passed': True, 'actual_tree_review': True, 'cancel_preserved_documents': True,
        'steps': completed, 'stable_file_ids': entries, 'crlf_unicode_bytes_preserved': True,
        'source_and_inputs_not_executed': True,
        'scope': 'Ordinary rendered links in one owned native WebView; separate real HTTP test verifies both devices'}
