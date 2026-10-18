// Only geometry from the two declared synthetic Canvas nodes, never content.
export const canvasViewIds = Object.freeze({
  a: '8792bc6e-cd56-45fa-8f5c-645ba12ae901',
  b: '8792bc6e-cd56-45fa-8f5c-645ba12ae902',
  edge: '8792bc6e-cd56-45fa-8f5c-645ba12ae903',
})

export function canvasViewExpression(dispatch, stage) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(dispatch)
      || !['before', 'after'].includes(stage)) throw Error('CANVAS_VIEW_SCOPE_REFUSED')
  return `JSON.stringify((()=>{
    const ids=${JSON.stringify(canvasViewIds)}, dispatch=${JSON.stringify(dispatch)}, stage=${JSON.stringify(stage)};
    const refuse=()=>{throw Error('CANVAS_VIEW_GEOMETRY_UNAVAILABLE')};
    const number=value=>{if(typeof value!=='number'||!Number.isFinite(value)||Math.abs(value)>131072)refuse();return value};
    const rect=element=>{const b=element.getBoundingClientRect();return {
      left:number(b.left),top:number(b.top),right:number(b.right),bottom:number(b.bottom),width:number(b.width),height:number(b.height)}};
    const hosts=Array.from(document.querySelectorAll('.canvas-editor .canvas-viewport'));
    if(hosts.length!==1)refuse();const host=hosts[0];
    const worlds=Array.from(host.querySelectorAll(':scope > .canvas-world'));
    if(worlds.length!==1)refuse();const world=worlds[0];
    const transform=getComputedStyle(world).transform;
    if(!/^matrix\\([^)]+\\)$/.test(transform))refuse();
    const matrix=transform.slice(7,-1).split(',').map(value=>number(Number(value.trim())));
    if(matrix.length!==6)refuse();
    const elements=Array.from(host.querySelectorAll('.canvas-node'));
    const nodeIds=elements.map(element=>element.getAttribute('data-node-id'));
    if(elements.length!==2||new Set(nodeIds).size!==2||!nodeIds.includes(ids.a)||!nodeIds.includes(ids.b))refuse();
    const nodes=elements.map(element=>({id:element.getAttribute('data-node-id'),rect:rect(element),
      selected:element.classList.contains('selected'),
      resize:Array.from(element.querySelectorAll('[data-resize="true"]')).slice(0,2).map(rect)}));
    if(nodes.some(node=>node.resize.length>1))refuse();
    const edgeElements=Array.from(host.querySelectorAll('g[data-edge]'));
    if(edgeElements.length!==1||edgeElements[0].getAttribute('data-edge')!==ids.edge)refuse();
    const saves=Array.from(document.querySelectorAll('.editor-header .save-button'));
    if(saves.length!==1||typeof saves[0].disabled!=='boolean')refuse();
    const visual=window.visualViewport;if(!visual)refuse();
    return {schema:1,scope:'canvas-view-geometry',dispatch_id:dispatch,stage,
      observed_ms:performance.now(),time_origin:performance.timeOrigin,
      window:{width:number(innerWidth),height:number(innerHeight),dpr:number(devicePixelRatio),scroll_x:number(scrollX),scroll_y:number(scrollY)},
      visual:{left:number(visual.offsetLeft),top:number(visual.offsetTop),width:number(visual.width),height:number(visual.height),scale:number(visual.scale)},
      viewport:rect(host),world:{matrix},nodes,edges:[{id:ids.edge,rect:rect(edgeElements[0])}],save:{enabled:!saves[0].disabled}};
  })())`
}
