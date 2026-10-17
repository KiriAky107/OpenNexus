"""Owned pair input bytes, not a provider actor or UI execution claim."""
import json

from android_runtime_probe import DRAFT,IMAGE_BYTES
from android_product_pair_stages import IMAGE_PATH

TEXT_NODE='owned-pair-text'
DESKTOP_NOTE=('# Desktop \u5df2\u786e\u8ba4\u4fee\u6539 \U0001f600\n\n'
              '\u6765\u81ea\u5b9e\u9645\u684c\u9762\u6e90\u7801\u7f16\u8f91\u5668\u3002\n\n'
              '!['+'\u539f\u56fe'+']('+IMAGE_PATH+')\n').encode()
ANDROID_NOTE=('# Android \u5df2\u786e\u8ba4\u56de\u4f20 \U0001f600\n\n'
              '\u4fdd\u7559\u540c\u4e00\u6587\u4ef6\u8eab\u4efd\u4e0e\u539f\u56fe\u3002\n\n'
              '!['+'\u539f\u56fe'+']('+IMAGE_PATH+')\n').encode()
DESKTOP_CANVAS_TEXT='Desktop \u753b\u5e03\u5df2\u786e\u8ba4 \U0001f600'
ANDROID_CANVAS_TEXT='Android \u753b\u5e03\u56de\u4f20\u5df2\u786e\u8ba4 \U0001f600'
DESKTOP_POSITION=(48,64)
ANDROID_POSITION=(96,128)
CANVAS_SEED=(json.dumps({'nodes':[
    {'id':TEXT_NODE,'type':'text','x':0,'y':0,'width':300,'height':180,'text':'Owned pair \u539f\u59cb\u753b\u5e03 \U0001f600','extension':{'keep':True}},
    {'id':'owned-pair-image','type':'file','x':0,'y':220,'width':320,'height':240,'file':IMAGE_PATH}],
    'edges':[],'vendor':{'original':'\u672a\u77e5\u6269\u5c55\u5c5e\u6027\u4e0e CRLF','nested':[1,True,{'keep':'original'}]}},
    ensure_ascii=False,indent=2).replace('\n','\r\n')+'\r\n').encode()
