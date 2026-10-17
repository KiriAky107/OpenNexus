"""Frozen tool wheel export only; no download or installation in these cases."""
from copy import deepcopy
import unittest

from android_pair_crypto_requirements import EXPECTED,requirement_lines


class CryptoOverlay(unittest.TestCase):
    def setUp(self):
        self.lock={'package':[{'name':name,'version':version,'wheels':[{
            'url':'https://files.pythonhosted.org/packages/aa/bb/'+name+'-'+version+'-'+tag,'hash':'sha256:'+digest}]}
            for name,(version,tag,digest) in EXPECTED.items()]}

    def test_only_three_exact_frozen_binary_wheels_and_original_hashes_are_exported(self):
        lines=requirement_lines(self.lock).splitlines();self.assertEqual(len(lines),3)
        self.assertTrue(all('.whl --hash=sha256:' in line for line in lines))
        for kind in ('version','hash','duplicate','source'):
            changed=deepcopy(self.lock);package=changed['package'][0]
            if kind=='version':package['version']='unknown'
            elif kind=='hash':package['wheels'][0]['hash']='sha256:'+'0'*64
            elif kind=='duplicate':changed['package'].append(deepcopy(package))
            else:package['wheels'][0]['url']=package['wheels'][0]['url'].replace('files.pythonhosted.org','unrelated.invalid')
            with self.subTest(kind=kind),self.assertRaises(ValueError):requirement_lines(changed)


if __name__=='__main__':unittest.main()
