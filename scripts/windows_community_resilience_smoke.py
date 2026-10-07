"""Verify catalog caching, cancellation and changed keys in an owned WebView."""
from __future__ import annotations

import json
from pathlib import Path
import time

from windows_community_smoke import NativeCatalog, installed


def caches(page):
    return page.evaluate("""() => Object.keys(localStorage)
        .filter(key => key.startsWith('community-cache-v2:'))
        .map(key => JSON.parse(localStorage.getItem(key)))""")


def query_cache(page, query):
    return [item for item in caches(page) if json.loads(item['identity'])['q'] == query]


def wait_network(catalog, predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = catalog.control('stats')['result']
        if predicate(value):
            return value
        time.sleep(.1)
    raise RuntimeError('Owned catalog request did not reach the expected state')


def staged(page):
    return page.evaluate("()=>smokeInvoke('extension_staged',{offset:0,limit:20})")


def exercise(page, process, work: Path, vault: Path, catalog: NativeCatalog):
    if vault.resolve() != work.resolve() / 'vault':
        raise RuntimeError('Catalog acceptance requires the owned test vault')
    result = {'passed': False}

    def checkpoint():
        (work/'native-community-resilience-checkpoint.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2)+'\n', 'utf-8')

    page.evaluate("()=>smokeRouter.push('/community')")
    panel = page.locator('.community-panel')
    panel.wait_for()
    page.get_by_label('来源地址', exact=False).fill(catalog.ready['url'])
    page.get_by_role('button', name='检查来源与公钥', exact=True).click()
    trust = page.get_by_role('dialog', name='核对来源公钥', exact=True)
    trust.wait_for()
    if 'native-key' not in trust.inner_text():
        raise RuntimeError('The actual source key was not displayed for confirmation')
    trust.get_by_role('button', name='确认来源设置', exact=True).click()
    trust.wait_for(state='hidden')
    saved_source = page.evaluate("()=>localStorage.getItem('community-sources-v1')")
    if staged(page) or installed(page):
        raise RuntimeError('Expected an empty owned package store')

    def search(query, expected=None):
        page.get_by_label('关键词', exact=False).fill(query)
        page.get_by_role('button', name='搜索 / 刷新', exact=True).click()
        if expected is not None:
            page.wait_for_function("n=>document.querySelectorAll('.community-card').length===n && !Array.from(document.querySelectorAll('.community-controls button')).some(b=>b.textContent==='取消')", arg=expected)

    query = '原生 persona'
    page.get_by_label('类别', exact=False).select_option('persona')
    search(query, 1)
    first = query_cache(page, query)
    if len(first) != 1 or not first[0]['etag']:
        raise RuntimeError('A live catalog page did not create a conditional cache')
    search(query, 1)
    second = query_cache(page, query)
    requests = catalog.control('stats')['result']['requests']
    conditional = [item for item in requests if item['path']=='/catalog/v1/packages' and item['status']==304]
    if len(conditional) != 1 or conditional[0]['if_none_match'] != first[0]['etag'] or second[0]['data'] != first[0]['data'] or second[0]['fetchedAt'] != first[0]['fetchedAt']:
        raise RuntimeError('Native conditional revalidation did not preserve the exact cached page')
    result['conditional_validation'] = {'status':304, 'etag':first[0]['etag'], 'fetched_at_preserved':True}
    checkpoint()

    catalog.control('network', status=503)
    search(query, 1)
    panel.get_by_text('当前为此查询的离线缓存', exact=False).wait_for()
    if query_cache(page, query) != second:
        raise RuntimeError('A failed connection rewrote the validated catalog cache')
    page.locator('.community-card').click()
    detail = page.get_by_role('dialog', name='发行详情与安装', exact=True)
    if not detail.get_by_role('button', name='校验并暂存', exact=True).is_disabled():
        raise RuntimeError('Offline browsing offered package staging')
    detail.press('Escape')
    result['unavailable_cache'] = {'status':503, 'same_query_page':True, 'staging_disabled':True}
    search('没有缓存的课程 #%')
    panel.get_by_role('alert').wait_for()
    if '没有离线缓存' not in panel.get_by_role('alert').inner_text() or page.locator('.community-card').count():
        raise RuntimeError('A different query reused an unrelated offline page')
    catalog.control('network', status=403)
    search(query)
    panel.get_by_role('alert').wait_for()
    if '(403)' not in panel.get_by_role('alert').inner_text() or page.locator('.community-card').count() or panel.get_by_text('当前为此查询的离线缓存', exact=False).count():
        raise RuntimeError('An authorization failure was disguised as offline browsing')
    result['cache_isolation'] = {'uncached_query_empty':True, '403_does_not_fall_back':True}
    checkpoint()

    catalog.control('network', status=200, delay=2)
    page.get_by_label('类别', exact=False).select_option('')
    cancelled_query = '分页课程 133'
    search(cancelled_query)
    wait_network(catalog, lambda s: s['active'] > 0)
    panel.get_by_role('button', name='取消', exact=True).click()
    wait_network(catalog, lambda s: s['active'] == 0)
    page.wait_for_timeout(150)
    if query_cache(page, cancelled_query) or page.locator('.community-card').count() or panel.get_by_role('alert').count():
        raise RuntimeError('A cancelled native reply changed the visible page or query cache')
    result['query_cancellation'] = {'actual_request_started':True, 'late_reply_not_cached':True}

    obsolete_query = '分页课程 132'
    search(obsolete_query)
    wait_network(catalog, lambda s: s['active'] > 0)
    search('原生 mcp', 1)
    wait_network(catalog, lambda s: s['active'] == 0)
    if query_cache(page, obsolete_query) or '原生 mcp' not in page.locator('.community-card').inner_text():
        raise RuntimeError('Changing the query allowed an obsolete native reply to overwrite results')
    result['query_replacement'] = {'old_reply_not_cached':True, 'current_query_visible':True}
    checkpoint()

    page.locator('.community-card').click()
    detail.wait_for()
    detail.get_by_role('button', name='校验并暂存', exact=True).click()
    wait_network(catalog, lambda s: s['active'] > 0)
    detail.press('Escape')
    panel.get_by_role('button', name='取消', exact=True).click()
    wait_network(catalog, lambda s: s['active'] == 0)
    page.wait_for_timeout(150)
    if staged(page) or installed(page):
        raise RuntimeError('Cancelling before source verification left a staged or installed package')
    result['stage_cancellation'] = {'cancelled_during_live_verification':True, 'no_staged_or_installed_packages':True}
    checkpoint()

    catalog.control('network', status=200)
    before_rotation = catalog.control('stats')['result']['requests']
    replacement = catalog.control('rotate-key')['result']['public_key']
    if replacement in saved_source:
        raise RuntimeError('The signing key did not change')
    page.locator('.community-card').click()
    detail.wait_for()
    detail.get_by_role('button', name='校验并暂存', exact=True).click()
    panel.get_by_role('alert').wait_for()
    if 'EXTENSION_TRUST_CHANGED' not in panel.get_by_role('alert').inner_text():
        raise RuntimeError('A changed live signing key was not rejected: '+panel.get_by_role('alert').inner_text())
    if staged(page) or installed(page) or page.evaluate("()=>localStorage.getItem('community-sources-v1')") != saved_source:
        raise RuntimeError('A changed signing key altered packages or saved trust without confirmation')
    detail.press('Escape')
    result['changed_signing_key'] = {'rejected':True, 'saved_pin_unchanged':True, 'no_staged_or_installed_packages':True}
    result['requests_after_rotation'] = catalog.control('stats')['result']['requests'][len(before_rotation):]
    result['scope'] = 'Real HTTPS catalog and Native Host IPC; controlled 503/403 and 2s replies, no production source, no OS offline simulation'
    result['passed'] = True
    page.screenshot(path=str(work/'native-community-resilience.png'))
    checkpoint()
    return result
