import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import socket
import base64
from email.message import EmailMessage
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError

from pactsignal import (
    IntentModel, LiveMailboxContext, Message, analyze, analyze_message, case_evidence_packet, evaluate, extract_amounts,
    evaluate_metamorphic, render_html, validate_messages, verify_report_integrity,
)
from mailbox_ingest import MailboxStore, parse_rfc822, sync_source
from gmail_connector import GmailReadOnlyConnector, GMAIL_READONLY_SCOPE
from settlement_ingest import read_settlements_csv

ROOT = Path(__file__).resolve().parent


def msg(ident="1", thread="t", body="We accept your proposal for 50 USD.", **extra):
    return {"id": ident, "thread_id": thread, "timestamp": extra.pop("timestamp", "2026-10-16T09:00:00Z"),
            "sender": extra.pop("sender", "demo@example.test"), "body": body, **extra}


class PactSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = IntentModel()

    def test_training_size(self):
        self.assertEqual(self.model.fit_examples, 120)

    def test_evaluation_is_reproducible(self):
        a, b = evaluate(self.model), evaluate(IntentModel())
        self.assertEqual(a, b)
        self.assertEqual(a["stress_examples"], 40)
        self.assertEqual(a["rules_only_correct"], 38)
        self.assertEqual(a["model_only_correct"], 26)
        self.assertEqual(a["guarded_pipeline_correct"], 40)
        self.assertEqual(a["wrong_non_abstained"], 0)
        self.assertEqual(a["metamorphic_checks"], 48)
        self.assertEqual(a["metamorphic_passed"], 48)

    def test_metamorphic_suite_is_reproducible(self):
        result = evaluate_metamorphic(self.model)
        self.assertEqual(result["checks"], 48)
        self.assertEqual(result["failures"], [])

    def test_amount_prefix_code(self):
        self.assertEqual(extract_amounts("USD 1,250.50")[0]["value"], 1250.5)

    def test_amount_suffix_code(self):
        self.assertEqual(extract_amounts("fee 75 EUR")[0]["currency"], "EUR")

    def test_amount_currency_symbols(self):
        out = extract_amounts("$20, €30, £40 and ₴500")
        self.assertEqual([(x["currency"], x["value"]) for x in out],
                         [("USD", 20), ("EUR", 30), ("GBP", 40), ("UAH", 500)])

    def test_decimal_comma(self):
        self.assertEqual(extract_amounts("EUR 10,50")[0]["value"], 10.5)

    def test_no_bare_number_as_money(self):
        self.assertEqual(extract_amounts("Delivered 3 files on 12 October"), [])

    def test_validate_requires_list(self):
        with self.assertRaises(ValueError): validate_messages({})

    def test_validate_requires_all_fields(self):
        with self.assertRaises(ValueError): validate_messages([{"id": "x"}])

    def test_validate_rejects_duplicate_ids(self):
        with self.assertRaises(ValueError): validate_messages([msg(), msg()])

    def test_validate_rejects_bad_timestamp(self):
        with self.assertRaises(ValueError): validate_messages([msg(timestamp="yesterday")])

    def test_validate_rejects_unknown_confirmation(self):
        with self.assertRaises(ValueError): validate_messages([msg(confirmation="trust_me")])

    def test_failed_payment_guard(self):
        m = Message(**validate_messages([msg(body="Payment of 50 USD failed and no funds arrived.")])[0].__dict__)
        self.assertEqual(analyze_message(m, self.model)["status"], "payment_failure")

    def test_failed_payment_outranks_acceptance_in_mixed_message(self):
        m = validate_messages([msg(body="The work is accepted, but the 180 GBP payout failed and was returned unpaid.")])[0]
        self.assertEqual(analyze_message(m, self.model)["status"], "payment_failure")

    def test_decision_basis_exposes_guard(self):
        m = validate_messages([msg(body="Payment of 50 USD failed and no funds arrived.")])[0]
        out = analyze_message(m, self.model)
        self.assertEqual(out["decision_basis"], "deterministic_guard")

    def test_model_explanation_has_features(self):
        prediction = self.model.predict("The available reward is 90 USD for the winner.")
        self.assertTrue(prediction["top_supporting_features"])
        self.assertTrue(all(x["contribution"] > 0 for x in prediction["top_supporting_features"]))

    def test_future_payment_guard(self):
        m = validate_messages([msg(body="We will pay 50 USD tomorrow.")])[0]
        self.assertEqual(analyze_message(m, self.model)["status"], "payment_promise")

    def test_offer_not_mistaken_for_payment(self):
        m = validate_messages([msg(body="First accepted correction earns $20.")])[0]
        self.assertEqual(analyze_message(m, self.model)["status"], "offer")

    def test_result_acceptance_is_not_a_new_offer(self):
        m = validate_messages([msg(body="I reproduced the bug and fixed it. The commit credits you.")])[0]
        self.assertEqual(analyze_message(m, self.model)["status"], "acceptance")

    def test_final_work_acceptance_is_recognized(self):
        m = validate_messages([msg(body="We accepted the final work. Everything is ready for launch.")])[0]
        self.assertEqual(analyze_message(m, self.model)["status"], "acceptance")

    def test_accepted_unpaid_work_is_actionable(self):
        out = analyze([msg(body="We accepted your correction and published it.")], self.model)
        self.assertEqual(out["threads"][0]["stage"], "accepted")
        self.assertIn("accepted_work_not_paid", out["threads"][0]["risks"])

    def test_rejection_overrides_budget_word(self):
        m = validate_messages([msg(body="Budget approval was denied; stop work.")])[0]
        self.assertEqual(analyze_message(m, self.model)["status"], "rejection")

    def test_explanation_is_admin(self):
        m = validate_messages([msg(body="Can you explain what payment received means in the guide?")])[0]
        self.assertEqual(analyze_message(m, self.model)["status"], "admin")

    def test_eligibility_reply_is_admin_not_offer(self):
        m = validate_messages([msg(body="Ukrainian participants are eligible for rewards and two free sandbox accounts.")])[0]
        self.assertEqual(analyze_message(m, self.model)["status"], "admin")

    def test_source_quote_retained(self):
        body = "Hello. We accept your proposal for 50 USD. Regards."
        m = validate_messages([msg(body=body)])[0]
        out = analyze_message(m, self.model)
        self.assertIn("We accept your proposal for 50 USD.", [x["quote"] for x in out["evidence"]])

    def test_hash_is_deterministic(self):
        m = validate_messages([msg()])[0]
        self.assertEqual(analyze_message(m, self.model)["text_sha256"], analyze_message(m, self.model)["text_sha256"])

    def test_notice_without_confirmation_not_income(self):
        out = analyze([msg(body="Payment completed successfully for 50 USD.")], self.model)
        self.assertEqual(out["summary"]["confirmed_received_by_currency"], {})
        self.assertIn("payment_notice_requires_independent_confirmation", out["threads"][0]["risks"])

    def test_confirmation_enters_explicit_total(self):
        out = analyze([msg(body="Payment completed successfully for 50 USD.", confirmation="user_confirmed_received")], self.model)
        self.assertEqual(out["summary"]["confirmed_received_by_currency"], {"USD": 50.0})

    def test_confirmation_on_offer_does_not_enter_total(self):
        out = analyze([msg(body="Our budget is 50 USD.", confirmation="user_confirmed_received")], self.model)
        self.assertEqual(out["summary"]["confirmed_received_by_currency"], {})

    def test_currencies_are_never_summed_together(self):
        out = analyze([
            msg("a", "x", "Payment completed successfully for 50 USD.", confirmation="user_confirmed_received"),
            msg("b", "y", "Payment completed successfully for 40 EUR.", timestamp="2026-10-16T10:00:00Z", confirmation="provider_notice_authenticated"),
        ], self.model)
        self.assertEqual(out["summary"]["confirmed_received_by_currency"], {"EUR": 40.0, "USD": 50.0})

    def test_authenticated_provider_receipt_links_across_threads(self):
        out = analyze([
            msg("deal", "client-thread", "We approve your quote for 10 EUR. Please proceed.",
                sender="Klaus Example <klaus@example.test>"),
            msg("receipt", "provider-thread", "You received 10 EUR from Klaus Example.",
                sender="PayPal <service@paypal.example>", timestamp="2026-10-17T10:00:00Z",
                confirmation="provider_notice_authenticated"),
        ], self.model)
        client = next(t for t in out["threads"] if t["thread_id"] == "client-thread")
        provider = next(t for t in out["threads"] if t["thread_id"] == "provider-thread")
        self.assertEqual(client["stage"], "confirmed_received")
        self.assertEqual(client["confirmed_received_by_currency"], {"EUR": 10.0})
        self.assertEqual(provider["stage"], "reconciled_receipt")

    def test_ambiguous_provider_receipt_is_not_linked(self):
        out = analyze([
            msg("a", "one", "We approve your quote for 10 EUR. Please proceed.", sender="Alex Example <a@example.test>"),
            msg("b", "two", "We approve your quote for 10 EUR. Please proceed.", sender="Alex Example <b@example.test>"),
            msg("receipt", "provider", "You received 10 EUR from Alex Example.",
                sender="PayPal <service@paypal.example>", timestamp="2026-10-17T10:00:00Z",
                confirmation="provider_notice_authenticated"),
        ], self.model)
        provider = next(t for t in out["threads"] if t["thread_id"] == "provider")
        self.assertIn("authenticated_receipt_not_uniquely_linked", provider["risks"])
        self.assertFalse(any(t.get("linked_receipts") for t in out["threads"]))

    def test_delivery_without_agreement_is_flagged(self):
        out = analyze([msg(body="The final files have been delivered for review.")], self.model)
        self.assertIn("delivery_without_detected_price_agreement", out["threads"][0]["risks"])

    def test_promise_without_receipt_is_flagged(self):
        out = analyze([msg(body="We will pay 50 USD tomorrow.")], self.model)
        self.assertIn("payment_promised_but_not_confirmed_received", out["threads"][0]["risks"])

    def test_relative_promise_due_date_is_normalized(self):
        out = analyze([msg(body="We will pay 50 USD tomorrow.", timestamp="2026-10-16T10:00:00Z")], self.model)
        self.assertEqual(out["threads"][0]["promised_due_at"], "2026-10-17")

    def test_explicit_promise_due_date_is_normalized(self):
        out = analyze([msg(body="We will pay 50 USD on 2026-10-22.")], self.model)
        self.assertEqual(out["threads"][0]["promised_due_at"], "2026-10-22")

    def test_amount_mismatch_is_flagged(self):
        out = analyze([
            msg("a", body="We accept your proposal for 50 USD."),
            msg("b", body="Payment completed successfully for 45 USD.", timestamp="2026-10-16T10:00:00Z", confirmation="user_confirmed_received"),
        ], self.model)
        self.assertIn("confirmed_USD_amount_differs_from_detected_agreement", out["threads"][0]["risks"])
        self.assertEqual(out["threads"][0]["stage"], "payment_issue")

    def test_receipt_must_match_amount_currency_and_chronology(self):
        for amount, timestamp in [("5 EUR", "2026-10-17T10:00:00Z"), ("10 USD", "2026-10-17T10:00:00Z"), ("10 EUR", "2026-10-15T10:00:00Z")]:
            with self.subTest(amount=amount, timestamp=timestamp):
                out = analyze([
                    msg("deal", "client", "We approve your quote for 10 EUR. Please proceed.", sender="Klaus Example <klaus@example.test>", timestamp="2026-10-16T10:00:00Z"),
                    msg("receipt", "provider", f"You received {amount} from Klaus Example.", sender="PayPal <service@paypal.example>", timestamp=timestamp, confirmation="provider_notice_authenticated"),
                ], self.model)
                client = next(t for t in out["threads"] if t["thread_id"] == "client")
                self.assertFalse(client.get("linked_receipts"))
                self.assertNotEqual(client["stage"], "confirmed_received")

    def test_wrong_currency_in_same_thread_requires_review(self):
        out = analyze([
            msg("a", body="We accept your proposal for 50 USD."),
            msg("b", body="Payment completed successfully for 50 EUR.", timestamp="2026-10-17T10:00:00Z", confirmation="user_confirmed_received"),
        ], self.model)
        self.assertEqual(out["threads"][0]["stage"], "payment_issue")

    def test_timeline_is_chronological(self):
        out = analyze([
            msg("late", body="The final files have been delivered for review.", timestamp="2026-10-17T10:00:00Z"),
            msg("early", body="We accept your proposal for 50 USD.", timestamp="2026-10-16T10:00:00Z"),
        ], self.model)
        self.assertEqual([x["id"] for x in out["threads"][0]["timeline"]], ["early", "late"])

    def test_integrity_chain_verifies(self):
        out = analyze([
            msg("a", body="We accept your proposal for 50 USD."),
            msg("b", body="The final files have been delivered.", timestamp="2026-10-16T10:00:00Z"),
        ], self.model)
        self.assertTrue(out["integrity_check"]["verified"])
        self.assertTrue(verify_report_integrity(out)["verified"])

    def test_integrity_chain_links_events(self):
        out = analyze([
            msg("a", body="We accept your proposal for 50 USD."),
            msg("b", body="The final files have been delivered.", timestamp="2026-10-16T10:00:00Z"),
        ], self.model)
        timeline = out["threads"][0]["timeline"]
        self.assertEqual(timeline[1]["integrity"]["previous_event_sha256"], timeline[0]["integrity"]["event_chain_sha256"])

    def test_integrity_chain_detects_tampering(self):
        out = analyze([msg(body="We accept your proposal for 50 USD.")], self.model)
        out["threads"][0]["timeline"][0]["status"] = "payment_notice"
        self.assertFalse(verify_report_integrity(out)["verified"])

    def test_integrity_chain_is_input_order_independent(self):
        early = msg("a", body="We accept your proposal for 50 USD.")
        late = msg("b", body="The final files have been delivered.", timestamp="2026-10-16T10:00:00Z")
        first = analyze([early, late], self.model)["threads"][0]["integrity"]["chain_head_sha256"]
        second = analyze([late, early], self.model)["threads"][0]["integrity"]["chain_head_sha256"]
        self.assertEqual(first, second)

    def test_html_escapes_source_fields(self):
        out = analyze([msg(sender="<script>alert(1)</script>")], self.model)
        page = render_html(out)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_html_displays_integrity_status(self):
        page = render_html(analyze([msg()], self.model))
        self.assertIn("Integrity chain:", page)
        self.assertIn("verified locally", page)

    def test_owner_view_leads_with_decisions(self):
        page = render_html(analyze([msg()], self.model))
        self.assertIn("What needs your attention", page)
        self.assertIn("OWNER CONTROL ROOM", page)
        self.assertIn("One decision queue", page)

    def test_owner_view_uses_plain_language_status(self):
        page = render_html(analyze([msg(body="We will pay 50 USD tomorrow.")], self.model))
        self.assertIn("Promised, not received", page)
        self.assertNotIn(">payment_promised<", page)

    def test_technical_trace_is_progressively_disclosed(self):
        page = render_html(analyze([msg()], self.model))
        self.assertIn("Timeline &amp; technical evidence", page)
        self.assertIn("Evidence &amp; model trace", page)

    def test_at_risk_summary_counts_unresolved_payment(self):
        out = analyze([
            msg("p", "promised", "We will pay 50 USD tomorrow."),
            msg("a", "agreed", "We approve your quote for 90 USD. Please proceed."),
        ], self.model)
        self.assertEqual(out["summary"]["at_risk_threads"], 1)

    def test_owner_metrics_split_reply_payment_active_and_closed(self):
        out = analyze([
            msg("a1", "reply", "We approve your quote for 90 USD. Please proceed."),
            msg("a2", "reply", "The final files have been delivered for review.", timestamp="2026-10-16T10:00:00Z"),
            msg("b1", "payment", "We accepted your work."),
            msg("c1", "active", "We approve your quote for 40 EUR. Please proceed."),
            msg("d1", "closed", "The proposal was declined and we will not proceed."),
        ], self.model)
        self.assertEqual(out["summary"]["awaiting_reply_threads"], 1)
        self.assertEqual(out["summary"]["awaiting_payment_threads"], 1)
        self.assertEqual(out["summary"]["active_work_threads"], 1)
        self.assertEqual(out["summary"]["closed_threads"], 1)

    def test_owner_can_drill_from_metric_to_correspondence(self):
        page = render_html(analyze([msg()], self.model))
        self.assertIn("data-filter='actionable'", page)
        self.assertIn("Open case", page)
        self.assertIn("Open correspondence", page)
        self.assertIn("Search client, subject or message", page)

    def test_settlement_csv_links_to_work_case(self):
        messages, provenance = read_settlements_csv(ROOT / "demo-settlements.csv")
        demo = [item for item in json.loads((ROOT / "demo.json").read_text()) if item["id"] != "a5"]
        out = analyze(demo + messages, self.model)
        atlas = next(t for t in out["threads"] if t["thread_id"] == "atlas-design")
        self.assertEqual(atlas["stage"], "confirmed_received")
        self.assertEqual(atlas["confirmed_received_by_currency"], {"USD": 300.0})
        self.assertEqual(provenance["rows_imported"], 1)
        self.assertEqual(provenance["rows_ignored_not_settled"], 1)

    def test_settlement_csv_rejects_missing_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.csv"
            path.write_text("id,amount\nx,10\n")
            with self.assertRaisesRegex(ValueError, "missing columns"):
                read_settlements_csv(path)

    def test_settlement_csv_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "duplicate.csv"
            path.write_text(
                "id,timestamp,payer,amount,currency,reference,status\n"
                "x,2026-10-16T00:00:00Z,Acme,10,USD,a,settled\n"
                "x,2026-10-16T00:00:00Z,Acme,10,USD,b,settled\n"
            )
            with self.assertRaisesRegex(ValueError, "duplicate settlement id"):
                read_settlements_csv(path)

    def test_cli_settlement_import_end_to_end(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "settled.json"
            input_path = Path(folder) / "without-receipt.json"
            demo = [item for item in json.loads((ROOT / "demo.json").read_text()) if item["id"] != "a5"]
            input_path.write_text(json.dumps(demo))
            p = subprocess.run([
                sys.executable, str(ROOT / "pactsignal.py"), "--input", str(input_path),
                "--settlements-csv", str(ROOT / "demo-settlements.csv"), "--output", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(report["summary"]["confirmed_received_by_currency"], {"USD": 300.0})
            self.assertTrue(report["settlement_import"]["read_only"])

    def test_rules_only_rows_are_exposed(self):
        out = evaluate(self.model)
        self.assertTrue(all("rules_only" in row for row in out["rows"]))

    def test_unresolved_value_uses_agreed_money(self):
        out = analyze([
            msg("a", body="We approve your quote for 90 USD. Please proceed."),
            msg("b", body="We will pay 90 USD tomorrow.", timestamp="2026-10-16T10:00:00Z"),
        ], self.model)
        self.assertEqual(out["summary"]["unresolved_value_by_currency"], {"USD": 90.0})

    def test_case_evidence_packet_is_scoped_and_hashed(self):
        out = analyze([
            msg("a", "one", "We approve your quote for 90 USD. Please proceed."),
            msg("b", "two", "We approve your quote for 50 EUR. Please proceed."),
        ], self.model)
        packet = case_evidence_packet(out, "one")
        self.assertEqual(packet["thread_id"], "one")
        self.assertEqual(len(packet["timeline"]), 1)
        self.assertEqual(len(packet["packet_sha256"]), 64)
        self.assertNotIn("two", json.dumps(packet))

    def test_case_evidence_packet_rejects_unknown_thread(self):
        with self.assertRaisesRegex(ValueError, "unknown thread id"):
            case_evidence_packet(analyze([msg()], self.model), "missing")

    def test_cli_case_export_end_to_end(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "case.json"
            p = subprocess.run([
                sys.executable, str(ROOT / "pactsignal.py"), "--input", str(ROOT / "demo.json"),
                "--export-case", "atlas-design", "--output", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            packet = json.loads(output.read_text())
            self.assertEqual(packet["schema"], "pactsignal-case-evidence-v1")

    def test_live_context_refreshes_settlement_export(self):
        with tempfile.TemporaryDirectory() as folder:
            context = LiveMailboxContext(
                ROOT / "demo-mailbox", Path(folder) / "live.sqlite3", self.model,
                ROOT / "demo-mailbox-settlements.csv",
            )
            context.sync()
            report = context.report()
            self.assertEqual(report["summary"]["confirmed_received_by_currency"], {"EUR": 10.0})
            self.assertEqual(report["settlement_import"]["rows_ignored_not_settled"], 1)
            context.store.close()

    def test_cli_json_end_to_end(self):
        with tempfile.TemporaryDirectory() as folder:
            inp, out = Path(folder)/"in.json", Path(folder)/"out.json"
            inp.write_text(json.dumps([msg()]))
            p = subprocess.run([sys.executable, str(ROOT/"pactsignal.py"), "--input", str(inp), "--output", str(out)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(out.read_text())["summary"]["threads"], 1)

    def test_cli_html_end_to_end(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)/"report.html"
            p = subprocess.run([sys.executable, str(ROOT/"pactsignal.py"), "--format", "html", "--output", str(out)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("PactSignal", out.read_text())

    def test_cli_verify_report_end_to_end(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder)/"analysis.json"
            report.write_text(json.dumps(analyze([msg()], self.model)))
            p = subprocess.run([sys.executable, str(ROOT/"pactsignal.py"), "--verify-report", str(report)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue(json.loads(p.stdout)["verified"])

    def test_local_server_get_and_post(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        p = subprocess.Popen([sys.executable, str(ROOT/"pactsignal.py"), "--serve", "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        try:
            for _ in range(150):
                try:
                    self.assertIn("local analyzer", urlopen(f"http://127.0.0.1:{port}/", timeout=.25).read().decode())
                    break
                except Exception:
                    time.sleep(.1)
            else: self.fail("local server did not start")
            payload = urlencode({"payload": json.dumps([msg()])}).encode()
            page = urlopen(Request(f"http://127.0.0.1:{port}/analyze", data=payload), timeout=2).read().decode()
            self.assertIn("agreement", page)
        finally:
            p.terminate(); p.wait(timeout=3)

    def test_live_mailbox_server_serves_dashboard_across_threads(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as folder:
            p = subprocess.Popen([
                sys.executable, str(ROOT / "pactsignal.py"), "--serve",
                "--mail-source", str(ROOT / "studio-demo-mailbox"),
                "--settlements-csv", str(ROOT / "studio-demo-settlements.csv"),
                "--database", str(Path(folder) / "studio.sqlite3"), "--port", str(port),
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
            try:
                for _ in range(80):
                    try:
                        page = urlopen(f"http://127.0.0.1:{port}/", timeout=1).read().decode()
                        break
                    except Exception:
                        time.sleep(.1)
                else:
                    self.fail("live mailbox dashboard did not start")
                self.assertIn("OWNER CONTROL ROOM", page)
                self.assertIn("Maya Ortiz", page)
                self.assertIn("Open correspondence", page)
            finally:
                p.terminate(); p.wait(timeout=3)

    def test_core_analysis_runs_with_outbound_network_disabled(self):
        with patch("socket.create_connection", side_effect=AssertionError("network attempted")):
            report = analyze([
                msg("offline-1", "offline", "We approve your quote for 90 USD. Please proceed."),
                msg("offline-2", "offline", "The final files were delivered.", timestamp="2026-10-16T10:00:00Z"),
            ], self.model)
        self.assertEqual(report["threads"][0]["stage"], "delivered")

    def test_rfc822_message_parsing(self):
        mail = EmailMessage()
        mail["From"] = "Client <client@example.test>"
        mail["To"] = "worker@example.test"
        mail["Date"] = "Fri, 16 Oct 2026 09:00:00 +0000"
        mail["Message-ID"] = "<mail-1@example.test>"
        mail["Subject"] = "Approved quote"
        mail.set_content("We approve your quote for 80 USD. Please proceed.\n")
        parsed = parse_rfc822(mail.as_bytes(), "fixture.eml")
        self.assertEqual(parsed["id"], "mail-1@example.test")
        self.assertIn("80 USD", parsed["body"])
        self.assertTrue(parsed["thread_id"].startswith("topic-"))

    def test_gmail_connector_requests_raw_read_only_messages(self):
        mail = EmailMessage()
        mail["From"] = "client@example.test"
        mail["To"] = "worker@example.test"
        mail["Date"] = "Fri, 16 Oct 2026 09:00:00 +0000"
        mail["Message-ID"] = "<gmail-fixture@example.test>"
        mail["Subject"] = "Approved"
        mail.set_content("We approve your quote for 25 USD. Please proceed.")
        encoded = base64.urlsafe_b64encode(mail.as_bytes()).decode().rstrip("=")

        class Request:
            def __init__(self, value): self.value = value
            def execute(self): return self.value
        class Messages:
            def list(self, **kwargs):
                self.list_args = kwargs
                return Request({"messages": [{"id": "g1"}]})
            def get(self, **kwargs):
                self.get_args = kwargs
                return Request({"id": "g1", "threadId": "gt1", "raw": encoded})
        class Users:
            def __init__(self, messages): self._messages = messages
            def messages(self): return self._messages
        class Service:
            def __init__(self): self.messages_api = Messages()
            def users(self): return Users(self.messages_api)

        service = Service()
        rows = list(GmailReadOnlyConnector(service, query="label:test", max_messages=5).iter_messages())
        self.assertEqual(rows[0][0]["thread_id"], "gmail-gt1")
        self.assertEqual(service.messages_api.get_args["format"], "raw")
        self.assertEqual(GMAIL_READONLY_SCOPE, "https://www.googleapis.com/auth/gmail.readonly")

    def test_mailbox_sync_is_incremental(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mail = EmailMessage()
            mail["From"] = "client@example.test"
            mail["To"] = "worker@example.test"
            mail["Date"] = "Fri, 16 Oct 2026 09:00:00 +0000"
            mail["Message-ID"] = "<mail-2@example.test>"
            mail["Subject"] = "Payment"
            mail.set_content("We will pay 80 USD tomorrow.")
            (root / "one.eml").write_bytes(mail.as_bytes())
            store = MailboxStore(root / "state.sqlite3")
            try:
                first, second = sync_source(root, store), sync_source(root, store)
                self.assertEqual((first.scanned, first.imported), (1, 1))
                self.assertEqual((second.scanned, second.imported, second.unchanged), (1, 0, 1))
                self.assertEqual(len(store.messages()), 1)
            finally:
                store.close()

    def test_local_confirmation_is_persisted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = MailboxStore(root / "state.sqlite3")
            mail = EmailMessage()
            mail["From"] = "service@paypal.example"
            mail["To"] = "worker@example.test"
            mail["Date"] = "Fri, 16 Oct 2026 09:00:00 +0000"
            mail["Message-ID"] = "<receipt@example.test>"
            mail["Subject"] = "Payment completed"
            mail.set_content("Payment completed successfully for 10 EUR.")
            raw = mail.as_bytes()
            parsed = parse_rfc822(raw, "receipt.eml")
            try:
                store.upsert(parsed, raw)
                store.confirm_received("receipt@example.test")
                self.assertEqual(store.messages()[0]["confirmation"], "user_confirmed_received")
            finally:
                store.close()

    def test_sync_mail_cli_end_to_end(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mail = EmailMessage()
            mail["From"] = "client@example.test"
            mail["To"] = "worker@example.test"
            mail["Date"] = "Fri, 16 Oct 2026 09:00:00 +0000"
            mail["Message-ID"] = "<mail-3@example.test>"
            mail["Subject"] = "Delivery"
            mail.set_content("The final report has been delivered for review.")
            (root / "one.eml").write_bytes(mail.as_bytes())
            out = root / "result.json"
            db = root / "state.sqlite3"
            p = subprocess.run([
                sys.executable, str(ROOT / "pactsignal.py"), "--sync-mail",
                "--mail-source", str(root), "--database", str(db), "--output", str(out)
            ], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            report = json.loads(out.read_text())
            self.assertEqual(report["mailbox_sync"]["imported"], 1)
            self.assertEqual(report["threads"][0]["stage"], "delivered")


if __name__ == "__main__": unittest.main(verbosity=2)
