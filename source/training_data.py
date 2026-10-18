"""Transparent synthetic training and stress data for PactSignal.

Every sentence is fictional and AI-authored.  The two sets use different
templates, names and amounts.  This is engineering test data, not customer
validation or an independent benchmark.
"""

LABELS = (
    "offer", "agreement", "delivery", "payment_promise",
    "payment_notice", "payment_failure", "rejection", "admin",
)

_TRAIN = {
    "offer": [
        "Our budget is {amount} for the requested audit.",
        "The published reward is {amount} for the best accepted entry.",
        "Could you quote this project? We can spend about {amount}.",
        "I can offer {amount} for the complete article.",
        "This task pays {amount} after acceptance.",
    ],
    "agreement": [
        "We accept your proposal for {amount}.",
        "Approved: deliver the package for {amount}.",
        "I agree to the scope and price of {amount}.",
        "Your quote for {amount} is confirmed; please begin.",
        "We have a deal at {amount} for the stated deliverables.",
    ],
    "delivery": [
        "The final files have been delivered for review.",
        "I submitted the completed article and source archive.",
        "Attached is the finished report matching the agreed scope.",
        "The implementation is complete and the tests pass.",
        "Here is the final deliverable for acceptance.",
    ],
    "payment_promise": [
        "We will pay {amount} tomorrow.",
        "Payment of {amount} will be sent after review.",
        "You should receive {amount} next week.",
        "We plan to transfer {amount} when finance approves it.",
        "The {amount} payout is pending acceptance.",
    ],
    "payment_notice": [
        "We paid {amount}; transaction reference DEMO-{n}.",
        "Payment completed successfully for {amount}.",
        "You received {amount} from the client.",
        "The transfer of {amount} has been completed.",
        "Receipt: {amount} was credited to your account.",
    ],
    "payment_failure": [
        "Payment of {amount} failed and no funds arrived.",
        "The {amount} transfer was reversed.",
        "You have not received the promised {amount}.",
        "The payout was rejected by the provider.",
        "The attempted payment is still missing.",
    ],
    "rejection": [
        "We declined the proposal and will not proceed.",
        "Your submission was not selected for the reward.",
        "The client cancelled the assignment before work started.",
        "We cannot accept this quote.",
        "The application is rejected with no further work requested.",
    ],
    "admin": [
        "Your support ticket DEMO-{n} is open.",
        "Please update the contact information on the form.",
        "This automated message confirms receipt of your enquiry.",
        "The meeting has moved to Thursday afternoon.",
        "Here are the documentation links you requested.",
    ],
}

_AMOUNTS = ("40 USD", "75 EUR", "250 UAH", "12 USDC")


def training_rows():
    rows = []
    for label, templates in _TRAIN.items():
        for i, template in enumerate(templates):
            for j, amount in enumerate(_AMOUNTS[:3]):
                rows.append((label, template.format(amount=amount, n=i * 3 + j + 1)))
    return rows  # 8 labels × 5 templates × 3 variations = 120


STRESS_ROWS = [
    ("offer", "A 90 GBP honorarium is available to the chosen contributor."),
    ("offer", "The ceiling for this engagement is €125, subject to selection."),
    ("offer", "Would 300 UAH cover the initial review?"),
    ("offer", "First accepted correction earns $20."),
    ("agreement", "Green light from us: the fixed fee is £90."),
    ("agreement", "The scope in your last message is authorised at 110 EUR."),
    ("agreement", "Proceed with milestone one; 500 UAH is approved."),
    ("agreement", "We confirm both deliverables and the quoted $45."),
    ("delivery", "The signed-off export and reproducibility notes are now attached."),
    ("delivery", "I have handed over the final build and checksum list."),
    ("delivery", "Submission is complete; the reviewer can reproduce all steps."),
    ("delivery", "Final copy delivered at 14:20 UTC with the source bundle."),
    ("payment_promise", "Finance expects to release £90 on Friday."),
    ("payment_promise", "You will get 18 USDC if the editor approves the piece."),
    ("payment_promise", "The money is scheduled but has not left our account."),
    ("payment_promise", "Upon acceptance, €125 is due within ten business days."),
    ("payment_notice", "Credit advice: €125 settled, reference SYN-441."),
    ("payment_notice", "The client sent £90 and marked the invoice paid."),
    ("payment_notice", "Settlement complete: 500 UAH credited."),
    ("payment_notice", "Transaction SYN-918 confirms a transfer of 18 USDC."),
    ("payment_failure", "The bank returned the €125 transfer unpaid."),
    ("payment_failure", "No credit was received; transaction SYN-441 failed."),
    ("payment_failure", "The provider cancelled the payout after initiation."),
    ("payment_failure", "Although marked paid, the funds never arrived."),
    ("rejection", "We chose another contributor and closed the assignment."),
    ("rejection", "This entry did not win and no payment is due."),
    ("rejection", "The proposed engagement will not move forward."),
    ("rejection", "Budget approval was denied; please stop work."),
    ("admin", "Case SYN-77 was routed to the billing team."),
    ("admin", "Please confirm which timezone you use."),
    ("admin", "The portal will be unavailable for maintenance."),
    ("admin", "We received your question and will reply separately."),
]

# Sentences intentionally combine competing signals.  A useful system should
# abstain or apply the high-stakes guard rather than treating a word as proof.
ADVERSARIAL_ROWS = [
    ("payment_failure", "It says paid, but payment was not received."),
    ("payment_promise", "We agreed on 80 USD and will pay after acceptance."),
    ("offer", "If selected, the winner may receive 100 USD."),
    ("rejection", "The proposal mentioned payment, but we declined it."),
    ("payment_failure", "A receipt was issued, then the transfer was reversed."),
    ("payment_promise", "Payment pending: 50 EUR is expected next week."),
    ("admin", "Can you explain what 'payment received' means in the guide?"),
    ("delivery", "Delivered the work; payment status is unknown."),
]
