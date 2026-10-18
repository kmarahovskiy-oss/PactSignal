"""Synthetic development checks; not an independent human usability study."""
import json
import unittest
from pathlib import Path
from pactsignal import aggregate_thread, analyze, IntentModel, verify_report_integrity
from workflow_signals import signals

ROOT = Path(__file__).resolve().parent.parent

def case(report, fragment):
    return next(t for t in report['threads'] if fragment in t['timeline'][0]['subject'])

class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.studio = json.loads((ROOT / 'dist/report.json').read_text())
        cls.repair = json.loads((ROOT / 'dist/repair-report.json').read_text())

    def test_reschedule_preserves_price_and_no_invented_deposit(self):
        t = case(self.studio, 'WS-22')
        self.assertEqual(t['agreed_by_currency'], {'EUR':300})
        self.assertEqual(t['confirmed_received_by_currency'], {})
        self.assertIn('24 October 2026', t['workflow_signals'][0]['quote'])
        self.assertEqual({s['kind'] for s in t['workflow_signals']}, {'rescheduled','deposit_carried'})

    def test_unapproved_scope_does_not_inflate_agreement(self):
        t = case(self.studio, 'MD-09')
        self.assertEqual(t['agreed_by_currency'], {'USD':240})
        self.assertIn('before doing more work', t['next_action'])

    def test_payment_claim_never_confirms_money(self):
        t = case(self.studio, 'FF-18')
        self.assertEqual(t['stage'], 'payment_notice')
        self.assertEqual(t['confirmed_received_by_currency'], {})

    def test_later_agreement_reopens(self):
        t = case(self.studio, 'OB-07')
        self.assertEqual(t['stage'], 'agreed')
        self.assertEqual([e['status'] for e in t['timeline']], ['rejection','agreement'])

    def test_ambiguous_receipt_cannot_close_either_order(self):
        for order in ['MG-101', 'MG-102']:
            t = case(self.studio, order)
            self.assertEqual(t['stage'], 'accepted')
            self.assertEqual(t['confirmed_received_by_currency'], {})
        receipt = case(self.studio, 'Settled 210.00')
        self.assertEqual(receipt['stage'], 'receipt_unallocated')
        self.assertIn('ambiguous', receipt['next_action'])
        self.assertEqual(self.studio['summary']['confirmed_received_by_currency'], {'EUR':660})

    def test_payer_is_distinct_from_approver(self):
        t = case(self.studio, 'CP-41')
        self.assertEqual(t['workflow_signals'][0]['kind'], 'payer_handoff')
        self.assertEqual(t['stage'], 'accepted')

    def test_estimate_is_not_order(self):
        t = case(self.repair, 'RW-11')
        self.assertEqual(t['agreed_by_currency'], {})
        self.assertEqual(t['stage'], 'open')

    def test_waiting_part_and_collection_are_not_failed_payments(self):
        for order, kind in [('RW-12','waiting_parts'),('RW-13','ready_collection')]:
            t = case(self.repair, order)
            self.assertEqual(t['stage'], 'agreed')
            self.assertEqual(t['workflow_signals'][0]['kind'], kind)
            self.assertNotIn('payment_failure_or_reversal_detected', t['risks'])

    def test_completed_repair_has_one_receipt(self):
        t = case(self.repair, 'RW-14')
        self.assertEqual(t['stage'], 'confirmed_received')
        self.assertEqual(t['confirmed_received_by_currency'], {'EUR':60})
        self.assertEqual(len(t['linked_receipts']), 1)

    def test_workflow_quotes_are_real_substrings(self):
        for report in [self.studio, self.repair]:
            self.assertTrue(verify_report_integrity(report)['verified'])
            for t in report['threads']:
                for signal in t['workflow_signals']:
                    event = next(e for e in t['timeline'] if e['id']==signal['event_id'])
                    self.assertIn(signal['quote'], event['body'])

    def test_negations_do_not_trigger_workflow_signals(self):
        self.assertEqual(signals([dict(id='x',timestamp='2026-10-17',body='The vacuum is not ready for collection. We are not waiting for a part. Additional repairs are approved.')]), [])

    def test_later_rejection_stays_closed(self):
        model=IntentModel()
        payload=[dict(id=str(i),thread_id='t',timestamp=f'2026-10-{12+i}T09:00:00Z',sender='Fictional <owner@example.test>',body=b) for i,b in enumerate(['We approve your quote for 100 EUR.', 'We will not proceed.'])]
        self.assertEqual(analyze(payload,model)['threads'][0]['stage'],'rejected')

if __name__ == '__main__':
    unittest.main()
