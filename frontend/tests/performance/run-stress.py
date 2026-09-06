"""Real Chromium benchmark. Run with backend/.venv/Scripts/python.exe; requires websockets.
Vite must be serving the frontend. Uses an isolated disposable browser profile.
"""
import argparse, asyncio, json, pathlib, subprocess, tempfile, urllib.request
import websockets

async def main(args):
    with tempfile.TemporaryDirectory(prefix='notes-stress-') as profile:
        process = subprocess.Popen([args.chrome, '--headless=new', '--no-first-run', '--no-proxy-server', '--no-default-browser-check', '--disable-background-networking', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--remote-debugging-port=0', '--window-size=1440,1000', f'--user-data-dir={profile}', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port_file = pathlib.Path(profile) / 'DevToolsActivePort'
            for _ in range(100):
                if port_file.exists(): break
                await asyncio.sleep(.1)
            port = port_file.read_text().splitlines()[0]
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json') as response: target = next(item for item in json.load(response) if item['type'] == 'page')
            async with websockets.connect(target['webSocketDebuggerUrl'], max_size=100_000_000) as socket:
                sequence = 0
                async def call(method, params=None):
                    nonlocal sequence
                    sequence += 1; request = sequence
                    await socket.send(json.dumps({'id':request,'method':method,'params':params or {}}))
                    while True:
                        response = json.loads(await asyncio.wait_for(socket.recv(), 180))
                        if response.get('method') in ['Runtime.exceptionThrown','Log.entryAdded','Network.loadingFailed']: print(json.dumps(response),flush=True)
                        if response.get('id') == request:
                            if 'error' in response: raise RuntimeError(response['error'])
                            return response.get('result', {})
                await call('Runtime.enable')
                await call('Log.enable')
                await call('Network.enable')
                await asyncio.sleep(1)
                results=[]
                for size in args.sizes:
                    for repeat in range(args.runs):
                        navigation = await call('Page.navigate', {'url':args.url})
                        if navigation.get('errorText'): raise RuntimeError(navigation['errorText'])
                        for _ in range(600):
                            state = await call('Runtime.evaluate', {'expression':'typeof window.runBenchmark', 'returnByValue':True})
                            if state.get('result',{}).get('value')=='function':break
                            await asyncio.sleep(.1)
                        else: raise RuntimeError('Benchmark page did not load; check the Vite URL and browser errors')
                        expression = f'window.prepareScrollBenchmark({size})' if args.scroll else f'window.runBenchmark({size})'
                        response = await call('Runtime.evaluate', {'expression':expression,'awaitPromise':True,'returnByValue':True})
                        if args.scroll and 'exceptionDetails' not in response:
                            point = response['result']['value']
                            if args.profile:
                                await call('Profiler.enable'); await call('Profiler.start')
                            await call('Input.dispatchMouseEvent', {'type':'mouseMoved', **point})
                            for step in range(120):
                                await call('Input.dispatchMouseEvent', {'type':'mouseWheel', **point, 'deltaX':0,'deltaY':900 if step < 90 else -900})
                                await asyncio.sleep(.016)
                            if args.profile:
                                profile_data = await call('Profiler.stop')
                                pathlib.Path(args.output + f'.{size}.{repeat+1}.cpuprofile').write_text(json.dumps(profile_data['profile']),encoding='utf-8')
                            await call('Runtime.evaluate', {'expression':'new Promise(r => setTimeout(r, 150))','awaitPromise':True})
                            response = await call('Runtime.evaluate', {'expression':'window.finishScrollBenchmark()','awaitPromise':True,'returnByValue':True})
                        if 'exceptionDetails' in response: raise RuntimeError(response['exceptionDetails'])
                        result = response['result']['value']; result['repeat']=repeat+1
                        results.append(result)
                        print(json.dumps(result,ensure_ascii=False),flush=True)
                        pathlib.Path(args.output).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            await asyncio.sleep(.5)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--chrome',default='C:/Program Files/Google/Chrome/Application/chrome.exe')
    parser.add_argument('--url',default='http://127.0.0.1:5173/tests/performance/stress.html')
    parser.add_argument('--sizes',nargs='+',type=int,default=[25000,60000,120000])
    parser.add_argument('--runs',type=int,default=3)
    parser.add_argument('--output',required=True)
    parser.add_argument('--profile',action='store_true',help='Save CPU profiles for scroll runs')
    parser.add_argument('--scroll',action='store_true',help='Dispatch real wheel events and check fold-to-top')
    args = parser.parse_args()
    if args.runs < 1 or any(size < 1 for size in args.sizes): parser.error('runs and sizes must be positive')
    pathlib.Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(args))
