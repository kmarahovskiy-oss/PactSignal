---
title: PactSignal: The conversation moved. The agreement should not disappear.
published: false
tags: devchallenge, hf26challenge
---

*Built for the Hacktoberfest Open-Source AI Challenge Week 2: Side Hustle.*

## What I Built


Monday morning. One client has asked for extra work in Telegram. Another has promised payment by email. A third says the money was sent through a WhatsApp message. An employee is away, and a colleague must take over their cases.

The manager’s question is practical: which of these situations needs a decision today?

PactSignal is a prototype for answering that question from the history of a client relationship. Its central idea is a case that preserves the current agreement, the changes leading to it, the responsible person and the evidence behind the next action.

## The expensive gap between a message and a commitment

A request is not necessarily an approved change. An old approval may no longer describe the current scope. A promise to pay does not establish receipt. When those distinctions get lost, a team can do unpaid extra work, follow up on the wrong issue or leave a client waiting.

The useful outcome is a short working view: these cases need attention, this is what changed, and here is the message that explains why. That matters on an ordinary working day as much as during a handover.

## Follow the agreement as it changes

Our fictional photography case begins with Rowan approving ten images for EUR 300, due Friday, by email. In Telegram, the client asks about twenty additional images—but wants a quote before approving them. Nina then takes over from Alex. A WhatsApp message claims that EUR 300 has been sent. Finally, an email withdraws the request for extras and keeps the original scope.

The important story is the sequence of decisions: original scope approved; extras proposed but not approved; responsibility transferred; payment reported but not verified; request for extras withdrawn.

PactSignal brings that sequence into the main case card. Nina can see the current scope and next action, then expand the chronological history and inspect the source messages. The intended difference is an inspectable history of the commitment: what changed, whether it was accepted and what remains unresolved.

## Case at a glance — fictional demonstration

| Field | Current state |
| --- | --- |
| Agreed scope | 10 images · EUR 300 · Friday |
| Additional-work request | Withdrawn; never approved |
| Responsible person | Nina |
| Payment | EUR 300 reported sent; receipt unverified |
| Next action | Verify receipt before marking the case paid |

The showcase uses fictional participants and messages, with the cross-channel association supplied by the case fixture. Live email, Telegram and WhatsApp connections and automatic identity matching are not implemented. A second, repair-workshop example demonstrates another business setting.

## A working view for the manager

The showcase lets a reviewer filter cases needing action, payment risks, work awaiting a client and closed cases. From the overview, the reviewer can open a case and inspect its evidence. A payment claim remains distinct from a confirmed receipt.

The product ambition is to make this an everyday routine: review exceptions, understand changes and assign the next action without repeatedly reconstructing the conversation. When a colleague takes over, the same history should preserve continuity.

PactSignal is intended to complement a CRM and accounting system. CRMs may already include conversation history and AI summaries; our focus is the traceable evolution of an agreement across channels. A small team could use it as a lightweight case workspace. A larger business could eventually connect it to existing records.

## Who would buy it—and why?

The first customer group to validate is small service teams with repeat client work and frequent scope changes, such as creative studios or repair businesses. The daily user handles the cases; the likely buyer is an owner or operations lead concerned about unresolved commitments and dependence on individual memory.

The commercial hypothesis is a team-workspace subscription with an included allowance for active cases and processing. The paid value would be maintained connections, up-to-date cases, shared responsibility and access controls. These service capabilities remain development work, not a description of a finished subscription product.

Before setting a price, a consented pilot should compare case-reconstruction time, correction effort and detection of unresolved decisions against the team’s existing process. Any saving must include the time spent checking the system. Paying customers, validated savings and unit economics are not yet established.

## Build automation around human judgment

The next step is authorized read-only connections: select permitted conversations, review proposed associations and correct uncertain conclusions. Later, CRM and authenticated payment-provider records could add corroborating evidence. Customers would need clear controls over access, retention and deletion.

That progression follows the business case rather than demanding a wholesale change of tools. It also preserves a boundary: analysis can prepare a decision; a message saying “paid” cannot authenticate a financial transaction.

## Try the harder case yourself

The decision challenge extends the simple case into two client workspaces, each with separate orders. The initial extra-work quote is EUR 180 against a EUR 150 authorization limit. A fictional EUR 100 receipt belongs to ST-24, while the client’s EUR 300 claim initially names no order. A requested Thursday deadline is still unconfirmed.

