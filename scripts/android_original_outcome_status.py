"""Bounded original instrumentation frames; partial status never authorizes IO."""
import re
import uuid


STAGES = ('old-frozen', 'stale-confirm-intent', 'known-stale-rejection',
          'new-frozen', 'interrupt-confirm-intent')
OUTCOME_STAGES = ('start','input','old-frozen','concurrent-write','stale-confirm','known-rejection',
                  'native-absence','new-frozen','compiled-renderer','debugger-armed','interrupt-confirm','original-paused',
                  'completed-native','stop-original-app','close-original-debugger','stop-original-driver',
                  'cold-original','copy-cycle','cold-draft','s3-originals','complete')


def need(value):
    if not value:
        raise RuntimeError('ORIGINAL_OUTCOME_STATUS_REFUSED')


class OriginalReviewStatus:
    def __init__(self, nonce):
        need(type(nonce) is str and str(uuid.UUID(nonce)) == nonce)
        self.nonce = nonce
        self.fields = {}
        self.seen = []
        self.bytes = 0
        self.frames = 0
        self.closed = False
        self.failed = False

    def feed(self, line):
        need(not self.closed and not self.failed)
        try:
            need(type(line) is bytes and 0 < len(line) <= 4096 and line.endswith(b'\n'))
            self.bytes += len(line)
            need(self.bytes <= 1024**2)
            text = line.rstrip(b'\r\n').decode('utf-8', 'strict')
            prefix = 'INSTRUMENTATION_STATUS: '
            end = 'INSTRUMENTATION_STATUS_CODE: '
            if text.startswith(prefix):
                body = text[len(prefix):]
                need('=' in body)
                key, value = body.split('=', 1)
                need(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', key) and key not in self.fields)
                need(len(self.fields) < 16)
                self.fields[key] = value
            elif text.startswith(end):
                self.frames += 1
                need(self.frames <= 32 and re.fullmatch(r'-?[0-9]{1,3}', text[len(end):]))
                owned = any(key.startswith('owned_outcome_') for key in self.fields)
                if owned:
                    need(set(self.fields) == {'owned_outcome_ready', 'owned_outcome_stage'}
                         and text[len(end):] == '1' and self.fields['owned_outcome_ready'] == self.nonce
                         and len(self.seen) < len(STAGES)
                         and self.fields['owned_outcome_stage'] == STAGES[len(self.seen)])
                    stage = self.fields['owned_outcome_stage']
                    self.fields = {}
                    self.seen.append(stage)
                    return stage
                self.fields = {}
            elif text.startswith('INSTRUMENTATION_STATUS_CODE:') or text.startswith('INSTRUMENTATION_STATUS:'):
                need(False)
            # All other output is bounded but discarded. Never export a raw
            # instrumentation error, credentials, source contents or a stack.
            return None
        except Exception:
            self.failed = True
            self.fields = {}
            raise RuntimeError('ORIGINAL_OUTCOME_STATUS_REFUSED') from None

    def finish(self):
        valid = not self.closed and not self.failed and not self.fields
        self.closed = True
        need(valid)
        return {'nonce': self.nonce, 'completed_ready_frames': list(self.seen),
                'all_review_ready_frames_observed': self.seen == list(STAGES),
                'original_instrumentation_exit_proven_by_stream': False,
                'ordinary_UI_phase_success_claimed': False, 'raw_stream_exported': False}
