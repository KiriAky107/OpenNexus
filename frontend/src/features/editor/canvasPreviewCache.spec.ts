import { afterEach, expect, it, vi } from 'vitest'
import { CanvasPreviewCache } from './canvasPreviewCache'
const source=(path:string,hash='one',vault='vault')=>({vault,path,key:JSON.stringify([vault,path,hash])})
afterEach(()=>vi.restoreAllMocks())

it('coalesces concurrent aliases and reuses hash-bound previews across unrelated changes',async()=>{
 const cache=new CanvasPreviewCache(),target=source('/note.md')
 cache.configure('vault',[target]);let finish!:(value:{text:string})=>void
 const loader=vi.fn(()=>new Promise<{text:string}>(resolve=>{finish=resolve}))
 const first=cache.read(target,loader),second=cache.read(target,loader)
 await Promise.resolve();finish({text:'Actual summary'})
 expect(await first).toEqual({text:'Actual summary'});expect(await second).toEqual(await first)
 cache.configure('vault',[target]);await cache.read(target,loader)
 expect(loader).toHaveBeenCalledTimes(1);expect(cache.size).toBe(1)
 cache.dispose();expect(cache.bytes).toBe(0)
})

it('revokes replaced image URLs and ignores stale pending results on vault change or disposal',async()=>{
 const create=vi.spyOn(URL,'createObjectURL').mockReturnValue('blob:owned'),revoke=vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
 const cache=new CanvasPreviewCache(),old=source('/image.png'),fresh=source('/image.png','two')
 cache.configure('vault',[old]);await cache.read(old,async()=>({blob:new Blob(['image'])}))
 cache.configure('vault',[fresh]);expect(revoke).toHaveBeenCalledWith('blob:owned')
 let finish!:(value:{blob:Blob})=>void
 const pending=cache.read(fresh,()=>new Promise(resolve=>{finish=resolve}));await Promise.resolve()
 cache.configure('other',[source('/image.png','two','other')]);finish({blob:new Blob(['old-vault'])})
 expect(await pending).toBeUndefined();expect(create).toHaveBeenCalledTimes(1)
 const current=source('/image.png','two','other');cache.configure('other',[current])
 const disposed=cache.read(current,()=>new Promise(resolve=>{finish=resolve}));await Promise.resolve()
 cache.dispose();finish({blob:new Blob(['disposed'])})
 expect(await disposed).toBeUndefined();expect(create).toHaveBeenCalledTimes(1)
})

it('bounds entries and bytes, evicts unused LRU URLs and retries capacity-limited previews when zoom releases space',async()=>{
 let count=0
 const create=vi.spyOn(URL,'createObjectURL').mockImplementation(()=>`blob:${++count}`),revoke=vi.spyOn(URL,'revokeObjectURL').mockImplementation(()=>{})
 const cache=new CanvasPreviewCache(2,12),a=source('/a.png'),b=source('/b.png'),c=source('/c.png')
 cache.configure('vault',[a]);await cache.read(a,async()=>({blob:new Blob(['0123456789'])}))
 cache.configure('vault',[b]);await cache.read(b,async()=>({blob:new Blob(['0123456789'])}))
 expect(revoke).toHaveBeenCalledWith('blob:1')
 cache.configure('vault',[b,c]);const loader=vi.fn(async()=>({blob:new Blob(['01234567'])}))
 expect(await cache.read(c,loader)).toEqual({limited:true})
 expect(cache.bytes).toBe(10);expect(cache.size).toBe(2)
 await cache.read(c,loader);expect(loader).toHaveBeenCalledTimes(1)
 cache.configure('vault',[c]);expect(await cache.read(c,loader)).toEqual({url:'blob:3'})
 expect(cache.bytes).toBe(8);expect(cache.size).toBe(1);expect(create).toHaveBeenCalledTimes(3)
 cache.dispose();expect(revoke).toHaveBeenCalledTimes(3)
})

it('does not allocate a URL when an obsolete hash read finishes',async()=>{
 const create=vi.spyOn(URL,'createObjectURL')
 const cache=new CanvasPreviewCache(),old=source('/image.png'),fresh=source('/image.png','fresh')
 cache.configure('vault',[old]);let finish!:(value:{blob:Blob})=>void
 const pending=cache.read(old,()=>new Promise(resolve=>{finish=resolve}));await Promise.resolve()
 cache.configure('vault',[fresh]);finish({blob:new Blob(['stale'])})
 expect(await pending).toBeUndefined();expect(create).not.toHaveBeenCalled();cache.dispose()
})