1. [Open the decision challenge](https://pactsignal-showcase.wyyy5h2x5k.chatgpt.site/scenario). Inspect why the agreed value stays at EUR 300 and why the payment claim remains unallocated.
2. Approve the extra quote or withdraw the request. Compare the agreed scope and balance; approval within the original limit followed by withdrawal instead requires cancellation agreement.
3. Use the Email, Telegram and WhatsApp panels to add fictional messages at chosen scenario times. Confirm a supported interpretation and inspect the changed card and source history.
4. Switch clients or owners. Verify that another client’s cases and the second order’s receipts do not change.
5. [Try arbitrary English text in the model lab](https://pactsignal-showcase.wyyy5h2x5k.chatgpt.site/lab) to inspect the classifier separately.

If a message cannot be interpreted automatically, the reviewer can specify its order and meaning, inspect the proposed effect and confirm a human interpretation. Both orders support scope, date and owner changes. The session summary records actual confirmed actions and unresolved messages; it is not an accuracy score.

The event challenge uses explicit rules and a limited, confirmation-based message parser. It is not unrestricted language understanding. The English classifier provides a separate intent suggestion. Case IDs are local to each selected client workspace; this is an explicit fixture association, not automatic identity resolution. State is held in the current page, not saved to an account. The original simple case remains available in the overview.

## Try what exists today

The live showcase offers prepared cases, filters, source messages and the cross-channel handover. The model lab lets visitors replace an English message and run actual inference locally in the browser, inspecting competing scores and influential features. The Python prototype combines an open-source text classifier with explicit workflow rules; the lab exposes the raw classifier alongside the separate contextual evidence review.

A fresh synthetic check produced 13 correct labels out of 20 for the classifier and 14 with existing rules. The JavaScript port matched Python on all twenty at the reported precision. These results expose a generalization weakness, not production validation. Full examples and technical details are linked below.

AI assisted development, writing and synthetic tests. The owner personally explored the interface: feedback on navigation and the difficulty of finding the cross-channel history led to revisions and its placement in the main case card. Automated tests remain distinct from human review.

## The result we want

On Monday morning, the manager should see which cases need a decision and why. When Nina takes over, she should inherit the agreement’s history—not a guessing exercise.

PactSignal’s aim is to make the business’s commitments understandable, reviewable and continuous, even when the conversation moves.


The browser review now adds a separate contextual layer: it distinguishes multiple events, quotations, conditions and negation, and flags ambiguous amounts. Its first synthetic check matched 35 of 36 expected event sets; after the known miss was fixed, the same 36 became passing development regressions. These results do not replace the raw-model score or establish accuracy on real correspondence. Every proposed business change still requires explicit confirmation.


## Demo

[Live on Render](https://pactsignal.onrender.com/) · [Render judge sandbox](https://pactsignal.onrender.com/scenario.html) · [Render model lab](https://pactsignal.onrender.com/lab.html).

Two separate English videos: [Product overview](https://pactsignal.onrender.com/video.html#overview) and [Workflow walkthrough](https://pactsignal.onrender.com/video.html#workflow).

[Open the complete review kit](https://pactsignal-showcase.wyyy5h2x5k.chatgpt.site/final) · [Interactive judge challenge](https://pactsignal-showcase.wyyy5h2x5k.chatgpt.site/scenario) · [English videos](https://pactsignal-showcase.wyyy5h2x5k.chatgpt.site/video).

## Code

[Inspect the public source repository](https://github.com/kmarahovskiy-oss/PactSignal). It includes the browser frontend, exported model, Python prototype and checks. Public examples are fictional; no private mailbox or business data is included.

## How I Built It

The Python prototype uses scikit-learn's open-source TF-IDF vectorizers and logistic regression, trained on 120 fictional examples. Exported feature vocabulary, inverse-document-frequency values, coefficients and intercepts run through a JavaScript implementation in the visitor's browser. The visible model lab performs actual inference; it is not a remote proprietary model call. Conservative contextual rules handle quotations, negation, conditions, multiple events and ambiguous monetary formats alongside that model. Explicit confirmation governs supported changes in the judge sandbox.

## Why Does Open Innovation Matter?

The trained weights, feature contributions and inference path can be inspected and run without paid inference or transmitting the entered message to a model server. A small team could adapt the classifier to its vocabulary and measure the result. The present synthetic tests also expose where that approach is insufficient; openness makes those limits inspectable rather than hiding them behind a summary.

## My First User

The project owner exercised the interface and requested corrections to navigation, filters, mixed-channel history and monetary alignment. This is owner feedback, not an independent customer pilot or evidence of willingness to pay. AI assisted implementation, writing and tests.

## Prize Categories

This entry requests consideration for the Overall prize and Best Use of Render. Render hosts the interactive PactSignal frontend, judge sandbox and exported model assets at https://pactsignal.onrender.com/. Model inference runs locally in the visitor’s browser; Render is not running a server-side model or connecting to private accounts. The ChatGPT-hosted showcase is an additional mirror. One entry can receive at most one prize; category eligibility and awards are decided by the organizers.
