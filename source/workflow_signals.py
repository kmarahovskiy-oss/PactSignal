"""Conservative, evidence-linked workflow hints; not a general language parser.

These rules surface explicit English phrases for owner review. They do not
authorize work, authenticate senders, or confirm payments.
"""
import re

RULES = (
    ('rescheduled', r'\b(?:reschedule(?:d)? (?:the )?(?:shoot|appointment) to|new appointment date:)\s*([^.!?\n]+)', 'Date changed', 'Use the latest stated date; check calendar availability before confirming.'),
    ('scope_change', r'\b(?:additional (?:retouching|repairs) (?:is|are) not yet approved|extra work has not been approved)\b', 'Extra scope not approved', 'Agree the additional scope and price before doing more work.'),
    ('payer_handoff', r'\b(?:accounts payable|finance team) (?:handles|will handle)\b[^.!?\n]*', 'Different payment contact', 'Follow up with the named payment contact; approval is not a receipt.'),
    ('awaiting_estimate', r'\b(?:awaiting your estimate|please send (?:a |the )?repair estimate)\b', 'Estimate needed', 'Send an estimate for approval; do not treat the repair as authorized.'),
    ('waiting_parts', r'\b(?:waiting for (?:a |the )?(?:replacement )?part|part is on backorder)\b', 'Waiting for a part', 'Check the supplier date and update the customer; completion is not yet confirmed.'),
    ('ready_collection', r'\b(?:ready for collection|ready for pickup)\b', 'Ready for collection', 'Arrange collection and check any remaining payment separately.'),
    ('deposit_carried', r'\bdeposit (?:remains|carries over)\b[^.!?\n]*', 'Deposit carried forward — stated', 'Preserve the stated deposit allocation; verify it against payment evidence.'),
)


def signals(events):
    latest = {}
    for event in events:
        for key, pattern, label, action in RULES:
            match = re.search(pattern, event['body'], re.I)
            if match and re.search(r'\b(?:not|never|no longer)\s+(?:\w+\s+){0,2}$', event['body'][:match.start()], re.I):
                continue
            if match:
                latest[key] = dict(kind=key, label=label, action=action,
                                   quote=match.group(0), event_id=event['id'],
                                   timestamp=event['timestamp'])
    return list(latest.values())


def annotate_threads(threads):
    for thread in threads:
        found = signals(thread['timeline'])
        thread['workflow_signals'] = found
        if found and thread['stage'] not in ('confirmed_received', 'rejected', 'reconciled_receipt'):
            # Operational blockers are a separate axis from financial stage.
            chosen = next((x for key in ('scope_change', 'rescheduled', 'waiting_parts', 'ready_collection', 'awaiting_estimate', 'payer_handoff') for x in found if x['kind'] == key), found[-1])
            thread['next_action'] = chosen['action'] + ' Financial status: ' + thread['stage'].replace('_', ' ') + '.'
    return threads
