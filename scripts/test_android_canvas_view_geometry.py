from copy import deepcopy
import unittest
from android_canvas_view_geometry import A,B,E,MODEL,geometry,calibrate,effect

DISPATCH='278eccde-7cda-42a4-8a3b-132c7bbba901'
def box(left,top,width,height):return dict(left=left,top=top,right=left+width,bottom=top+height,width=width,height=height)
def observed(stage='before',matrix=None):
    matrix=matrix or [1,0,0,1,20,35]
    return dict(schema=1,scope='canvas-view-geometry',dispatch_id=DISPATCH,stage=stage,observed_ms=100 if stage=='before' else 200,time_origin=1700000000000,
        window=dict(width=500,height=900,dpr=3,scroll_x=0,scroll_y=0),
        visual=dict(left=0,top=0,width=500,height=900,scale=1),viewport=box(0,250,500,650),world=dict(matrix=matrix),
        nodes=[dict(id=identifier,rect=box(matrix[4]+x*matrix[0],250+matrix[5]+y*matrix[0],width*matrix[0],height*matrix[0]),
            selected=identifier==A,resize=[]) for identifier,(x,y,width,height) in MODEL.items()],
        edges=[dict(id=E,rect=box(280,360,70,100))],save=dict(enabled=False))
def native(stage='before',phase='view-pan'):
    return dict(schema=1,scope='canvas-view',dispatch_id=DISPATCH,phase=phase,stage=stage,owner='cc.kronecker.opennexus.dev',
        webview=[0,100,1500,2800],clip=[0,850,1500,2800],node=[300,1180,900,1705],recorded_ms=1000)

class ViewGeometry(unittest.TestCase):
    def test_declared_metadata_calibrates_without_accessing_content_or_executing_input(self):
        before=observed();original=deepcopy(before)
        self.assertIs(geometry(before,DISPATCH,'before'),before)
        self.assertEqual(calibrate(before,native(),'view-pan'),3);self.assertEqual(before,original)

    def test_extra_private_fields_wrong_identity_and_changed_original_geometry_are_refused(self):
        for mutate in (lambda value:value.update(content='never export'),lambda value:value['nodes'][0].update(value='never export'),
            lambda value:value['nodes'][1].update(id=A),lambda value:value['edges'][0].update(id=B),
            lambda value:value['nodes'][0]['rect'].update(left=101,right=301),lambda value:value['save'].update(enabled=True)):
            value=observed();mutate(value)
            with self.assertRaises(ValueError):geometry(value,DISPATCH,'before')

    def test_nonfinite_values_browser_zoom_scrolling_and_calibration_mismatch_refuse(self):
        for mutate in (lambda value:value['world']['matrix'].__setitem__(0,float('nan')),
            lambda value:value['world']['matrix'].__setitem__(1,.1),lambda value:value['window'].update(dpr=True),
            lambda value:value['visual'].update(scale=1.2),lambda value:value['window'].update(scroll_y=10)):
            value=observed();mutate(value)
            with self.assertRaises(ValueError):geometry(value,DISPATCH,'before')
        changed=native();changed['webview'][3]+=50
        with self.assertRaises(ValueError):calibrate(observed(),changed,'view-pan')
        changed=native();changed['owner']='another.package'
        with self.assertRaises(ValueError):calibrate(observed(),changed,'view-pan')

    def test_actual_transform_delta_is_measured_in_calibrated_native_pixels(self):
        before=observed();after=observed('after',[1,0,0,1,20+32/3,43])
        result=effect(before,after,native(),native('after'),'view-pan',[600,2400,632,2424])
        self.assertEqual(result['dpr'],3);self.assertFalse(result['actual_input_or_raw_preservation_proved_by_geometry_alone'])
        wrong=observed('after',[1,0,0,1,20+31/3,43])
        with self.assertRaisesRegex(ValueError,'TRANSFORM_EFFECT'):effect(before,wrong,native(),native('after'),'view-pan',[600,2400,632,2424])

    def test_pinch_requires_original_focal_transform_and_no_extra_remaining_finger_pan(self):
        phase='view-pinch';plan=[500,2400,700,2400]
        focal=[200,(2400-100)/3-250]
        matrix=[1.1,0,0,1.1,focal[0]-(focal[0]-20)*1.1,focal[1]-(focal[1]-35)*1.1]
        after=observed('after',matrix)
        effect(observed(),after,native(phase=phase),native('after',phase),phase,plan)
        wrong=matrix.copy();wrong[4]+=8/3
        with self.assertRaises(ValueError):effect(observed(),observed('after',wrong),native(phase=phase),native('after',phase),phase,plan)

    def test_takeover_uses_original_second_finger_pair_and_preserves_its_moving_center(self):
        phase='view-takeover';plan=[400,1400,550,1500];secondary=[1000,2000,1045,2050]
        start=[775/3,((1750-100)/3)-250];end=[797.5/3,((1775-100)/3)-250]
        matrix=[1.1,0,0,1.1,end[0]-(start[0]-20)*1.1,end[1]-(start[1]-35)*1.1]
        effect(observed(),observed('after',matrix),native(phase=phase),native('after',phase),phase,plan,secondary)
        with self.assertRaises(ValueError):effect(observed(),observed('after',matrix),native(phase=phase),native('after',phase),phase,plan,[1000,2000,1050,2050])

    def test_cancel_and_background_require_original_geometry_and_same_observation_context(self):
        for phase in ('view-cancel','view-background'):
            effect(observed(),observed('after'),native(phase=phase),native('after',phase),phase,[400,1400,550,1500])
            after=observed('after');after['time_origin']+=1
            with self.assertRaises(ValueError):effect(observed(),after,native(phase=phase),native('after',phase),phase,[400,1400,550,1500])
            with self.assertRaises(ValueError):effect(observed(),observed('after',[1,0,0,1,21,35]),native(phase=phase),native('after',phase),phase,[400,1400,550,1500])

if __name__=='__main__':unittest.main()
