"""Original frame boundaries only; no instrumentation, ADB or sockets."""
import unittest
from android_original_outcome_status import OriginalReviewStatus, STAGES

NONCE = '00000000-0000-4000-8000-000000000001'


def ready(nonce=NONCE):
    return ('INSTRUMENTATION_STATUS: owned_outcome_ready=' + nonce + '\n').encode()


def stage(value):
    return ('INSTRUMENTATION_STATUS: owned_outcome_stage=' + value + '\n').encode()


END = b'INSTRUMENTATION_STATUS_CODE: 1\n'


class FrameBoundary(unittest.TestCase):
    def test_no_ready_file_read_signal_until_complete_frame_in_either_key_order(self):
        for reverse in (False, True):
            parser = OriginalReviewStatus(NONCE)
            fields = [ready(), stage('old-frozen')]
            if reverse: fields.reverse()
            for line in fields:
                self.assertIsNone(parser.feed(line))
                self.assertEqual(parser.seen, [])
            self.assertEqual(parser.feed(END), 'old-frozen')

    def test_five_exact_once_ordered_frames_do_not_claim_ordinary_UI_success(self):
        parser = OriginalReviewStatus(NONCE)
        for value in STAGES:
            parser.feed(stage(value)); parser.feed(ready())
            self.assertEqual(parser.feed(END), value)
        value = parser.finish()
        self.assertTrue(value['all_review_ready_frames_observed'])
        self.assertFalse(value['original_instrumentation_exit_proven_by_stream'])
        self.assertFalse(value['ordinary_UI_phase_success_claimed'])
        with self.assertRaises(RuntimeError): parser.feed(END)

    def test_eof_with_only_first_key_or_both_keys_never_completes_status(self):
        for fields in ([ready()], [ready(), stage('old-frozen')]):
            parser = OriginalReviewStatus(NONCE)
            for line in fields: parser.feed(line)
            with self.assertRaises(RuntimeError): parser.finish()
            self.assertEqual(parser.seen, [])
            with self.assertRaises(RuntimeError): parser.feed(END)

    def test_foreign_nonce_missing_field_extra_field_duplicate_and_reordered_stage_refused(self):
        cases = [[ready('00000000-0000-4000-8000-000000000002'), stage('old-frozen'), END],
                 [ready(), END], [ready(), stage('old-frozen'), b'INSTRUMENTATION_STATUS: password=PRIVATE\n', END],
                 [ready(), ready(), stage('old-frozen'), END], [ready(), stage('new-frozen'), END]]
        for lines in cases:
            parser = OriginalReviewStatus(NONCE)
            with self.assertRaisesRegex(RuntimeError, '^ORIGINAL_OUTCOME_STATUS_REFUSED$'):
                for line in lines: parser.feed(line)
            self.assertEqual(parser.seen, [])
            self.assertNotIn('PRIVATE', str(parser.__dict__))

    def test_repeated_original_stage_is_not_authorized_twice(self):
        parser = OriginalReviewStatus(NONCE)
        parser.feed(ready()); parser.feed(stage('old-frozen')); parser.feed(END)
        with self.assertRaises(RuntimeError):
            parser.feed(ready()); parser.feed(stage('old-frozen')); parser.feed(END)
        self.assertEqual(parser.seen, ['old-frozen'])

    def test_status_nonone_code_malformed_line_invalid_utf8_and_bounds_refuse(self):
        cases = [[ready(), stage('old-frozen'), b'INSTRUMENTATION_STATUS_CODE: -1\n'],
                 [b'INSTRUMENTATION_STATUS_CODE:x\n'], [b'INSTRUMENTATION_STATUS: x=\xff\n'],
                 [b'x'*4096+b'\n'], [b'partial-without-newline']]
        for lines in cases:
            parser = OriginalReviewStatus(NONCE)
            with self.assertRaises(RuntimeError):
                for line in lines: parser.feed(line)

    def test_unrelated_bounded_standard_frames_and_raw_text_are_discarded(self):
        parser = OriginalReviewStatus(NONCE)
        for line in (b'INSTRUMENTATION_STATUS: id=runner\n', b'INSTRUMENTATION_STATUS_CODE: 1\n',
                     b'private incidental output never retained\n'):
            self.assertIsNone(parser.feed(line))
        self.assertNotIn('private', str(parser.__dict__))
        self.assertEqual(parser.finish()['completed_ready_frames'], [])


if __name__ == '__main__': unittest.main()
