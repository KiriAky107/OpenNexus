import copy
import unittest
from android_canvas_graph_contract import *


def node(identity,kind='text',value='原文 😀'):
    item={'id':identity,'type':kind,'x':20,'y':30,'width':300,'height':180,'vendor-node':{'keep':[True,None,17]}}
    item[{'text':'text','file':'file','link':'url','group':'label'}[kind]]=value
    return item


class GraphContract(unittest.TestCase):
    def setUp(self):
        self.base={'nodes':[node('a'),node('b','link','https://example.invalid/owned')],
            'edges':[{'id':'ab','fromNode':'a','toNode':'b','label':'原连接 😀','vendor-edge':{'keep':17}}],
            'vendor-root':{'opaque':['保留',True,None]}}
    def raw(self,value=None):return encoded(self.base if value is None else value)
    def rejects(self,action):
        with self.assertRaises((RuntimeError,ValueError,TypeError,KeyError,UnicodeError)):action()
    def test_original_decode_and_extensions(self):self.assertEqual(graph(self.raw()),self.base)
    def test_duplicate_node_refuses(self):
        x=copy.deepcopy(self.base);x['nodes'].append(node('a'));self.rejects(lambda:graph(self.raw(x)))
    def test_dangling_edge_refuses(self):
        x=copy.deepcopy(self.base);x['edges'][0]['toNode']='other';self.rejects(lambda:graph(self.raw(x)))
    def test_duplicate_edge_refuses(self):
        x=copy.deepcopy(self.base);x['edges'].append(copy.deepcopy(x['edges'][0]));self.rejects(lambda:graph(self.raw(x)))
    def test_nonfinite_and_invalid_utf8_refuse(self):
        for x in [b'{"nodes":[{"id":"a","type":"text","x":NaN,"y":0,"width":300,"height":180}],"edges":[]}',b'\xff']:
            self.rejects(lambda:graph(x))
    def test_boolean_geometry_refuses(self):
        x=copy.deepcopy(self.base);x['nodes'][0]['x']=True;self.rejects(lambda:graph(self.raw(x)))
    def test_each_node_kind_requires_exact_content(self):
        for kind,value in [('text','中文输入 😀'),('file','资料/原笔记😀.md'),('link','https://example.invalid/owned'),('group','实际分组 😀')]:
            x=copy.deepcopy(self.base)
            if kind=='group':x['nodes'].insert(0,node('new',kind,value))
            else:x['nodes'].append(node('new',kind,value))
            self.assertEqual(added_node(self.raw(),self.raw(x),kind,value),'new')
            self.rejects(lambda:added_node(self.raw(),self.raw(x),kind,'different'))
    def test_append_preserves_original_order(self):
        x=copy.deepcopy(self.base);x['nodes'].insert(0,node('new'));self.rejects(lambda:added_node(self.raw(),self.raw(x),'text','原文 😀'))
    def test_group_is_prepended_below_existing_nodes(self):
        x=copy.deepcopy(self.base);x['nodes'].append(node('new','group','group'))
        self.rejects(lambda:added_node(self.raw(),self.raw(x),'group','group'))
    def test_duplicate_root_or_opaque_extension_JSON_keys_refuse(self):
        for raw in [b'{"nodes":[],"nodes":[],"edges":[]}',b'{"nodes":[],"edges":[],"vendor":{"keep":1,"keep":2}}']:
            self.rejects(lambda:graph(raw))
    def test_original_extension_drop_refuses(self):
        x=copy.deepcopy(self.base);x['nodes'].append(node('new'));x.pop('vendor-root');self.rejects(lambda:added_node(self.raw(),self.raw(x),'text','原文 😀'))
    def test_exact_node_property_update(self):
        x=copy.deepcopy(self.base);x['nodes'][0].update(width=420,height=240,color='4');self.assertEqual(edited_node(self.raw(),self.raw(x),'a',{'width':420,'height':240,'color':'4'}),'a')
    def test_other_node_edit_refuses(self):
        x=copy.deepcopy(self.base);x['nodes'][0]['x']=99;x['nodes'][1]['url']='https://other.invalid';self.rejects(lambda:edited_node(self.raw(),self.raw(x),'a',{'x':99}))
    def test_identity_or_type_edit_refuses(self):
        for field in ['id','type']:self.rejects(lambda:edited_node(self.raw(),self.raw(),'a',{field:'new'}))
    def test_node_extension_drop_refuses(self):
        x=copy.deepcopy(self.base);x['nodes'][0].pop('vendor-node');x['nodes'][0]['x']=99;self.rejects(lambda:edited_node(self.raw(),self.raw(x),'a',{'x':99}))
    def test_edge_creation_and_direction(self):
        x=copy.deepcopy(self.base);x['edges'].append({'id':'ba','fromNode':'b','toNode':'a','label':''});self.assertEqual(added_edge(self.raw(),self.raw(x),'b','a'),'ba')
        self.rejects(lambda:added_edge(self.raw(),self.raw(x),'a','b'))
    def test_edge_properties_keep_identity_extension(self):
        fields={'label':'连线更新 😀','fromSide':'right','toSide':'left','fromEnd':'none','toEnd':'arrow','color':'3'}
        x=copy.deepcopy(self.base);x['edges'][0].update(fields);self.assertEqual(edited_edge(self.raw(),self.raw(x),'ab',fields),'ab')
    def test_edge_property_other_changes_refuse(self):
        x=copy.deepcopy(self.base);x['edges'][0]['label']='new';x['nodes'][0]['x']=90;self.rejects(lambda:edited_edge(self.raw(),self.raw(x),'ab',{'label':'new'}))
    def test_edge_identity_edit_refuses(self):self.rejects(lambda:edited_edge(self.raw(),self.raw(),'ab',{'id':'new'}))
    def copied(self):
        x=copy.deepcopy(self.base)
        for n in self.base['nodes']:x['nodes'].append(dict(n,id=n['id']+'copy',x=n['x']+40,y=n['y']+40))
        x['edges'].append(dict(self.base['edges'][0],id='abcopy',fromNode='acopy',toNode='bcopy'))
        return x
    def test_two_node_copy_preserves_internal_edge_and_extensions(self):
        self.assertEqual(pasted_selection(self.raw(),self.raw(self.copied()),['a','b']),{'a':'acopy','b':'bcopy'})
    def test_copy_wrong_offset_refuses(self):
        x=self.copied();x['nodes'][-1]['x']+=1;self.rejects(lambda:pasted_selection(self.raw(),self.raw(x),['a','b']))
    def test_copy_missing_edge_refuses(self):
        x=self.copied();x['edges'].pop();self.rejects(lambda:pasted_selection(self.raw(),self.raw(x),['a','b']))
    def test_copy_original_identity_or_property_change_refuses(self):
        x=self.copied();x['nodes'][0]['text']='lost';self.rejects(lambda:pasted_selection(self.raw(),self.raw(x),['a','b']))
    def test_copy_duplicate_or_unknown_selection_refuses(self):
        for selection in [['a','a'],['a','missing'],[]]:self.rejects(lambda:pasted_selection(self.raw(),self.raw(self.copied()),selection))
    def test_delete_node_cascades_only_related_edge(self):
        x=copy.deepcopy(self.base);x['nodes']=x['nodes'][1:];x['edges']=[];removed_selection(self.raw(),self.raw(x),['a'])
    def test_delete_edge_preserves_nodes(self):
        x=copy.deepcopy(self.base);x['edges']=[];removed_selection(self.raw(),self.raw(x),[],'ab')
    def test_delete_unrelated_node_refuses(self):
        x=copy.deepcopy(self.base);x['nodes']=[];x['edges']=[];self.rejects(lambda:removed_selection(self.raw(),self.raw(x),['a']))
    def test_missing_delete_original_refuses(self):self.rejects(lambda:removed_selection(self.raw(),self.raw(),['missing']))
    def test_undo_redo_and_view_only_require_original_bytes(self):
        raw=self.raw();identical_original_bytes(raw,raw)
        self.rejects(lambda:identical_original_bytes(raw,json.dumps(self.base,ensure_ascii=False,indent=2).encode()))
    def test_no_counts_only_pass(self):
        x=copy.deepcopy(self.base);x['nodes'][0]['text']='same count different original';self.rejects(lambda:identical_original_bytes(self.raw(),self.raw(x)))


if __name__=='__main__':unittest.main()
