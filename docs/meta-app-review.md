# Meta App Review — submission pack

App: **Stepan by Zapleo** · ID `2315128069295857` · Business `237014350216399`
Domain `stepan2.zapleo.com` · demo branch **TEST (7)**, channel 16, Page *Zapleo Soft*.

Meta retired the old `/app-review/permissions/` page — that URL now redirects to Use Cases.
Everything below is submitted from **Use cases → Customize → Permissions → Actions → "Add to
app review request"**, per permission.

## State as of 2026-08-02

| Item | State |
|---|---|
| Business verification | done |
| App icon, display name, category (Messaging) | done |
| Privacy / Terms / Data deletion URLs | live, HTTP 200 |
| App domain `zapleo.com` | set |
| Required actions | none outstanding |
| Use cases | Messenger + Instagram, both configured |
| Permissions with live API calls | 7, all "Ready for testing" |
| App mode | **Not published** |
| Review request | **not created yet** |
| Screencasts | **not recorded yet** |

API-call counts Meta already sees: `business_management`, `pages_messaging`,
`pages_read_engagement`, `pages_show_list`, `pages_manage_metadata` — 5.8k each;
`instagram_basic`, `instagram_manage_messages` — 2.9k each. Calls must be **within 30 days**
of submission; they are current as of this file's date, so submit without a long pause.

## The one hard constraint on recording

Meta requires the **cursor to be visible** in every screencast. Claude cannot record these:
the tooling that drives a real browser clicks through the DOM without moving the physical
cursor, and the tooling that moves the real cursor treats browsers as read-only. So the
recording is done by a human. Claude prepares the scenarios, watches the server logs during
each take, and confirms the call actually landed.

Format: 1080p, ≤1440 px wide, one video **per permission**, no test credentials on screen,
English UI preferred.

## Recording scenarios

Record the connect flow **once** (take A) — it evidences four permissions — then two short
message takes. Trim one copy per permission and give each its own description.

### Take A — connecting a business (consent screen)

Evidences `pages_show_list`, `pages_read_engagement`, `pages_manage_metadata`,
`business_management`.

1. Open `https://stepan2.zapleo.com`, sign in, go to the connector page for the demo branch.
2. Click **Connect Facebook**. The Meta consent dialog must be fully visible — this is the
   screen Meta specifically wants to see.
3. Show the requested permissions on the dialog, continue, pick the Page **Zapleo Soft**,
   confirm.
4. Land back on the connector page showing the Page connected.

### Take B — replying on Messenger

Evidences `pages_messaging`.

1. From a second account, message the Zapleo Soft Page on Messenger.
2. Show the message arriving in the Stepan inbox.
3. Show the reply going out and appearing in Messenger on the sender's side.

### Take C — replying on Instagram Direct

Evidences `instagram_basic`, `instagram_manage_messages`.

1. From a second account, DM `@zapleosoft`.
2. Show it arriving in the same inbox, with the Instagram badge.
3. Show the reply delivered in Direct.

## Permission descriptions

One per permission — Meta rejects submissions that repeat the same text. English, states what
the app does with the data and why the feature cannot work without it.

**pages_show_list** — After a business owner grants access, we show them the list of Facebook
Pages they manage so they can choose which single Page to connect to Stepan. Without it we
cannot present that choice and the owner could not tell us which Page the assistant should
answer for. We store only the id and name of the Page they pick.

**pages_messaging** — Stepan replies to people who message the connected Page on Messenger. We
read the incoming conversation to understand the question and send one reply back inside the
standard messaging window. This is the core function of the product: the business is buying an
assistant that answers its customers, which is impossible without sending and receiving Page
messages.

**pages_read_engagement** — We read the connected Page's own profile and the identity of the
person writing in, so a conversation is attributed to the right customer and the reply is
addressed to them by name. Without it every conversation would be anonymous and our inbox
could not tell two customers apart.

**pages_manage_metadata** — We subscribe the connected Page to message webhooks so that an
incoming customer message reaches us immediately. Without the subscription we would have to
poll continuously, which is slower for the customer and heavier on Meta's infrastructure.

**instagram_basic** — We read the Instagram professional account linked to the connected Page:
its id and username. This tells us which participant in a Direct thread is the business itself
and which is the customer, so we reply to the customer rather than to our own account.

**instagram_manage_messages** — Stepan reads and answers messages the business receives in
Instagram Direct — the same assistant function as on Messenger, on the channel most of our
customers actually use. Without it the product cannot serve Instagram businesses at all.

**business_management** — We resolve which Business the connected Page belongs to and obtain
the Page access token scoped to it, which is how Meta requires an app to act for a business
asset. It is also how we keep one customer's data strictly separated from another's.

## Order of operations

1. Record takes A, B, C (human at the keyboard; Claude verifies each call in the logs).
2. Add each of the 7 permissions to the review request.
3. Paste the matching description and upload the matching video.
4. Submit. Publishing the app is a separate switch, done after approval.
