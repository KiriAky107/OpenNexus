import unittest
from release_plan import ReleaseError
from check_android_release_manifest import network_resource,network_tree,reviewed_network_tree


class NetworkPolicyBoundaries(unittest.TestCase):
    def test_sdk_tree_preserves_nesting_text_and_boolean_attributes(self):
        value=network_tree("E: network-security-config (line=2)\n    E: base-config (line=3)\n      A: cleartextTrafficPermitted=false\n    E: domain-config (line=4)\n      A: cleartextTrafficPermitted=true\n        E: domain (line=5)\n          A: includeSubdomains=true\n            T: 'synthetic.invalid'\n")
        self.assertEqual(value,['network-security-config',[],'',[
            ['base-config',[('cleartextTrafficPermitted','false')],'',[]],
            ['domain-config',[('cleartextTrafficPermitted','true')],'',[
                ['domain',[('includeSubdomains','true')],'synthetic.invalid',[]]]]]])
        with self.assertRaisesRegex(ReleaseError,'DIFFERS_FROM_PINNED'):
            reviewed_network_tree('E: network-security-config (line=2)\n    E: base-config (line=3)\n      A: cleartextTrafficPermitted=false\n')

    def test_duplicate_attribute_text_namespace_or_added_trust_shape_refused(self):
        trees=[
            'E: base-config (line=1)\n  A: cleartextTrafficPermitted=false\n  A: cleartextTrafficPermitted=true\n',
            "E: domain (line=1)\n  T: 'a.invalid'\n  T: 'b.invalid'\n",
            'E: base-config (line=1)\n  A: android:cleartextTrafficPermitted=false\n',
            'E: network-security-config (line=1)\n  E: trust-anchors (line=2)\n    E: certificates (line=3)\n      A: src="user"\n',
            'E: network-security-config (line=1)\nE: network-security-config (line=2)\n',
        ]
        for text in trees:
            with self.subTest(text=text),self.assertRaises(ReleaseError):reviewed_network_tree(text)

    def test_real_resource_id_resolves_one_default_xml_even_after_resource_shrinking(self):
        text='Package name=owned id=7f\n    resource 0x7f120000 xml/file_paths\n      () (file) res/8K.xml type=XML\n    resource 0x7f120001 xml/network_security_config\n      () (file) res/8G.xml type=XML\n'
        self.assertEqual(network_resource(text,'@0x7f120001'),'res/8G.xml')
        for bad in [text.replace('xml/network_security_config','xml/unreviewed'),
                    text+'    resource 0x7f120001 xml/network_security_config\n      () (file) res/other.xml type=XML\n',
                    text+'      (v24) (file) res/other.xml type=XML\n',text.replace('res/8G.xml','res/../raw.xml')]:
            with self.subTest(text=bad),self.assertRaises(ReleaseError):network_resource(bad,'@0x7f120001')
        for ref in ('@xml/network_security_config','@0x7f120001\n','@0x01120001'):
            with self.subTest(ref=ref),self.assertRaises(ReleaseError):network_resource(text,ref)


if __name__=='__main__':unittest.main()
