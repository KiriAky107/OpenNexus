"""Remaining property data contracts only; no GUI/model/Save is executed."""
from copy import deepcopy
import unittest
from android_canvas_graph_contract import graph,encoded
import android_canvas_properties as subject

def crlf(value):return encoded(value).replace(b'\n',b'\r\n')

class RemainingPropertyContract(unittest.TestCase):
    def test_remaining_scope_declares_only_the_unaccepted_edge_and_cold(self):
        self.assertEqual(subject.property_steps(True),('properties-seed','properties-open','properties-edge','properties-cold'))
        self.assertEqual(subject.selected_transactions(True),subject.TRANSACTIONS[-1:])
        self.assertEqual(sum(len(v['fields']) for v in subject.selected_transactions(True)),7)
        self.assertEqual(subject.property_steps(False),subject.STEPS)
        self.assertEqual(subject.initial_property_fixture(False),subject.FIXTURE)

    def test_remaining_fixture_is_explicit_prior_updates_without_replaying_their_forms(self):
        raw=subject.initial_property_fixture(True);expected,_=subject.before_transaction('properties-edge')
        self.assertEqual(graph(raw),expected)
        self.assertIn(b'\r\n',raw);self.assertNotIn(b'\n',raw.replace(b'\r\n',b''))
        updated=deepcopy(expected);updated['edges'][0].update(subject.TRANSACTIONS[-1]['fields'])
        self.assertEqual(subject.validate_transaction(raw,crlf(updated),'properties-edge'),subject.TRANSACTIONS[-1])
        for bad in ('remaining',1,None):
            with self.subTest(bad=bad),self.assertRaises(RuntimeError):subject.initial_property_fixture(bad)

    def test_declared_fixture_and_scope_exclude_accepted_actions_and_retain_extensions(self):
        value=graph(subject.FIXTURE)
        self.assertEqual({v['type'] for v in value['nodes']},{'text','file','link','group'})
        self.assertEqual(len(value['nodes']),4);self.assertEqual(len(value['edges']),1)
        self.assertEqual(subject.declared_fields(),14);self.assertEqual(len(subject.TRANSACTIONS),5)
        self.assertTrue(all('vendor' in v for v in value['nodes']+value['edges']))
        self.assertNotIn('properties-move',subject.STEPS);self.assertNotIn('properties-copy',subject.STEPS)

    def test_five_form_transactions_change_only_fourteen_declared_fields_in_original_order(self):
        raw=subject.FIXTURE
        for transaction in subject.TRANSACTIONS:
            value=graph(raw);rows=value['nodes' if transaction['kind']=='node' else 'edges']
            next(v for v in rows if v['id']==transaction['identity']).update(transaction['fields'])
            after=crlf(value)
            self.assertEqual(subject.validate_transaction(raw,after,transaction['phase']),transaction)
            raw=after
        final=graph(raw)
        self.assertEqual(final['vendor-root'],graph(subject.FIXTURE)['vendor-root'])
        self.assertEqual(final['edges'][0]['fromNode'],subject.G)
        self.assertEqual(final['edges'][0]['toNode'],subject.C)

    def test_unrelated_content_identity_geometry_or_extension_changes_are_not_property_success(self):
        before,transaction=subject.before_transaction('properties-text')
        for change in ('text','identity','geometry','root','extension','edge'):
            after=deepcopy(before);next(v for v in after['nodes'] if v['id']==subject.A).update(transaction['fields'])
            if change=='text':after['nodes'][1]['text']='unexpected'
            elif change=='identity':after['nodes'][1]['id']='replacement'
            elif change=='geometry':after['nodes'][2]['x']+=1
            elif change=='root':after['vendor-root']={}
            elif change=='extension':after['nodes'][1]['vendor']={}
            else:after['edges'][0]['color']='unexpected'
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                subject.validate_transaction(crlf(before),crlf(after),'properties-text')

    def test_wrong_phase_missing_original_or_lf_rewrite_is_rejected(self):
        before,item=subject.before_transaction('properties-file');after=deepcopy(before)
        next(v for v in after['nodes'] if v['id']==subject.B).update(item['fields'])
        for raw,phase in ((subject.FIXTURE,'properties-file'),(crlf(before),'properties-edge')):
            with self.subTest(phase=phase),self.assertRaises(RuntimeError):subject.validate_transaction(raw,crlf(after),phase)
        with self.assertRaises(RuntimeError):subject.validate_transaction(crlf(before),encoded(after),'properties-file')
        with self.assertRaises(RuntimeError):subject.before_transaction('accepted-old-case')

    def test_driver_forms_name_only_existing_property_controls_and_bind_their_current_original_values(self):
        forms=[subject.driver_form(v['phase']) for v in subject.TRANSACTIONS]
        self.assertEqual(sum(len(v['fields']) for v in forms),14)
        self.assertEqual(forms[0]['fields'][0],dict(key='label',name='Label',kind='input',before='Original node label',after='Node label updated 😀'))
        self.assertEqual(forms[1]['fields'][1]['name'],'Background image path')
        self.assertEqual(forms[4]['fields'][0],dict(key='fromNode',name='Source node',kind='select',before='Properties node before',after='Group label updated 中文'))
        self.assertEqual(forms[4]['fields'][4]['after'],'Arrow')
        self.assertEqual(forms[4]['fields'][5]['after'],'No arrow')
        self.assertEqual(forms[4]['fields'][-1]['name'],'Color')
        self.assertEqual(forms[4]['edge_label'],'Properties edge original')
        self.assertEqual(forms[3]['target_node_label'],'file: reference-before.md')

if __name__=='__main__':unittest.main()
